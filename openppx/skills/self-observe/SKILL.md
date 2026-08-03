---
name: self-observe
description: Observe OpenPPX Node health with usage, automation status, audit facts, logs, and quick diagnostics.
---

# Self Observe Skill

Use this skill when the user asks for agent self-inspection, runtime health checks, or diagnostics such as token cost, error logs, and service status.

## What To Check

1. Token usage:
```bash
ppx operations usage --json
ppx operations usage --provider google --limit 50 --json
ppx operations usage --provider openai --limit 50 --json
```

2. Runtime status:
```bash
ppx operations status --json
ppx operations health --json
ppx operations heartbeat status --json
ppx operations cron list --json
```

3. Error logs (read-only):
```bash
tail -n 200 ~/.openppx/logs/node.err.log
rg -n "ERROR|Error|Traceback|Exception|failed|timeout" ~/.openppx/logs/node.err.log ~/.openppx/logs/node.out.log
```

4. SQLite quick verification (if `sqlite3` exists):
```bash
sqlite3 ~/.openppx/database/token_usage.db "SELECT provider, COUNT(*) AS requests, SUM(total_tokens) AS total_tokens FROM llm_token_usage_events GROUP BY provider ORDER BY total_tokens DESC;"
sqlite3 ~/.openppx/database/token_usage.db "SELECT response_at, provider, model, request_tokens, response_tokens, total_tokens FROM llm_token_usage_events ORDER BY response_at_ms DESC LIMIT 20;"
```

## Fast Path

Generate one consolidated report:

```bash
bash openppx/skills/self-observe/scripts/self_status_report.sh
```

## Output Format

When reporting to user, include:

1. Runtime Summary: Node/heartbeat/cron health highlights.
2. Token Summary: total requests/tokens and provider split.
3. Recent Errors: latest error signatures with file and timestamp.
4. Risks: what might break soon (missing usage data, repeated failures, disconnected provider).
5. Next Actions: concrete commands to validate/fix.

## Guardrails

- Keep checks read-only by default.
- Do not delete or truncate logs unless the user explicitly asks.
- If status/log files are missing, report "not found" explicitly instead of guessing.
- Prefer structured output (`--json`) first, then summarize in natural language.
