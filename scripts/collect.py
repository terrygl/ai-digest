#!/usr/bin/env python3
"""ai-digest collector — fetch RSS/Reddit/HN/GitHub sources, merge into data/items-YYYYMMDD.jsonl

Stdlib only. Idempotent: re-running the same day merges by link.
Prints a per-source status summary as JSON to stdout.
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)
UA = {"User-Agent": "ai-digest-bot/0.1 (personal news pipeline; contact: root)"}

# (category, name, url, weight)
RSS_SOURCES = [
    # --- 大V个人博客 ---
    ("blog", "simonwillison", "https://simonwillison.net/atom/everything/", 3),
    ("blog", "interconnects-nathanlambert", "https://www.interconnects.ai/feed", 3),
    ("blog", "lilianweng", "https://lilianweng.github.io/index.xml", 3),
    ("blog", "karpathy", "https://karpathy.bearblog.dev/feed/", 3),
    ("blog", "sebastianraschka", "https://magazine.sebastianraschka.com/feed", 3),
    ("blog", "eugeneyan", "https://eugeneyan.com/rss/", 3),
    # --- 实验室/官方 ---
    ("blog", "openai-news", "https://openai.com/news/rss.xml", 3),
    ("blog", "deepmind-blog", "https://deepmind.google/blog/rss.xml", 3),
    ("blog", "google-ai-blog", "https://blog.google/technology/ai/rss/", 3),
    ("blog", "huggingface-blog", "https://huggingface.co/blog/feed.xml", 3),
    # --- 海外AI Newsletter ---
    ("blog", "therundown", "https://www.therundown.ai/feed/", 3),
    # --- Google News RSS（搜索引擎层的正规RSS出口，聚合主流媒体报道，限24h）---
    ("blog", "gnews-en-model", "https://news.google.com/rss/search?q=AI+model+release+when:1d&hl=en-US&gl=US&ceid=US:en", 2),
    ("blog", "gnews-en-llm", "https://news.google.com/rss/search?q=LLM+OR+%22large+language+model%22+when:1d&hl=en-US&gl=US&ceid=US:en", 2),
    ("blog", "gnews-cn", "https://news.google.com/rss/search?q=%E5%A4%A7%E6%A8%A1%E5%9E%8B+when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", 2),
    # --- 中文科技媒体 ---
    ("blog", "qbitai", "https://www.qbitai.com/feed", 2),
    # --- Reddit（RSS，无需API；数据中心IP易被限流，部分成功即可）---
    ("reddit", "r-MachineLearning", "https://www.reddit.com/r/MachineLearning/hot/.rss?limit=25", 2),
    ("reddit", "r-LocalLLaMA", "https://www.reddit.com/r/LocalLLaMA/hot/.rss?limit=25", 2),
    ("reddit", "r-singularity", "https://www.reddit.com/r/singularity/hot/.rss?limit=25", 2),
    ("reddit", "r-OpenAI", "https://www.reddit.com/r/OpenAI/hot/.rss?limit=25", 2),
    # --- 技术社区备胎 ---
    ("hn", "lobsters", "https://lobste.rs/rss", 1),
]

GITHUB_KEYWORDS = re.compile(
    r"\b(ai|llm|gpt|agent|rag|transformer|diffusion|whisper|tts|asr|ocr|vllm"
    r"|inference|fine.?tun|embedding|multimodal|deep.?learn|neural|chatbot"
    r"|copilot|llama|qwen|deepseek|kimi|glm|mistral|vision)\b"
)


def fetch(url, timeout=15, tries=2):
    """Fetch with one retry; longer backoff for rate-limit/gateway errors."""
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as ex:
            last = ex
            time.sleep(10 if re.search(r"\b(429|502|503)\b", str(ex)) else 2)
    raise last


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(body):
    """Parse RSS 2.0 / Atom into dicts. Tolerant of missing fields."""
    root = ET.fromstring(body)
    items = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        d = {}
        for c in el:
            cn = c.tag.rsplit("}", 1)[-1]
            if cn == "link":
                d.setdefault("link", (c.get("href") or c.text or "").strip())
            elif cn == "title":
                d["title"] = strip_html(c.text)
            elif cn in ("description", "summary", "content"):
                d.setdefault("desc", strip_html(c.text))
            elif cn in ("pubDate", "published", "updated"):
                d.setdefault("ts", (c.text or "").strip())
        if d.get("title") and d.get("link"):
            items.append(d)
    return items


def fetch_hn():
    """Hacker News via Algolia API (hnrss.org is flaky)."""
    out, seen = [], set()
    queries = [
        "https://hn.algolia.com/api/v1/search?tags=front_page",
        "https://hn.algolia.com/api/v1/search_by_date?query=LLM&tags=story&numericFilters=points%3E50",
        "https://hn.algolia.com/api/v1/search_by_date?query=%22AI%20model%22&tags=story&numericFilters=points%3E40",
    ]
    for u in queries:
        data = json.loads(fetch(u, timeout=20))
        for h in data.get("hits", [])[:20]:
            link = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            if link in seen:
                continue
            seen.add(link)
            out.append({"title": h.get("title", ""), "link": link,
                        "desc": strip_html(h.get("story_text") or "")[:400],
                        "points": h.get("points", 0),
                        "comments": h.get("num_comments", 0)})
        time.sleep(1)
    return out


def fetch_github(since_days=3):
    d = (date.today() - timedelta(days=since_days)).isoformat()
    q = urllib.parse.quote(f"created:>{d} stars:>40")
    url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=50"
    data = json.loads(fetch(url))
    out = []
    for r in data.get("items", []):
        text = " ".join([r["name"], r.get("description") or "",
                         " ".join(r.get("topics", []))]).lower()
        if not GITHUB_KEYWORDS.search(text):
            continue
        out.append({
            "title": r["full_name"],
            "link": r["html_url"],
            "desc": (r.get("description") or "")[:400],
            "stars": r["stargazers_count"],
            "lang": r.get("language") or "",
        })
    return out[:15]


def main():
    today = (datetime.utcnow() + timedelta(hours=8)).date().isoformat()  # 北京时间命名
    out_path = os.path.join(DATA, f"items-{today}.jsonl")
    seen_links = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    seen_links.add(json.loads(line)["link"])
                except Exception:
                    pass

    new_items, status = [], {}
    for cat, name, url, weight in RSS_SOURCES:
        ok = err = n = 0
        try:
            entries = parse_feed(fetch(url))[:40]  # cap per source (full-history feeds)
            for e in entries:
                link = e["link"]
                if link in seen_links:
                    continue
                title = re.sub(r"^r/\w+ - ", "", e["title"])  # clean reddit prefix
                meta = {}
                m = re.search(r"Points:\s*(\d+)", e.get("desc", ""))
                if m:
                    meta["points"] = int(m.group(1))
                new_items.append({"src": name, "cat": cat, "w": weight,
                                  "title": title[:200], "link": link,
                                  "desc": e.get("desc", "")[:400],
                                  "ts": e.get("ts", ""), "meta": meta})
                seen_links.add(link)
                n += 1
            ok = 1
        except Exception as ex:
            err = str(ex)[:120]
        status[name] = {"ok": ok, "new": n, "err": err}
        time.sleep(6 if cat == "reddit" else 1.5)  # reddit rate-limits hard from DC IPs

    try:
        gh = fetch_github()
        for r in gh:
            if r["link"] in seen_links:
                continue
            new_items.append({"src": "github-trending", "cat": "github", "w": 2,
                              "title": r["title"], "link": r["link"],
                              "desc": r["desc"], "ts": "",
                              "meta": {"stars": r["stars"], "lang": r["lang"]}})
            seen_links.add(r["link"])
        status["github-trending"] = {"ok": 1, "new": len(gh), "err": ""}
    except Exception as ex:
        status["github-trending"] = {"ok": 0, "new": 0, "err": str(ex)[:120]}

    try:
        hn = fetch_hn()
        n = 0
        for h in hn:
            if h["link"] in seen_links:
                continue
            new_items.append({"src": "hn-algolia", "cat": "hn", "w": 2,
                              "title": h["title"], "link": h["link"],
                              "desc": h["desc"], "ts": "",
                              "meta": {"points": h["points"], "comments": h["comments"]}})
            seen_links.add(h["link"])
            n += 1
        status["hn-algolia"] = {"ok": 1, "new": n, "err": ""}
    except Exception as ex:
        status["hn-algolia"] = {"ok": 0, "new": 0, "err": str(ex)[:120]}

    if os.path.exists(out_path):
        with open(out_path, "a", encoding="utf-8") as f:
            for it in new_items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            for it in new_items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")

    total = sum(1 for _ in open(out_path, encoding="utf-8"))
    for name, st in status.items():
        print(f"{name}: {'ok new=' + str(st['new']) if st['ok'] else 'ERR ' + st['err']}")
    print(f"DAY={today} NEW={len(new_items)} TOTAL={total}")


if __name__ == "__main__":
    sys.exit(main())
