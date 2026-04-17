"""Build daily features from canonical observations."""

from __future__ import annotations

from collections import defaultdict
from datetime import timezone
from statistics import mean
from zoneinfo import ZoneInfo

from vitalclaw.schema import DailyFeature, Observation


def materialize_daily_features(
    observations: list[Observation],
    *,
    timezone_name: str,
) -> list[DailyFeature]:
    """Group observations by local day and metric."""
    zone = ZoneInfo(timezone_name) if timezone_name and timezone_name != "UTC" else timezone.utc
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    bounds: dict[str, tuple[object | None, object | None]] = {}

    for observation in observations:
        local_time = observation.end_at.astimezone(zone)
        feature_key = local_time.date().isoformat()
        grouped[feature_key][observation.metric].append(observation.value)

        window_start, window_end = bounds.get(feature_key, (None, None))
        if window_start is None or observation.start_at < window_start:
            window_start = observation.start_at
        if window_end is None or observation.end_at > window_end:
            window_end = observation.end_at
        bounds[feature_key] = (window_start, window_end)

    features: list[DailyFeature] = []
    for feature_key in sorted(grouped):
        metric_groups = grouped[feature_key]
        metrics: dict[str, float] = {}
        for metric, values in metric_groups.items():
            if metric == "sleep_duration_hours":
                metrics[metric] = round(sum(values), 3)
            else:
                metrics[metric] = round(mean(values), 3)
        window_start, window_end = bounds.get(feature_key, (None, None))
        features.append(
            DailyFeature(
                feature_date=_parse_date(feature_key),
                metrics=metrics,
                observation_count=sum(len(values) for values in metric_groups.values()),
                window_start=window_start,
                window_end=window_end,
            )
        )
    return features


def _parse_date(value: str):
    from datetime import date

    return date.fromisoformat(value)
