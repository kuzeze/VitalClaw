from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vitalclaw import service
from vitalclaw.cli import main
from vitalclaw.mcp_server import build_mcp_server
from vitalclaw.service import answer_health_question, initialize_project

from tests.helpers import write_fake_he


def _make_project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.0.0'\n", encoding="utf-8")
    fake_he = write_fake_he(tmp_path)
    initialize_project(project_root=project_root, account_key="test-account-key", he_path=str(fake_he))
    return project_root


def test_answer_health_question_uses_briefing_before_answering(tmp_path: Path, monkeypatch) -> None:
    project_root = _make_project(tmp_path)
    calls: list[bool | None] = []

    def fake_get_briefing(*, project_root=None, force_refresh=None):
        calls.append(force_refresh)
        return {
            "status": {"label": "Mild drift", "reason": "A few signals moved.", "tone": "warn"},
            "sync": {"refreshed_now": True, "last_success_at": "2026-04-18T23:57:03+00:00"},
            "latest_feature_date": "2026-04-17",
            "open_alert_count": 0,
            "metrics": [
                {
                    "metric": "resting_heart_rate",
                    "label": "Resting heart rate",
                    "current_display": "59 bpm",
                    "baseline_display": "62 bpm",
                    "delta": "↓ 3.00",
                    "tone": "warn",
                }
            ],
            "missing_data_notes": [],
        }

    monkeypatch.setattr(service, "get_briefing", fake_get_briefing)

    result = answer_health_question(project_root=project_root, question="What is my health now?")

    assert calls == [True]
    assert result["question"] == "What is my health now?"
    assert result["status"]["label"] == "Mild drift"
    assert "VitalClaw" in result["answer"]
    assert result["data_points_used"][0].startswith("Status:")


def test_answer_health_question_adds_cautious_general_context_when_needed(tmp_path: Path, monkeypatch) -> None:
    project_root = _make_project(tmp_path)

    def fake_get_briefing(*, project_root=None, force_refresh=None):
        return {
            "status": {"label": "Mild drift", "reason": "A few signals moved.", "tone": "warn"},
            "sync": {"refreshed_now": False, "last_success_at": "2026-04-18T23:57:03+00:00"},
            "latest_feature_date": "2026-04-17",
            "open_alert_count": 0,
            "metrics": [
                {
                    "metric": "wrist_temperature_celsius",
                    "label": "Wrist temperature",
                    "current_display": "n/a",
                    "baseline_display": "n/a",
                    "delta": "Missing",
                    "tone": "warn",
                }
            ],
            "missing_data_notes": ["Wrist temperature is missing for the latest feature day."],
        }

    monkeypatch.setattr(service, "get_briefing", fake_get_briefing)

    result = answer_health_question(project_root=project_root, question="Does this mean I'm sick?")

    assert result["general_context"] is not None
    assert "not diagnostic" in result["general_context"].lower() or "reduce certainty" in result["general_context"].lower()
    assert result["missing_data_notes"] == ["Wrist temperature is missing for the latest feature day."]


def test_cli_answer_round_trip(tmp_path: Path, capsys) -> None:
    project_root = _make_project(tmp_path)

    main(
        [
            "--project-root",
            str(project_root),
            "--format",
            "json",
            "answer",
            "--question",
            "What is my health now?",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["question"] == "What is my health now?"
    assert "answer" in payload
    assert "freshness" in payload
    assert "data_points_used" in payload


def test_mcp_server_exposes_answer_health_question_tool(tmp_path: Path) -> None:
    project_root = _make_project(tmp_path)
    server = build_mcp_server(project_root=project_root)

    tools = asyncio.run(server.list_tools())
    assert "answer_health_question" in {tool.name for tool in tools}

    result = asyncio.run(server.call_tool("answer_health_question", {"question": "What is my health now?"}))
    payload = _mcp_payload(result)

    assert payload["question"] == "What is my health now?"
    assert "VitalClaw" in payload["answer"]


def test_repo_plugin_and_instruction_artifacts_route_to_answer_wrapper() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    agents_text = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    skill_text = (
        repo_root
        / "plugins"
        / "vitalclaw-health-routing"
        / "skills"
        / "health-question-routing"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    plugin_json = json.loads(
        (repo_root / "plugins" / "vitalclaw-health-routing" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    marketplace = json.loads((repo_root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))

    assert "answer_health_question" in agents_text
    assert "answer_health_question" in skill_text
    assert "get_briefing" not in skill_text
    assert plugin_json["mcpServers"] == "./.mcp.json"
    assert plugin_json["skills"] == "./skills/"
    assert marketplace["plugins"][0]["policy"]["installation"] == "INSTALLED_BY_DEFAULT"


def _mcp_payload(result):
    if isinstance(result, dict):
        return result
    assert isinstance(result, list)
    assert result
    return json.loads(result[0].text)
