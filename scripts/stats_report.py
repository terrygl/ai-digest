#!/usr/bin/env python3
"""ai-digest stats_report — 每日北京时间12:30由cron触发，推送上一日访问统计到飞书。

用法: python3 stats_report.py [YYYY-MM-DD]   # 带参数=手动补发指定日
"""
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import web  # 复用 feishu_webhook / STATS_DB

DAY = sys.argv[1] if len(sys.argv) > 1 else \
    (datetime.utcnow() + timedelta(hours=8) - timedelta(days=1)).date().isoformat()


def build_text(day):
    c = sqlite3.connect(web.STATS_DB)
    try:
        tot, uv, bots, valid = c.execute(
            "SELECT COUNT(*), COUNT(DISTINCT ip), SUM(bot),"
            "SUM(CASE WHEN status<400 THEN 1 ELSE 0 END) FROM hits WHERE day=?", (day,)).fetchone()
        if not tot:
            return None
        top = c.execute(
            "SELECT path, COUNT(*) n FROM hits WHERE day=? AND status<400 "
            "GROUP BY path ORDER BY n DESC LIMIT 6", (day,)).fetchall()
        hours = dict(c.execute(
            "SELECT hour, COUNT(*) FROM hits WHERE day=? AND status<400 GROUP BY hour",
            (day,)).fetchall())
        peak = f"{max(hours, key=hours.get)}点(峰值{max(hours.values())}次)" if hours else "无"
        fm, am = c.execute(
            "SELECT SUM(CASE WHEN mode='front' THEN 1 ELSE 0 END),"
            "SUM(CASE WHEN mode='admin' THEN 1 ELSE 0 END) FROM hits "
            "WHERE day=? AND status<400", (day,)).fetchone()
    finally:
        c.close()
    name = {"": "首页"}
    tops = "\n".join(
        f"{i}. {(name.get(t[0]) or t[0])[:46]} — {t[1]}次"
        for i, t in enumerate(top, 1)) or "无"
    return (
        f"【AI精选·访问日报】{day} 访问情况\n"
        f"总请求: {tot}（有效 {valid or 0} / 爬虫 {bots or 0}）\n"
        f"独立访客: {uv}\n"
        f"前台/后台: {fm or 0}/{am or 0}　峰值时段: {peak}\n"
        f"热门内容:\n{tops}\n"
        f"— ai-digest · 每天12:30自动推送")


def main():
    text = build_text(DAY)
    if not text:
        print(f"stats_report: {DAY} 无访问数据，跳过")
        return 0
    hook = web.feishu_webhook()
    if not hook:
        print("stats_report: 无飞书webhook，仅打印：\n" + text)
        return 0
    body = json.dumps({"msg_type": "text", "content": {"text": text[:2800]}}).encode()
    req = urllib.request.Request(hook, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()
    print(f"stats_report: {DAY} 已推送飞书")
    return 0


if __name__ == "__main__":
    sys.exit(main())
