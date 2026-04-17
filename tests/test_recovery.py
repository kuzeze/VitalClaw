from vitalclaw.monitor.recovery import evaluate_recovery_suppression
from vitalclaw.schema import BaselineProfile


def test_recovery_suppression_requires_two_signals() -> None:
    features = {
        "sleep_duration_hours": 6.0,
        "resting_heart_rate": 63.0,
        "heart_rate_variability_sdnn": 28.0,
    }
    baselines = {
        "sleep_duration_hours": BaselineProfile("sleep_duration_hours", 7.4, 0.4),
        "resting_heart_rate": BaselineProfile("resting_heart_rate", 56.0, 1.2),
        "heart_rate_variability_sdnn": BaselineProfile("heart_rate_variability_sdnn", 40.0, 3.0),
    }

    alert = evaluate_recovery_suppression(features=features, baselines=baselines)

    assert alert is not None
    assert alert.kind == "recovery_suppression"
    assert len(alert.supporting_signals) == 3


def test_recovery_suppression_returns_none_for_single_signal() -> None:
    features = {"sleep_duration_hours": 6.2}
    baselines = {"sleep_duration_hours": BaselineProfile("sleep_duration_hours", 7.0, 0.3)}

    alert = evaluate_recovery_suppression(features=features, baselines=baselines)

    assert alert is None
