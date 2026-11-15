#!/usr/bin/env python3
"""etf-radar 共享基础 — 配置加载、关注标的、北京时间、目录与飞书告警。

各模块(collect/analyze/report/mailer/web)统一从这里拿 WATCHLIST 与路径，
改关注标的只动这一处。stdlib only。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
REPORTS = os.path.join(BASE, "reports")
STATE = os.path.join(BASE, "state")
LOGS = os.path.join(BASE, "logs")

# (code, 名称, 东财secid, 消息面查询词)  secid: 沪=1.xxxxxx 深=0.xxxxxx
WATCHLIST = [
    ("510300", "沪深300ETF", "1.510300", "沪深300 OR A股"),
    ("510500", "中证500ETF", "1.510500", "中证500"),
    ("588200", "科创芯片ETF", "1.588200", "半导体 OR 芯片"),
    ("513350", "标普油气ETF", "1.513350", "国际油价 OR 原油"),
    ("159699", "恒生消费ETF", "0.159699", "恒生消费 OR 港股消费"),
    ("600150", "中国船舶", "1.600150", "中国船舶 OR 造船"),
    ("600036", "招商银行", "1.600036", "招商银行 OR 银行股"),
    ("603259", "药明康德", "1.603259", "药明康德 OR CXO"),
]
WATCH = {c: (c, n, s, q) for c, n, s, q in WATCHLIST}

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) etf-radar/0.1 (personal; contact: root)",
      "Referer": "https://quote.eastmoney.com/"}


def ensure_dirs():
    for d in (DATA, REPORTS, STATE, LOGS):
        os.makedirs(d, exist_ok=True)


def bj_now():
    return datetime.utcnow() + timedelta(hours=8)  # 全流程按北京时间计日


def bj_today():
    return bj_now().date().isoformat()


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


def fetch(url, timeout=15, tries=2, headers=None):
    """带一次重试的抓取；429/502/503 退避更久（沿用 ai-digest collect 的经验）。"""
    last = None
    h = dict(UA)
    h.update(headers or {})
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as ex:
            last = ex
            import re
            time.sleep(10 if re.search(r"\b(429|502|503)\b", str(ex)) else 2)
    raise last


def feishu_alert(text, cfg):
    """同步推飞书（管道失败告警）；未配置则只打日志。"""
    hook = cfg.get("FEISHU_WEBHOOK", "")
    if not hook:
        print("alert(feishu): no webhook, skip:", text.replace("\n", " ")[:100])
        return False
    try:
        body = json.dumps({"msg_type": "text", "content": {"text": text[:2800]}}).encode()
        req = urllib.request.Request(hook, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
        print("alert(feishu): sent ->", text.splitlines()[0])
        return True
    except Exception as ex:
        print("alert(feishu): failed:", ex)
        return False


# ---------- LLM token 用量 ----------
def log_tokens(purpose, usage):
    """LLM调用的token用量落盘(state/tokens.jsonl), 供日报/周报统计。"""
    try:
        ensure_dirs()
        rec = {"ts": bj_now().strftime("%F %T"), "day": bj_today(),
               "purpose": purpose,
               "prompt": int(usage.get("prompt_tokens") or 0),
               "completion": int(usage.get("completion_tokens") or 0)}
        rec["total"] = rec["prompt"] + rec["completion"]
        with open(os.path.join(STATE, "tokens.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def token_stats(day=None, since=None, upto=None):
    """聚合token用量: day=单日 或 since<=day<=upto。返回{calls,prompt,completion,total}。"""
    out = {"calls": 0, "prompt": 0, "completion": 0, "total": 0}
    p = os.path.join(STATE, "tokens.jsonl")
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            d = r.get("day", "")
            if day is not None and d != day:
                continue
            if since is not None and d < since:
                continue
            if upto is not None and d > upto:
                continue
            out["calls"] += 1
            out["prompt"] += r.get("prompt", 0) or 0
            out["completion"] += r.get("completion", 0) or 0
            out["total"] += r.get("total") or 0
    return out


def market_db():
    """行情+预测库（各进程独立连接，用完即关）。"""
    import sqlite3
    db = sqlite3.connect(os.path.join(DATA, "market.db"))
    db.execute("CREATE TABLE IF NOT EXISTS klines("
               "symbol TEXT NOT NULL, date TEXT NOT NULL, open REAL, close REAL,"
               "high REAL, low REAL, volume REAL, amount REAL,"
               "PRIMARY KEY(symbol, date))")
    db.execute("CREATE TABLE IF NOT EXISTS predictions("
               "symbol TEXT NOT NULL, date TEXT NOT NULL, close REAL,"
               "prob_up REAL, band_low REAL, band_high REAL, signal TEXT, score REAL,"
               "sent REAL, sent_n INTEGER, features TEXT,"
               "actual_close REAL, hit_dir INTEGER, hit_band INTEGER,"
               "PRIMARY KEY(symbol, date))")
    # 迁移: 2026-11-09 起记录预测价(已存在的库补列)
    try:
        db.execute("ALTER TABLE predictions ADD COLUMN pred_close REAL")
    except Exception:
        pass  # 列已存在
    return db


if __name__ == "__main__":
    # CLI: python3 common.py alert "告警内容" —— run.sh 管道失败时调用
    if len(sys.argv) >= 3 and sys.argv[1] == "alert":
        feishu_alert(sys.argv[2], load_cfg())
    else:
        print("usage: common.py alert <msg>")
