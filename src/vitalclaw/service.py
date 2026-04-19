"""Application services for VitalClaw."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from vitalclaw.external.healthexport import HealthExportCLI
from vitalclaw.features.materialize import materialize_daily_features
from vitalclaw.ingest.health_export_remote import extract_observations, resolve_required_types
from vitalclaw.monitor.baselines import compute_baseline_profiles
from vitalclaw.monitor.recovery import evaluate_recovery_suppression
from vitalclaw.runtime import (
    AppConfig,
    RuntimePaths,
    ensure_runtime_dirs,
    get_runtime_paths,
    load_config,
    local_timezone_name,
    save_config,
)
from vitalclaw.schema import AlertCandidate, DailyFeature, StoredAlert
from vitalclaw.schema import (
    BRIEFING_MODES,
    Briefing,
    BriefingMetric,
    BriefingSyncStatus,
    UserProfile,
)
from vitalclaw.storage.db import Repository, connect, initialize


def initialize_project(
    *,
    project_root: Path | None = None,
    account_key: str,
    he_path: str | None = None,
) -> dict[str, Any]:
    """Initialize the local VitalClaw runtime and perform the first sync."""
    paths = get_runtime_paths(project_root)
    ensure_runtime_dirs(paths)

    connection = connect(paths.db_path)
    initialize(connection)
    repository = Repository(connection)

    config = load_config(paths) or AppConfig(timezone=local_timezone_name())
    cli = HealthExportCLI(paths=paths, he_path=he_path or config.he_path, api_url=config.api_url)
    resolved_he_path = str(cli.ensure_available())
    auth_status = cli.configure_account_key(account_key)
    required_types = resolve_required_types(cli.list_types())
    config.he_path = resolved_he_path
    config.initialized_at = datetime.now(timezone.utc).isoformat()
    config.required_types = {metric: health_type.id for metric, health_type in required_types.items()}
    save_config(paths, config)

    sync_result = sync_remote_data(project_root=paths.project_root, from_date=_days_ago(90), to_date=_today_iso())
    feature_result = build_latest_features(project_root=paths.project_root)
    alert_result = check_alerts(project_root=paths.project_root)

    return {
        "runtime_dir": str(paths.runtime_dir),
        "db_path": str(paths.db_path),
        "config_path": str(paths.config_path),
        "he_path": resolved_he_path,
        "authenticated": auth_status.authenticated,
        "timezone": config.timezone,
        "required_types": {metric: health_type.name for metric, health_type in required_types.items()},
        "sync": sync_result,
        "materialize": feature_result,
        "alerts": alert_result,
    }


def sync_remote_data(
    *,
    project_root: Path | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Fetch remote health data, persist raw snapshots, and upsert observations."""
    paths, config, repository = _load_runtime(project_root)
    cli = HealthExportCLI(paths=paths, he_path=config.he_path, api_url=config.api_url)

    effective_to = to_date or _today_iso()
    if from_date:
        effective_from = from_date
    else:
        last_success = repository.get_metadata("last_success_at")
        if last_success:
            effective_from = (datetime.fromisoformat(last_success).date() - timedelta(days=2)).isoformat()
        else:
            effective_from = _days_ago(90)

    sync_started = datetime.now(timezone.utc)
    sync_run_id = repository.create_sync_run(started_at=sync_started, from_date=effective_from, to_date=effective_to)
    raw_snapshot_path = None
    observations = []
    try:
        packages = cli.fetch_data(
            type_ids=list(config.required_types.values()),
            from_date=effective_from,
            to_date=effective_to,
        )
        raw_snapshot_path = _write_raw_snapshot(paths, packages, from_date=effective_from, to_date=effective_to)
        observations = extract_observations(packages, required_type_ids=config.required_types)
        processed = repository.upsert_observations(observations, sync_run_id=sync_run_id)
        finished_at = datetime.now(timezone.utc)
        repository.finish_sync_run(
            sync_run_id=sync_run_id,
            finished_at=finished_at,
            status="success",
            raw_snapshot_path=raw_snapshot_path,
            observation_count=processed,
            message=None,
        )
        repository.set_metadata("last_success_at", finished_at.isoformat())
        repository.set_metadata("last_sync_from", effective_from)
        repository.set_metadata("last_sync_to", effective_to)
        return {
            "status": "success",
            "from_date": effective_from,
            "to_date": effective_to,
            "raw_snapshot_path": raw_snapshot_path,
            "processed_observations": processed,
            "stored_observations": repository.observation_count(),
        }
    except Exception as exc:  # noqa: BLE001
        finished_at = datetime.now(timezone.utc)
        repository.finish_sync_run(
            sync_run_id=sync_run_id,
            finished_at=finished_at,
            status="failed",
            raw_snapshot_path=raw_snapshot_path,
            observation_count=len(observations),
            message=str(exc),
        )
        raise


def build_latest_features(*, project_root: Path | None = None) -> dict[str, Any]:
    """Materialize daily features from canonical observations."""
    _, config, repository = _load_runtime(project_root)
    observations = repository.list_observations(metrics=list(config.required_types.keys()))
    features = materialize_daily_features(observations, timezone_name=config.timezone)
    count = repository.upsert_daily_features(features)
    latest = repository.latest_feature()
    return {
        "feature_days": count,
        "latest_feature_date": latest.feature_date.isoformat() if latest else None,
        "latest_metrics": latest.metrics if latest else {},
    }


def check_alerts(*, project_root: Path | None = None) -> dict[str, Any]:
    """Evaluate the latest feature day and update alert state."""
    _, _, repository = _load_runtime(project_root)
    features = repository.list_daily_features()
    latest = features[-1] if features else None
    if latest is None:
        return {"status": "empty", "message": "No materialized daily features are available yet."}

    active = repository.get_active_alert("recovery_suppression")
    excluded_dates = _excluded_baseline_dates(repository, active)
    baselines = compute_baseline_profiles(features, target_date=latest.feature_date, excluded_dates=excluded_dates)
    repository.replace_baseline_profiles(feature_date=latest.feature_date, profiles=baselines)
    candidate = evaluate_recovery_suppression(features=latest.metrics, baselines=baselines)

    if candidate is None:
        resolved = repository.resolve_alert(
            kind="recovery_suppression",
            summary="Recovery metrics returned to baseline and the alert was closed.",
        )
        return {
            "status": "resolved" if resolved else "clear",
            "active_alerts": [asdict(alert) for alert in repository.list_open_alerts()],
            "latest_feature_date": latest.feature_date.isoformat(),
        }

    status = _status_for_candidate(candidate, repository, active)
    alert = repository.upsert_alert(candidate=candidate, feature_date=latest.feature_date, status=status)
    return {
        "status": "active",
        "latest_feature_date": latest.feature_date.isoformat(),
        "alert": _alert_to_dict(alert),
    }


def explain_latest_alert(*, project_root: Path | None = None) -> dict[str, Any]:
    """Explain the latest alert with fixed sections."""
    _, _, repository = _load_runtime(project_root)
    alert = repository.get_latest_alert()
    if alert is None:
        return {
            "status": "empty",
            "changed": "No alert history is available yet.",
            "supporting_signals": [],
            "missing_context": "No follow-up question.",
            "history": "No prior episode history.",
        }

    latest_feature = repository.latest_feature()
    baselines = repository.get_baseline_profiles(alert.feature_date)
    context_events = repository.list_context_events(alert.episode_id)
    prior_episode = repository.latest_resolved_episode(alert.kind, exclude_episode_id=alert.episode_id)
    prior_context = repository.list_context_events(prior_episode.id) if prior_episode else []
    prior_outcomes = repository.list_intervention_outcomes(prior_episode.id) if prior_episode else []

    changed = _build_changed_summary(alert, latest_feature, baselines)
    missing_context = _build_missing_context(alert, context_events)
    history = _build_history_summary(prior_episode, prior_context, prior_outcomes)

    return {
        "status": alert.status,
        "alert_id": alert.id,
        "kind": alert.kind,
        "changed": changed,
        "supporting_signals": list(alert.supporting_signals),
        "missing_context": missing_context,
        "history": history,
        "context_events": [asdict(event) for event in context_events],
    }


def record_context_event(
    *,
    event_type: str,
    note: str,
    effective_date: str | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Persist a user-provided context event against the active episode if present."""
    _, _, repository = _load_runtime(project_root)
    active = repository.get_active_alert("recovery_suppression")
    event = repository.add_context_event(
        event_type=event_type,
        note=note,
        effective_date=date.fromisoformat(effective_date) if effective_date else _today_date(),
        episode_id=active.episode_id if active else None,
    )
    return {
        "status": "recorded",
        "episode_id": event.episode_id,
        "event": asdict(event),
    }


def list_open_alerts(*, project_root: Path | None = None) -> dict[str, Any]:
    """Return open alerts."""
    _, _, repository = _load_runtime(project_root)
    alerts = repository.list_open_alerts()
    return {"alerts": [_alert_to_dict(alert) for alert in alerts]}


def get_user_profile(*, project_root: Path | None = None) -> dict[str, Any]:
    """Return the durable per-project bootstrap profile."""
    _, _, repository = _load_runtime(project_root)
    return _profile_to_dict(repository.get_user_profile())


def set_user_profile(
    *,
    project_root: Path | None = None,
    auto_brief_enabled: bool | None = None,
    always_sync_on_brief: bool | None = None,
    default_briefing_mode: str | None = None,
    preferred_metrics: list[str] | None = None,
    standing_instruction: str | None = None,
) -> dict[str, Any]:
    """Persist durable per-project bootstrap preferences."""
    _, _, repository = _load_runtime(project_root)
    normalized_metrics = None if preferred_metrics is None else _normalize_metric_names(preferred_metrics)
    if default_briefing_mode is not None and default_briefing_mode not in BRIEFING_MODES:
        raise ValueError(
            f"Unsupported briefing mode {default_briefing_mode!r}. "
            f"Expected one of: {', '.join(BRIEFING_MODES)}"
        )
    profile = repository.update_user_profile(
        auto_brief_enabled=auto_brief_enabled,
        always_sync_on_brief=always_sync_on_brief,
        default_briefing_mode=default_briefing_mode,
        preferred_metrics=normalized_metrics,
        standing_instruction=standing_instruction,
    )
    return _profile_to_dict(profile)


def get_briefing(*, project_root: Path | None = None, force_refresh: bool | None = None) -> dict[str, Any]:
    """Build the fresh-chat bootstrap briefing from durable local state."""
    briefing = _build_briefing(project_root=project_root, force_refresh=force_refresh)
    return _briefing_to_dict(briefing)


def answer_health_question(*, question: str, project_root: Path | None = None) -> dict[str, Any]:
    """Answer a health question from VitalClaw data first, with cautious context when needed."""
    clean_question = question.strip()
    if not clean_question:
        raise ValueError("Question must not be empty.")

    _, config, repository = _load_runtime(project_root)
    profile = repository.get_user_profile()
    briefing = get_briefing(
        project_root=project_root,
        force_refresh=profile.always_sync_on_brief if profile.auto_brief_enabled else False,
    )
    open_alerts = (
        list_open_alerts(project_root=project_root).get("alerts", [])
        if _question_targets_alerts(clean_question) or int(briefing.get("open_alert_count", 0) or 0) > 0
        else []
    )

    freshness = {
        "refreshed_now": bool(briefing["sync"]["refreshed_now"]),
        "last_success_at": briefing["sync"].get("last_success_at"),
        "last_success_at_local": _format_timestamp_local(briefing["sync"].get("last_success_at"), config.timezone),
        "latest_feature_date": briefing.get("latest_feature_date"),
        "timezone": config.timezone,
    }
    data_points_used = _build_data_points_used(briefing, open_alerts, freshness)
    general_context = _build_general_context(clean_question, briefing)

    return {
        "question": clean_question,
        "status": dict(briefing.get("status") or {}),
        "freshness": freshness,
        "answer": _compose_health_answer(clean_question, briefing, open_alerts, freshness),
        "data_points_used": data_points_used,
        "missing_data_notes": list(briefing.get("missing_data_notes") or []),
        "general_context": general_context,
    }


def _build_briefing(*, project_root: Path | None = None, force_refresh: bool | None = None) -> Briefing:
    """Internal briefing builder shared by bootstrap and answer wrapper."""
    _, _, repository = _load_runtime(project_root)
    profile = repository.get_user_profile()
    refreshed_now = False
    sync_status = "cached"
    should_refresh = profile.always_sync_on_brief if force_refresh is None else force_refresh

    if should_refresh:
        sync_result = sync_remote_data(project_root=project_root)
        build_latest_features(project_root=project_root)
        check_alerts(project_root=project_root)
        refreshed_now = True
        sync_status = str(sync_result.get("status") or "success")

    _, _, repository = _load_runtime(project_root)
    profile = repository.get_user_profile()
    snapshot = dashboard_snapshot(project_root=project_root)
    metrics = _select_briefing_metrics(snapshot, profile)
    missing_data_notes = _build_missing_data_notes(metrics)

    briefing = Briefing(
        profile=profile,
        sync=BriefingSyncStatus(
            refreshed_now=refreshed_now,
            status=sync_status,
            last_success_at=repository.get_metadata("last_success_at"),
            last_sync_from=repository.get_metadata("last_sync_from"),
            last_sync_to=repository.get_metadata("last_sync_to"),
        ),
        latest_feature_date=snapshot.get("latest_feature_date"),
        status=dict(snapshot.get("status") or {}),
        open_alert_count=int(snapshot.get("open_alert_count", 0) or 0),
        metrics=metrics,
        missing_data_notes=missing_data_notes,
        standing_instruction=profile.standing_instruction,
        snapshot=snapshot if profile.default_briefing_mode == "full_snapshot" else None,
    )
    return briefing


def dashboard_snapshot(*, project_root: Path | None = None) -> dict[str, Any]:
    """Return the latest monitoring console snapshot."""
    from vitalclaw.ui_snapshot import dashboard_snapshot as _dashboard_snapshot

    return _dashboard_snapshot(project_root=project_root)


def _load_runtime(project_root: Path | None = None) -> tuple[RuntimePaths, AppConfig, Repository]:
    paths = get_runtime_paths(project_root)
    ensure_runtime_dirs(paths)
    connection = connect(paths.db_path)
    initialize(connection)
    config = load_config(paths)
    if config is None:
        raise RuntimeError("VitalClaw is not initialized. Run `vitalclaw init --account-key <key>` first.")
    return paths, config, Repository(connection)


def _write_raw_snapshot(paths: RuntimePaths, packages: list[dict[str, Any]], *, from_date: str, to_date: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = paths.raw_dir / f"sync-{timestamp}.json"
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "from_date": from_date,
        "to_date": to_date,
        "packages": packages,
    }
    snapshot_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(snapshot_path)


def _excluded_baseline_dates(repository: Repository, active: StoredAlert | None) -> set[date]:
    if active is None:
        return set()
    episode = repository.get_episode(active.episode_id)
    if episode is None:
        return {active.feature_date}
    dates: set[date] = set()
    current = episode.first_feature_date
    while current <= episode.last_feature_date:
        dates.add(current)
        current += timedelta(days=1)
    return dates


def _status_for_candidate(
    candidate: AlertCandidate,
    repository: Repository,
    active: StoredAlert | None,
):
    if active and repository.list_context_events(active.episode_id):
        return "monitoring"
    if candidate.question:
        return "waiting_for_user_input"
    return "open"


def _build_changed_summary(
    alert: StoredAlert,
    latest_feature: DailyFeature | None,
    baselines: dict[str, Any],
) -> str:
    if latest_feature is None:
        return "No latest feature row is available."
    pieces = []
    for metric, value in latest_feature.metrics.items():
        if metric not in baselines:
            continue
        profile = baselines[metric]
        pieces.append(f"{metric}={value:.2f} vs baseline {profile.long_median:.2f}")
    if not pieces:
        return "The latest feature set does not have comparable baseline history yet."
    return " ; ".join(pieces)


def _build_missing_context(alert: StoredAlert, context_events: list[Any]) -> str:
    if context_events:
        latest = context_events[-1]
        return f"Latest recorded context: {latest.event_type} - {latest.note}"
    return alert.question or "No follow-up question."


def _build_history_summary(prior_episode, prior_context, prior_outcomes) -> str:
    if prior_episode is None:
        return "No prior similar episode has been recorded."
    pieces = [
        f"Prior similar episode closed on {prior_episode.closed_at.date().isoformat() if prior_episode.closed_at else 'unknown date'}."
    ]
    if prior_context:
        summary = "; ".join(f"{event.event_type}: {event.note}" for event in prior_context[-3:])
        pieces.append(f"Context then: {summary}.")
    if prior_outcomes:
        summary = "; ".join(f"{outcome.action} -> {outcome.outcome}" for outcome in prior_outcomes[-3:])
        pieces.append(f"Outcomes then: {summary}.")
    else:
        pieces.append("No intervention outcomes were recorded for that prior episode.")
    return " ".join(pieces)


def _alert_to_dict(alert: StoredAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "episode_id": alert.episode_id,
        "kind": alert.kind,
        "title": alert.title,
        "summary": alert.summary,
        "supporting_signals": list(alert.supporting_signals),
        "status": alert.status,
        "question": alert.question,
        "feature_date": alert.feature_date.isoformat(),
        "first_seen_at": alert.first_seen_at.isoformat(),
        "last_seen_at": alert.last_seen_at.isoformat(),
    }


def _profile_to_dict(profile: UserProfile) -> dict[str, Any]:
    return {
        "auto_brief_enabled": profile.auto_brief_enabled,
        "always_sync_on_brief": profile.always_sync_on_brief,
        "default_briefing_mode": profile.default_briefing_mode,
        "preferred_metrics": list(profile.preferred_metrics),
        "standing_instruction": profile.standing_instruction,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def _briefing_to_dict(briefing: Briefing) -> dict[str, Any]:
    return {
        "profile": _profile_to_dict(briefing.profile),
        "sync": {
            "refreshed_now": briefing.sync.refreshed_now,
            "status": briefing.sync.status,
            "last_success_at": briefing.sync.last_success_at,
            "last_sync_from": briefing.sync.last_sync_from,
            "last_sync_to": briefing.sync.last_sync_to,
        },
        "latest_feature_date": briefing.latest_feature_date,
        "status": dict(briefing.status),
        "open_alert_count": briefing.open_alert_count,
        "metrics": [
            {
                "metric": metric.metric,
                "label": metric.label,
                "current_display": metric.current_display,
                "baseline_display": metric.baseline_display,
                "delta": metric.delta,
                "tone": metric.tone,
            }
            for metric in briefing.metrics
        ],
        "missing_data_notes": list(briefing.missing_data_notes),
        "standing_instruction": briefing.standing_instruction,
        "snapshot": briefing.snapshot,
    }


def _compose_health_answer(
    question: str,
    briefing: dict[str, Any],
    open_alerts: list[dict[str, Any]],
    freshness: dict[str, Any],
) -> str:
    lowered = question.lower()
    status = dict(briefing.get("status") or {})
    status_label = str(status.get("label") or "Unknown status")
    latest_feature_date = str(briefing.get("latest_feature_date") or "unknown date")
    last_sync = str(freshness.get("last_success_at_local") or freshness.get("last_success_at") or "unknown time")
    open_alert_count = int(briefing.get("open_alert_count", 0) or 0)
    metric_summary = _summarize_metrics_for_answer(briefing.get("metrics", []))

    if _question_targets_alerts(lowered):
        if open_alert_count == 0:
            return (
                f"VitalClaw does not have an active alert right now. Current status is {status_label}. "
                f"The latest completed feature day is {latest_feature_date}, and the last successful sync was {last_sync}. "
                f"{metric_summary}"
            )
        alert_titles = ", ".join(alert["title"] for alert in open_alerts[:3]) or f"{open_alert_count} active alert(s)"
        return (
            f"VitalClaw currently has {open_alert_count} open alert(s): {alert_titles}. "
            f"Current status is {status_label}. The latest completed feature day is {latest_feature_date}, "
            f"and the last successful sync was {last_sync}. {metric_summary}"
        )

    if _question_targets_trends(lowered):
        return (
            f"Compared with your current baseline, VitalClaw shows {status_label.lower()} on {latest_feature_date}. "
            f"{metric_summary} The last successful sync was {last_sync}."
        )

    if _question_targets_recovery(lowered):
        return (
            f"VitalClaw currently reads as {status_label.lower()} on the latest completed feature day, {latest_feature_date}. "
            f"There are {open_alert_count} open alert(s). {metric_summary} The last successful sync was {last_sync}."
        )

    return (
        f"Based on VitalClaw, your current status is {status_label}. "
        f"The latest completed feature day is {latest_feature_date}, there are {open_alert_count} open alert(s), "
        f"and the last successful sync was {last_sync}. {metric_summary}"
    )


def _build_data_points_used(
    briefing: dict[str, Any],
    open_alerts: list[dict[str, Any]],
    freshness: dict[str, Any],
) -> list[str]:
    points = [
        f"Status: {briefing['status'].get('label', 'Unknown')} ({briefing['status'].get('reason', 'No reason provided')})",
        f"Latest completed feature day: {briefing.get('latest_feature_date') or 'unknown'}",
        f"Last successful sync: {freshness.get('last_success_at_local') or freshness.get('last_success_at') or 'unknown'}",
        f"Open alerts: {int(briefing.get('open_alert_count', 0) or 0)}",
    ]
    for metric in briefing.get("metrics", [])[:5]:
        points.append(
            f"{metric['label']}: {metric['current_display']} vs baseline {metric['baseline_display']} ({metric['delta']})"
        )
    for alert in open_alerts[:3]:
        points.append(f"Open alert: {alert['title']} on {alert['feature_date']}")
    return points


def _summarize_metrics_for_answer(metrics: list[dict[str, Any]]) -> str:
    prioritized = sorted(
        metrics,
        key=lambda metric: (
            0 if metric.get("tone") == "alert" else 1 if metric.get("tone") == "warn" else 2,
            1 if str(metric.get("delta") or "").lower() == "missing" else 0,
        ),
    )
    facts: list[str] = []
    for metric in prioritized:
        if len(facts) == 3:
            break
        delta = str(metric.get("delta") or "")
        label = str(metric.get("label") or "Metric")
        current = str(metric.get("current_display") or "n/a")
        baseline = str(metric.get("baseline_display") or "n/a")
        if delta.lower() == "missing" or current.lower() == "n/a":
            facts.append(f"{label} is missing for the latest feature day.")
            continue
        relation = "above" if delta.startswith("↑") else "below" if delta.startswith("↓") else "near"
        facts.append(f"{label} is {relation} baseline at {current} versus {baseline}.")
    if not facts:
        return "VitalClaw does not have comparable metric cards for the latest feature day yet."
    return " ".join(facts)


def _build_general_context(question: str, briefing: dict[str, Any]) -> str | None:
    lowered = question.lower()
    if not _question_needs_general_context(lowered):
        return None

    if briefing.get("missing_data_notes"):
        return (
            "General context: Missing recovery signals reduce certainty, so any interpretation should be treated as "
            "incomplete rather than diagnostic."
        )

    if int(briefing.get("open_alert_count", 0) or 0) > 0:
        return (
            "General context: A VitalClaw alert means several personal recovery signals moved together, but that pattern "
            "is still nonspecific and can reflect illness, stress, sleep disruption, or training load."
        )

    return (
        "General context: Mild drift or baseline changes in recovery signals are not diagnostic on their own. They are "
        "usually best interpreted alongside symptoms, sleep disruption, stress, travel, alcohol, or training load."
    )


def _question_targets_alerts(question: str) -> bool:
    return any(token in question for token in ("alert", "warning", "warn", "flag", "issue", "problem"))


def _question_targets_trends(question: str) -> bool:
    return any(
        token in question
        for token in ("trend", "trending", "compare", "compared", "change", "changing", "week", "better", "worse")
    )


def _question_targets_recovery(question: str) -> bool:
    return any(token in question for token in ("recovery", "readiness", "ready", "strain"))


def _question_needs_general_context(question: str) -> bool:
    return any(
        token in question
        for token in ("mean", "why", "sick", "ill", "danger", "dangerous", "worry", "normal", "bad", "serious")
    )


def _select_briefing_metrics(snapshot: dict[str, Any], profile: UserProfile) -> list[BriefingMetric]:
    if profile.default_briefing_mode == "status_only":
        return []

    cards = {str(metric["metric"]): metric for metric in snapshot.get("metrics", [])}
    selected: list[BriefingMetric] = []
    metrics_to_use = (
        list(cards.keys()) if profile.default_briefing_mode == "full_snapshot" else list(profile.preferred_metrics)
    )
    for metric_name in metrics_to_use:
        card = cards.get(metric_name)
        if card is None:
            selected.append(
                BriefingMetric(
                    metric=metric_name,
                    label=_metric_label(metric_name),
                    current_display="n/a",
                    baseline_display="n/a",
                    delta="Missing",
                    tone="warn",
                )
            )
            continue
        selected.append(
            BriefingMetric(
                metric=metric_name,
                label=str(card.get("label") or _metric_label(metric_name)),
                current_display=str(card.get("current_display") or "n/a"),
                baseline_display=str(card.get("baseline_display") or "n/a"),
                delta=str(card.get("delta") or "Missing"),
                tone=str(card.get("tone") or "warn"),
            )
        )
    return selected


def _build_missing_data_notes(metrics: list[BriefingMetric]) -> list[str]:
    notes: list[str] = []
    for metric in metrics:
        current = metric.current_display.strip().lower()
        delta = metric.delta.strip().lower()
        if current == "n/a" or delta == "missing":
            notes.append(f"{metric.label} is missing for the latest feature day.")
    return notes


def _normalize_metric_names(metrics: list[str]) -> list[str]:
    normalized: list[str] = []
    for metric in metrics:
        name = str(metric).strip()
        if not name or name in normalized:
            continue
        normalized.append(name)
    return normalized


def _metric_label(metric: str) -> str:
    return metric.replace("_", " ").title()


def _format_timestamp_local(value: str | None, timezone_name: str) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.strftime("%Y-%m-%d %H:%M")
    try:
        return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def _today_iso() -> str:
    return _today_date().isoformat()


def _today_date() -> date:
    return datetime.now(timezone.utc).date()


def _days_ago(days: int) -> str:
    return (_today_date() - timedelta(days=days)).isoformat()
