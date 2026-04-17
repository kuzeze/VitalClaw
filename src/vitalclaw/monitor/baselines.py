"""Robust baseline computation."""

from __future__ import annotations

from datetime import date
from statistics import median

from vitalclaw.schema import BaselineProfile, DailyFeature


def compute_baseline_profiles(
    features: list[DailyFeature],
    *,
    target_date: date,
    excluded_dates: set[date] | None = None,
) -> dict[str, BaselineProfile]:
    """Compute per-metric baselines for a target feature date."""
    excluded_dates = excluded_dates or set()
    historical = [
        feature
        for feature in features
        if feature.feature_date < target_date and feature.feature_date not in excluded_dates
    ]
    profiles: dict[str, BaselineProfile] = {}
    metrics = {
        metric
        for feature in historical
        for metric in feature.metrics
    }
    for metric in metrics:
        values = [feature.metrics[metric] for feature in historical if metric in feature.metrics]
        if not values:
            continue
        long_values = values[-56:]
        short_values = values[-14:]
        long_median = float(median(long_values))
        deviations = [abs(value - long_median) for value in long_values]
        long_mad = float(median(deviations)) if deviations else 0.0
        profiles[metric] = BaselineProfile(
            metric=metric,
            long_median=round(long_median, 4),
            long_mad=round(long_mad, 4),
            short_median=round(float(median(short_values)), 4) if short_values else None,
            sample_count=len(long_values),
        )
    return profiles
