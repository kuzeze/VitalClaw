# VitalClaw

**VitalClaw keeps a grip on your baseline.**

VitalClaw is an open-source personal health observability engine. It watches your Apple Health trends over time, learns what is normal for *you*, and flags meaningful drift before your data turns into a pile of charts nobody looks at twice.

This is not a generic AI health chatbot.  
This is not a diagnosis product.  
This is a local-first monitoring system with Codex as the interface.

## What It Does

VitalClaw currently connects to `HealthExport Remote`, pulls your Apple Health data locally, builds daily features, computes personal baselines, and runs the first alert family:

- `sleep_duration_hours`
- `resting_heart_rate`
- `heart_rate_variability_sdnn`
- `respiratory_rate`
- `wrist_temperature_celsius`

Then it asks a harder question than most health apps:

> Is this just noise, or is something actually drifting?

## Why This Exists

Most health products do one of these two things:

- show dashboards
- let you ask questions about raw data

VitalClaw is aimed at a different layer:

- `longitudinal memory`
- `personal baseline`
- `low-noise alerting`
- `context-aware follow-up`

The goal is simple:

**Catch meaningful change early enough that it is worth your attention.**

## Current Shape

VitalClaw v1 is intentionally narrow:

- `Apple Health only`
- `HealthExport Remote + official he CLI`
- `local SQLite runtime`
- `one alert family: recovery_suppression`
- `Codex-native workflow`

It already supports:

- local runtime under `.vitalclaw/`
- raw snapshot caching
- normalized observations
- daily feature materialization
- baseline computation
- alert / episode / context persistence
- CLI entrypoints
- MCP server for Codex

## Quick Start

1. Clone the repo.
2. Add it as a Codex `project`.
3. Install the package locally:

```bash
python3 -m pip install -e .
```

4. Install `Health Export CSV` on iPhone and enable `Remote`.
5. Copy your account key from:

[`https://remote.healthexport.app/settings/sharing`](https://remote.healthexport.app/settings/sharing)

6. Run:

```bash
vitalclaw init --account-key "<your-account-key>"
```

That first run will:

- verify or install the official `he` CLI
- create `.vitalclaw/`
- save local config
- sync your health data
- build daily features
- run the first alert pass

## Main Commands

```bash
vitalclaw sync
vitalclaw materialize
vitalclaw alerts
vitalclaw explain --latest
vitalclaw context add --type symptoms --note "sore throat"
vitalclaw open-alerts
vitalclaw mcp
```

## How It Feels

There are really only two modes:

### Ask

Use Codex to ask:

- “What changed this week?”
- “Why did this alert fire?”
- “Do I look off compared to my normal?”
- “What context am I missing?”

### Watch

Let Codex automation run the daily loop:

1. `vitalclaw sync`
2. `vitalclaw materialize`
3. `vitalclaw alerts`
4. `vitalclaw explain --latest` when needed

## Repo Map

```text
docs/                  product and system docs
src/vitalclaw/cli.py   CLI entrypoint
src/vitalclaw/external/ official HealthExport integration
src/vitalclaw/ingest/  observation normalization
src/vitalclaw/features/ daily feature materialization
src/vitalclaw/monitor/ baseline + alert policies
src/vitalclaw/storage/ local SQLite persistence
src/vitalclaw/mcp_server.py  Codex-facing MCP server
```

## Local Data

VitalClaw stores data locally inside the repo runtime:

- `.vitalclaw/config.toml`
- `.vitalclaw/vitalclaw.sqlite3`
- `.vitalclaw/raw/`

Finder hides dot-folders by default on macOS.  
Use `Command + Shift + .` to show them.

## Safety Boundary

VitalClaw is currently a `wellness / monitoring` project.

It does **not**:

- diagnose disease
- replace a clinician
- claim medical-grade thresholds
- guarantee that every anomaly is meaningful

The product boundary is:

**detect meaningful changes from personal baseline for monitoring purposes**

## Status

What is working now:

- real `HealthExport Remote` integration
- local storage
- baseline + recovery suppression monitoring
- context event capture
- Codex automation compatibility

What is not here yet:

- labs
- genes
- medication intelligence
- multiple alert families
- web UI
- consumer-grade onboarding

## Roadmap

- stronger data quality gates
- more alert families
- episode similarity / recurrence tracking
- intervention outcome learning
- richer Codex MCP tools
- better visual timeline surface

## Contributing

This repo is still early, opinionated, and moving fast.

Good contributions are likely to be:

- better alert evaluation logic
- baseline robustness improvements
- safer wording and UX around alerts
- stronger local-first privacy and runtime ergonomics
- clearer integrations with Codex skills / MCP / automation

If you want to contribute, start by reading the docs in [docs/](/Users/renzeli/Desktop/VitalClaw/docs).

## License

MIT
