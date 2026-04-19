---
name: health-question-routing
description: Use this when the user asks about their own health, current status, trends, alerts, readiness, recovery, or what VitalClaw sees in this project. First call the `answer_health_question` VitalClaw tool. Only add broader health interpretation afterward and label it as general context.
---

# VitalClaw Health Routing

For project health questions in this workspace:

1. Call `answer_health_question` first.
2. Use its output as the primary answer.
3. If the user wants broader interpretation, add clearly labeled `General context` after the VitalClaw answer.

Do not use this skill for normal coding tasks or for abstract medical questions that are not about this user's VitalClaw data.
