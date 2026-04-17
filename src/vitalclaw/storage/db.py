"""SQLite schema and repository helpers."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable
from uuid import uuid4

from vitalclaw.schema import (
    AlertCandidate,
    AlertState,
    BaselineProfile,
    ContextEvent,
    DailyFeature,
    Episode,
    InterventionOutcome,
    Observation,
    StoredAlert,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    raw_snapshot_path TEXT,
    observation_count INTEGER NOT NULL DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    context_json TEXT NOT NULL,
    sync_run_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_features (
    feature_date TEXT PRIMARY KEY,
    metrics_json TEXT NOT NULL,
    observation_count INTEGER NOT NULL,
    window_start TEXT,
    window_end TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS baseline_profiles (
    feature_date TEXT NOT NULL,
    metric TEXT NOT NULL,
    long_median REAL NOT NULL,
    long_mad REAL NOT NULL,
    short_median REAL,
    sample_count INTEGER NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (feature_date, metric)
);

CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    first_feature_date TEXT NOT NULL,
    last_feature_date TEXT NOT NULL,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    question TEXT,
    supporting_signals_json TEXT NOT NULL,
    feature_date TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT,
    event_type TEXT NOT NULL,
    note TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intervention_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
"""

ACTIVE_ALERT_STATES = {"open", "monitoring", "waiting_for_user_input"}


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the project-local SQLite database."""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    """Create the SQLite schema if it does not exist."""
    connection.executescript(SCHEMA)
    connection.commit()


class Repository:
    """SQLite persistence wrapper."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def set_metadata(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.connection.commit()

    def get_metadata(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def create_sync_run(self, *, started_at: datetime, from_date: str, to_date: str) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO sync_runs(started_at, status, from_date, to_date)
            VALUES (?, 'running', ?, ?)
            """,
            (started_at.isoformat(), from_date, to_date),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        *,
        sync_run_id: int,
        finished_at: datetime,
        status: str,
        raw_snapshot_path: str | None,
        observation_count: int,
        message: str | None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE sync_runs
            SET finished_at = ?, status = ?, raw_snapshot_path = ?, observation_count = ?, message = ?
            WHERE id = ?
            """,
            (
                finished_at.isoformat(),
                status,
                raw_snapshot_path,
                observation_count,
                message,
                sync_run_id,
            ),
        )
        self.connection.commit()

    def upsert_observations(self, observations: Iterable[Observation], *, sync_run_id: int) -> int:
        count = 0
        now = datetime.now().astimezone().isoformat()
        for observation in observations:
            self.connection.execute(
                """
                INSERT INTO observations(
                    metric, value, unit, start_at, end_at, source, external_id, context_json, sync_run_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET
                    value = excluded.value,
                    unit = excluded.unit,
                    start_at = excluded.start_at,
                    end_at = excluded.end_at,
                    source = excluded.source,
                    context_json = excluded.context_json,
                    sync_run_id = excluded.sync_run_id
                """,
                (
                    observation.metric,
                    observation.value,
                    observation.unit,
                    observation.start_at.isoformat(),
                    observation.end_at.isoformat(),
                    observation.source,
                    observation.external_id,
                    json.dumps(observation.context, sort_keys=True),
                    sync_run_id,
                    now,
                ),
            )
            count += 1
        self.connection.commit()
        return count

    def observation_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM observations").fetchone()
        return int(row["count"])

    def list_observations(self, *, metrics: list[str] | None = None) -> list[Observation]:
        query = """
            SELECT metric, value, unit, start_at, end_at, source, external_id, context_json
            FROM observations
        """
        params: list[object] = []
        if metrics:
            placeholders = ", ".join("?" for _ in metrics)
            query += f" WHERE metric IN ({placeholders})"
            params.extend(metrics)
        query += " ORDER BY start_at ASC"
        rows = self.connection.execute(query, params).fetchall()
        observations: list[Observation] = []
        for row in rows:
            observations.append(
                Observation(
                    metric=str(row["metric"]),
                    value=float(row["value"]),
                    unit=str(row["unit"]),
                    start_at=datetime.fromisoformat(str(row["start_at"])),
                    end_at=datetime.fromisoformat(str(row["end_at"])),
                    source=str(row["source"]),
                    external_id=str(row["external_id"]),
                    context=json.loads(str(row["context_json"])),
                )
            )
        return observations

    def upsert_daily_features(self, features: Iterable[DailyFeature]) -> int:
        count = 0
        now = datetime.now().astimezone().isoformat()
        for feature in features:
            self.connection.execute(
                """
                INSERT INTO daily_features(
                    feature_date, metrics_json, observation_count, window_start, window_end, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feature_date) DO UPDATE SET
                    metrics_json = excluded.metrics_json,
                    observation_count = excluded.observation_count,
                    window_start = excluded.window_start,
                    window_end = excluded.window_end,
                    updated_at = excluded.updated_at
                """,
                (
                    feature.feature_date.isoformat(),
                    json.dumps(feature.metrics, sort_keys=True),
                    feature.observation_count,
                    feature.window_start.isoformat() if feature.window_start else None,
                    feature.window_end.isoformat() if feature.window_end else None,
                    now,
                    now,
                ),
            )
            count += 1
        self.connection.commit()
        return count

    def list_daily_features(self) -> list[DailyFeature]:
        rows = self.connection.execute(
            """
            SELECT feature_date, metrics_json, observation_count, window_start, window_end
            FROM daily_features
            ORDER BY feature_date ASC
            """
        ).fetchall()
        features: list[DailyFeature] = []
        for row in rows:
            features.append(
                DailyFeature(
                    feature_date=date.fromisoformat(str(row["feature_date"])),
                    metrics={str(key): float(value) for key, value in json.loads(str(row["metrics_json"])).items()},
                    observation_count=int(row["observation_count"]),
                    window_start=datetime.fromisoformat(str(row["window_start"])) if row["window_start"] else None,
                    window_end=datetime.fromisoformat(str(row["window_end"])) if row["window_end"] else None,
                )
            )
        return features

    def latest_feature(self) -> DailyFeature | None:
        rows = self.list_daily_features()
        return rows[-1] if rows else None

    def replace_baseline_profiles(self, *, feature_date: date, profiles: dict[str, BaselineProfile]) -> None:
        self.connection.execute("DELETE FROM baseline_profiles WHERE feature_date = ?", (feature_date.isoformat(),))
        computed_at = datetime.now().astimezone().isoformat()
        for metric, profile in profiles.items():
            self.connection.execute(
                """
                INSERT INTO baseline_profiles(
                    feature_date, metric, long_median, long_mad, short_median, sample_count, computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feature_date.isoformat(),
                    metric,
                    profile.long_median,
                    profile.long_mad,
                    profile.short_median,
                    profile.sample_count,
                    computed_at,
                ),
            )
        self.connection.commit()

    def get_baseline_profiles(self, feature_date: date) -> dict[str, BaselineProfile]:
        rows = self.connection.execute(
            """
            SELECT metric, long_median, long_mad, short_median, sample_count
            FROM baseline_profiles
            WHERE feature_date = ?
            """,
            (feature_date.isoformat(),),
        ).fetchall()
        return {
            str(row["metric"]): BaselineProfile(
                metric=str(row["metric"]),
                long_median=float(row["long_median"]),
                long_mad=float(row["long_mad"]),
                short_median=float(row["short_median"]) if row["short_median"] is not None else None,
                sample_count=int(row["sample_count"]),
            )
            for row in rows
        }

    def get_active_alert(self, kind: str = "recovery_suppression") -> StoredAlert | None:
        placeholders = ", ".join("?" for _ in ACTIVE_ALERT_STATES)
        row = self.connection.execute(
            f"""
            SELECT *
            FROM alerts
            WHERE kind = ? AND status IN ({placeholders})
            ORDER BY last_seen_at DESC
            LIMIT 1
            """,
            (kind, *ACTIVE_ALERT_STATES),
        ).fetchone()
        return _row_to_alert(row) if row else None

    def list_open_alerts(self) -> list[StoredAlert]:
        placeholders = ", ".join("?" for _ in ACTIVE_ALERT_STATES)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM alerts
            WHERE status IN ({placeholders})
            ORDER BY last_seen_at DESC
            """,
            tuple(ACTIVE_ALERT_STATES),
        ).fetchall()
        return [_row_to_alert(row) for row in rows]

    def get_latest_alert(self) -> StoredAlert | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM alerts
            ORDER BY last_seen_at DESC
            LIMIT 1
            """
        ).fetchone()
        return _row_to_alert(row) if row else None

    def upsert_alert(
        self,
        *,
        candidate: AlertCandidate,
        feature_date: date,
        status: AlertState,
    ) -> StoredAlert:
        existing = self.get_active_alert(candidate.kind)
        now = datetime.now().astimezone().isoformat()
        if existing:
            self.connection.execute(
                """
                UPDATE alerts
                SET title = ?, summary = ?, status = ?, question = ?, supporting_signals_json = ?, feature_date = ?, last_seen_at = ?
                WHERE id = ?
                """,
                (
                    candidate.title,
                    candidate.summary,
                    status,
                    candidate.question,
                    json.dumps(candidate.supporting_signals),
                    feature_date.isoformat(),
                    now,
                    existing.id,
                ),
            )
            self.connection.execute(
                """
                UPDATE episodes
                SET status = ?, last_feature_date = ?, summary = ?
                WHERE id = ?
                """,
                (status, feature_date.isoformat(), candidate.summary, existing.episode_id),
            )
            self.connection.commit()
            return self.get_active_alert(candidate.kind)  # type: ignore[return-value]

        episode_id = str(uuid4())
        self.connection.execute(
            """
            INSERT INTO episodes(id, kind, status, opened_at, first_feature_date, last_feature_date, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                candidate.kind,
                status,
                now,
                feature_date.isoformat(),
                feature_date.isoformat(),
                candidate.summary,
            ),
        )
        cursor = self.connection.execute(
            """
            INSERT INTO alerts(
                episode_id, kind, title, summary, status, question, supporting_signals_json, feature_date, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                candidate.kind,
                candidate.title,
                candidate.summary,
                status,
                candidate.question,
                json.dumps(candidate.supporting_signals),
                feature_date.isoformat(),
                now,
                now,
            ),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM alerts WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _row_to_alert(row)

    def resolve_alert(self, *, kind: str, summary: str | None = None) -> StoredAlert | None:
        active = self.get_active_alert(kind)
        if not active:
            return None
        now = datetime.now().astimezone().isoformat()
        self.connection.execute(
            """
            UPDATE alerts
            SET status = 'resolved', summary = ?, last_seen_at = ?
            WHERE id = ?
            """,
            (summary or active.summary, now, active.id),
        )
        self.connection.execute(
            """
            UPDATE episodes
            SET status = 'resolved', closed_at = ?, summary = ?
            WHERE id = ?
            """,
            (now, summary or active.summary, active.episode_id),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM alerts WHERE id = ?", (active.id,)).fetchone()
        return _row_to_alert(row)

    def count_alerts(self, *, status: str | None = None) -> int:
        if status is None:
            row = self.connection.execute("SELECT COUNT(*) AS count FROM alerts").fetchone()
        else:
            row = self.connection.execute("SELECT COUNT(*) AS count FROM alerts WHERE status = ?", (status,)).fetchone()
        return int(row["count"])

    def get_episode(self, episode_id: str) -> Episode | None:
        row = self.connection.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        return _row_to_episode(row) if row else None

    def latest_resolved_episode(self, kind: str, *, exclude_episode_id: str | None = None) -> Episode | None:
        query = """
            SELECT *
            FROM episodes
            WHERE kind = ? AND status = 'resolved'
        """
        params: list[object] = [kind]
        if exclude_episode_id:
            query += " AND id != ?"
            params.append(exclude_episode_id)
        query += " ORDER BY closed_at DESC LIMIT 1"
        row = self.connection.execute(query, params).fetchone()
        return _row_to_episode(row) if row else None

    def add_context_event(
        self,
        *,
        event_type: str,
        note: str,
        effective_date: date,
        episode_id: str | None,
    ) -> ContextEvent:
        now = datetime.now().astimezone().isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO context_events(episode_id, event_type, note, effective_date, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (episode_id, event_type, note, effective_date.isoformat(), now),
        )
        if episode_id:
            self.connection.execute(
                """
                UPDATE alerts
                SET status = CASE WHEN status = 'waiting_for_user_input' THEN 'monitoring' ELSE status END
                WHERE episode_id = ?
                """,
                (episode_id,),
            )
            self.connection.execute(
                """
                UPDATE episodes
                SET status = CASE WHEN status = 'waiting_for_user_input' THEN 'monitoring' ELSE status END
                WHERE id = ?
                """,
                (episode_id,),
            )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM context_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _row_to_context_event(row)

    def list_context_events(self, episode_id: str | None) -> list[ContextEvent]:
        if episode_id is None:
            return []
        rows = self.connection.execute(
            """
            SELECT * FROM context_events
            WHERE episode_id = ?
            ORDER BY created_at ASC
            """,
            (episode_id,),
        ).fetchall()
        return [_row_to_context_event(row) for row in rows]

    def add_intervention_outcome(self, *, episode_id: str, action: str, outcome: str, recorded_at: datetime) -> InterventionOutcome:
        self.connection.execute(
            """
            INSERT INTO intervention_outcomes(episode_id, action, outcome, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            (episode_id, action, outcome, recorded_at.isoformat()),
        )
        self.connection.commit()
        return InterventionOutcome(episode_id=episode_id, action=action, outcome=outcome, recorded_at=recorded_at)

    def list_intervention_outcomes(self, episode_id: str | None) -> list[InterventionOutcome]:
        if episode_id is None:
            return []
        rows = self.connection.execute(
            """
            SELECT * FROM intervention_outcomes
            WHERE episode_id = ?
            ORDER BY recorded_at ASC
            """,
            (episode_id,),
        ).fetchall()
        return [
            InterventionOutcome(
                episode_id=str(row["episode_id"]),
                action=str(row["action"]),
                outcome=str(row["outcome"]),
                recorded_at=datetime.fromisoformat(str(row["recorded_at"])),
            )
            for row in rows
        ]


def _row_to_alert(row: sqlite3.Row) -> StoredAlert:
    return StoredAlert(
        id=int(row["id"]),
        episode_id=str(row["episode_id"]),
        kind=str(row["kind"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        supporting_signals=list(json.loads(str(row["supporting_signals_json"]))),
        status=str(row["status"]),
        question=str(row["question"]) if row["question"] is not None else None,
        feature_date=date.fromisoformat(str(row["feature_date"])),
        first_seen_at=datetime.fromisoformat(str(row["first_seen_at"])),
        last_seen_at=datetime.fromisoformat(str(row["last_seen_at"])),
    )


def _row_to_episode(row: sqlite3.Row) -> Episode:
    return Episode(
        id=str(row["id"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        opened_at=datetime.fromisoformat(str(row["opened_at"])),
        first_feature_date=date.fromisoformat(str(row["first_feature_date"])),
        last_feature_date=date.fromisoformat(str(row["last_feature_date"])),
        closed_at=datetime.fromisoformat(str(row["closed_at"])) if row["closed_at"] else None,
        summary=str(row["summary"]) if row["summary"] is not None else None,
    )


def _row_to_context_event(row: sqlite3.Row) -> ContextEvent:
    return ContextEvent(
        id=int(row["id"]),
        event_type=str(row["event_type"]),
        note=str(row["note"]),
        effective_date=date.fromisoformat(str(row["effective_date"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        episode_id=str(row["episode_id"]) if row["episode_id"] is not None else None,
    )
