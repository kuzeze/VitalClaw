from vitalclaw.external.healthexport import HealthTypeRef
from vitalclaw.ingest.health_export_remote import extract_observations, resolve_required_types


def test_resolve_required_types_matches_minimum_recovery_signals() -> None:
    available = [
        HealthTypeRef(24, "Time asleep", "time_asleep", "record", "Sleep"),
        HealthTypeRef(7, "Resting heart rate", "resting_heart_rate", "aggregated", "Heart"),
        HealthTypeRef(88, "Resting heart rate", "resting_heart_rate", "record", "Heart"),
        HealthTypeRef(89, "Heart rate variability (SDNN)", "heart_rate_variability_sdnn", "record", "Heart"),
        HealthTypeRef(90, "Respiratory rate", "respiratory_rate", "record", "Respiration"),
        HealthTypeRef(91, "Wrist temperature", "wrist_temperature", "record", "Temperature"),
    ]

    resolved = resolve_required_types(available)

    assert resolved["sleep_duration_hours"].id == 24
    assert resolved["resting_heart_rate"].id == 7
    assert resolved["wrist_temperature_celsius"].id == 91


def test_extract_observations_normalizes_sleep_units() -> None:
    packages = [
        {
            "type": 24,
            "type_name": "Time asleep",
            "data": [
                {
                    "units": "minutes",
                    "records": [{"time": "2026-04-17T07:00:00Z", "value": "420"}],
                }
            ],
        },
        {
            "type": 88,
            "type_name": "Resting heart rate",
            "data": [
                {
                    "units": "beats/min",
                    "records": [{"time": "2026-04-17T07:00:00Z", "value": "57"}],
                }
            ],
        },
    ]

    observations = extract_observations(
        packages,
        required_type_ids={
            "sleep_duration_hours": 24,
            "resting_heart_rate": 88,
        },
    )

    assert [item.metric for item in observations] == ["resting_heart_rate", "sleep_duration_hours"]
    assert observations[1].value == 7.0
    assert observations[1].unit == "hours"
