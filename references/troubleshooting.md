# Troubleshooting — every failure mode seen in production

| Symptom | Root cause | Fix |
|---|---|---|
| `hn-frontpage` 502 / timeout | hnrss.org itself is unstable | already replaced by the HN Algolia API in `fetch_hn`; if you add hnrss back, expect flakiness |
| Reddit sources `HTTP 429` | Reddit rate-limits datacenter IPs aggressively | by design: sleeps 6s between subs + one retry; 2/4 subs succeeding is normal coverage (subs overlap heavily) |
| A feed fails XML parse (`mismatched tag`) | some sites emit malformed XML | drop the source (no HTML-repair dependency wanted) |
| Feed returns 404 | site has no RSS (e.g. Anthropic) | remove; its news arrives via HN/media sources anyway |
| One source ingests hundreds of old posts | full-history feeds | per-source cap of 40 newest entries; stale entries also lose 6 score points after 7 days |
| Feishu push returns `19024 Key Words Not Found` | bot's custom keyword not present in message | set keyword to a word guaranteed in every report (e.g. the title keyword) |
| LLM call fails, report falls back to list mode | auth/quota/network | test the key directly with curl; check the exact error body — plan-expired keys on some providers return HTTP 429 with a subscription error code |
| Digest shows `HN 0分·0评论`-style labels | zero-valued popularity passed to the LLM | already fixed: zero values are filtered from the payload; keep it that way |
| Digest items get **each other's** links or stats | small models misalign parallel JSON arrays | already fixed: entries are pre-formatted as numbered blocks (`标题/链接/数据/摘要`) and the prompt demands verbatim copy; if you switch models and see misalignment again, strengthen the numbered-block format |
| After redeploying, key/webhook disappear | deploy overwrote server-side `config.env` | deploy.sh intentionally never ships config.env — keep runtime config server-only |
| Report includes off-topic viral posts (non-AI) | popularity alone outranked relevance | the guard: non-blog items with zero keyword hits score 0; tune `MIN_SCORE` up for stricter |
| Nothing collected (`TOTAL=0`) | no outbound HTTPS / DNS broken on server | `curl -sI https://hnrss.org` from the server to verify |

## Tuning quality

- More/less items → `MAX_ITEMS`
- Stricter selection → raise `MIN_SCORE` (8 loose … 15 strict)
- Noisy GitHub projects → raise the `stars:>` threshold in `fetch_github`
- Different language output → edit the prompt in `summarize.py` (rules 0-5);
  the structure (TLDR + sections + per-item data/links) is what keeps it readable
