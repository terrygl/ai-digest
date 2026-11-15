#!/usr/bin/env python3
"""etf-radar intraday — 盘中速报: 实时价 vs 当日预测价/区间。

  timer 触发: 交易时段每小时(北京 10/11/13/14/15), 见 remote-install.sh
  手动运行: python3 intraday.py

实时价: 腾讯 qt.gtimg.cn(主) / 新浪 hq.sinajs.cn(兜底, 需Referer)。
(原东财push2于2026-11-13随行情源整体切换弃用)
"""
import json
import re
import sys

import common
import push
from common import WATCHLIST, bj_today, fetch, load_cfg


def _tx_symbols():
    return [("sh" if s.startswith("1.") else "sz") + s.split(".", 1)[1]
            for _, _, s, _ in WATCHLIST]


def _quotes_tx():
    text = fetch("https://qt.gtimg.cn/q=" + ",".join(_tx_symbols()),
                 timeout=15).decode("gbk", "ignore")
    out = {}
    for m in re.finditer(r'v_(?:sh|sz)(\d{6})="([^"]*)"', text):
        f = m.group(2).split("~")
        try:
            out[m.group(1)] = {"price": float(f[3]), "pct": float(f[32])}
        except (ValueError, IndexError):
            continue
    return out


def _quotes_sina():
    text = fetch("https://hq.sinajs.cn/list=" + ",".join(_tx_symbols()), timeout=15,
                 headers={"Referer": "https://finance.sina.com.cn"}).decode("gbk", "ignore")
    out = {}
    for m in re.finditer(r'hq_str_(?:sh|sz)(\d{6})="([^"]*)"', text):
        f = m.group(2).split(",")
        try:
            price, prev = float(f[3]), float(f[2])
            out[m.group(1)] = {"price": price,
                               "pct": (price / prev - 1) * 100 if prev else 0.0}
        except (ValueError, IndexError):
            continue
    return out


def fetch_quotes():
    """{code: {price, pct}} —— 腾讯主源, 新浪兜底。"""
    try:
        out = _quotes_tx()
        if out:
            return out
        print("quotes tx empty, fallback sina")
    except Exception as ex:
        print(f"quotes tx failed: {str(ex)[:80]}, fallback sina")
    return _quotes_sina()


def build_text():
    # 晨报模式: 引用最新分析(其预测目标=今日); 过旧(>4天)视为停更跳过
    D, a = push.latest_analysis()
    if not a or not a.get("symbols"):
        return None
    d = bj_today()
    try:
        from datetime import date
        if (date.fromisoformat(d) - date.fromisoformat(D)).days > 4:
            return None
    except ValueError:
        return None
    quotes = fetch_quotes()
    lines = [f"⏱ ETF雷达 盘中速报 {d} {common.bj_now().strftime('%H:%M')}", ""]
    for code, s in a["symbols"].items():
        q = quotes.get(code)
        if not q:
            continue
        price, pct = q["price"], q["pct"]
        pred = s.get("pred_close")
        if pred:
            gap = (price / pred - 1) * 100
            pred_s = f"预{pred:.3f}({gap:+.2f}%)"
        else:
            pred_s = "预-"
        lo, hi = s.get("band_low", 0), s.get("band_high", 0)
        if price > hi:
            st = "▲超区间上沿"
        elif price < lo:
            st = "▼破区间下沿"
        else:
            st = "区间内✓"
        lines.append(f"{s['name']} 现{price:.3f} {pct:+.2f}%｜{pred_s}｜{st}")
    lines += ["", "（盘中参考；完整日报每交易日 8:30 晨报推送）",
              "（模型统计输出，不构成投资建议）",
              "— etf-radar 日报管道"]
    return "\n".join(lines)


def main():
    common.ensure_dirs()
    text = build_text()
    if not text:
        print("intraday: 无当日分析(非交易日?)，跳过")
        return 0
    print(text)
    cfg = load_cfg()
    try:
        sent = push.push_all(text, cfg)
        print(f"intraday: pushed -> {','.join(sent) or '无渠道'}")
    except Exception as ex:
        print(f"intraday: 推送失败 {str(ex)[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
