"""Normalize Open Wearables data into canonical observations."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from vitalclaw.schema import Observation

TEMPERATURE_TYPES = {"skin_temperature", "body_temperature"}


def extract_observations(
    *,
    recovery_summary: list[dict[str, Any]],
    sleep_summary: list[dict[str, Any]],
    timeseries: list[dict[str, Any]],
    timezone_name: str,
) -> list[Observation]:
    """Convert Open Wearables summary and timeseries payloads into observations."""
    zone = ZoneInfo(timezone_name) if timezone_name else timezone.utc
    observations: list[Observation] = []

    temperature_samples = _preferred_temperature_samples(timeseries)
    respiratory_days = _timeseries_local_days(timeseries, zone, metric_type="respiratory_rate")

    for sample in timeseries:
        if not isinstance(sample, dict):
            continue
        metric = _metric_from_timeseries_type(str(sample.get("type") or ""))
        if metric is None:
            continue
        timestamp = _parse_datetime(sample.get("timestamp"))
        if timestamp is None:
            continue
        provider = _provider_name(sample)
        if metric == "wrist_temperature_celsius":
            key = (provider, timestamp.isoformat())
            preferred = temperature_samples.get(key)
            if preferred is None or preferred is not sample:
                continue
        value = _coerce_float(sample.get("value"))
        if value is None:
            continue
        normalized_value, normalized_unit = _normalize_timeseries_value(metric=metric, value=value, units=str(sample.get("unit") or ""))
        observations.append(
            Observation(
                metric=metric,
                value=normalized_value,
                unit=normalized_unit,
                start_at=timestamp,
                end_at=timestamp,
                source="open-wearables",
                external_id=f"ow:{provider}:{metric}:{timestamp.isoformat()}:{normalized_value}",
                context=_sample_context(sample),
            )
        )

    for sample in recovery_summary:
        if not isinstance(sample, dict):
            continue
        summary_date = _parse_date(sample.get("date"))
        if summary_date is None:
            continue
        provider = _provider_name(sample)
        timestamp = _local_noon(summary_date, zone)
        for metric, field_name, divisor, unit in (
            ("sleep_duration_hours", "sleep_duration_seconds", 3600.0, "hours"),
            ("resting_heart_rate", "resting_heart_rate_bpm", 1.0, "beats/min"),
            ("heart_rate_variability_sdnn", "avg_hrv_sdnn_ms", 1.0, "ms"),
        ):
            value = _coerce_float(sample.get(field_name))
            if value is None:
                continue
            observations.append(
                Observation(
                    metric=metric,
                    value=round(value / divisor, 6),
                    unit=unit,
                    start_at=timestamp,
                    end_at=timestamp,
                    source="open-wearables",
                    external_id=f"ow:{provider}:{metric}:{summary_date.isoformat()}:recovery",
                    context=_summary_context(sample, summary_type="recovery"),
                )
            )

    for sample in sleep_summary:
        if not isinstance(sample, dict):
            continue
        summary_date = _parse_date(sample.get("date"))
        if summary_date is None:
            continue
        provider = _provider_name(sample)
        if (provider, summary_date.isoformat()) in respiratory_days:
            continue
        value = _coerce_float(sample.get("avg_respiratory_rate"))
        if value is None:
            continue
        timestamp = _local_noon(summary_date, zone)
        observations.append(
            Observation(
                metric="respiratory_rate",
                value=value,
                unit="breaths/min",
                start_at=timestamp,
                end_at=timestamp,
                source="open-wearables",
                external_id=f"ow:{provider}:respiratory_rate:{summary_date.isoformat()}:sleep",
                context=_summary_context(sample, summary_type="sleep"),
            )
        )

    observations.sort(key=lambda item: (item.start_at, item.metric, item.external_id or ""))
    return observations


def _preferred_temperature_samples(samples: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        sample_type = str(sample.get("type") or "")
        if sample_type not in TEMPERATURE_TYPES:
            continue
        timestamp = _parse_datetime(sample.get("timestamp"))
        if timestamp is None:
            continue
        provider = _provider_name(sample)
        key = (provider, timestamp.isoformat())
        existing = grouped.get(key)
        if existing is None or sample_type == "skin_temperature":
            grouped[key] = sample
    return grouped


def _timeseries_local_days(samples: list[dict[str, Any]], zone: ZoneInfo | timezone, *, metric_type: str) -> set[tuple[str, str]]:
    days: set[tuple[str, str]] = set()
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        if str(sample.get("type") or "") != metric_type:
            continue
        timestamp = _parse_datetime(sample.get("timestamp"))
        if timestamp is None:
            continue
        provider = _provider_name(sample)
        days.add((provider, timestamp.astimezone(zone).date().isoformat()))
    return days


def _metric_from_timeseries_type(value: str) -> str | None:
    if value == "respiratory_rate":
        return "respiratory_rate"
    if value == "resting_heart_rate":
        return "resting_heart_rate"
    if value == "heart_rate_variability_sdnn":
        return "heart_rate_variability_sdnn"
    if value in TEMPERATURE_TYPES:
        return "wrist_temperature_celsius"
    return None


def _normalize_timeseries_value(*, metric: str, value: float, units: str) -> tuple[float, str]:
    lowered = units.lower()
    if metric == "wrist_temperature_celsius":
        if lowered.startswith("f") and "c" not in lowered:
            return (value - 32.0) * 5.0 / 9.0, "degC"
        return value, "degC"
    if metric == "respiratory_rate":
        return value, units or "breaths/min"
    if metric == "resting_heart_rate":
        return value, units or "beats/min"
    if metric == "heart_rate_variability_sdnn":
        return value, units or "ms"
    return value, units or "count"


def _sample_context(sample: dict[str, Any]) -> dict[str, str]:
    source = sample.get("source", {})
    provider = _provider_name(sample)
    context = {
        "provider": provider,
        "sample_type": str(sample.get("type") or ""),
    }
    if isinstance(source, dict):
        device = str(source.get("device") or "").strip()
        if device:
            context["device"] = device
    return context


def _summary_context(sample: dict[str, Any], *, summary_type: str) -> dict[str, str]:
    source = sample.get("source", {})
    provider = _provider_name(sample)
    context = {
        "provider": provider,
        "summary_type": summary_type,
    }
    if isinstance(source, dict):
        device = str(source.get("device") or "").strip()
        if device:
            context["device"] = device
    return context


def _provider_name(sample: dict[str, Any]) -> str:
    source = sample.get("source", {})
    if isinstance(source, dict):
        provider = str(source.get("provider") or "").strip()
        if provider:
            return provider
    return "open_wearables"


def _local_noon(summary_date: date, zone: ZoneInfo | timezone) -> datetime:
    local_noon = datetime.combine(summary_date, time(hour=12, minute=0, second=0), tzinfo=zone)
    return local_noon.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        timestamp = _parse_datetime(text)
        if timestamp is None:
            return None
        return timestamp.date()


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
