#!/usr/bin/env python3
"""etf-radar pusher — 日报/周报摘要推送到飞书/Telegram。

  python3 push.py          # 推当日日报摘要(当日无分析则跳过, 与邮件同策略)
  python3 push.py weekly   # 推周报摘要(基于 market.db 最近5个交易日)

推送为紧凑摘要(速览+消息要点+网页链接), 不整篇转发; 按渠道上限分块。
飞书机器人有「日报」关键词限制, 签名行已包含。
推送失败只记日志不影响管道(run.sh 中以 || true 调用)。
"""
import json
import os
import re
import sys
import urllib.request

import common
from common import WATCHLIST, bj_today, load_cfg

TG_CHUNK, FS_CHUNK = 3500, 2800


def latest_analysis():
    """最新一份分析(按文件名日期最大); 晨报模式下其预测目标=今日。"""
    best, best_d = None, ""
    if os.path.isdir(common.DATA):
        for f in os.listdir(common.DATA):
            m = re.match(r"analysis-(\d{4}-\d{2}-\d{2})\.json$", f)
            if m and m.group(1) > best_d:
                best_d, best = m.group(1), f
    if best:
        with open(os.path.join(common.DATA, best), encoding="utf-8") as fh:
            return best_d, json.load(fh)
    return None, None


def build_daily():
    D, a = latest_analysis()
    if not a or not a.get("symbols"):
        return None
    lines = [f"📊 ETF趋势雷达 日报 {bj_today()}（{D}收盘数据）", ""]
    for code, s in a["symbols"].items():
        pred = s.get("pred_close")
        pred_s = f"→预{pred:.3f}({s['pred_ret']:+.2f}%)" if pred is not None else ""
        lines.append(f"{s['name']} 昨收{s['close']:.3f} {s['pct']:+.2f}% {pred_s}"
                     f"｜{s['signal']} 看涨{s['prob_up']}%")
        r = s.get("reasons") or {}
        if r.get("up"):
            lines.append(f"  ▲ {'；'.join(r['up'][:3])}")
        if r.get("down"):
            lines.append(f"  ▼ {'；'.join(r['down'][:3])}")
    note = a.get("note")
    if note:
        lines.append(f"⚠️ {note}")
    ts = common.token_stats(day=bj_today())
    if ts["calls"]:
        lines.append(f"🤖 LLM今日: {ts['calls']}次 · {ts['total']:,} tokens"
                     f"（入{ts['prompt']:,}/出{ts['completion']:,}）")
    lines += ["", f"🌐 详情 https://{load_cfg().get('WEB_DOMAIN', 'etf.example.com')}",
              "（模型统计输出，不构成投资建议）",
              "— etf-radar 日报管道"]
    return "\n".join(lines)


def build_weekly():
    db = common.market_db()
    dates = [r[0] for r in db.execute(
        "SELECT DISTINCT date FROM klines ORDER BY date DESC LIMIT 5").fetchall()][::-1]
    if len(dates) < 2:
        db.close()
        return None
    lines = [f"📈 ETF趋势雷达 周报 {dates[0]} ~ {dates[-1]}", ""]
    for code, name, _, _ in WATCHLIST:
        rows = db.execute("SELECT close FROM klines WHERE symbol=? AND date>=?"
                          " ORDER BY date", (code, dates[0])).fetchall()
        if len(rows) >= 2:
            pct = (rows[-1][0] / rows[0][0] - 1) * 100
            lines.append(f"{name} 周{pct:+.2f}%（{rows[0][0]:.3f}→{rows[-1][0]:.3f}）")
    hits = db.execute("SELECT COUNT(*), SUM(hit_dir) FROM predictions"
                      " WHERE hit_dir IS NOT NULL AND date>=?",
                      (dates[0],)).fetchone()
    if hits and hits[0]:
        lines.append(f"本周信号方向命中 {hits[1]}/{hits[0]}")
    db.close()
    ts = common.token_stats(since=dates[0], upto=bj_today())
    if ts["calls"]:
        lines.append(f"🤖 LLM本周: {ts['calls']}次 · {ts['total']:,} tokens")
    lines += ["", f"🌐 详情 https://{load_cfg().get('WEB_DOMAIN', 'etf.example.com')}",
              "（模型统计输出，不构成投资建议）",
              "— etf-radar 日报管道"]
    return "\n".join(lines)


def _send(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def push_all(text, cfg):
    sent = []
    if cfg.get("TELEGRAM_BOT_TOKEN") and cfg.get("TELEGRAM_CHAT_ID"):
        api = f"https://api.telegram.org/bot{cfg['TELEGRAM_BOT_TOKEN']}/sendMessage"
        for i in range(0, len(text), TG_CHUNK):
            _send(api, {"chat_id": cfg["TELEGRAM_CHAT_ID"],
                        "text": text[i:i + TG_CHUNK]})
        sent.append("telegram")
    if cfg.get("FEISHU_WEBHOOK"):
        for i in range(0, len(text), FS_CHUNK):
            _send(cfg["FEISHU_WEBHOOK"],
                  {"msg_type": "text", "content": {"text": text[i:i + FS_CHUNK]}})
        sent.append("feishu")
    return sent


def main():
    common.ensure_dirs()
    cfg = load_cfg()
    kind = "weekly" if "weekly" in sys.argv else "daily"
    text = build_weekly() if kind == "weekly" else build_daily()
    if not text:
        print(f"push: 没有{kind}内容可推送")
        return 0
    if not (cfg.get("FEISHU_WEBHOOK") or
            (cfg.get("TELEGRAM_BOT_TOKEN") and cfg.get("TELEGRAM_CHAT_ID"))):
        print("push: 未配置任何推送渠道")
        return 0
    try:
        sent = push_all(text, cfg)
        print(f"push: {kind} -> {','.join(sent) or '无渠道'} ({len(text)}字)")
    except Exception as ex:
        print(f"push: 失败 {str(ex)[:150]}（不影响管道）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
