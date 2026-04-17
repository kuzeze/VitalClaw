# MVP Scope

## Single Promise

When a person's recovery state meaningfully drifts away from their normal pattern, VitalClaw should notice it, explain it, and remember what happened next.

## In Scope

- Apple Health data via `HealthExport Remote`
- 60-90 day historical backfill
- daily monitoring loop
- one alert family: `recovery_suppression`
- manual context capture for:
  - symptoms
  - travel
  - alcohol
  - medication changes
  - unusual training load
- intervention / outcome memory

## Out Of Scope

- custom iPhone app or direct HealthKit bridge
- gene data
- multi-source device integrations
- real-time alerts
- diagnosis claims
- clinic workflows
- large dashboard surface

## Success Metrics

- the system can build a baseline from a real Apple Health export
- one daily run produces stable nightly features
- the first alert family has an understandable false-positive profile
- alerts can be resolved, suppressed, or followed up
- a user can look back at one prior episode and compare what happened

## First Alert Family

`recovery_suppression`

This alert opens when at least two corroborating signals suggest the person is not recovering the way they usually do. The first signals to support are:

- sleep duration down versus baseline
- resting heart rate up versus baseline
- HRV down versus baseline
- respiratory rate up versus baseline
- wrist temperature up versus baseline

## First Follow-Up Questions

- Any symptoms in the last 48 hours?
- Travel or unusual sleep disruption?
- Alcohol last night?
- Harder-than-usual training block?
- Medication change this week?
