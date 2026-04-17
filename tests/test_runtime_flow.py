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


def test_init_sync_materialize_and_alerts_round_trip(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    fake_he = _write_fake_he(tmp_path)

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
    fake_he = _write_fake_he(tmp_path)

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


def _write_fake_he(tmp_path: Path) -> Path:
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
