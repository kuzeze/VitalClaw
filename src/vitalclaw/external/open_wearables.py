"""REST client for the Open Wearables API."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from vitalclaw.runtime import DEFAULT_OPEN_WEARABLES_API_URL


@dataclass(slots=True)
class OpenWearablesConnection:
    """One connected wearable provider for a user."""

    id: str
    user_id: str
    provider: str
    status: str
    last_synced_at: str | None = None
    provider_username: str | None = None


class OpenWearablesClient:
    """Minimal client for the Open Wearables API."""

    def __init__(self, *, api_key: str, api_url: str | None = None) -> None:
        self.api_key = api_key.strip()
        self.api_url = _normalize_base_url(api_url or DEFAULT_OPEN_WEARABLES_API_URL)
        if not self.api_key:
            raise RuntimeError("Open Wearables API key is not configured.")

    def developer_login(self, *, email: str, password: str) -> str:
        data = urlencode({"username": email, "password": password}).encode("utf-8")
        request = Request(
            f"{self.api_url}/api/v1/auth/login",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Open Wearables developer login failed ({exc.code}): {body}") from exc
        payload = json.loads(raw or "{}")
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Open Wearables developer login did not return an access token.")
        return token

    def create_user(
        self,
        *,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        external_user_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "external_user_id": external_user_id,
        }
        return self._request_json("POST", "/api/v1/users", payload={k: v for k, v in payload.items() if v is not None}, expected_status={201})

    def list_users(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._request_json("GET", "/api/v1/users", params={"page": page, "limit": 100})
            page_items = payload.get("items", [])
            if not isinstance(page_items, list):
                raise RuntimeError("Unexpected Open Wearables users response.")
            items.extend(item for item in page_items if isinstance(item, dict))
            if not payload.get("has_next"):
                return items
            page += 1

    def get_user(self, user_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/v1/users/{user_id}")

    def list_connections(self, user_id: str) -> list[OpenWearablesConnection]:
        payload = self._request_json("GET", f"/api/v1/users/{user_id}/connections")
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Open Wearables connections response.")
        connections: list[OpenWearablesConnection] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            connections.append(
                OpenWearablesConnection(
                    id=str(item.get("id") or ""),
                    user_id=str(item.get("user_id") or user_id),
                    provider=str(item.get("provider") or "unknown"),
                    status=str(item.get("status") or "unknown"),
                    last_synced_at=_optional_str(item.get("last_synced_at")),
                    provider_username=_optional_str(item.get("provider_username")),
                )
            )
        return connections

    def list_providers(self, *, enabled_only: bool = True, cloud_only: bool = False) -> list[dict[str, Any]]:
        payload = self._request_json(
            "GET",
            "/api/v1/oauth/providers",
            params={"enabled_only": str(enabled_only).lower(), "cloud_only": str(cloud_only).lower()},
        )
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Open Wearables providers response.")
        return [item for item in payload if isinstance(item, dict)]

    def generate_invitation_code(self, user_id: str, *, developer_token: str | None = None) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/api/v1/users/{user_id}/invitation-code",
            expected_status={201},
            bearer_token=developer_token,
            use_api_key=not bool(developer_token),
        )

    def get_recovery_summary(self, *, user_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return self._cursor_results(
            f"/api/v1/users/{user_id}/summaries/recovery",
            params={"start_date": start_date, "end_date": end_date, "limit": 100},
        )

    def get_sleep_summary(self, *, user_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return self._cursor_results(
            f"/api/v1/users/{user_id}/summaries/sleep",
            params={"start_date": start_date, "end_date": end_date, "limit": 100},
        )

    def get_timeseries(
        self,
        *,
        user_id: str,
        start_time: str,
        end_time: str,
        types: list[str],
        resolution: str = "raw",
    ) -> list[dict[str, Any]]:
        return self._cursor_results(
            f"/api/v1/users/{user_id}/timeseries",
            params={
                "start_time": start_time,
                "end_time": end_time,
                "types": types,
                "resolution": resolution,
                "limit": 100,
            },
        )

    def trigger_provider_sync(self, *, provider: str, user_id: str, historical: bool = False) -> dict[str, Any] | None:
        endpoint = (
            f"/api/v1/providers/{provider}/users/{user_id}/sync/historical"
            if historical
            else f"/api/v1/providers/{provider}/users/{user_id}/sync"
        )
        payload = self._request_json("POST", endpoint, expected_status={200, 201, 202, 204})
        return payload if isinstance(payload, dict) else None

    def _cursor_results(self, path: str, *, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            payload = self._request_json("GET", path, params=page_params)
            page_items = payload.get("data", [])
            if not isinstance(page_items, list):
                raise RuntimeError(f"Unexpected Open Wearables cursor response for {path}.")
            items.extend(item for item in page_items if isinstance(item, dict))
            pagination = payload.get("pagination", {})
            if not isinstance(pagination, dict) or not pagination.get("has_more"):
                return items
            cursor = _optional_str(pagination.get("next_cursor"))
            if not cursor:
                return items

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        expected_status: set[int] | None = None,
        bearer_token: str | None = None,
        use_api_key: bool = True,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if use_api_key:
            headers["X-Open-Wearables-API-Key"] = self.api_key
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        url = f"{self.api_url}{path}"
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        request = Request(url, data=data, headers=headers, method=method)
        allowed = expected_status or {200}
        try:
            with urlopen(request) as response:  # noqa: S310
                status = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Open Wearables request failed ({exc.code}) for {method} {url}: {body}") from exc
        if status not in allowed:
            raise RuntimeError(f"Open Wearables request failed ({status}) for {method} {url}: {raw}")
        if not raw.strip():
            return {}
        return json.loads(raw)


def _normalize_base_url(value: str) -> str:
    base = value.strip().rstrip("/")
    for suffix in ("/api/v1", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base or DEFAULT_OPEN_WEARABLES_API_URL


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
