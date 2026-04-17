<p align="center">
  <img src="./docs/assets/vitalclaw-logo-wordmark.png" alt="VitalClaw" width="720" />
</p>

<p align="center">
  Keeps a grip on your baseline.
</p>

<p align="center">
  <a href="https://github.com/kuzeze/VitalClaw/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/kuzeze/VitalClaw"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.12+-3776AB">
  <img alt="Status" src="https://img.shields.io/badge/status-early_beta-C75C34">
  <img alt="Local First" src="https://img.shields.io/badge/local--first-yes-216343">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-ready-6C4CF1">
</p>

VitalClaw is a local-first personal health observability engine for Apple Health, built to work naturally with Codex.

It pulls your Apple Health data locally, learns your baseline over time, and flags meaningful drift before your signals turn into charts you never look at again.

This is not a generic AI health chatbot.  
This is not a diagnosis product.  
This is a monitoring engine with Codex as the interface.

## Overview

VitalClaw is aimed at one layer:

- `longitudinal memory`
- `personal baseline`
- `low-noise alerting`
- `context-aware follow-up`

The core question is:

> Is this just noise, or is something actually drifting?

Current v1 scope:

- `Apple Health only`, via `HealthExport Remote`
- `local-first`, with project-local SQLite under `.vitalclaw/`
- `Codex-native`, with CLI + MCP surfaces
- `baseline-aware`, not population-threshold-first
- `precision-first`, with one alert family in v1: `recovery_suppression`

## Visuals

<p align="center">
  <img src="./docs/assets/vitalclaw-logo-icon.png" alt="VitalClaw icon" width="220" />
</p>

### Anatomy of an Alert

<p align="center">
  <img src="./docs/assets/alert-anatomy.svg" alt="Anatomy of a VitalClaw alert" width="100%" />
</p>

The point is not “AI looked at my chart.”

The point is:

- raw signals become daily features
- daily features are compared to a personal baseline
- multiple corroborating deviations are required before opening an alert
- the model is used for explanation and follow-up, not threshold invention

## Demo

```text
$ vitalclaw sync
✓ Pulled fresh data from HealthExport Remote
✓ Normalized local observations into SQLite

$ vitalclaw alerts
⚠ recovery_suppression
  Multiple recovery markers drifted together
  Follow-up: Any symptoms in the last 48 hours?

$ vitalclaw explain --latest
Changed: HRV down, resting HR up, sleep below baseline
Missing context: Any symptoms in the last 48 hours?
History: No prior similar episode has been recorded.
```

## Workflow

```mermaid
flowchart LR
    A["Apple Health"] --> B["HealthExport Remote"]
    B --> C["Official he CLI"]
    C --> D["Ingest / Normalize"]
    D --> E["Local SQLite"]
    E --> F["Daily Features"]
    F --> G["Baseline Engine"]
    G --> H["Alert Policies"]
    H --> I["MCP Server"]
    I --> J["Codex"]
```

VitalClaw currently monitors:

- `sleep_duration_hours`
- `resting_heart_rate`
- `heart_rate_variability_sdnn`
- `respiratory_rate`
- `wrist_temperature_celsius`

## Quick Start

### Easiest Path

1. Clone the repo.
2. Add it as a Codex `project`.
3. Tell Codex to set up VitalClaw.
4. Install `Health Export CSV` on iPhone and enable `Remote`.
5. Copy your account key from:

[`https://remote.healthexport.app/settings/sharing`](https://remote.healthexport.app/settings/sharing)

6. Paste the key into the Codex chat.
7. Done.

Codex should handle the rest:

- install the local package
- verify or install the official `he` CLI
- create `.vitalclaw/`
- save local config
- sync your health data
- build daily features
- run the first alert pass

### Manual Fallback

```bash
python3 -m pip install -e .
vitalclaw init --account-key "<your-account-key>"
```

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

## Local Data

VitalClaw stores data locally inside the repo runtime:

- `.vitalclaw/config.toml`
- `.vitalclaw/vitalclaw.sqlite3`
- `.vitalclaw/raw/`

Finder hides dot-folders by default on macOS.  
Use `Command + Shift + .` to show them.

## Current Status

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

## Repo Map

```text
docs/                  product and system docs
docs/assets/           logos and README visuals
src/vitalclaw/cli.py   CLI entrypoint
src/vitalclaw/external/ official HealthExport integration
src/vitalclaw/ingest/  observation normalization
src/vitalclaw/features/ daily feature materialization
src/vitalclaw/monitor/ baseline + alert policies
src/vitalclaw/storage/ local SQLite persistence
src/vitalclaw/mcp_server.py  Codex-facing MCP server
```

## Safety Boundary

VitalClaw is currently a `wellness / monitoring` project.

It does **not**:

- diagnose disease
- replace a clinician
- claim medical-grade thresholds
- guarantee that every anomaly is meaningful

The product boundary is:

**detect meaningful changes from personal baseline for monitoring purposes**

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

If you want to contribute, start by reading the docs in [`docs/`](./docs).

## License

MIT
