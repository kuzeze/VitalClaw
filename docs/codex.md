# Codex Interface

## Role

Codex is the interface layer for VitalClaw today. It should:

- initialize the repo against a user's `HealthExport Remote` account key
- inspect recent features and alerts
- explain why an alert opened
- compare a current episode to a prior one
- ask one follow-up question
- generate short summaries for review

Codex should not be the primary detector.

## Interface Contract To Grow Toward

The implemented engine already has matching CLI/MCP behaviors and should continue to expose tools like:

- `sync_remote_data`
- `build_latest_features`
- `list_open_alerts`
- `explain_latest_alert`
- `record_context_event`

## Automation Stance

Codex automation is useful for founder operations and testing. It should not be treated as the final end-user runtime.

The long-term scheduler should live inside VitalClaw's own system. Codex remains the operator-facing interface.
