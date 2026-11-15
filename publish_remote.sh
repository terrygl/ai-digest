#!/usr/bin/env bash
# 一键发布：本地改动 → 传VPS → 由VPS代推GitHub（绕开本机代理/网络问题）
# 用法: bash publish_remote.sh "本次改动说明"
set -euo pipefail
cd "$(dirname "$0")"
NSSH="/c/Users/terry/.zcode/skills/nerd-vps/scripts/nssh.sh"
MSG="${1:-update ai-digest}"

tar czf - . --exclude=.git | bash "$NSSH" \
  "cd /root/pub/ai-digest-public && tar xzf - -C . && bash publish.sh '$MSG'"
echo "✓ 已由VPS代推GitHub"
