from datetime import date, datetime, timezone

from vitalclaw.features.materialize import materialize_daily_features
from vitalclaw.monitor.baselines import compute_baseline_profiles
from vitalclaw.schema import Observation


def test_materialize_daily_features_averages_record_metrics() -> None:
    observations = [
        Observation(
            metric="sleep_duration_hours",
            value=7.5,
            unit="hours",
            start_at=datetime(2026, 4, 16, 23, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 4, 17, 7, 0, tzinfo=timezone.utc),
            source="test",
            external_id="sleep-1",
        ),
        Observation(
            metric="resting_heart_rate",
            value=56.0,
            unit="beats/min",
            start_at=datetime(2026, 4, 17, 8, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 4, 17, 8, 0, tzinfo=timezone.utc),
            source="test",
            external_id="rhr-1",
        ),
        Observation(
            metric="resting_heart_rate",
            value=58.0,
            unit="beats/min",
            start_at=datetime(2026, 4, 17, 20, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 4, 17, 20, 0, tzinfo=timezone.utc),
            source="test",
            external_id="rhr-2",
        ),
    ]

    features = materialize_daily_features(observations, timezone_name="UTC")

    assert len(features) == 1
    assert features[0].feature_date == date(2026, 4, 17)
    assert features[0].metrics["sleep_duration_hours"] == 7.5
    assert features[0].metrics["resting_heart_rate"] == 57.0


def test_compute_baseline_profiles_excludes_active_anomaly_dates() -> None:
    observations = []
    for day, sleep in enumerate([7.8, 7.7, 7.6, 7.5, 7.4, 7.3, 5.5], start=1):
        observations.append(
            Observation(
                metric="sleep_duration_hours",
                value=sleep,
                unit="hours",
                start_at=datetime(2026, 4, day, 7, 0, tzinfo=timezone.utc),
                end_at=datetime(2026, 4, day, 7, 0, tzinfo=timezone.utc),
                source="test",
                external_id=f"sleep-{day}",
            )
        )
    features = materialize_daily_features(observations, timezone_name="UTC")

    profiles = compute_baseline_profiles(
        features,
        target_date=date(2026, 4, 7),
        excluded_dates={date(2026, 4, 6)},
    )

    assert round(profiles["sleep_duration_hours"].long_median, 2) == 7.6
    assert profiles["sleep_duration_hours"].sample_count == 5
