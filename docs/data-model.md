# Data Model

VitalClaw should start with relational storage and materialized views. "Health graph" is a product concept, not a reason to adopt a graph database on day one.

## Core Entities

### Person

The subject being monitored.

### Source

Where data came from:

- Apple Watch
- iPhone
- third-party app
- manual input
- exporter

### Observation

One canonical measurement with:

- metric
- value
- unit
- start/end timestamps
- source
- external identifier if available

### WindowFeature

A derived daily or nightly feature set built from observations.

### BaselineProfile

The user's personal reference range for one metric under a given context.

### Deviation

A current feature compared with its baseline.

### ContextEvent

User-provided or imported context such as:

- symptoms
- travel
- medication changes
- alcohol
- unusual stress
- training block

### Alert

A stateful monitoring object with:

- kind
- title
- summary
- supporting signals
- status
- follow-up question

### Episode

A grouping of alerts and context around one health drift period.

### InterventionOutcome

What the user did and what happened after.

## Minimum Relationships

- `Observation -> Source`
- `WindowFeature -> Observation set`
- `BaselineProfile -> Person x Metric`
- `Deviation -> WindowFeature x BaselineProfile`
- `Alert -> Deviation set`
- `Episode -> Alert set`
- `ContextEvent -> Episode`
- `InterventionOutcome -> Episode`

## First Query Questions The Model Must Answer

- What changed?
- What likely drove the change?
- Has this pattern happened before?
- What happened after last time?
