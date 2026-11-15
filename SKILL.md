---
name: ai-digest
description: Deploy an automated daily AI-news digest pipeline on any Linux server — 20+ sources (AI blogs, lab RSS, Reddit, Hacker News, GitHub trending), dedupe + scoring, LLM summary with anti-hallucination guards, push to Feishu/Telegram or leave on disk. Use when the user wants a daily AI/tech briefing, newsletter raw-material pipeline, or automated news aggregation.
---

# ai-digest — daily AI news digest pipeline

Turns any cheap Linux server into a self-running AI newsroom: every morning it
collects from 20+ sources, filters to the ~40 items that matter, writes a
digest with an LLM (facts + popularity data + source links), and pushes it to
a chat channel.

## What you need before starting

1. A Linux server reachable over SSH (any 1-vCPU/512MB VPS is enough)
2. An API key for any OpenAI-compatible chat endpoint (OpenAI / Zhipu GLM /
   Moonshot Kimi / DeepSeek — set in `config.env`)
3. Optional: a push channel — Feishu custom-bot webhook, Telegram bot, or neither

## Deploy steps

```bash
# 1. Copy this folder's scripts/ and assets/ to the working machine
# 2. Push code to the server (config.env is deliberately NOT shipped):
SSH_TARGET=user@your-server bash scripts/deploy.sh
# 3. Configure on the server (never commit the real config):
ssh user@your-server
cp ~/ai-digest/assets/config.env.example ~/ai-digest/config.env  # if assets copied; otherwise create from the example
vi ~/ai-digest/config.env        # fill LLM_* and optionally push channels
# 4. Test one full run:
cd ~/ai-digest && python3 collect.py && python3 summarize.py --no-push
# 5. Read today's report:
cat reports/$(date +%F).md
```

No third-party Python packages are required — stdlib only.

## Schedule

Daily digest via cron (example: 07:30 Beijing time on a UTC server):

```
30 23 * * * /bin/bash ~/ai-digest/run.sh
```

## Tune

All knobs live in `config.env` (`MIN_SCORE`, `MAX_ITEMS`) and the source list
lives at the top of `scripts/collect.py` (add/remove RSS feeds freely; the
Reddit rate-limit caveat is documented in the references).

## References

- Full walkthrough, adding sources, push-channel setup, security notes:
  [references/deployment.md](references/deployment.md)
- Every failure mode seen in production and its fix:
  [references/troubleshooting.md](references/troubleshooting.md)

Read the deployment reference when pushing to a new server for the first time,
and the troubleshooting reference when a run fails or output quality degrades.
