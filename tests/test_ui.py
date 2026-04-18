from pathlib import Path

from starlette.testclient import TestClient

from vitalclaw.service import dashboard_snapshot, initialize_project
from vitalclaw.ui import build_ui_app

from tests.helpers import write_fake_he


def test_dashboard_snapshot_returns_monitoring_console_shape(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    fake_he = write_fake_he(tmp_path)

    initialize_project(
        project_root=project_root,
        account_key="test-account-key",
        he_path=str(fake_he),
    )

    snapshot = dashboard_snapshot(project_root=project_root)

    assert snapshot["status"]["label"] == "Recovery suppressed"
    assert snapshot["open_alert_count"] == 1
    assert len(snapshot["metrics"]) == 5
    assert snapshot["metrics"][0]["trend"]


def test_ui_renders_twin_panel(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    fake_he = write_fake_he(tmp_path)

    initialize_project(
        project_root=project_root,
        account_key="test-account-key",
        he_path=str(fake_he),
    )

    app = build_ui_app(project_root=project_root)
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert "VitalClaw" in response.text
    assert "Digital Twin" in response.text
    # fixture seeds an active alert → snapshot tone 'alert' → twin defaults to alert state
    assert '"state": "alert"' in response.text

    glb = client.get("/assets/Project.glb")
    assert glb.status_code == 200
    assert glb.headers["content-type"] == "model/gltf-binary"


def test_context_route_flashes_confirmation(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    fake_he = write_fake_he(tmp_path)
    initialize_project(project_root=project_root, account_key="test-account-key", he_path=str(fake_he))

    app = build_ui_app(project_root=project_root)
    client = TestClient(app)
    response = client.post(
        "/context",
        data={"event_type": "symptoms", "note": "mild sore throat"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Context saved" in response.text
