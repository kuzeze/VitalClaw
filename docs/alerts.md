# Alert Policy

## Daily Loop

1. Ingest new observations.
2. Materialize the latest nightly and daily features.
3. Compare features against context-aware baselines.
4. Open or update alerts only when the signal is actionable.
5. Ask one follow-up question if the alert is ambiguous.
6. Review unresolved alerts in the next run.

## Alert States

- `open`
- `monitoring`
- `waiting_for_user_input`
- `resolved`
- `suppressed`
- `escalated`

## First Alert: Recovery Suppression

Open this alert only when at least two signals agree that recovery is drifting:

- sleep duration materially below baseline
- resting heart rate materially above baseline
- HRV materially below baseline
- respiratory rate materially above baseline
- wrist temperature materially above baseline

## Suppression Rules

- do not alert on a single weak signal
- do not re-alert immediately after a recent suppression
- prefer `monitoring` over `open` when the drift is new and mild

## Follow-Up Strategy

Ask exactly one question that most reduces uncertainty. Example order:

1. symptoms
2. travel or sleep disruption
3. alcohol
4. training load
5. medication change

## Weekly Rollup

Summarize only:

- recurring alert patterns
- common explanatory contexts
- interventions that seemed helpful
