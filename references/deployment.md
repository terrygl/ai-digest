# Deployment guide

## Requirements

- Any Linux box with Python 3.8+ (stdlib only — no pip installs)
- Outbound HTTPS access (RSS/API sources)
- ~50MB RAM headroom for the run itself

## Step-by-step

1. **Push the code**
   ```bash
   SSH_TARGET=user@your-server bash scripts/deploy.sh
   # deploys collect.py / summarize.py / run.sh to ~/ai-digest
   ```

2. **Create config on the server**
   ```bash
   ssh user@your-server
   mkdir -p ~/ai-digest && cd ~/ai-digest
   # paste assets/config.env.example content, save as config.env, fill values
   chmod 600 config.env
   ```

3. **First run**
   ```bash
   python3 collect.py      # prints per-source status, writes data/items-DATE.jsonl
   python3 summarize.py --no-push   # writes reports/DATE.md without pushing
   cat reports/$(date +%F).md
   ```
   Expect most sources `ok`; some Reddit subs may `ERR 429` (see
   troubleshooting). Re-running the same day is safe — collection merges by
   link and summarization is idempotent within a day.

4. **Verify push** — run `python3 summarize.py` (no flag) once a real push
   channel is configured; check the chat for the digest.

5. **Schedule** — pick a delivery time and convert it to the server timezone:
   ```bash
   timedatectl | grep "Time zone"     # servers are often UTC
   crontab -e                          # e.g. 07:30 Beijing == 23:30 UTC:
   30 23 * * * /bin/bash ~/ai-digest/run.sh
   ```

## Adding / removing sources

Edit `RSS_SOURCES` at the top of `collect.py`:
`(category, name, feed_url, weight)` — categories: `blog` / `hn` / `reddit`.
Blogs bypass the keyword gate (official announcements don't need keyword hits);
everything else must match the AI keyword list to qualify. Weights 1-3 scale
survival probability. Keep the 40-items-per-source cap in mind: full-history
feeds only contribute their newest 40 entries.

Conventions that keep quality high:
- Prefer **official lab RSS** and **primary blogs** over aggregators.
- HN goes through the Algolia API (`fetch_hn`) because hnrss.org is flaky.
- GitHub trending comes from the search API (`fetch_github`), keyword-filtered.

## Push channels

- **Feishu/Lark**: group → settings → bots → add **Custom Bot**. Safety
  setting: *custom keyword* matching a word that appears in every report
  (the default report title starts with "AI 日报" / "AI digest"). If the bot
  rejects with error `19024 Key Words Not Found`, the message text doesn't
  contain the configured keyword.
- **Telegram**: `@BotFather` → create bot → token; send any message to the
  bot, then read `getUpdates` for your `chat_id`. Fill both fields.

## X/Twitter coverage (optional pattern)

X has no usable free RSS/API, so the pipeline deliberately excludes it.
A practical complement: schedule an agent-side task (e.g. a ZCode / Claude /
ChatGPT scheduled prompt) that runs a web search for `site:x.com` plus key
people names every morning and appends an "X-side highlights" section. X
content escapes within hours via search engines and media reposts — enough
for a daily digest without scraping (and without ToS risk).

## Optional: web site + email subscription

Two systemd units run the dual-mode site (front 8800 / admin 8801):

```bash
python3 web.py init <admin-password>          # creates web_config.json (admin hash + secret)
# /etc/systemd/system/ai-digest-web.service   → Environment=MODE=front  WEB_PORT=8800
# /etc/systemd/system/ai-digest-admin.service → Environment=MODE=admin  WEB_PORT=8801
# both: ExecStart=/usr/bin/python3 /root/ai-digest/web.py, Restart=always
systemctl enable --now ai-digest-web ai-digest-admin
```

Email subscription: fill `smtp_host/port/user/pass/from` and `site_url` in
`web_config.json` (any provider with an app password — QQ mail, 163, Gmail).
Users register with an email, click a verification link, then `mailer.py`
(added to `run.sh`) sends each day's report as HTML with a per-user
unsubscribe link. Without SMTP the front end still works; mailing is skipped.

Notes: `web_config.json` and `users.db` are server-local runtime state — never
deploy or commit them. Change the admin password with
`python3 web.py set-password <new>`.

## Security notes

- `config.env` holds secrets: `chmod 600`, never commit, never re-deploy over it.
- Any internet-facing SSH server gets brute-forced: install fail2ban, prefer
  key auth, disable password login if you can.
- LLM keys: use a low-quota key; the pipeline needs ~1 call/day.
