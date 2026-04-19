"""Application services for VitalClaw."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from vitalclaw.external.healthexport import HealthExportCLI
from vitalclaw.external.open_wearables import OpenWearablesClient
from vitalclaw.features.materialize import materialize_daily_features
from vitalclaw.ingest.health_export_remote import extract_observations, resolve_required_types
from vitalclaw.ingest.open_wearables import extract_observations as extract_open_wearables_observations
from vitalclaw.monitor.baselines import compute_baseline_profiles
from vitalclaw.monitor.recovery import evaluate_recovery_suppression
from vitalclaw.runtime import (
    AppConfig,
    DEFAULT_OPEN_WEARABLES_API_URL,
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
    DEFAULT_PREFERRED_METRICS,
    UserProfile,
)
from vitalclaw.storage.db import Repository, connect, initialize


def initialize_project(
    *,
    project_root: Path | None = None,
    account_key: str | None = None,
    he_path: str | None = None,
    source: str = "health_export",
    ow_api_key: str | None = None,
    ow_api_url: str | None = None,
    ow_developer_email: str | None = None,
    ow_developer_password: str | None = None,
) -> dict[str, Any]:
    """Initialize the local VitalClaw runtime and perform the first sync."""
    paths = get_runtime_paths(project_root)
    ensure_runtime_dirs(paths)

    connection = connect(paths.db_path)
    initialize(connection)
    repository = Repository(connection)

    config = load_config(paths) or AppConfig(timezone=local_timezone_name())
    config.source = source
    config.initialized_at = datetime.now(timezone.utc).isoformat()

    if source == "open_wearables":
        return _initialize_open_wearables_project(
            paths=paths,
            repository=repository,
            config=config,
            ow_api_key=ow_api_key,
            ow_api_url=ow_api_url,
            ow_developer_email=ow_developer_email,
            ow_developer_password=ow_developer_password,
        )

    if not account_key:
        raise RuntimeError("Health Export initialization requires --account-key.")

    cli = HealthExportCLI(paths=paths, he_path=he_path or config.he_path, api_url=config.api_url)
    resolved_he_path = str(cli.ensure_available())
    auth_status = cli.configure_account_key(account_key)
    required_types = resolve_required_types(cli.list_types())
    config.he_path = resolved_he_path
    config.required_types = {metric: health_type.id for metric, health_type in required_types.items()}
    save_config(paths, config)

    sync_result = sync_remote_data(project_root=paths.project_root, from_date=_days_ago(90), to_date=_today_iso())
    feature_result = build_latest_features(project_root=paths.project_root)
    alert_result = check_alerts(project_root=paths.project_root)

    repository.set_metadata("active_source", "health_export")
    repository.set_metadata("connected_providers_json", json.dumps(["health_export_remote"]))

    return {
        "runtime_dir": str(paths.runtime_dir),
        "db_path": str(paths.db_path),
        "config_path": str(paths.config_path),
        "source": config.source,
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
    if config.source == "open_wearables":
        return _sync_open_wearables_data(
            paths=paths,
            config=config,
            repository=repository,
            from_date=from_date,
            to_date=to_date,
        )

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
        repository.set_metadata("active_source", "health_export")
        repository.set_metadata("connected_providers_json", json.dumps(["health_export_remote"]))
        return {
            "status": "success",
            "source": "health_export",
            "from_date": effective_from,
            "to_date": effective_to,
            "raw_snapshot_path": raw_snapshot_path,
            "processed_observations": processed,
            "stored_observations": repository.observation_count(),
            "connected_providers": ["health_export_remote"],
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


def open_wearables_connect_app(*, project_root: Path | None = None) -> dict[str, Any]:
    """Generate a fresh Open Wearables invitation code and return app connection instructions."""
    paths, config, _ = _load_runtime(project_root)
    doctor = _ensure_local_open_wearables_running(config)
    if doctor.get("mode") == "local" and not doctor.get("api_reachable"):
        raise RuntimeError(doctor.get("error") or "Local Open Wearables backend is unavailable.")
    client = _load_open_wearables_client(config)
    user_id = _ensure_open_wearables_user(client, config)
    invitation = _generate_open_wearables_invitation(client, config, user_id)
    config.ow_last_invitation_code = str(invitation.get("code") or "")
    save_config(paths, config)
    return {
        "source": config.source,
        "api_url": config.ow_api_url,
        "user_id": user_id,
        "invitation_code": config.ow_last_invitation_code,
        "instructions": _open_wearables_app_instructions(config.ow_api_url or DEFAULT_OPEN_WEARABLES_API_URL, config.ow_last_invitation_code),
    }


def open_wearables_status(*, project_root: Path | None = None) -> dict[str, Any]:
    """Return Open Wearables connection status for the current project."""
    _, config, repository = _load_runtime(project_root)
    doctor = open_wearables_doctor(project_root=project_root)
    if doctor.get("mode") == "local" and not doctor.get("api_reachable"):
        return {
            "source": config.source,
            "api_url": config.ow_api_url,
            "user_id": config.ow_user_id,
            "last_invitation_code": config.ow_last_invitation_code,
            "connections": [],
            "connected_providers": [],
            "last_success_at": repository.get_metadata("last_success_at"),
            "doctor": doctor,
        }
    client = _load_open_wearables_client(config)
    user_id = config.ow_user_id
    if not user_id:
        return {
            "source": config.source,
            "api_url": config.ow_api_url,
            "user_id": None,
            "last_invitation_code": config.ow_last_invitation_code,
            "connections": [],
            "connected_providers": [],
            "last_success_at": repository.get_metadata("last_success_at"),
            "doctor": doctor,
        }
    connections = client.list_connections(user_id)
    connected_providers = sorted({connection.provider for connection in connections if connection.status == "active"})
    return {
        "source": config.source,
        "api_url": config.ow_api_url,
        "user_id": user_id,
        "last_invitation_code": config.ow_last_invitation_code,
        "connections": [
            {
                "id": connection.id,
                "provider": connection.provider,
                "status": connection.status,
                "last_synced_at": connection.last_synced_at,
                "provider_username": connection.provider_username,
            }
            for connection in connections
        ],
        "connected_providers": connected_providers,
        "last_success_at": repository.get_metadata("last_success_at"),
        "doctor": doctor,
    }


def open_wearables_doctor(*, project_root: Path | None = None) -> dict[str, Any]:
    """Inspect and best-effort recover a local Open Wearables instance."""
    _, config, _ = _load_runtime(project_root)
    if not _is_local_open_wearables_api_url(config.ow_api_url):
        return {
            "mode": "remote",
            "api_url": config.ow_api_url or DEFAULT_OPEN_WEARABLES_API_URL,
            "api_reachable": _open_wearables_api_reachable(config.ow_api_url or DEFAULT_OPEN_WEARABLES_API_URL),
            "frontend_reachable": None,
            "containers": {},
            "recovered": False,
        }

    return _ensure_local_open_wearables_running(config)


def _initialize_open_wearables_project(
    *,
    paths: RuntimePaths,
    repository: Repository,
    config: AppConfig,
    ow_api_key: str | None,
    ow_api_url: str | None,
    ow_developer_email: str | None,
    ow_developer_password: str | None,
) -> dict[str, Any]:
    config.ow_api_key = (ow_api_key or config.ow_api_key or "").strip() or None
    config.ow_api_url = (ow_api_url or config.ow_api_url or DEFAULT_OPEN_WEARABLES_API_URL).strip()
    config.ow_developer_email = (ow_developer_email or config.ow_developer_email or "").strip() or None
    config.ow_developer_password = (ow_developer_password or config.ow_developer_password or "").strip() or None
    if not config.ow_api_key:
        raise RuntimeError("Open Wearables initialization requires --ow-api-key.")
    doctor = _ensure_local_open_wearables_running(config)
    if doctor.get("mode") == "local" and not doctor.get("api_reachable"):
        raise RuntimeError(doctor.get("error") or "Local Open Wearables backend is unavailable.")

    client = _load_open_wearables_client(config)
    user_id = _ensure_open_wearables_user(client, config)
    config.ow_user_id = user_id
    status = _open_wearables_status_data(config, repository, client)
    invitation: dict[str, Any] | None = None
    if not status["connected_providers"]:
        invitation = _generate_open_wearables_invitation(client, config, user_id)
        config.ow_last_invitation_code = str(invitation.get("code") or "")
    save_config(paths, config)
    repository.set_metadata("active_source", "open_wearables")
    repository.set_metadata("connected_providers_json", json.dumps(status["connected_providers"]))

    sync_result: dict[str, Any] | None = None
    feature_result: dict[str, Any] | None = None
    alert_result: dict[str, Any] | None = None
    bootstrap_status = "waiting_for_mobile_sync"

    if status["connected_providers"]:
        sync_result = sync_remote_data(project_root=paths.project_root, from_date=_days_ago(90), to_date=_today_iso())
        if int(sync_result.get("stored_observations", 0) or 0) > 0:
            feature_result = build_latest_features(project_root=paths.project_root)
            alert_result = check_alerts(project_root=paths.project_root)
            bootstrap_status = "ready"

    return {
        "runtime_dir": str(paths.runtime_dir),
        "db_path": str(paths.db_path),
        "config_path": str(paths.config_path),
        "source": config.source,
        "authenticated": True,
        "timezone": config.timezone,
        "bootstrap_status": bootstrap_status,
        "doctor": doctor,
        "open_wearables": {
            "api_url": config.ow_api_url,
            "user_id": user_id,
            "invitation_code": config.ow_last_invitation_code,
            "connected_providers": status["connected_providers"],
            "instructions": _open_wearables_app_instructions(config.ow_api_url, config.ow_last_invitation_code),
        },
        "sync": sync_result,
        "materialize": feature_result,
        "alerts": alert_result,
    }


def _sync_open_wearables_data(
    *,
    paths: RuntimePaths,
    config: AppConfig,
    repository: Repository,
    from_date: str | None,
    to_date: str | None,
) -> dict[str, Any]:
    doctor = _ensure_local_open_wearables_running(config)
    if doctor.get("mode") == "local" and not doctor.get("api_reachable"):
        raise RuntimeError(doctor.get("error") or "Local Open Wearables backend is unavailable.")
    client = _load_open_wearables_client(config)
    if not config.ow_user_id:
        raise RuntimeError("Open Wearables user is not configured. Run `vitalclaw init --source open_wearables --ow-api-key ...` first.")

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
    observations: list[Any] = []
    try:
        connections = client.list_connections(config.ow_user_id)
        active_connections = [connection for connection in connections if connection.status == "active"]
        active_providers = sorted({connection.provider for connection in active_connections})
        repository.set_metadata("active_source", "open_wearables")
        repository.set_metadata("connected_providers_json", json.dumps(active_providers))

        trigger_results: list[dict[str, Any]] = []
        for connection in active_connections:
            if _is_sdk_provider(connection.provider):
                continue
            try:
                trigger_results.append(
                    {
                        "provider": connection.provider,
                        "status": "triggered",
                        "response": client.trigger_provider_sync(provider=connection.provider, user_id=config.ow_user_id),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                trigger_results.append({"provider": connection.provider, "status": "failed", "message": str(exc)})

        recovery_summary = _safe_open_wearables_recovery_summary(
            client,
            user_id=config.ow_user_id,
            start_date=effective_from,
            end_date=effective_to,
        )
        sleep_summary = client.get_sleep_summary(
            user_id=config.ow_user_id,
            start_date=effective_from,
            end_date=effective_to,
        )
        timeseries = client.get_timeseries(
            user_id=config.ow_user_id,
            start_time=_start_of_local_day_utc(effective_from, config.timezone).isoformat(),
            end_time=_end_of_local_day_utc(effective_to, config.timezone).isoformat(),
            types=["respiratory_rate", "skin_temperature", "body_temperature"],
        )

        payload = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "from_date": effective_from,
            "to_date": effective_to,
            "connections": [
                {
                    "id": connection.id,
                    "provider": connection.provider,
                    "status": connection.status,
                    "last_synced_at": connection.last_synced_at,
                    "provider_username": connection.provider_username,
                }
                for connection in connections
            ],
            "trigger_results": trigger_results,
            "recovery_summary": recovery_summary,
            "sleep_summary": sleep_summary,
            "timeseries": timeseries,
        }
        raw_snapshot_path = _write_raw_snapshot_payload(paths, payload, from_date=effective_from, to_date=effective_to)
        observations = extract_open_wearables_observations(
            recovery_summary=recovery_summary,
            sleep_summary=sleep_summary,
            timeseries=timeseries,
            timezone_name=config.timezone,
        )
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
            "source": "open_wearables",
            "from_date": effective_from,
            "to_date": effective_to,
            "raw_snapshot_path": raw_snapshot_path,
            "processed_observations": processed,
            "stored_observations": repository.observation_count(),
            "connected_providers": active_providers,
            "trigger_results": trigger_results,
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
    observations = repository.list_observations(metrics=list(DEFAULT_PREFERRED_METRICS))
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
    latest = _select_monitorable_feature(features)
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
        "active_source": briefing.get("active_source"),
        "connected_providers": list(briefing.get("connected_providers") or []),
        "status": dict(briefing.get("status") or {}),
        "freshness": freshness,
        "answer": _compose_health_answer(clean_question, briefing, open_alerts, freshness),
        "data_points_used": data_points_used,
        "missing_data_notes": list(briefing.get("missing_data_notes") or []),
        "general_context": general_context,
    }


def _build_briefing(*, project_root: Path | None = None, force_refresh: bool | None = None) -> Briefing:
    """Internal briefing builder shared by bootstrap and answer wrapper."""
    _, config, repository = _load_runtime(project_root)
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
        active_source=str(snapshot.get("active_source") or config.source),
        connected_providers=[str(item) for item in snapshot.get("connected_providers", [])],
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
        raise RuntimeError(
            "VitalClaw is not initialized. Run `vitalclaw init --source health_export --account-key <key>` "
            "or `vitalclaw init --source open_wearables --ow-api-key <key>` first."
        )
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


def _write_raw_snapshot_payload(paths: RuntimePaths, payload: dict[str, Any], *, from_date: str, to_date: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = paths.raw_dir / f"sync-{timestamp}.json"
    envelope = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "from_date": from_date,
        "to_date": to_date,
        "payload": payload,
    }
    snapshot_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
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
        "active_source": briefing.active_source,
        "connected_providers": list(briefing.connected_providers),
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
        f"Active source: {briefing.get('active_source') or 'unknown'}",
        "Connected providers: " + ", ".join(briefing.get("connected_providers") or ["none"]),
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


def _select_monitorable_feature(features: list[DailyFeature], *, minimum_metrics: int = 2) -> DailyFeature | None:
    if not features:
        return None
    preferred = set(DEFAULT_PREFERRED_METRICS)
    for feature in reversed(features):
        metric_count = sum(1 for metric in feature.metrics if metric in preferred)
        if metric_count >= minimum_metrics:
            return feature
    return features[-1]


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


def _load_open_wearables_client(config: AppConfig) -> OpenWearablesClient:
    return OpenWearablesClient(
        api_key=config.ow_api_key or "",
        api_url=config.ow_api_url or DEFAULT_OPEN_WEARABLES_API_URL,
    )


def _login_open_wearables_developer(client: OpenWearablesClient, config: AppConfig) -> str:
    if not config.ow_developer_email or not config.ow_developer_password:
        raise RuntimeError(
            "Open Wearables developer credentials are required for this self-hosted operation. "
            "Set --ow-developer-email and --ow-developer-password or save them in .vitalclaw/config.toml."
    )
    return client.developer_login(email=config.ow_developer_email, password=config.ow_developer_password)


def _generate_open_wearables_invitation(client: OpenWearablesClient, config: AppConfig, user_id: str) -> dict[str, Any]:
    if _is_local_open_wearables_api_url(config.ow_api_url):
        developer_token = _login_open_wearables_developer(client, config)
        return client.generate_invitation_code(user_id, developer_token=developer_token)
    return client.generate_invitation_code(user_id, developer_token="")


def _ensure_open_wearables_user(client: OpenWearablesClient, config: AppConfig) -> str:
    if config.ow_user_id:
        client.get_user(config.ow_user_id)
        return config.ow_user_id

    users = client.list_users()
    if len(users) == 1:
        return str(users[0]["id"])
    if len(users) > 1:
        raise RuntimeError(
            "Multiple Open Wearables users exist for this account, but no user_id is configured in VitalClaw. "
            "Set [open_wearables].user_id in .vitalclaw/config.toml or delete extra users."
        )
    created = client.create_user()
    return str(created["id"])


def _open_wearables_status_data(config: AppConfig, repository: Repository, client: OpenWearablesClient) -> dict[str, Any]:
    user_id = config.ow_user_id
    if not user_id:
        return {
            "source": config.source,
            "api_url": config.ow_api_url,
            "user_id": None,
            "last_invitation_code": config.ow_last_invitation_code,
            "connections": [],
            "connected_providers": [],
            "last_success_at": repository.get_metadata("last_success_at"),
        }
    connections = client.list_connections(user_id)
    connected_providers = sorted({connection.provider for connection in connections if connection.status == "active"})
    return {
        "source": config.source,
        "api_url": config.ow_api_url,
        "user_id": user_id,
        "last_invitation_code": config.ow_last_invitation_code,
        "connections": [
            {
                "id": connection.id,
                "provider": connection.provider,
                "status": connection.status,
                "last_synced_at": connection.last_synced_at,
                "provider_username": connection.provider_username,
            }
            for connection in connections
        ],
        "connected_providers": connected_providers,
        "last_success_at": repository.get_metadata("last_success_at"),
    }


def _safe_open_wearables_recovery_summary(
    client: OpenWearablesClient,
    *,
    user_id: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    try:
        return client.get_recovery_summary(user_id=user_id, start_date=start_date, end_date=end_date)
    except RuntimeError as exc:
        message = str(exc)
        if "(501)" in message and "Not implemented" in message:
            return []
        raise


def _open_wearables_app_instructions(api_url: str | None, invitation_code: str | None) -> list[str]:
    host = api_url or DEFAULT_OPEN_WEARABLES_API_URL
    code = invitation_code or "N/A"
    return [
        f"Open the official Open Wearables TestFlight app and enter the API host `{host}` (not the dashboard URL).",
        f"Paste the invitation code `{code}` and connect the app to your Open Wearables user.",
        "Grant Apple Health permissions when prompted and enable background sync in the app.",
        "After the first background sync completes, run `vitalclaw sync` again or refresh the VitalClaw UI.",
    ]


def _is_sdk_provider(provider: str) -> bool:
    return provider in {"apple_health", "google_health_connect", "samsung_health"}


def _is_local_open_wearables_api_url(api_url: str | None) -> bool:
    if not api_url:
        return False
    host = (urlparse(api_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1"}


def _open_wearables_api_reachable(api_url: str) -> bool:
    try:
        subprocess.run(
            ["curl", "-fsS", f"{api_url.rstrip('/')}/"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def _open_wearables_frontend_reachable() -> bool:
    try:
        subprocess.run(
            ["curl", "-fsS", "http://127.0.0.1:3001/"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def _docker_container_status(name: str) -> str | None:
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Status}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "docker ps failed")
    status = result.stdout.strip()
    return status or None


def _docker_start_container(name: str) -> None:
    subprocess.run(["docker", "start", name], capture_output=True, text=True, check=True, timeout=30)


def _docker_restart_container(name: str) -> None:
    subprocess.run(["docker", "restart", name], capture_output=True, text=True, check=True, timeout=30)


def _ensure_local_open_wearables_running(config: AppConfig) -> dict[str, Any]:
    api_url = config.ow_api_url or DEFAULT_OPEN_WEARABLES_API_URL
    report = {
        "mode": "local",
        "api_url": api_url,
        "api_reachable": False,
        "frontend_reachable": _open_wearables_frontend_reachable(),
        "containers": {},
        "recovered": False,
    }
    if not _is_local_open_wearables_api_url(api_url):
        return report

    if _open_wearables_api_reachable(api_url):
        report["api_reachable"] = True
        for name in ("backend__open-wearables", "postgres__open-wearables", "redis__open-wearables", "frontend__open-wearables"):
            try:
                report["containers"][name] = _docker_container_status(name)
            except Exception as exc:  # noqa: BLE001
                report["containers"][name] = f"error: {exc}"
        return report

    try:
        statuses = {
            name: _docker_container_status(name)
            for name in ("backend__open-wearables", "postgres__open-wearables", "redis__open-wearables", "frontend__open-wearables")
        }
        report["containers"] = dict(statuses)
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"Docker unavailable: {exc}"
        return report

    for dependency in ("postgres__open-wearables", "redis__open-wearables"):
        status = statuses.get(dependency)
        if status and not status.lower().startswith("up"):
            _docker_start_container(dependency)
            report["recovered"] = True
            report["containers"][dependency] = _docker_container_status(dependency)

    backend_status = statuses.get("backend__open-wearables")
    if backend_status:
        if backend_status.lower().startswith("up"):
            _docker_restart_container("backend__open-wearables")
        else:
            _docker_start_container("backend__open-wearables")
        report["recovered"] = True
        report["containers"]["backend__open-wearables"] = _docker_container_status("backend__open-wearables")

    for _ in range(20):
        if _open_wearables_api_reachable(api_url):
            report["api_reachable"] = True
            report["frontend_reachable"] = _open_wearables_frontend_reachable()
            return report
        time.sleep(1)

    report["api_reachable"] = False
    report["frontend_reachable"] = _open_wearables_frontend_reachable()
    report["error"] = "Local Open Wearables backend is still unavailable after recovery attempts."
    return report


def _start_of_local_day_utc(value: str, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name) if timezone_name else timezone.utc
    local = datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=zone)
    return local.astimezone(timezone.utc)


def _end_of_local_day_utc(value: str, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name) if timezone_name else timezone.utc
    local = datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=zone) + timedelta(days=1)
    return local.astimezone(timezone.utc)


def _today_iso() -> str:
    return _today_date().isoformat()


def _today_date() -> date:
    return datetime.now(timezone.utc).date()


def _days_ago(days: int) -> str:
    return (_today_date() - timedelta(days=days)).isoformat()
