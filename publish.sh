#!/usr/bin/env bash
# ai-digest 一键发布到GitHub：密钥扫描 → commit → push
# 首次使用前: git remote add origin https://github.com/<你的用户名>/ai-digest.git
# 用法: bash publish.sh "本次改动说明"
set -euo pipefail
cd "$(dirname "$0")"

echo "=== ① 隐私/密钥扫描（发现即中止）"
PATTERNS='23\.94\.2\.181|0fb50942|c799c40099|e0b1c9066a|51d2b0e35f|MNH2IUPBK3JYXGQ|j8VnUf6imwz|terrygl|sohu\.com|ge85|racknerd|sk-[A-Za-z0-9]{20}|[a-f0-9]{32}\.[A-Za-z0-9]{16}'
if git ls-files -mo --exclude-standard | grep -E '\.(py|sh|md|env|json|yml|txt)$|^[^./]*$' | xargs -r grep -lE "$PATTERNS" 2>/dev/null; then
  echo "⚠️ 上述文件疑似包含密钥/隐私，发布中止！请清理后重试。"; exit 1
fi
echo "✓ 扫描通过"

echo "=== ② 提交"
git add -A
git commit -m "${1:-update ai-digest}" || echo "(无变更可提交)"

echo "=== ③ 推送"
git push origin HEAD
echo "✓ 发布完成"
