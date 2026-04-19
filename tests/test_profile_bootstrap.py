from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vitalclaw import service
from vitalclaw.cli import main
from vitalclaw.mcp_server import build_mcp_server
from vitalclaw.runtime import get_runtime_paths
from vitalclaw.service import get_briefing, initialize_project, set_user_profile
from vitalclaw.storage.db import Repository, connect, initialize

from tests.helpers import write_fake_he


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    fake_he = write_fake_he(tmp_path)
    initialize_project(project_root=project_root, account_key="test-account-key", he_path=str(fake_he))
    return project_root, fake_he


def test_repository_creates_default_user_profile(tmp_path: Path) -> None:
    db_path = tmp_path / "vitalclaw.sqlite3"
    connection = connect(db_path)
    initialize(connection)
    repository = Repository(connection)

    profile = repository.get_user_profile()

    assert profile.auto_brief_enabled is True
    assert profile.always_sync_on_brief is True
    assert profile.default_briefing_mode == "status_plus_key_metrics"
    assert profile.preferred_metrics
    assert profile.updated_at is not None


def test_repository_updates_profile_partially_without_clobbering_existing_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "vitalclaw.sqlite3"
    connection = connect(db_path)
    initialize(connection)
    repository = Repository(connection)
    original = repository.get_user_profile()

    updated = repository.update_user_profile(
        standing_instruction="Always extract my panel snapshot first.",
        preferred_metrics=["resting_heart_rate", "heart_rate_variability_sdnn"],
    )

    assert updated.auto_brief_enabled == original.auto_brief_enabled
    assert updated.always_sync_on_brief == original.always_sync_on_brief
    assert updated.default_briefing_mode == original.default_briefing_mode
    assert updated.preferred_metrics == ["resting_heart_rate", "heart_rate_variability_sdnn"]
    assert updated.standing_instruction == "Always extract my panel snapshot first."


def test_repository_profile_persists_across_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "vitalclaw.sqlite3"
    connection = connect(db_path)
    initialize(connection)
    repository = Repository(connection)
    repository.update_user_profile(
        auto_brief_enabled=False,
        standing_instruction="Use briefing before answering health questions.",
    )
    connection.close()

    reopened = Repository(connect(db_path))
    profile = reopened.get_user_profile()

    assert profile.auto_brief_enabled is False
    assert profile.standing_instruction == "Use briefing before answering health questions."


def test_get_briefing_returns_profile_and_compact_metrics(tmp_path: Path) -> None:
    project_root, _ = _make_project(tmp_path)
    set_user_profile(
        project_root=project_root,
        preferred_metrics=["resting_heart_rate", "heart_rate_variability_sdnn"],
        standing_instruction="Always extract these metrics first.",
    )

    briefing = get_briefing(project_root=project_root)

    assert briefing["sync"]["refreshed_now"] is True
    assert briefing["status"]["label"] == "Recovery suppressed"
    assert briefing["profile"]["preferred_metrics"] == ["resting_heart_rate", "heart_rate_variability_sdnn"]
    assert briefing["standing_instruction"] == "Always extract these metrics first."
    assert [metric["metric"] for metric in briefing["metrics"]] == [
        "resting_heart_rate",
        "heart_rate_variability_sdnn",
    ]


def test_get_briefing_runs_refresh_pipeline_in_order(tmp_path: Path, monkeypatch) -> None:
    project_root, _ = _make_project(tmp_path)
    set_user_profile(project_root=project_root, preferred_metrics=["resting_heart_rate"])
    order: list[str] = []

    def fake_sync_remote_data(*, project_root=None, from_date=None, to_date=None):
        order.append("sync")
        return {"status": "success"}

    def fake_build_latest_features(*, project_root=None):
        order.append("materialize")
        return {"feature_days": 11, "latest_feature_date": "2026-04-17", "latest_metrics": {}}

    def fake_check_alerts(*, project_root=None):
        order.append("alerts")
        return {"status": "clear", "latest_feature_date": "2026-04-17", "active_alerts": []}

    def fake_dashboard_snapshot(*, project_root=None):
        order.append("snapshot")
        return {
            "latest_feature_date": "2026-04-17",
            "status": {"label": "On baseline", "reason": "All good", "tone": "good"},
            "open_alert_count": 0,
            "metrics": [
                {
                    "metric": "resting_heart_rate",
                    "label": "Resting heart rate",
                    "current_display": "60 bpm",
                    "baseline_display": "58 bpm",
                    "delta": "↑ 2.00",
                    "tone": "good",
                }
            ],
        }

    monkeypatch.setattr(service, "sync_remote_data", fake_sync_remote_data)
    monkeypatch.setattr(service, "build_latest_features", fake_build_latest_features)
    monkeypatch.setattr(service, "check_alerts", fake_check_alerts)
    monkeypatch.setattr(service, "dashboard_snapshot", fake_dashboard_snapshot)

    briefing = service.get_briefing(project_root=project_root)

    assert order == ["sync", "materialize", "alerts", "snapshot"]
    assert briefing["sync"]["refreshed_now"] is True
    assert briefing["metrics"][0]["metric"] == "resting_heart_rate"


def test_get_briefing_reports_missing_metric_notes(tmp_path: Path, monkeypatch) -> None:
    project_root, _ = _make_project(tmp_path)
    set_user_profile(
        project_root=project_root,
        always_sync_on_brief=False,
        preferred_metrics=["resting_heart_rate", "wrist_temperature_celsius"],
    )

    def fake_dashboard_snapshot(*, project_root=None):
        return {
            "latest_feature_date": "2026-04-17",
            "status": {"label": "Mild drift", "reason": "A few signals moved.", "tone": "warn"},
            "open_alert_count": 0,
            "metrics": [
                {
                    "metric": "resting_heart_rate",
                    "label": "Resting heart rate",
                    "current_display": "60 bpm",
                    "baseline_display": "58 bpm",
                    "delta": "↑ 2.00",
                    "tone": "good",
                },
                {
                    "metric": "wrist_temperature_celsius",
                    "label": "Wrist temperature",
                    "current_display": "n/a",
                    "baseline_display": "n/a",
                    "delta": "Missing",
                    "tone": "warn",
                },
            ],
        }

    monkeypatch.setattr(service, "dashboard_snapshot", fake_dashboard_snapshot)

    briefing = service.get_briefing(project_root=project_root)

    assert briefing["missing_data_notes"] == ["Wrist temperature is missing for the latest feature day."]


def test_cli_profile_set_and_briefing_round_trip(tmp_path: Path, capsys) -> None:
    project_root, _ = _make_project(tmp_path)

    main(
        [
            "--project-root",
            str(project_root),
            "--format",
            "json",
            "profile",
            "set",
            "--standing-instruction",
            "Always extract the briefing first.",
            "--preferred-metric",
            "resting_heart_rate",
            "--preferred-metric",
            "heart_rate_variability_sdnn",
        ]
    )
    profile = json.loads(capsys.readouterr().out)

    assert profile["standing_instruction"] == "Always extract the briefing first."
    assert profile["preferred_metrics"] == ["resting_heart_rate", "heart_rate_variability_sdnn"]

    main(["--project-root", str(project_root), "--format", "json", "briefing"])
    briefing = json.loads(capsys.readouterr().out)

    assert briefing["profile"]["standing_instruction"] == "Always extract the briefing first."
    assert isinstance(briefing["metrics"], list)
    assert briefing["sync"]["last_success_at"] is not None


def test_mcp_server_exposes_profile_and_briefing_tools(tmp_path: Path) -> None:
    project_root, _ = _make_project(tmp_path)
    server = build_mcp_server(project_root=project_root)

    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert {"get_user_profile", "set_user_profile", "get_briefing"} <= tool_names

    set_result = asyncio.run(
        server.call_tool(
            "set_user_profile",
            {"standing_instruction": "Use the bootstrap briefing before answering."},
        )
    )
    profile_result = asyncio.run(server.call_tool("get_user_profile", {}))
    briefing_result = asyncio.run(server.call_tool("get_briefing", {}))

    set_payload = _mcp_payload(set_result)
    profile_payload = _mcp_payload(profile_result)
    briefing_payload = _mcp_payload(briefing_result)

    assert set_payload["standing_instruction"] == "Use the bootstrap briefing before answering."
    assert profile_payload["standing_instruction"] == "Use the bootstrap briefing before answering."
    assert briefing_payload["standing_instruction"] == "Use the bootstrap briefing before answering."
    assert briefing_payload["profile"]["always_sync_on_brief"] is True


def test_profile_runtime_state_is_persisted_in_project_database(tmp_path: Path) -> None:
    project_root, _ = _make_project(tmp_path)
    set_user_profile(project_root=project_root, standing_instruction="Persist me.")

    repository = Repository(connect(get_runtime_paths(project_root).db_path))
    profile = repository.get_user_profile()

    assert profile.standing_instruction == "Persist me."


def _mcp_payload(result):
    if isinstance(result, dict):
        return result
    assert isinstance(result, list)
    assert result
    return json.loads(result[0].text)
