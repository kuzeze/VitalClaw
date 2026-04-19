"""Dashboard snapshot preparation for the local UI."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from vitalclaw.monitor.baselines import compute_baseline_profiles
from vitalclaw.schema import DailyFeature, StoredAlert
from vitalclaw.service import _load_runtime

METRIC_LABELS = {
    "sleep_duration_hours": "Sleep duration",
    "resting_heart_rate": "Resting heart rate",
    "heart_rate_variability_sdnn": "HRV (SDNN)",
    "respiratory_rate": "Respiratory rate",
    "wrist_temperature_celsius": "Wrist temperature",
}


def dashboard_snapshot(*, project_root=None) -> dict[str, Any]:
    """Prepare a compact monitoring console snapshot."""
    _, config, repository = _load_runtime(project_root)
    features = repository.list_daily_features()
    latest_feature = repository.latest_feature()
    active_alerts = repository.list_open_alerts()
    latest_alert = repository.get_latest_alert()
    last_sync_at = repository.get_metadata("last_success_at")
    recent_context = repository.list_context_events(active_alerts[0].episode_id) if active_alerts else []

    if latest_feature is None:
        return {
            "status": {
                "label": "No data yet",
                "reason": "Initialize the project and sync Apple Health before the console can evaluate drift.",
                "tone": "warn",
            },
            "latest_feature_date": None,
            "last_sync_at": _format_timestamp(last_sync_at, config.timezone),
            "metrics": [],
            "open_alert_count": 0,
            "latest_alert": None,
            "alert_header": "No alert history",
            "alert_title": "Nothing to show yet",
            "alert_copy": "Once observations and daily features exist, this panel will explain whether the monitor found anything worth your attention.",
        }

    excluded_dates = _episode_dates(repository, active_alerts[0]) if active_alerts else set()
    baselines = compute_baseline_profiles(features, target_date=latest_feature.feature_date, excluded_dates=excluded_dates)
    metrics = _build_metric_cards(features, latest_feature, baselines)
    status = _build_status(active_alerts, latest_feature, metrics)
    alert_header, alert_title, alert_copy = _alert_summary(active_alerts, latest_alert)

    return {
        "status": status,
        "latest_feature_date": latest_feature.feature_date.isoformat(),
        "last_sync_at": _format_timestamp(last_sync_at, config.timezone),
        "metrics": metrics,
        "signal_summary": _signal_summary(metrics),
        "open_alert_count": len(active_alerts),
        "latest_alert": asdict(active_alerts[0]) if active_alerts else (asdict(latest_alert) if latest_alert else None),
        "recent_context": [asdict(event) for event in recent_context[-5:]],
        "alert_header": alert_header,
        "alert_title": alert_title,
        "alert_copy": alert_copy,
    }


def _build_metric_cards(
    features: list[DailyFeature],
    latest_feature: DailyFeature,
    baselines: dict[str, Any],
) -> list[dict[str, Any]]:
    cards = []
    by_metric = {metric: [] for metric in METRIC_LABELS}
    for feature in features[-7:]:
        for metric, label in METRIC_LABELS.items():
            if metric in feature.metrics:
                by_metric[metric].append(feature.metrics[metric])

    for metric, label in METRIC_LABELS.items():
        current = latest_feature.metrics.get(metric)
        baseline = baselines.get(metric)
        trend = by_metric[metric]
        tone = "good"
        delta_text = "No drift"
        if current is None:
            cards.append(
                {
                    "metric": metric,
                    "label": label,
                    "current_display": "n/a",
                    "baseline_display": "n/a",
                    "delta": "Missing",
                    "tone": "warn",
                    "trend": trend,
                }
            )
            continue
        if baseline is not None:
            direction = _delta_direction(metric, current, baseline.long_median)
            drift_value = _drift_amount(metric, current, baseline.long_median)
            if drift_value >= _alert_distance(metric):
                tone = "alert"
            elif drift_value >= _warn_distance(metric):
                tone = "warn"
            delta_text = f"{direction} {drift_value:.2f}"
            baseline_display = _format_metric(metric, baseline.long_median)
        else:
            tone = "warn"
            delta_text = "No baseline"
            baseline_display = "n/a"

        cards.append(
            {
                "metric": metric,
                "label": label,
                "current_display": _format_metric(metric, current),
                "baseline_display": baseline_display,
                "delta": delta_text,
                "tone": tone,
                "trend": trend,
            }
        )
    return cards


def _build_status(active_alerts: list[StoredAlert], latest_feature: DailyFeature, metrics: list[dict[str, Any]]) -> dict[str, str]:
    if active_alerts:
        return {
            "label": "Recovery suppressed",
            "reason": active_alerts[0].summary,
            "tone": "alert",
        }
    if sum(1 for metric in metrics if metric["tone"] == "warn") >= 2:
        return {
            "label": "Mild drift",
            "reason": "A few signals are moving away from recent baseline, but not enough to open an alert yet.",
            "tone": "warn",
        }
    return {
        "label": "On baseline",
        "reason": "No alert-worthy drift was detected from recent personal baseline.",
        "tone": "good",
    }


def _alert_summary(active_alerts: list[StoredAlert], latest_alert: StoredAlert | None) -> tuple[str, str, str]:
    if active_alerts:
        alert = active_alerts[0]
        return (
            alert.kind.replace("_", " "),
            alert.title,
            "Latest open alert, with the signals that qualified to interrupt you and the one missing piece of context that matters most.",
        )
    if latest_alert:
        return (
            "No active alert",
            "No meaningful drift detected",
            "The monitor ran, compared today to baseline, and did not find enough corroborating drift to justify a user-facing interruption.",
        )
    return (
        "No active alert",
        "No meaningful drift detected",
        "The monitor has data and feature history, but no alert family has qualified to interrupt you yet.",
    )


def _signal_summary(metrics: list[dict[str, Any]]) -> list[str]:
    interesting = [metric for metric in metrics if metric["tone"] in {"warn", "alert"}]
    if not interesting:
        return ["No corroborating drift qualified to interrupt you today."]
    return [_summary_line(metric) for metric in interesting[:3]]


def _summary_line(metric: dict[str, Any]) -> str:
    direction = metric["delta"][:1]
    label = metric["label"]
    if label == "HRV (SDNN)" and direction == "↓":
        return "HRV is lower than your usual range."
    if label == "Sleep duration" and direction == "↓":
        return "Sleep duration is below your recent baseline."
    if label == "Resting heart rate" and direction == "↑":
        return "Resting heart rate is above your normal."
    if label == "Respiratory rate" and direction == "↑":
        return "Respiratory rate is elevated relative to baseline."
    if label == "Wrist temperature" and direction == "↑":
        return "Wrist temperature is warmer than your recent baseline."
    if direction == "↑":
        return f"{label} is above your recent baseline."
    return f"{label} is below your recent baseline."


def _delta_direction(metric: str, current: float, baseline: float) -> str:
    if metric in {"heart_rate_variability_sdnn", "sleep_duration_hours"}:
        return "↓" if current < baseline else "↑"
    return "↑" if current > baseline else "↓"


def _drift_amount(metric: str, current: float, baseline: float) -> float:
    if metric == "heart_rate_variability_sdnn":
        return abs(current - baseline)
    return abs(current - baseline)


def _warn_distance(metric: str) -> float:
    return {
        "sleep_duration_hours": 0.5,
        "resting_heart_rate": 3.0,
        "heart_rate_variability_sdnn": 8.0,
        "respiratory_rate": 0.6,
        "wrist_temperature_celsius": 0.2,
    }[metric]


def _alert_distance(metric: str) -> float:
    return {
        "sleep_duration_hours": 1.0,
        "resting_heart_rate": 5.0,
        "heart_rate_variability_sdnn": 12.0,
        "respiratory_rate": 1.0,
        "wrist_temperature_celsius": 0.3,
    }[metric]


def _format_metric(metric: str, value: float) -> str:
    if metric == "sleep_duration_hours":
        return f"{value:.2f} h"
    if metric == "resting_heart_rate":
        return f"{value:.0f} bpm"
    if metric == "heart_rate_variability_sdnn":
        return f"{value:.0f} ms"
    if metric == "respiratory_rate":
        return f"{value:.1f}/min"
    if metric == "wrist_temperature_celsius":
        return f"{value:.2f} °C"
    return f"{value:.2f}"


def _format_timestamp(value: str | None, timezone_name: str | None = None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.strftime("%Y-%m-%d %H:%M")

    if timezone_name:
        try:
            parsed = parsed.astimezone(ZoneInfo(timezone_name))
        except Exception:
            parsed = parsed.astimezone()
    else:
        parsed = parsed.astimezone()

    return parsed.strftime("%Y-%m-%d %H:%M")


def _episode_dates(repository, alert: StoredAlert) -> set[date]:
    episode = repository.get_episode(alert.episode_id)
    if episode is None:
        return {alert.feature_date}
    dates: set[date] = set()
    current = episode.first_feature_date
    while current <= episode.last_feature_date:
        dates.add(current)
        current = date.fromordinal(current.toordinal() + 1)
    return dates
