"""MCP server exposing the VitalClaw engine."""

from __future__ import annotations

from pathlib import Path

from vitalclaw.service import (
    answer_health_question,
    build_latest_features,
    explain_latest_alert,
    get_briefing,
    get_user_profile,
    list_open_alerts,
    record_context_event,
    set_user_profile,
    sync_remote_data,
)


def build_mcp_server(*, project_root: Path | None = None):
    """Build the VitalClaw MCP server with its registered tools."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in user environments
        raise RuntimeError("The `mcp` package is required to run `vitalclaw mcp`. Install project dependencies first.") from exc

    server = FastMCP("VitalClaw")

    @server.tool(name="sync_remote_data")
    def sync_remote_data_tool() -> dict:
        return sync_remote_data(project_root=project_root)

    @server.tool(name="build_latest_features")
    def build_latest_features_tool() -> dict:
        return build_latest_features(project_root=project_root)

    @server.tool(name="list_open_alerts")
    def list_open_alerts_tool() -> dict:
        return list_open_alerts(project_root=project_root)

    @server.tool(name="explain_latest_alert")
    def explain_latest_alert_tool() -> dict:
        return explain_latest_alert(project_root=project_root)

    @server.tool(name="record_context_event")
    def record_context_event_tool(event_type: str, note: str, effective_date: str | None = None) -> dict:
        return record_context_event(
            project_root=project_root,
            event_type=event_type,
            note=note,
            effective_date=effective_date,
        )

    @server.tool(name="get_user_profile")
    def get_user_profile_tool() -> dict:
        return get_user_profile(project_root=project_root)

    @server.tool(name="set_user_profile")
    def set_user_profile_tool(
        auto_brief_enabled: bool | None = None,
        always_sync_on_brief: bool | None = None,
        default_briefing_mode: str | None = None,
        preferred_metrics: list[str] | None = None,
        standing_instruction: str | None = None,
    ) -> dict:
        return set_user_profile(
            project_root=project_root,
            auto_brief_enabled=auto_brief_enabled,
            always_sync_on_brief=always_sync_on_brief,
            default_briefing_mode=default_briefing_mode,
            preferred_metrics=preferred_metrics,
            standing_instruction=standing_instruction,
        )

    @server.tool(name="get_briefing")
    def get_briefing_tool() -> dict:
        return get_briefing(project_root=project_root)

    @server.tool(name="answer_health_question")
    def answer_health_question_tool(question: str) -> dict:
        return answer_health_question(project_root=project_root, question=question)

    return server


def run_mcp_server(*, project_root: Path | None = None) -> None:
    """Run the VitalClaw MCP server over stdio."""
    server = build_mcp_server(project_root=project_root)
    server.run()
