from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4


def write_fake_he(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "fake-he-data.json"
    dataset = {
        "types": [
            {"id": 24, "name": "Time asleep", "category": "record", "subcategory": "Sleep"},
            {"id": 88, "name": "Resting heart rate", "category": "record", "subcategory": "Heart"},
            {"id": 89, "name": "Heart rate variability (SDNN)", "category": "record", "subcategory": "Heart"},
            {"id": 90, "name": "Respiratory rate", "category": "record", "subcategory": "Respiration"},
            {"id": 91, "name": "Wrist temperature", "category": "record", "subcategory": "Temperature"},
        ],
        "packages": _fake_packages(),
    }
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    script_path = tmp_path / "he"
    script_path.write_text(
        _fake_he_script(dataset_path),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    return script_path


def _fake_packages() -> list[dict]:
    days = [
        ("2026-04-07T07:00:00Z", 7.8, 56, 41, 14.0, 36.45),
        ("2026-04-08T07:00:00Z", 7.7, 56, 42, 14.1, 36.40),
        ("2026-04-09T07:00:00Z", 7.6, 57, 41, 14.0, 36.50),
        ("2026-04-10T07:00:00Z", 7.5, 56, 40, 13.9, 36.48),
        ("2026-04-11T07:00:00Z", 7.6, 55, 43, 14.0, 36.47),
        ("2026-04-12T07:00:00Z", 7.8, 56, 42, 14.2, 36.42),
        ("2026-04-13T07:00:00Z", 7.7, 57, 42, 14.1, 36.46),
        ("2026-04-14T07:00:00Z", 7.6, 56, 41, 14.0, 36.44),
        ("2026-04-15T07:00:00Z", 7.5, 56, 40, 14.1, 36.43),
        ("2026-04-16T07:00:00Z", 7.7, 55, 42, 14.0, 36.45),
        ("2026-04-17T07:00:00Z", 5.6, 64, 28, 16.0, 36.95),
    ]
    return [
        _package(24, "Time asleep", "hours", [{"time": ts, "value": str(sleep)} for ts, sleep, *_ in days]),
        _package(88, "Resting heart rate", "beats/min", [{"time": ts, "value": str(rhr)} for ts, _, rhr, *_ in days]),
        _package(89, "Heart rate variability (SDNN)", "ms", [{"time": ts, "value": str(hrv)} for ts, *_, hrv, __, ___ in days]),
        _package(90, "Respiratory rate", "breaths/min", [{"time": ts, "value": str(resp)} for ts, *_, resp, __ in days]),
        _package(91, "Wrist temperature", "degC", [{"time": ts, "value": str(temp)} for ts, *_, temp in days]),
    ]


def _package(type_id: int, name: str, units: str, records: list[dict]) -> dict:
    return {"type": type_id, "type_name": name, "data": [{"units": units, "records": records}]}


def _fake_he_script(dataset_path: Path) -> str:
    return f"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

DATA = json.loads(Path({str(dataset_path)!r}).read_text())
XDG = Path(os.environ["XDG_CONFIG_HOME"]) / "healthexport"
XDG.mkdir(parents=True, exist_ok=True)
CFG = XDG / "config.json"

def load_cfg():
    if CFG.exists():
        return json.loads(CFG.read_text())
    return {{}}

def save_cfg(cfg):
    CFG.write_text(json.dumps(cfg))

args = sys.argv[1:]
cfg = load_cfg()

if args == ["version"]:
    print("he version v0-test")
    sys.exit(0)

if args[:2] == ["config", "set"]:
    cfg[args[2]] = args[3]
    save_cfg(cfg)
    print(f"Config updated: {{args[2]}} = {{args[3]}}", file=sys.stderr)
    sys.exit(0)

if args[:2] == ["auth", "status"]:
    if cfg.get("account_key"):
        print("Authenticated", file=sys.stderr)
        print("  Account key: ********", file=sys.stderr)
        print("  UID: fakeuid", file=sys.stderr)
        print("  Source: config", file=sys.stderr)
        sys.exit(0)
    print("Not authenticated", file=sys.stderr)
    sys.exit(2)

if args[:2] == ["types", "--format"]:
    print(json.dumps(DATA["types"]))
    sys.exit(0)

if args[:2] == ["mcp", "status"]:
    print(json.dumps({{"authenticated": bool(cfg.get("account_key")), "he_version": "v0-test"}}))
    sys.exit(0)

if args and args[0] == "data":
    requested = []
    for index, item in enumerate(args):
        if item == "--type":
            requested.append(int(args[index + 1]))
    packages = [pkg for pkg in DATA["packages"] if pkg["type"] in requested]
    print(json.dumps(packages))
    sys.exit(0)

print("unexpected args: " + " ".join(args), file=sys.stderr)
sys.exit(1)
"""


class FakeOpenWearablesServer(AbstractContextManager["FakeOpenWearablesServer"]):
    def __init__(
        self,
        *,
        users: list[dict[str, Any]] | None = None,
        connections: list[dict[str, Any]] | None = None,
        recovery: list[dict[str, Any]] | None = None,
        sleep: list[dict[str, Any]] | None = None,
        timeseries: list[dict[str, Any]] | None = None,
        providers: list[dict[str, Any]] | None = None,
        expected_api_key: str = "ow-test-key",
        developer_email: str = "admin@admin.com",
        developer_password: str = "your-secure-password",
    ) -> None:
        self.state: dict[str, Any] = {
            "users": list(users or []),
            "connections": list(connections or []),
            "recovery": list(recovery or []),
            "sleep": list(sleep or []),
            "timeseries": list(timeseries or []),
            "providers": list(providers or []),
            "expected_api_key": expected_api_key,
            "developer_email": developer_email,
            "developer_password": developer_password,
            "developer_token": "dev-token",
            "last_request_headers": {},
            "triggered_providers": [],
            "generated_codes": [],
        }
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self.api_url: str | None = None

    def __enter__(self) -> "FakeOpenWearablesServer":
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def _json_response(self, payload: Any, *, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json_body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or "0")
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8")
                return json.loads(raw) if raw.strip() else {}

            def _read_form_body(self) -> dict[str, str]:
                length = int(self.headers.get("Content-Length") or "0")
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8")
                return {key: values[0] for key, values in parse_qs(raw).items()}

            def _require_api_key(self) -> bool:
                state["last_request_headers"] = dict(self.headers.items())
                if self.headers.get("X-Open-Wearables-API-Key") != state["expected_api_key"]:
                    self._json_response({"detail": "Unauthorized"}, status=401)
                    return False
                return True

            def _require_developer_token(self) -> bool:
                state["last_request_headers"] = dict(self.headers.items())
                if self.headers.get("Authorization") != f"Bearer {state['developer_token']}":
                    self._json_response({"detail": "Could not validate credentials"}, status=401)
                    return False
                return True

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)

                if path == "/":
                    self._json_response({"message": "Server is running!"})
                    return

                if not self._require_api_key():
                    return

                if path == "/api/v1/users":
                    self._json_response(
                        {
                            "items": state["users"],
                            "total": len(state["users"]),
                            "page": 1,
                            "limit": 100,
                            "pages": 1,
                            "has_next": False,
                            "has_prev": False,
                        }
                    )
                    return

                if path.startswith("/api/v1/users/") and path.endswith("/connections"):
                    user_id = path.split("/")[4]
                    payload = [item for item in state["connections"] if str(item.get("user_id")) == user_id]
                    self._json_response(payload)
                    return

                if path.startswith("/api/v1/users/") and "/summaries/recovery" in path:
                    self._json_response({"data": state["recovery"], "pagination": {"has_more": False}, "metadata": {}})
                    return

                if path.startswith("/api/v1/users/") and "/summaries/sleep" in path:
                    self._json_response({"data": state["sleep"], "pagination": {"has_more": False}, "metadata": {}})
                    return

                if path.startswith("/api/v1/users/") and path.endswith("/timeseries"):
                    requested_types = set(query.get("types", []))
                    payload = [item for item in state["timeseries"] if not requested_types or str(item.get("type")) in requested_types]
                    self._json_response({"data": payload, "pagination": {"has_more": False}, "metadata": {}})
                    return

                if path == "/api/v1/oauth/providers":
                    self._json_response(state["providers"])
                    return

                if path.startswith("/api/v1/users/"):
                    user_id = path.split("/")[4]
                    for user in state["users"]:
                        if str(user.get("id")) == user_id:
                            self._json_response(user)
                            return
                    self._json_response({"detail": "Not found"}, status=404)
                    return

                self._json_response({"detail": f"Unhandled GET {path}"}, status=404)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/api/v1/auth/login":
                    payload = self._read_form_body()
                    if (
                        payload.get("username") == state["developer_email"]
                        and payload.get("password") == state["developer_password"]
                    ):
                        self._json_response(
                            {
                                "access_token": state["developer_token"],
                                "token_type": "bearer",
                                "refresh_token": "rt-dev-token",
                                "expires_in": 3600,
                            }
                        )
                    else:
                        self._json_response({"detail": "Incorrect email or password"}, status=401)
                    return

                if path.startswith("/api/v1/users/") and path.endswith("/invitation-code"):
                    if not self._require_developer_token():
                        return
                    user_id = path.split("/")[4]
                    invitation = {
                        "id": str(uuid4()),
                        "code": f"{len(state['generated_codes']) + 1:08d}".replace("0", "A"),
                        "user_id": user_id,
                        "expires_at": datetime.now(timezone.utc).isoformat(),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    state["generated_codes"].append(invitation)
                    self._json_response(invitation, status=201)
                    return

                if not self._require_api_key():
                    return

                if path == "/api/v1/users":
                    payload = self._read_json_body()
                    user = {
                        "id": str(uuid4()),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "first_name": payload.get("first_name"),
                        "last_name": payload.get("last_name"),
                        "email": payload.get("email"),
                        "external_user_id": payload.get("external_user_id"),
                    }
                    state["users"].append(user)
                    self._json_response(user, status=201)
                    return

                if path.startswith("/api/v1/providers/") and path.endswith("/sync"):
                    provider = path.split("/")[4]
                    state["triggered_providers"].append(provider)
                    self._json_response({"provider": provider, "status": "queued"})
                    return

                if path.startswith("/api/v1/providers/") and path.endswith("/sync/historical"):
                    provider = path.split("/")[4]
                    state["triggered_providers"].append(f"{provider}:historical")
                    self._json_response({"provider": provider, "status": "queued"})
                    return

                self._json_response({"detail": f"Unhandled POST {path}"}, status=404)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.api_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return None
