"""Normalize HealthExport Remote CLI output into canonical observations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from vitalclaw.external.healthexport import HealthTypeRef
from vitalclaw.schema import Observation

REQUIRED_SIGNAL_ALIASES: dict[str, set[str]] = {
    "sleep_duration_hours": {"time_asleep", "sleep_duration_hours", "sleep_analysis"},
    "resting_heart_rate": {"resting_heart_rate"},
    "heart_rate_variability_sdnn": {"heart_rate_variability_sdnn"},
    "respiratory_rate": {"respiratory_rate"},
    "wrist_temperature_celsius": {"wrist_temperature", "wrist_temperature_celsius"},
}

TYPE_PREFERENCES: dict[str, tuple[str, ...]] = {
    "sleep_duration_hours": ("record", "aggregated"),
    "resting_heart_rate": ("aggregated", "record"),
    "heart_rate_variability_sdnn": ("record", "aggregated"),
    "respiratory_rate": ("record", "aggregated"),
    "wrist_temperature_celsius": ("record", "aggregated"),
}


def resolve_required_types(available_types: Iterable[HealthTypeRef]) -> dict[str, HealthTypeRef]:
    """Resolve the five required recovery signals from the remote type catalog."""
    catalog: dict[str, list[HealthTypeRef]] = {}
    for health_type in available_types:
        catalog.setdefault(health_type.slug, []).append(health_type)
    resolved: dict[str, HealthTypeRef] = {}
    missing: list[str] = []

    for metric, aliases in REQUIRED_SIGNAL_ALIASES.items():
        matches: list[HealthTypeRef] = []
        for alias in aliases:
            matches.extend(catalog.get(alias, []))
        match = _select_preferred_type(metric, matches)
        if match is None:
            missing.append(metric)
            continue
        resolved[metric] = match

    if missing:
        joined = ", ".join(sorted(missing))
        raise ValueError(f"HealthExport Remote is missing required types: {joined}")
    return resolved


def extract_observations(
    packages: Iterable[dict[str, Any]],
    *,
    required_type_ids: dict[str, int],
) -> list[Observation]:
    """Convert HealthExport CLI JSON packages into canonical observations."""
    observations: list[Observation] = []
    reverse = {type_id: metric for metric, type_id in required_type_ids.items()}

    for package in packages:
        type_id = int(package.get("type", -1))
        metric = reverse.get(type_id) or _resolve_metric_from_name(str(package.get("type_name", "")))
        if not metric:
            continue

        groups = package.get("data", [])
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            units = str(group.get("units", "")).strip()
            records = group.get("records", [])
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                timestamp = _parse_datetime(record.get("time") or record.get("period"))
                if timestamp is None:
                    continue
                raw_value = record.get("value")
                value = _coerce_float(raw_value)
                if value is None:
                    continue
                normalized_value, normalized_unit = _normalize_value(metric=metric, value=value, units=units)
                observations.append(
                    Observation(
                        metric=metric,
                        value=normalized_value,
                        unit=normalized_unit,
                        start_at=timestamp,
                        end_at=timestamp,
                        source="health-export-remote",
                        external_id=f"{type_id}:{timestamp.isoformat()}:{normalized_value}:{normalized_unit}",
                        context={"type_name": str(package.get("type_name", ""))},
                    )
                )

    observations.sort(key=lambda item: (item.start_at, item.metric))
    return observations


def _resolve_metric_from_name(type_name: str) -> str | None:
    slug = _slugify(type_name)
    for metric, aliases in REQUIRED_SIGNAL_ALIASES.items():
        if slug in aliases:
            return metric
    return None


def _normalize_value(*, metric: str, value: float, units: str) -> tuple[float, str]:
    lowered = units.lower()
    if metric == "sleep_duration_hours":
        if lowered in {"min", "mins", "minute", "minutes"}:
            return value / 60.0, "hours"
        if lowered in {"s", "sec", "secs", "second", "seconds"}:
            return value / 3600.0, "hours"
        return value, "hours"
    if metric == "wrist_temperature_celsius":
        if "f" in lowered and "c" not in lowered:
            return (value - 32.0) * 5.0 / 9.0, "degC"
        return value, "degC"
    return value, units or _default_unit(metric)


def _default_unit(metric: str) -> str:
    return {
        "resting_heart_rate": "beats/min",
        "heart_rate_variability_sdnn": "ms",
        "respiratory_rate": "breaths/min",
    }.get(metric, "count")


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _slugify(value: str) -> str:
    slug = "".join(character if character.isalnum() else "_" for character in value.lower())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _select_preferred_type(metric: str, matches: list[HealthTypeRef]) -> HealthTypeRef | None:
    if not matches:
        return None
    preferences = TYPE_PREFERENCES.get(metric, ("record", "aggregated"))
    for category in preferences:
        for match in matches:
            if match.category == category:
                return match
    return matches[0]
