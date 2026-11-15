#!/usr/bin/env bash
# Push the pipeline to your server over plain SSH (uses your existing key auth).
# Usage: SSH_TARGET=user@host [SSH_PORT=22] bash deploy.sh [remote-dir]
# NOTE: config.env is intentionally NOT deployed — secrets only live on the server.
set -euo pipefail
TARGET="${SSH_TARGET:?Set SSH_TARGET=user@your-server}"
PORT="${SSH_PORT:-22}"
DIR="${1:-~/ai-digest}"

tar czf - collect.py summarize.py run.sh \
  | ssh -p "$PORT" "$TARGET" "mkdir -p $DIR && tar xzf - -C $DIR"
echo "Deployed to $TARGET:$DIR — now configure config.env ON THE SERVER (see assets/config.env.example)"
