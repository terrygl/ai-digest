#!/usr/bin/env bash
# ai-digest 每日入口：采集 -> 摘要 -> 推送，日志落 logs/
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p logs
PY="$(command -v python3 || echo /usr/bin/python3)"
{
  echo "===== $(date -Is) run start ====="
  "$PY" collect.py && "$PY" summarize.py
  echo "===== $(date -Is) run end ====="
} >> logs/run.log 2>&1
