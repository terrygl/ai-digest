#!/usr/bin/env python3
"""ai-digest summarizer — dedupe + score + optional LLM digest + optional push.

Reads today's (or yesterday's) items file, filters against the seen-DB,
scores by source weight + keyword hits + popularity, then:
  - always writes a markdown list report
  - if LLM_API_KEY set: calls an OpenAI-compatible chat API for a Chinese digest
  - if TELEGRAM/FEISHU configured: pushes the report
Usage: python3 summarize.py [--no-push]
"""
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(BASE, "reports")
STATE = os.path.join(BASE, "state")
os.makedirs(REPORTS, exist_ok=True)
os.makedirs(STATE, exist_ok=True)


def bj_today():
    return (datetime.utcnow() + timedelta(hours=8)).date()  # 报告按北京时间计日


def load_cfg():
    cfg = {}
    p = os.path.join(BASE, "config.env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


CFG = load_cfg()
MIN_SCORE = int(CFG.get("MIN_SCORE", 11))
MAX_ITEMS = int(CFG.get("MAX_ITEMS", 40))

KEYWORDS = re.compile(
    r"(llm|gpt|claude|gemini|kimi|deepseek|qwen|llama|mistral|glm|grok|sora"
    r"|agent|rag|fine.?tun|inference|vllm|transformer|diffusion|multimodal"
    r"|open.?weight|benchmark|scaling|reasoning|lora|quantiz|moe|rlhf"
    r"|gpu|npu|ascend|cuda|大模型|开源|发布|推理|训练|智能体)", re.I)


def score_item(it):
    hits = len(KEYWORDS.findall(it["title"] + " " + it.get("desc", "")))
    # off-topic guard: community sources (hn/reddit/lobsters/github) must match keywords
    if it.get("cat") != "blog" and hits == 0:
        return 0
    s = it.get("w", 1) * 3 + min(hits * 2, 10)
    meta = it.get("meta", {})
    s += meta.get("points", 0) // 25
    s += meta.get("stars", 0) // 25
    # freshness penalty when a parseable timestamp exists and is stale
    ts = it.get("ts", "")
    if ts:
        try:
            import email.utils
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                dt = email.utils.parsedate_to_datetime(ts)
            if dt.tzinfo is None:
                from datetime import timezone
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - dt).days
            if age_days > 7:
                s -= 6
        except Exception:
            pass
    return s


def load_items():
    for offset in (0, 1):
        d = (bj_today() - timedelta(days=offset)).isoformat()
        p = os.path.join(BASE, "data", f"items-{d}.jsonl")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            with open(p, encoding="utf-8") as f:
                return d, [json.loads(l) for l in f if l.strip()]
    return None, []


def seen_db():
    db = sqlite3.connect(os.path.join(STATE, "seen.db"))
    db.execute("CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, day TEXT)")
    return db


def est_tokens(s):
    """token估算：中文字符≈1/字，其他（英文/URL/符号）≈3.5字符/个。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    return int(cjk + (len(s) - cjk) / 3.5)


def call_llm(items, cfg):
    # 预格式化编号条目：数据做成现成字符串让模型逐字复制，
    # 避免 glm-4-flash 在并行JSON数组里把相邻条目的链接/数据张冠李戴。
    lines = []
    est = []  # (编号, 该条输入token估算)
    for n, i in enumerate(items, 1):
        m = i.get("meta", {})
        pop = ""
        if m.get("points"):
            pop = f"HN {m['points']}分"
            if m.get("comments"):
                pop += f"·{m['comments']}评论"
        if m.get("stars"):
            pop = (pop + "；" if pop else "") + f"GitHub {m['stars']}星"
        block = (f"[{n}] 标题：{i['title']}\n"
                 f"    链接：{i['link']}\n"
                 f"    数据：{pop or '无'}\n"
                 f"    摘要：{i.get('desc', '')[:200] or '（无摘要，仅标题）'}")
        lines.append(block)
        est.append((n, est_tokens(block)))
    entries_text = "\n".join(lines)

    body = json.dumps({
        "model": cfg.get("LLM_MODEL", "glm-4-flash"),
        "temperature": 0.3,
        "max_tokens": 4000,
        "messages": [
            {"role": "system", "content":
                "你是一名资深AI行业资讯编辑，为中文AI工程师/从业者编写每日行业日报。"
                "要求：信息密度高、克制不夸张；保留关键产品名/模型名/数字的原文。"},
            {"role": "user", "content":
                f"以下是今天({bj_today().isoformat()})采集的{len(items)}条AI相关资讯，"
                "每条含：标题/链接/数据/摘要 四个字段。\n\n"
                + entries_text
                + "\n\n请输出中文markdown日报，规则：\n"
                  "0) 最开头写【今日速览】：一段150-250字的口语化中文，把今天最重要的3-5件事"
                  "串成一段连贯的话（可带最有分量的1-2个数据），像跟朋友聊天一样讲清楚，"
                  "不用列表、不放链接、不堆术语，让完全不懂AI的人也能读懂；\n"
                  "1) 速览之后按【模型与产品发布】【研究与论文观点】【开源项目】【社区讨论】【值得一读】分板块；\n"
                  "2) 每条2-4句，必须包含：a)事实与关键数字（模型名/参数/价格/性能数字，取自该条的摘要字段，"
                  "摘要为空就基于标题写事实，绝不虚构数字）；"
                  "b)热度标注——把该条自己的『数据』字段原样复制成「（HN 821分·312评论）」「（GitHub 2.4k星）」"
                  "格式放在句末；数据为「无」的条目禁止出现任何热度标注、禁止借用其他条目的数据；"
                  "c)一句点评：为什么值得关注/意味着什么；"
                  "d)来源——每条末尾放 [原文](该条自己的链接)，链接必须逐字复制该条的『链接』字段，"
                  "禁止混用其他条目的链接；\n"
                  "3) 合并同事件的多条来源，只留一条（数据取热度更高的）；\n"
                  "4) 末尾加【编辑观察】3句话总结今日主线；\n"
                  "5) 无实质内容的条目直接舍弃。"}
        ]}).encode()
    url = cfg.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.get('LLM_API_KEY', '')}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    u = data.get("usage") or {}
    return (data["choices"][0]["message"]["content"],
            u.get("prompt_tokens", 0), u.get("completion_tokens", 0), est)


def build_list_md(day, items):
    lines = [f"# AI 日报（原始清单） {day}", ""]
    by_cat = {}
    for it in items:
        by_cat.setdefault(it["cat"], []).append(it)
    for cat in ("blog", "hn", "reddit", "github"):
        if cat not in by_cat:
            continue
        names = {"blog": "博客/官方", "hn": "Hacker News", "reddit": "Reddit", "github": "GitHub新项目"}
        lines.append(f"## {names[cat]}")
        for it in by_cat[cat]:
            pop = ""
            m = it.get("meta", {})
            if m.get("points"):
                pop = f" ({m['points']}pts·{m.get('comments', 0)}评)"
            if m.get("stars"):
                pop = f" (★{m['stars']})"
            lines.append(f"- [{it['title']}]({it['link']}){pop}")
        lines.append("")
    return "\n".join(lines)


def push_all(text, cfg):
    chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)]
    for chunk in chunks:
        if cfg.get("TELEGRAM_BOT_TOKEN") and cfg.get("TELEGRAM_CHAT_ID"):
            try:
                body = json.dumps({"chat_id": cfg["TELEGRAM_CHAT_ID"], "text": chunk}).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{cfg['TELEGRAM_BOT_TOKEN']}/sendMessage",
                    data=body, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=30).read()
            except Exception as ex:
                print(f"telegram push failed: {ex}")
        if cfg.get("FEISHU_WEBHOOK"):
            try:
                body = json.dumps({"msg_type": "text", "content": {"text": chunk[:2800]}}).encode()
                req = urllib.request.Request(cfg["FEISHU_WEBHOOK"], data=body,
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=30).read()
            except Exception as ex:
                print(f"feishu push failed: {ex}")


def main():
    no_push = "--no-push" in sys.argv
    day, items = load_items()
    if not items:
        print("no items collected yet")
        return 1
    db = seen_db()
    today = bj_today().isoformat()
    # skip: already reported on an EARLIER day (same-day rerun keeps items)
    rows = dict(db.execute("SELECT url, day FROM seen").fetchall())
    fresh = [i for i in items if not (i["link"] in rows and rows[i["link"]] != today)]
    scored = sorted(((score_item(i), i) for i in fresh), key=lambda x: -x[0])
    picked = [i for s, i in scored if s >= MIN_SCORE][:MAX_ITEMS]
    if not picked:
        picked = [i for _, i in scored[:15]]

    report = f"# AI 日报 {day}\n\n"
    llm_note = ""
    tok_note = ""
    if CFG.get("LLM_API_KEY"):
        try:
            digest, pt, ct, est = call_llm(picked, CFG)
            report += digest + "\n"
            import tokens as tk
            tk.log("日报", CFG.get("LLM_MODEL", "glm-4-flash"), pt, ct)
            cells = [f"#{n}·{t}tk" for n, t in est]
            rows = [" · ".join(cells[i:i + 6]) for i in range(0, len(cells), 6)]
            tok_note = (f"\n---\n📊 本期LLM消耗（{CFG.get('LLM_MODEL', 'glm-4-flash')}）："
                        f"输入{pt} · 输出{ct} · 合计{pt + ct} tokens\n"
                        f"\n**条目输入token估算**（{len(est)}条合计≈{sum(t for _, t in est)}，"
                        f"与实际输入{pt}的差值为指令模板开销）：\n" + "\n".join(rows))
        except Exception as ex:
            llm_note = f"LLM摘要失败({str(ex)[:100]})，已回退为原始清单"
    else:
        llm_note = "未配置LLM_API_KEY，输出原始清单"
    report += "\n---\n" + build_list_md(day, picked) + tok_note

    out = os.path.join(REPORTS, f"{day}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    for i in picked:
        db.execute("INSERT OR REPLACE INTO seen VALUES (?,?)", (i["link"], today))
    db.execute("DELETE FROM seen WHERE day < ?", ((bj_today() - timedelta(days=14)).isoformat(),))
    db.commit()
    db.close()

    if not no_push:
        push_all(report, CFG)
    print(json.dumps({"day": day, "fresh": len(fresh), "picked": len(picked),
                      "report": out, "note": llm_note}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
