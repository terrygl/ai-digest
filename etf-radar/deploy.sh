#!/usr/bin/env bash
# Push etf-radar to your server over plain SSH (uses your existing key auth).
# Usage: SSH_TARGET=user@host [SSH_PORT=22] bash deploy.sh [remote-dir]
# NOTE: config.env / web_config.json are intentionally NOT deployed — secrets only live on the server.
set -euo pipefail
TARGET="${SSH_TARGET:?Set SSH_TARGET=user@your-server}"
PORT="${SSH_PORT:-22}"
DIR="${1:-/opt/etf-radar}"
cd "$(dirname "$0")"
PY="$(command -v python3 || command -v python)"
"$PY" -m py_compile common.py collect.py analyze.py report.py mailer.py push.py intraday.py web.py
tar czf - common.py collect.py analyze.py report.py mailer.py push.py intraday.py web.py \
           run.sh weekly.sh remote-install.sh \
  | ssh -p "$PORT" "$TARGET" "mkdir -p $DIR && tar xzf - -C $DIR && chmod +x $DIR/*.sh"
echo "Deployed to $TARGET:$DIR"
echo "Next ON THE SERVER: cp config.env.example config.env (fill LLM/SMTP/webhook),"
echo "python3 web.py init <访问口令>, then bash remote-install.sh"
