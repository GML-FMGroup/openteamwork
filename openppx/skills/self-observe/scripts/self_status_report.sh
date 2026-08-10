#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="${HOME}/.openteamwork"
LOG_DIR="${DATA_DIR}/logs"
TOKEN_DB="${DATA_DIR}/database/token_usage.db"

echo "=== OpenTeamwork Self Status Report ==="
date '+generated_at=%Y-%m-%dT%H:%M:%S%z'

echo
echo "[1] Runtime status"
otw operations status --json 2>/dev/null || echo '{"node":"unavailable"}'
otw operations health --json 2>/dev/null || echo '{"health":"unavailable"}'
otw operations heartbeat status --json 2>/dev/null || echo '{"heartbeat":"unavailable"}'
otw operations cron list --json 2>/dev/null || echo '{"cron":"unavailable"}'

echo
echo "[2] Token usage"
otw operations usage --json 2>/dev/null || echo '{"usage":"unavailable"}'

echo
echo "[3] Recent errors"
if [[ -d "${LOG_DIR}" ]]; then
  for f in node.err.log node.out.log; do
    p="${LOG_DIR}/${f}"
    if [[ -f "${p}" ]]; then
      echo "--- ${p} (last 80 lines) ---"
      tail -n 80 "${p}" || true
    else
      echo "--- ${p} (not found) ---"
    fi
  done
  echo
  echo "[error patterns]"
  rg -n "ERROR|Error|Traceback|Exception|failed|timeout" "${LOG_DIR}"/*.log 2>/dev/null || echo "no matched error patterns"
else
  echo "log dir not found: ${LOG_DIR}"
fi

echo
echo "[4] Token DB quick query"
if [[ -f "${TOKEN_DB}" ]] && command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "${TOKEN_DB}" "SELECT provider, COUNT(*) AS requests, COALESCE(SUM(total_tokens),0) AS total_tokens FROM llm_token_usage_events GROUP BY provider ORDER BY total_tokens DESC;" || true
else
  echo "sqlite query skipped (missing db or sqlite3): ${TOKEN_DB}"
fi
