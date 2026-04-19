"""Core types for the VitalClaw monitoring engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

AlertState = Literal[
    "open",
    "monitoring",
    "waiting_for_user_input",
    "resolved",
    "suppressed",
    "escalated",
]

BriefingMode = Literal[
    "status_plus_key_metrics",
    "status_only",
    "full_snapshot",
]

DEFAULT_BRIEFING_MODE: BriefingMode = "status_plus_key_metrics"
BRIEFING_MODES: tuple[BriefingMode, ...] = (
    "status_plus_key_metrics",
    "status_only",
    "full_snapshot",
)
DEFAULT_PREFERRED_METRICS = [
    "sleep_duration_hours",
    "resting_heart_rate",
    "heart_rate_variability_sdnn",
    "respiratory_rate",
    "wrist_temperature_celsius",
]


@dataclass(slots=True)
class Observation:
    """One normalized health measurement."""

    metric: str
    value: float
    unit: str
    start_at: datetime
    end_at: datetime
    source: str
    external_id: str | None = None
    context: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DailyFeature:
    """Materialized per-day features used by alert policies."""

    feature_date: date
    metrics: dict[str, float]
    observation_count: int
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass(slots=True)
class BaselineProfile:
    """Robust baseline information for one metric."""

    metric: str
    long_median: float
    long_mad: float
    short_median: float | None = None
    sample_count: int = 0


@dataclass(slots=True)
class AlertCandidate:
    """A possible alert emitted by the monitor layer."""

    kind: str
    title: str
    summary: str
    supporting_signals: list[str]
    status: AlertState = "open"
    question: str | None = None


@dataclass(slots=True)
class StoredAlert:
    """Persisted alert record."""

    id: int
    episode_id: str
    kind: str
    title: str
    summary: str
    supporting_signals: list[str]
    status: AlertState
    question: str | None
    feature_date: date
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(slots=True)
class Episode:
    """Persisted alert episode."""

    id: str
    kind: str
    status: AlertState
    opened_at: datetime
    first_feature_date: date
    last_feature_date: date
    closed_at: datetime | None = None
    summary: str | None = None


@dataclass(slots=True)
class ContextEvent:
    """Context recorded against an episode."""

    id: int
    event_type: str
    note: str
    effective_date: date
    created_at: datetime
    episode_id: str | None = None


@dataclass(slots=True)
class InterventionOutcome:
    """A user action and the observed outcome afterward."""

    episode_id: str
    action: str
    outcome: str
    recorded_at: datetime


@dataclass(slots=True)
class UserProfile:
    """Durable per-project preferences for Codex bootstrap behavior."""

    auto_brief_enabled: bool
    always_sync_on_brief: bool
    default_briefing_mode: BriefingMode
    preferred_metrics: list[str] = field(default_factory=list)
    standing_instruction: str | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class BriefingMetric:
    """Compact metric card for the Codex bootstrap briefing."""

    metric: str
    label: str
    current_display: str
    baseline_display: str
    delta: str
    tone: str


@dataclass(slots=True)
class BriefingSyncStatus:
    """Sync metadata attached to a health briefing."""

    refreshed_now: bool
    status: str
    last_success_at: str | None = None
    last_sync_from: str | None = None
    last_sync_to: str | None = None


@dataclass(slots=True)
class Briefing:
    """Fresh-chat bootstrap payload for Codex."""

    profile: UserProfile
    sync: BriefingSyncStatus
    latest_feature_date: str | None
    status: dict[str, str]
    open_alert_count: int
    metrics: list[BriefingMetric] = field(default_factory=list)
    missing_data_notes: list[str] = field(default_factory=list)
    standing_instruction: str | None = None
    snapshot: dict[str, object] | None = None
