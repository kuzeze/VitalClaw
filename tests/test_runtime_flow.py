from __future__ import annotations

import json
from pathlib import Path

from vitalclaw.runtime import get_runtime_paths
from vitalclaw.service import (
    check_alerts,
    explain_latest_alert,
    initialize_project,
    record_context_event,
    sync_remote_data,
)
from vitalclaw.storage.db import Repository, connect
from tests.helpers import write_fake_he


def test_init_sync_materialize_and_alerts_round_trip(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    fake_he = write_fake_he(tmp_path)

    result = initialize_project(
        project_root=project_root,
        account_key="test-account-key",
        he_path=str(fake_he),
    )

    assert result["authenticated"] is True
    assert result["sync"]["processed_observations"] == 55
    assert result["materialize"]["feature_days"] == 11
    assert result["alerts"]["status"] == "active"
    assert result["alerts"]["alert"]["status"] == "waiting_for_user_input"

    paths = get_runtime_paths(project_root)
    repository = Repository(connect(paths.db_path))
    assert repository.observation_count() == 55
    assert repository.count_alerts() == 1

    sync_remote_data(project_root=project_root)
    check_alerts(project_root=project_root)

    repository = Repository(connect(paths.db_path))
    assert repository.observation_count() == 55
    assert repository.count_alerts() == 1


def test_context_event_changes_explanation_and_alert_state(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    fake_he = write_fake_he(tmp_path)

    initialize_project(
        project_root=project_root,
        account_key="test-account-key",
        he_path=str(fake_he),
    )

    before = explain_latest_alert(project_root=project_root)
    assert "Any symptoms" in before["missing_context"]

    record_context_event(
        project_root=project_root,
        event_type="symptoms",
        note="sore throat and mild fatigue",
    )

    after = explain_latest_alert(project_root=project_root)
    assert "sore throat and mild fatigue" in after["missing_context"]

    repository = Repository(connect(get_runtime_paths(project_root).db_path))
    active = repository.get_active_alert()
    assert active is not None
    assert active.status == "monitoring"
