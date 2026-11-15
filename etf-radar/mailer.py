#!/usr/bin/env python3
"""etf-radar mailer — 把日报/周报以HTML邮件发给固定收件人（config.env: MAIL_TO）。

  python3 mailer.py          # 发当日日报(etf-YYYY-MM-DD.md, 找不到昨天, 再没有则跳过)
  python3 mailer.py weekly   # 发最近一份周报(weekly-YYYY-MM-DD.md)

复用 web.py 的 md2html / send_mail；逐个发送，间隔1秒。邮件尾部附免责声明与网页入口。
"""
import os
import sys
import time
from datetime import timedelta

import common
from common import bj_now, load_cfg

import web  # md2html / send_mail


def report_md(kind):
    # 日报只发"今天"的(节假日无分析->无文件->不发, 避免重发旧报告);
    # 周报周五生成、文件名是最近交易日, 允许往前找3天
    offsets = (0, 1, 2, 3) if kind == "weekly" else (0,)
    for offset in offsets:
        d = (bj_now().date() - timedelta(days=offset)).isoformat()
        name = (f"weekly-{d}.md" if kind == "weekly" else f"etf-{d}.md")
        p = os.path.join(common.REPORTS, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return d, name, f.read()
    return None, None, None


def build_html(day, kind, md):
    domain = load_cfg().get("WEB_DOMAIN", "etf.example.com")
    site = f"https://{domain}"
    label = "周报" if kind == "weekly" else "日报"
    body = web.md2html(md)
    return f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'></head>
<body style="margin:0;background:#f7f6f3;font-family:-apple-system,'Segoe UI',Roboto,
'PingFang SC','Microsoft YaHei',sans-serif;color:#26272b;line-height:1.75">
<div style="background:#1f2a30;color:#fff;text-align:center;padding:26px 12px">
<div style="display:inline-block;font-size:21px;font-weight:800;letter-spacing:2px;
border-bottom:3px solid #0e7490;padding-bottom:6px">ETF趋势雷达</div>
<div style="color:#aab4bb;font-size:13px;margin-top:10px">{day} {label} · 量化规则+消息面模型</div></div>
<div style="max-width:720px;margin:24px auto;padding:24px;background:#fff;
border:1px solid #e6e3dd;border-radius:12px">{body}</div>
<div style="text-align:center;color:#8b8a86;font-size:12px;padding:16px;max-width:720px;margin:0 auto">
本邮件为程序自动生成的统计模型输出，不构成投资建议 ·
<a href="{site}" style="color:#8b8a86">网页版仪表盘</a>（口令访问）</div></body></html>"""


def main():
    kind = "weekly" if "weekly" in sys.argv else "daily"
    cfg = load_cfg()
    if not cfg.get("SMTP_HOST"):
        print("mailer: smtp未配置，跳过")
        return 0
    tos = [x.strip() for x in cfg.get("MAIL_TO", "").split(",") if x.strip()]
    if not tos:
        print("mailer: MAIL_TO为空，跳过")
        return 0
    day, name, md = report_md(kind)
    if not md:
        print(f"mailer: 没有{kind}报告可发送")
        return 0
    # 发送去重: 每份报告只发一次(重跑/补跑不重发); 重发删 state/last_sent_{kind}
    marker = os.path.join(common.STATE, f"last_sent_{kind}")
    if os.path.exists(marker) and open(marker, encoding="utf-8").read().strip() == day:
        print(f"mailer: {kind} {day} 已发送过，跳过")
        return 0
    label = "周报" if kind == "weekly" else "日报"
    ok = fail = 0
    for to in tos:
        good, msg = web.send_mail(cfg, to, f"【ETF雷达】{day} {label}",
                                  build_html(day, kind, md))
        if good:
            ok += 1
        else:
            fail += 1
            print(f"mailer: fail {to}: {msg}")
        time.sleep(1)
    if ok:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(day)
    print(f"mailer: {label} {day}（{name}）发送完成 ok={ok} fail={fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
