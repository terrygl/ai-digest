#!/usr/bin/env bash
# etf-radar 周报入口(周六 北京 8:40 = UTC 0:40): 周报生成 -> 邮件 -> 飞书。
# 周六跑覆盖完整一周(周一~周五); 基于已入库K线与预测, 不做新分析。
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p logs
PY="$(command -v python3 || echo /usr/bin/python3)"

{
  echo "===== $(date -Is) weekly start ====="
  stage=""
  "$PY" report.py --weekly || stage="weekly-report"
  if [ -z "$stage" ]; then "$PY" mailer.py weekly || stage="weekly-mailer"; fi
  if [ -z "$stage" ]; then "$PY" push.py weekly || true; fi
  if [ -n "$stage" ]; then
    echo "WEEKLY FAILED at: $stage"
    "$PY" common.py alert "【ETF雷达日报】⚠️ 周报生成失败: ${stage}
时间(UTC): $(date -u '+%F %T')
排查: /opt/etf-radar/logs/weekly.log" || true
    exit 1
  fi
  echo "===== $(date -Is) weekly end ====="
} >> logs/weekly.log 2>&1
