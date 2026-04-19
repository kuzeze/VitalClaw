"""Command-line interface for VitalClaw."""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import sys

from vitalclaw.mcp_server import run_mcp_server
from vitalclaw.schema import BRIEFING_MODES
from vitalclaw.service import (
    answer_health_question,
    build_latest_features,
    check_alerts,
    dashboard_snapshot,
    explain_latest_alert,
    get_briefing,
    get_user_profile,
    initialize_project,
    list_open_alerts,
    record_context_event,
    set_user_profile,
    sync_remote_data,
)
from vitalclaw.ui import run_ui_server


def build_parser() -> argparse.ArgumentParser:
    """Build the VitalClaw CLI parser."""
    parser = argparse.ArgumentParser(prog="vitalclaw")
    parser.add_argument("--project-root", default=None, help="Project root containing pyproject.toml. Defaults to the current repo.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize VitalClaw and perform the first sync.")
    init_parser.add_argument("--account-key", required=True, help="HealthExport Remote account key.")
    init_parser.add_argument("--he-path", default=None, help="Path to the official `he` binary.")

    sync_parser = subparsers.add_parser("sync", help="Fetch fresh data from HealthExport Remote.")
    sync_parser.add_argument("--from-date", default=None, help="Override the sync start date (YYYY-MM-DD).")
    sync_parser.add_argument("--to-date", default=None, help="Override the sync end date (YYYY-MM-DD).")

    subparsers.add_parser("materialize", help="Build daily features from canonical observations.")
    subparsers.add_parser("alerts", help="Evaluate the latest daily feature row and update alerts.")

    explain_parser = subparsers.add_parser("explain", help="Explain the latest alert.")
    explain_parser.add_argument("--latest", action="store_true", help="Explain the latest alert. Included for compatibility with the planned interface.")

    context_parser = subparsers.add_parser("context", help="Record context events against the active episode.")
    context_subparsers = context_parser.add_subparsers(dest="context_command", required=True)
    add_parser = context_subparsers.add_parser("add", help="Add a context event.")
    add_parser.add_argument("--type", required=True, dest="event_type", help="Event type such as symptoms, travel, alcohol, training_load, or medication_change.")
    add_parser.add_argument("--note", required=True, help="Free-form note.")
    add_parser.add_argument("--effective-date", default=None, help="Effective date in YYYY-MM-DD.")

    profile_parser = subparsers.add_parser("profile", help="Get or update the durable Codex bootstrap profile.")
    profile_subparsers = profile_parser.add_subparsers(dest="profile_command", required=True)
    profile_subparsers.add_parser("get", help="Return the current bootstrap profile.")
    profile_set_parser = profile_subparsers.add_parser("set", help="Update one or more bootstrap profile fields.")
    profile_set_parser.add_argument(
        "--auto-brief-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable automatic briefing for fresh chats.",
    )
    profile_set_parser.add_argument(
        "--always-sync-on-brief",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable sync/materialize/alerts before a briefing is built.",
    )
    profile_set_parser.add_argument(
        "--default-briefing-mode",
        choices=BRIEFING_MODES,
        default=None,
        help="Default briefing detail level.",
    )
    profile_set_parser.add_argument(
        "--preferred-metric",
        action="append",
        default=None,
        dest="preferred_metrics",
        help="Preferred metric to include in key-metric briefings. Repeat for multiple metrics.",
    )
    profile_set_parser.add_argument(
        "--standing-instruction",
        default=None,
        help="Persistent freeform instruction that Codex should read from the profile.",
    )

    subparsers.add_parser("mcp", help="Run the VitalClaw MCP server.")
    subparsers.add_parser("open-alerts", help="List currently open alerts.")
    subparsers.add_parser("snapshot", help="Return the latest monitoring console snapshot.")
    subparsers.add_parser("briefing", help="Build the fresh-chat bootstrap briefing.")
    answer_parser = subparsers.add_parser("answer", help="Answer a health question from VitalClaw data.")
    answer_parser.add_argument("--question", required=True, help="Health question to answer from VitalClaw state.")
    ui_parser = subparsers.add_parser("ui", help="Run the local monitoring console.")
    ui_parser.add_argument("--host", default="127.0.0.1", help="UI host.")
    ui_parser.add_argument("--port", type=int, default=3000, help="UI port.")
    ui_parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")

    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = Path(args.project_root).resolve() if args.project_root else None
    command_key = args.command

    if args.command == "mcp":
        run_mcp_server(project_root=project_root)
        return
    if args.command == "ui":
        run_ui_server(
            project_root=project_root,
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
        )
        return

    if args.command == "init":
        result = initialize_project(
            project_root=project_root,
            account_key=args.account_key,
            he_path=args.he_path,
        )
    elif args.command == "sync":
        result = sync_remote_data(
            project_root=project_root,
            from_date=args.from_date,
            to_date=args.to_date,
        )
    elif args.command == "materialize":
        result = build_latest_features(project_root=project_root)
    elif args.command == "alerts":
        result = check_alerts(project_root=project_root)
    elif args.command == "explain":
        result = explain_latest_alert(project_root=project_root)
    elif args.command == "open-alerts":
        result = list_open_alerts(project_root=project_root)
    elif args.command == "snapshot":
        result = dashboard_snapshot(project_root=project_root)
    elif args.command == "briefing":
        result = get_briefing(project_root=project_root)
    elif args.command == "answer":
        result = answer_health_question(project_root=project_root, question=args.question)
    elif args.command == "profile" and args.profile_command == "get":
        command_key = "profile_get"
        result = get_user_profile(project_root=project_root)
    elif args.command == "profile" and args.profile_command == "set":
        command_key = "profile_set"
        result = set_user_profile(
            project_root=project_root,
            auto_brief_enabled=args.auto_brief_enabled,
            always_sync_on_brief=args.always_sync_on_brief,
            default_briefing_mode=args.default_briefing_mode,
            preferred_metrics=args.preferred_metrics,
            standing_instruction=args.standing_instruction,
        )
    elif args.command == "context" and args.context_command == "add":
        result = record_context_event(
            project_root=project_root,
            event_type=args.event_type,
            note=args.note,
            effective_date=args.effective_date,
        )
    else:
        parser.error("Unknown command")
        return

    if args.format == "json":
        json.dump(result, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    sys.stdout.write(_format_text(command_key, result))
    sys.stdout.write("\n")


def _format_text(command: str, result: dict) -> str:
    if command == "init":
        return (
            "VitalClaw initialized.\n"
            f"Runtime: {result['runtime_dir']}\n"
            f"HealthExport CLI: {result['he_path']}\n"
            f"Initial sync processed {result['sync']['processed_observations']} observations.\n"
            f"Materialized {result['materialize']['feature_days']} feature days.\n"
            f"Alert status: {result['alerts']['status']}."
        )
    if command == "sync":
        return (
            f"Synced {result['processed_observations']} observations "
            f"from {result['from_date']} to {result['to_date']}.\n"
            f"Stored observations: {result['stored_observations']}."
        )
    if command == "materialize":
        return (
            f"Materialized {result['feature_days']} feature days.\n"
            f"Latest feature date: {result['latest_feature_date']}."
        )
    if command == "alerts":
        if result["status"] == "active":
            alert = result["alert"]
            return (
                f"Active alert: {alert['title']} ({alert['status']}).\n"
                f"Signals: {', '.join(alert['supporting_signals'])}\n"
                f"Question: {alert['question'] or 'none'}"
            )
        return f"Alert status: {result['status']}."
    if command == "explain":
        return (
            f"Changed: {result['changed']}\n"
            f"Signals: {', '.join(result['supporting_signals']) or 'none'}\n"
            f"Missing context: {result['missing_context']}\n"
            f"History: {result['history']}"
        )
    if command == "open-alerts":
        alerts = result.get("alerts", [])
        if not alerts:
            return "No open alerts."
        lines = ["Open alerts:"]
        lines.extend(f"- {alert['title']} ({alert['status']}) on {alert['feature_date']}" for alert in alerts)
        return "\n".join(lines)
    if command == "snapshot":
        status = result["status"]
        return (
            f"Today: {status['label']}.\n"
            f"Reason: {status['reason']}\n"
            f"Latest feature date: {result['latest_feature_date']}\n"
            f"Open alerts: {result['open_alert_count']}"
        )
    if command in {"profile_get", "profile_set"}:
        return (
            f"Auto-brief enabled: {result['auto_brief_enabled']}\n"
            f"Always sync on brief: {result['always_sync_on_brief']}\n"
            f"Default briefing mode: {result['default_briefing_mode']}\n"
            f"Preferred metrics: {', '.join(result['preferred_metrics']) or 'none'}\n"
            f"Standing instruction: {result['standing_instruction'] or 'none'}\n"
            f"Updated at: {result['updated_at']}"
        )
    if command == "briefing":
        status = result["status"]
        lines = [
            f"Briefing status: {status['label']}.",
            f"Reason: {status['reason']}",
            f"Last sync: {result['sync']['last_success_at'] or 'never'}",
            f"Latest feature date: {result['latest_feature_date']}",
            f"Open alerts: {result['open_alert_count']}",
        ]
        if result.get("metrics"):
            lines.append(
                "Key metrics: "
                + "; ".join(
                    f"{metric['label']} {metric['current_display']} vs {metric['baseline_display']} ({metric['delta']})"
                    for metric in result["metrics"]
                )
            )
        if result.get("standing_instruction"):
            lines.append(f"Standing instruction: {result['standing_instruction']}")
        return "\n".join(lines)
    if command == "answer":
        lines = [
            f"Status: {result['status'].get('label', 'Unknown')}",
            (
                f"Freshness: latest feature day {result['freshness'].get('latest_feature_date')}, "
                f"last sync {result['freshness'].get('last_success_at_local') or result['freshness'].get('last_success_at') or 'unknown'}"
            ),
            f"Answer: {result['answer']}",
        ]
        if result.get("missing_data_notes"):
            lines.append("Missing data: " + "; ".join(result["missing_data_notes"]))
        if result.get("general_context"):
            lines.append(f"General context: {result['general_context']}")
        return "\n".join(lines)
    if command == "context":
        event = result["event"]
        return (
            f"Recorded context event {event['event_type']} for episode {result['episode_id'] or 'none'}.\n"
            f"Note: {event['note']}"
        )
    return json.dumps(result, indent=2, default=str)
