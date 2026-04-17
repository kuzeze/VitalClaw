"""First-pass recovery suppression alert policy."""

from __future__ import annotations

from typing import Mapping

from vitalclaw.schema import AlertCandidate, BaselineProfile


def evaluate_recovery_suppression(
    *,
    features: Mapping[str, float],
    baselines: Mapping[str, BaselineProfile],
) -> AlertCandidate | None:
    """Emit an alert when multiple recovery signals drift together."""
    supporting_signals: list[str] = []

    sleep = features.get("sleep_duration_hours")
    sleep_baseline = baselines.get("sleep_duration_hours")
    if (
        sleep is not None
        and sleep_baseline
        and sleep <= sleep_baseline.long_median - max(sleep_baseline.long_mad * 2, 1.0)
    ):
        supporting_signals.append(
            f"sleep {sleep:.1f}h vs baseline {sleep_baseline.long_median:.1f}h"
        )

    resting_hr = features.get("resting_heart_rate")
    resting_hr_baseline = baselines.get("resting_heart_rate")
    if (
        resting_hr is not None
        and resting_hr_baseline
        and resting_hr >= resting_hr_baseline.long_median + max(resting_hr_baseline.long_mad * 2, 5.0)
    ):
        supporting_signals.append(
            f"resting HR {resting_hr:.1f} vs baseline {resting_hr_baseline.long_median:.1f}"
        )

    hrv = features.get("heart_rate_variability_sdnn")
    hrv_baseline = baselines.get("heart_rate_variability_sdnn")
    if (
        hrv is not None
        and hrv_baseline
        and hrv <= hrv_baseline.long_median - max(hrv_baseline.long_mad * 2, hrv_baseline.long_median * 0.2)
    ):
        supporting_signals.append(
            f"HRV {hrv:.1f} vs baseline {hrv_baseline.long_median:.1f}"
        )

    respiratory = features.get("respiratory_rate")
    respiratory_baseline = baselines.get("respiratory_rate")
    if (
        respiratory is not None
        and respiratory_baseline
        and respiratory >= respiratory_baseline.long_median + max(respiratory_baseline.long_mad * 2, 1.0)
    ):
        supporting_signals.append(
            f"respiratory rate {respiratory:.1f} vs baseline {respiratory_baseline.long_median:.1f}"
        )

    temperature = features.get("wrist_temperature_celsius")
    temperature_baseline = baselines.get("wrist_temperature_celsius")
    if (
        temperature is not None
        and temperature_baseline
        and temperature >= temperature_baseline.long_median + max(temperature_baseline.long_mad * 2, 0.3)
    ):
        supporting_signals.append(
            f"wrist temperature {temperature:.2f} vs baseline {temperature_baseline.long_median:.2f}"
        )

    if len(supporting_signals) < 2:
        return None

    return AlertCandidate(
        kind="recovery_suppression",
        title="Recovery looks suppressed",
        summary=(
            "Multiple recovery signals drifted away from baseline at the same time. "
            "Keep the alert open, ask one context question, and recheck on the next daily run."
        ),
        supporting_signals=supporting_signals,
        question=_pick_follow_up_question(supporting_signals),
    )


def _pick_follow_up_question(supporting_signals: list[str]) -> str:
    joined = " ".join(supporting_signals).lower()
    if "temperature" in joined or "respiratory" in joined:
        return "Any symptoms in the last 48 hours?"
    if "sleep" in joined:
        return "Any travel, alcohol, or unusual sleep disruption last night?"
    return "Has training load or medication changed this week?"
