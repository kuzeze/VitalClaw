"""Application services for VitalClaw."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

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


def _today_iso() -> str:
    return _today_date().isoformat()


def _today_date() -> date:
    return datetime.now(timezone.utc).date()


def _days_ago(days: int) -> str:
    return (_today_date() - timedelta(days=days)).isoformat()
