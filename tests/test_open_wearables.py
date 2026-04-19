from __future__ import annotations

import json
from pathlib import Path

from vitalclaw import service
from vitalclaw.external.open_wearables import OpenWearablesClient
from vitalclaw.ingest.open_wearables import extract_observations
from vitalclaw.runtime import AppConfig, get_runtime_paths, load_config, save_config
from vitalclaw.service import (
    answer_health_question,
    build_latest_features,
    get_briefing,
    initialize_project,
    open_wearables_connect_app,
    open_wearables_status,
)
from vitalclaw.storage.db import Repository, connect

from tests.helpers import FakeOpenWearablesServer


def test_open_wearables_client_uses_api_key_header() -> None:
    with FakeOpenWearablesServer() as server:
        client = OpenWearablesClient(api_key="ow-test-key", api_url=server.api_url)
        assert client.list_users() == []
        headers = {key.lower(): value for key, value in server.state["last_request_headers"].items()}
        assert headers["x-open-wearables-api-key"] == "ow-test-key"


def test_runtime_config_round_trip_supports_open_wearables(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    paths = get_runtime_paths(project_root)

    save_config(
        paths,
        AppConfig(
            source="open_wearables",
            timezone="America/Chicago",
            ow_api_url="https://api.openwearables.test",
            ow_api_key="secret",
            ow_user_id="user-123",
            ow_last_invitation_code="ABCDEFGH",
            ow_developer_email="admin@admin.com",
            ow_developer_password="local-password",
        ),
    )

    loaded = load_config(paths)

    assert loaded is not None
    assert loaded.source == "open_wearables"
    assert loaded.ow_api_url == "https://api.openwearables.test"
    assert loaded.ow_api_key == "secret"
    assert loaded.ow_user_id == "user-123"
    assert loaded.ow_last_invitation_code == "ABCDEFGH"
    assert loaded.ow_developer_email == "admin@admin.com"
    assert loaded.ow_developer_password == "local-password"


def test_open_wearables_client_supports_developer_login() -> None:
    with FakeOpenWearablesServer(developer_email="dev@example.com", developer_password="pw123456") as server:
        client = OpenWearablesClient(api_key="ow-test-key", api_url=server.api_url)
        token = client.developer_login(email="dev@example.com", password="pw123456")
        assert token == "dev-token"


def test_open_wearables_init_waits_for_mobile_sync_when_no_data_exists(tmp_path: Path) -> None:
    project_root = _make_project(tmp_path)
    with FakeOpenWearablesServer() as server:
        result = initialize_project(
            project_root=project_root,
            source="open_wearables",
            ow_api_key="ow-test-key",
            ow_api_url=server.api_url,
            ow_developer_email="admin@admin.com",
            ow_developer_password="your-secure-password",
        )

        assert result["bootstrap_status"] == "waiting_for_mobile_sync"
        assert result["open_wearables"]["user_id"]
        assert result["open_wearables"]["invitation_code"]
        config = load_config(get_runtime_paths(project_root))
        assert config is not None
        assert config.source == "open_wearables"
        assert config.ow_user_id == result["open_wearables"]["user_id"]
        assert config.ow_last_invitation_code == result["open_wearables"]["invitation_code"]


def test_open_wearables_extract_observations_maps_and_prefers_skin_temperature() -> None:
    observations = extract_observations(
        recovery_summary=[
            {
                "date": "2026-04-18",
                "source": {"provider": "apple_health", "device": "Apple Watch"},
                "sleep_duration_seconds": 28800,
                "resting_heart_rate_bpm": 58,
                "avg_hrv_sdnn_ms": 52,
            }
        ],
        sleep_summary=[
            {
                "date": "2026-04-18",
                "source": {"provider": "apple_health", "device": "Apple Watch"},
                "avg_respiratory_rate": 15.4,
            }
        ],
        timeseries=[
            {
                "timestamp": "2026-04-18T07:00:00Z",
                "type": "respiratory_rate",
                "value": 16.2,
                "unit": "breaths/min",
                "source": {"provider": "apple_health", "device": "Apple Watch"},
            },
            {
                "timestamp": "2026-04-18T07:00:00Z",
                "type": "body_temperature",
                "value": 36.8,
                "unit": "celsius",
                "source": {"provider": "apple_health", "device": "Apple Watch"},
            },
            {
                "timestamp": "2026-04-18T07:00:00Z",
                "type": "skin_temperature",
                "value": 36.6,
                "unit": "celsius",
                "source": {"provider": "apple_health", "device": "Apple Watch"},
            },
        ],
        timezone_name="America/Chicago",
    )

    metrics = [ob.metric for ob in observations]
    assert "sleep_duration_hours" in metrics
    assert "resting_heart_rate" in metrics
    assert "heart_rate_variability_sdnn" in metrics
    assert metrics.count("respiratory_rate") == 1
    temp_obs = [ob for ob in observations if ob.metric == "wrist_temperature_celsius"]
    assert len(temp_obs) == 1
    assert round(temp_obs[0].value, 2) == 36.60


def test_open_wearables_init_sync_and_briefing_round_trip(tmp_path: Path) -> None:
    project_root = _make_project(tmp_path)
    user_id = "176be8de-8452-4eb7-a7ea-147fec925d9d"
    with FakeOpenWearablesServer(
        users=[{"id": user_id, "created_at": "2026-04-18T00:00:00Z", "email": None, "first_name": None, "last_name": None, "external_user_id": None}],
        connections=[
            {
                "user_id": user_id,
                "provider": "apple_health",
                "id": "c1",
                "status": "active",
                "last_synced_at": "2026-04-18T20:00:00Z",
                "created_at": "2026-04-18T00:00:00Z",
                "updated_at": "2026-04-18T20:00:00Z",
                "provider_user_id": None,
                "provider_username": "me",
                "scope": None,
            },
            {
                "user_id": user_id,
                "provider": "garmin",
                "id": "c2",
                "status": "active",
                "last_synced_at": "2026-04-18T20:00:00Z",
                "created_at": "2026-04-18T00:00:00Z",
                "updated_at": "2026-04-18T20:00:00Z",
                "provider_user_id": None,
                "provider_username": "me",
                "scope": None,
            },
        ],
        recovery=_ow_recovery_data(),
        sleep=_ow_sleep_data(),
        timeseries=_ow_temperature_timeseries(),
        providers=[{"slug": "garmin"}, {"slug": "apple_health"}],
    ) as server:
        result = initialize_project(
            project_root=project_root,
            source="open_wearables",
            ow_api_key="ow-test-key",
            ow_api_url=server.api_url,
            ow_developer_email="admin@admin.com",
            ow_developer_password="your-secure-password",
        )

        assert result["bootstrap_status"] == "ready"
        assert result["sync"]["source"] == "open_wearables"
        assert "garmin" in result["sync"]["connected_providers"]
        assert "apple_health" in result["sync"]["connected_providers"]
        assert "garmin" in server.state["triggered_providers"]
        assert "apple_health" not in server.state["triggered_providers"]

        repository = Repository(connect(get_runtime_paths(project_root).db_path))
        assert repository.observation_count() > 0

        briefing = get_briefing(project_root=project_root, force_refresh=False)
        assert briefing["active_source"] == "open_wearables"
        assert set(briefing["connected_providers"]) == {"apple_health", "garmin"}

        answer = answer_health_question(project_root=project_root, question="What is my health now?")
        assert answer["active_source"] == "open_wearables"
        assert set(answer["connected_providers"]) == {"apple_health", "garmin"}


def test_open_wearables_connect_app_regenerates_code_and_status_reports_connections(tmp_path: Path) -> None:
    project_root = _make_project(tmp_path)
    user_id = "176be8de-8452-4eb7-a7ea-147fec925d9d"
    with FakeOpenWearablesServer(
        users=[{"id": user_id, "created_at": "2026-04-18T00:00:00Z", "email": None, "first_name": None, "last_name": None, "external_user_id": None}],
        connections=[
            {
                "user_id": user_id,
                "provider": "apple_health",
                "id": "c1",
                "status": "active",
                "last_synced_at": "2026-04-18T20:00:00Z",
                "created_at": "2026-04-18T00:00:00Z",
                "updated_at": "2026-04-18T20:00:00Z",
                "provider_user_id": None,
                "provider_username": "me",
                "scope": None,
            }
        ],
    ) as server:
        initialize_project(
            project_root=project_root,
            source="open_wearables",
            ow_api_key="ow-test-key",
            ow_api_url=server.api_url,
            ow_developer_email="admin@admin.com",
            ow_developer_password="your-secure-password",
        )
        first_code = load_config(get_runtime_paths(project_root)).ow_last_invitation_code

        reconnect = open_wearables_connect_app(project_root=project_root)
        status = open_wearables_status(project_root=project_root)

        assert reconnect["invitation_code"] != first_code
        assert reconnect["instructions"]
        assert status["connected_providers"] == ["apple_health"]


def test_open_wearables_status_returns_doctor_report_when_local_backend_unavailable(tmp_path: Path, monkeypatch) -> None:
    project_root = _make_project(tmp_path)
    save_config(
        get_runtime_paths(project_root),
        AppConfig(
            source="open_wearables",
            timezone="America/Chicago",
            ow_api_url="http://127.0.0.1:8000",
            ow_api_key="ow-test-key",
            ow_user_id="user-123",
        ),
    )
    monkeypatch.setattr(
        service,
        "open_wearables_doctor",
        lambda project_root=None: {
            "mode": "local",
            "api_url": "http://127.0.0.1:8000",
            "api_reachable": False,
            "frontend_reachable": True,
            "containers": {"backend__open-wearables": "Restarting"},
            "recovered": False,
            "error": "Local Open Wearables backend is still unavailable after recovery attempts.",
        },
    )

    status = open_wearables_status(project_root=project_root)

    assert status["doctor"]["api_reachable"] is False
    assert status["connections"] == []
    assert status["connected_providers"] == []


def test_open_wearables_init_skips_invitation_for_local_instance_with_active_connections(tmp_path: Path, monkeypatch) -> None:
    project_root = _make_project(tmp_path)
    user_id = "176be8de-8452-4eb7-a7ea-147fec925d9d"
    with FakeOpenWearablesServer(
        users=[{"id": user_id, "created_at": "2026-04-18T00:00:00Z", "email": None, "first_name": None, "last_name": None, "external_user_id": None}],
        connections=[
            {
                "user_id": user_id,
                "provider": "apple_health",
                "id": "c1",
                "status": "active",
                "last_synced_at": "2026-04-18T20:00:00Z",
                "created_at": "2026-04-18T00:00:00Z",
                "updated_at": "2026-04-18T20:00:00Z",
                "provider_user_id": None,
                "provider_username": "me",
                "scope": None,
            }
        ],
        recovery=_ow_recovery_data(),
        sleep=_ow_sleep_data(),
        timeseries=_ow_temperature_timeseries(),
    ) as server:
        monkeypatch.setattr(service, "_is_local_open_wearables_api_url", lambda api_url: True)
        monkeypatch.setattr(service, "_ensure_local_open_wearables_running", lambda config: {"mode": "local", "api_reachable": True, "frontend_reachable": True, "containers": {}, "recovered": False})
        result = initialize_project(
            project_root=project_root,
            source="open_wearables",
            ow_api_key="ow-test-key",
            ow_api_url=server.api_url,
            ow_developer_email="admin@admin.com",
            ow_developer_password="your-secure-password",
        )

        assert result["bootstrap_status"] == "ready"
        assert result["open_wearables"]["invitation_code"] is None or result["open_wearables"]["invitation_code"] == ""


def test_briefing_uses_latest_monitorable_feature_day_when_today_is_partial(tmp_path: Path) -> None:
    project_root = _make_project(tmp_path)
    with FakeOpenWearablesServer(
        users=[{"id": "user-123", "created_at": "2026-04-18T00:00:00Z", "email": None, "first_name": None, "last_name": None, "external_user_id": None}],
        connections=[
            {
                "user_id": "user-123",
                "provider": "whoop",
                "id": "c1",
                "status": "active",
                "last_synced_at": "2026-04-19T20:00:00Z",
                "created_at": "2026-04-18T00:00:00Z",
                "updated_at": "2026-04-19T20:00:00Z",
                "provider_user_id": None,
                "provider_username": "me",
                "scope": None,
            }
        ],
        recovery=[
            {
                "date": "2026-04-18",
                "source": {"provider": "whoop", "device": None},
                "sleep_duration_seconds": 8 * 3600,
                "resting_heart_rate_bpm": 56,
                "avg_hrv_sdnn_ms": 48,
            }
        ],
        sleep=[
            {
                "date": "2026-04-18",
                "source": {"provider": "whoop", "device": None},
                "avg_respiratory_rate": 15.2,
            }
        ],
        timeseries=[
            {
                "timestamp": "2026-04-18T13:00:00Z",
                "type": "skin_temperature",
                "value": 33.8,
                "unit": "celsius",
                "source": {"provider": "whoop", "device": None},
            },
            {
                "timestamp": "2026-04-19T13:00:00Z",
                "type": "skin_temperature",
                "value": 33.2,
                "unit": "celsius",
                "source": {"provider": "whoop", "device": None},
            },
        ],
    ) as server:
        initialize_project(
            project_root=project_root,
            source="open_wearables",
            ow_api_key="ow-test-key",
            ow_api_url=server.api_url,
            ow_developer_email="admin@admin.com",
            ow_developer_password="your-secure-password",
        )
        build_latest_features(project_root=project_root)
        briefing = get_briefing(project_root=project_root, force_refresh=False)

        assert briefing["latest_feature_date"] == "2026-04-18"
        assert briefing["metrics"][0]["metric"] == "sleep_duration_hours"
        assert briefing["metrics"][0]["current_display"] != "n/a"


def _make_project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    return project_root


def _ow_recovery_data() -> list[dict[str, object]]:
    days = [
        ("2026-04-07", 7.8, 56, 41),
        ("2026-04-08", 7.7, 56, 42),
        ("2026-04-09", 7.6, 57, 41),
        ("2026-04-10", 7.5, 56, 40),
        ("2026-04-11", 7.6, 55, 43),
        ("2026-04-12", 7.8, 56, 42),
        ("2026-04-13", 7.7, 57, 42),
        ("2026-04-14", 7.6, 56, 41),
        ("2026-04-15", 7.5, 56, 40),
        ("2026-04-16", 7.7, 55, 42),
        ("2026-04-17", 5.6, 64, 28),
    ]
    return [
        {
            "date": day,
            "source": {"provider": "apple_health", "device": "Apple Watch"},
            "sleep_duration_seconds": sleep * 3600,
            "resting_heart_rate_bpm": rhr,
            "avg_hrv_sdnn_ms": hrv,
        }
        for day, sleep, rhr, hrv in days
    ]


def _ow_sleep_data() -> list[dict[str, object]]:
    values = [
        ("2026-04-07", 14.0),
        ("2026-04-08", 14.1),
        ("2026-04-09", 14.0),
        ("2026-04-10", 13.9),
        ("2026-04-11", 14.0),
        ("2026-04-12", 14.2),
        ("2026-04-13", 14.1),
        ("2026-04-14", 14.0),
        ("2026-04-15", 14.1),
        ("2026-04-16", 14.0),
        ("2026-04-17", 16.0),
    ]
    return [
        {
            "date": day,
            "source": {"provider": "apple_health", "device": "Apple Watch"},
            "avg_respiratory_rate": respiratory,
        }
        for day, respiratory in values
    ]


def _ow_temperature_timeseries() -> list[dict[str, object]]:
    values = [
        ("2026-04-07T07:00:00Z", 36.45),
        ("2026-04-08T07:00:00Z", 36.40),
        ("2026-04-09T07:00:00Z", 36.50),
        ("2026-04-10T07:00:00Z", 36.48),
        ("2026-04-11T07:00:00Z", 36.47),
        ("2026-04-12T07:00:00Z", 36.42),
        ("2026-04-13T07:00:00Z", 36.46),
        ("2026-04-14T07:00:00Z", 36.44),
        ("2026-04-15T07:00:00Z", 36.43),
        ("2026-04-16T07:00:00Z", 36.45),
        ("2026-04-17T07:00:00Z", 36.95),
    ]
    return [
        {
            "timestamp": ts,
            "type": "skin_temperature",
            "value": value,
            "unit": "celsius",
            "source": {"provider": "apple_health", "device": "Apple Watch"},
        }
        for ts, value in values
    ]
