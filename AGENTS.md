# VitalClaw Project Routing

- For user questions about their own health, current status, trends, alerts, readiness, recovery, or what VitalClaw sees in this workspace, call the VitalClaw tool `answer_health_question` first.
- If that tool is unavailable, use the project CLI fallback `vitalclaw answer --question "<user question>"`.
- Base the answer on VitalClaw data and profile state first. Only add broader health interpretation afterward, and label it clearly as `General context`.
- Do not answer those project health questions from generic reasoning alone when VitalClaw data is available.
- This routing rule is only for VitalClaw/user-health questions. Normal coding, debugging, and product work in this repo should follow the standard workflow.
