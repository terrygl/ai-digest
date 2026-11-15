# ai-digest

Turn any cheap Linux server into a self-running AI newsroom. Every morning it
collects from 20+ sources, filters to the ~40 items that matter, writes a
digest with an LLM (facts + popularity data + source links, with
anti-hallucination guards), and pushes it to Feishu / Telegram — or leaves it
on disk.

> Zero third-party Python dependencies · runs on a 1-core/512MB VPS ·
> ~1 LLM call per day · total cost ≈ $1/month.

## Sample output

```markdown
# AI 日报 2026-11-07

## 【今日速览】
今天AI界有点热闹：OpenAI 发布 GPT-6 Cyber，在 ExploitGym 上得分 48.5%，
比上一代高 16 个点；月之暗面开源了 Kimi K3.1 Pro 的权重；
Agility 的人形机器人公司以 25 亿美元估值登陆纳斯达克……

## 【模型与产品发布】
- OpenAI发布GPT-6 Cyber……（HN 821分·312评论）[原文](https://...)
- ……

## 【编辑观察】
（3句话总结今日主线）
```

## Sources (20+)

| Category | Sources |
|---|---|
| AI bloggers | Simon Willison, Nathan Lambert, Lilian Weng, Karpathy, Sebastian Raschka, Eugene Yan |
| Labs / official | OpenAI, DeepMind, Google AI, Hugging Face |
| Newsletter | The Rundown AI (350万读者) |
| Community | Hacker News (Algolia API), Lobsters, Reddit r/MachineLearning · r/LocalLLaMA · r/singularity · r/OpenAI |
| Code | GitHub trending (new repos, keyword-filtered, star-counted) |
| CN media | 量子位 |

Adding a source = one line in `collect.py`.

## Quick start

```bash
# on your laptop, inside this repo
SSH_TARGET=user@your-server bash scripts/deploy.sh

# on the server
cd ~/ai-digest
cp /path/to/config.env.example config.env && vi config.env   # LLM key + optional push
python3 collect.py && python3 summarize.py --no-push         # first test run
cat reports/$(date +%F).md

# schedule (07:30 Beijing time on a UTC server)
( crontab -l; echo "30 23 * * * /bin/bash ~/ai-digest/run.sh" ) | crontab -
```

Any OpenAI-compatible LLM endpoint works (OpenAI / Zhipu GLM / Moonshot Kimi /
DeepSeek — free tiers are enough).

## Optional: web site + email subscription

`web.py` (stdlib-only, ~25MB RAM) ships a dual-mode site:

- **Front end** (`MODE=front`, port 8800): public reading with a clean
  newsletter-style template, email registration/login, per-user daily email
  subscription (verify / pause / one-click unsubscribe).
- **Admin** (`MODE=admin`, port 8801): dedicated port + dedicated password,
  Markdown editor for every report. Registration is impossible there.
- `mailer.py` runs after each digest and emails the report (HTML) to verified
  subscribers — silently skipped until SMTP is filled into `web_config.json`.

```bash
# on the server
python3 web.py init <admin-password>          # one-time (creates web_config.json)
WEB_PORT=8800  MODE=front python3 web.py &    # or use the systemd units in references/deployment.md
WEB_PORT=8801  MODE=admin python3 web.py &
```

## How it stays honest (anti-hallucination)

Small cheap LLMs make two predictable mistakes when summarizing many items:
inventing popularity numbers, and attaching item A's link to item B. ai-digest
guards both in code, not just prompts:

1. Popularity data (HN points/comments, GitHub stars) is **pre-formatted into
   copy-ready strings** — the model copies, it never computes.
2. Zero values are stripped so "0 points" can never appear.
3. Entries are fed as numbered blocks and the prompt requires verbatim link
   copying; cross-item bleed became a non-issue after this change.

## Docs

- [SKILL.md](SKILL.md) — agent-facing usage (this repo doubles as a Codex/ZCode skill)
- [references/deployment.md](references/deployment.md) — full walkthrough, sources, push setup, security
- [references/troubleshooting.md](references/troubleshooting.md) — every production failure mode & fix

## License

MIT — see [LICENSE](LICENSE).
