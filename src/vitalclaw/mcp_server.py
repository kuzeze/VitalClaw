"""MCP server exposing the VitalClaw engine."""

from __future__ import annotations

from pathlib import Path

from vitalclaw.service import (
    build_latest_features,
    explain_latest_alert,
    list_open_alerts,
    record_context_event,
    sync_remote_data,
)


def run_mcp_server(*, project_root: Path | None = None) -> None:
    """Run the VitalClaw MCP server over stdio."""
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

    server.run()
