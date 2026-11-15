#!/usr/bin/env bash
# etf-radar 每日晨报入口(北京 8:30 = UTC 0:30): 采集 -> 分析 -> 报告 -> 邮件+飞书。
# 分析对象=最新收盘日, 预测目标=今日; 周报由 weekly.sh 周六早单独跑。
# 任一核心阶段失败推飞书; 推送本身失败只记日志(不算管道失败)。
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p logs
PY="$(command -v python3 || echo /usr/bin/python3)"

{
  echo "===== $(date -Is) run start ====="
  stage=""
  "$PY" collect.py || stage="collect"
  if [ -z "$stage" ]; then "$PY" analyze.py || stage="analyze"; fi
  if [ -z "$stage" ]; then "$PY" report.py   || stage="report"; fi
  if [ -z "$stage" ]; then "$PY" mailer.py   || stage="mailer"; fi
  if [ -z "$stage" ]; then "$PY" push.py || true; fi
  if [ -n "$stage" ]; then
    echo "PIPELINE FAILED at: $stage"
    "$PY" common.py alert "【ETF雷达日报】⚠️ 晨报管道失败: ${stage}
时间(UTC): $(date -u '+%F %T')
排查: /opt/etf-radar/logs/run.log" || true
    exit 1
  fi
  echo "===== $(date -Is) run end ====="
} >> logs/run.log 2>&1

