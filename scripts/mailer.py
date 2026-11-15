#!/usr/bin/env python3
"""ai-digest mailer — 把当日日报以HTML邮件发给订阅用户（run.sh 在 summarize 之后调用）。

依赖 web_config.json 的 smtp_* 与 site_url 字段。逐个发送，间隔1秒。
每封邮件尾部附专属退订链接（/unsub?t=token）。
"""
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

import web  # 复用 md2html / load_cfg / send_mail / db 路径

CFG = web.load_cfg()
def _bj():
    return datetime.utcnow() + timedelta(hours=8)


TODAY = _bj().date().isoformat()


def report_md():
    for d in (TODAY, (_bj().date() - timedelta(days=1)).isoformat()):
        p = os.path.join(web.REPORTS, f"{d}.md")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return d, f.read()
    return None, None


def build_html(day, md, email, token):
    site = CFG["site_url"].rstrip("/")
    unsub = f"{site}/unsub?t={token}"
    body = web.md2html(md)
    return f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'></head>
<body style="margin:0;background:#f7f6f3;font-family:-apple-system,'Segoe UI',Roboto,
'PingFang SC','Microsoft YaHei',sans-serif;color:#26272b;line-height:1.75">
<div style="background:#20232a;color:#fff;text-align:center;padding:28px 12px">
<div style="display:inline-block;font-size:22px;font-weight:800;letter-spacing:2px;
border-bottom:3px solid #b4531f;padding-bottom:6px">AI科技精选</div>
<div style="color:#b9bcc4;font-size:13px;margin-top:10px">{day} 日报 · 自动采集+人工编辑</div></div>
<div style="max-width:720px;margin:24px auto;padding:24px;background:#fff;
border:1px solid #e6e3dd;border-radius:12px">{body}</div>
<div style="text-align:center;color:#8b8a86;font-size:12px;padding:16px">
发送给 {email} · <a href="{unsub}" style="color:#8b8a86">退订</a> ·
<a href="{site}" style="color:#8b8a86">网页版</a></div></body></html>"""


def main():
    if not CFG.get("smtp_host"):
        print("mailer: smtp未配置，跳过邮件发送")
        return 0
    day, md = report_md()
    if not md:
        print("mailer: 没有可发送的报告")
        return 0
    c = web.db()
    users = c.execute("SELECT email, token FROM users WHERE verified=1 AND subscribed=1").fetchall()
    c.close()
    if not users:
        print("mailer: 无已验证订阅用户")
        return 0
    ok = fail = 0
    for email, token in users:
        good, msg = web.send_mail(CFG, email, f"【AI科技精选】{day} 日报",
                                  build_html(day, md, email, token))
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        if not good:
            print(f"mailer: fail {email}: {msg}")
        time.sleep(1)
    print(f"mailer: {day} 发送完成 ok={ok} fail={fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
