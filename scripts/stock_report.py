#!/usr/bin/env python3
"""ai-digest stock_report — 每日股票观察：中国船舶 + 纳指ETF(513100/QQQ)。

数据: 腾讯行情/K线(A股+ETF) + stooq(QQQ) → MA/RSI/量比 → GLM中文解读 → 飞书推送+落盘
用法: python3 stock_report.py          # 正常跑（当日）
cron: 10 10 * * * UTC = 北京18:00（A股收盘后、美股盘前）
免责: 仅供参考，非投资建议。
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import web  # 复用 feishu_webhook

UA = {"User-Agent": "Mozilla/5.0 ai-digest-stock/1.0"}
TARGETS = [
    ("sh600150", "中国船舶", "tencent"),
    ("sh513100", "纳指ETF", "tencent"),
    ("qqq.us", "QQQ(纳指100美股)", "stooq"),
]


def load_env():
    cfg = {}
    p = os.path.join(BASE, "config.env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def http_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def tencent_kline(sym, n=70):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},qfq"
    data = json.loads(http_get(url).decode("utf-8", "ignore"))
    node = data["data"][sym]
    rows = node.get("qfqday") or node.get("day") or []
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
    # (date, open, close, high, low, volume)


def tencent_quote(syms):
    url = "https://qt.gtimg.cn/q=" + ",".join(syms)
    text = http_get(url).decode("gbk", "ignore")
    out = {}
    for m in re.finditer(r'v_(\w+)="([^"]+)"', text):
        p = m.group(2).split("~")
        out[m.group(1)] = {"name": p[1], "price": float(p[3]),
                           "pct": float(p[32]) if p[32] else 0.0}
    return out


def stooq_kline(sym, n=70):
    csv = http_get(f"https://stooq.com/q/d/l/?s={sym}&i=d").decode()
    lines = [l for l in csv.strip().splitlines() if not l.startswith("Date")]
    rows = []
    for l in lines[-n:]:
        d, o, h, low, c, vol = l.split(",")
        rows.append((d, float(o), float(c), float(h), float(low), float(vol or 0)))
    return rows


def ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    return 100.0 if losses == 0 else round(100 - 100 / (1 + gains / losses), 1)


def analyze(rows):
    closes = [r[2] for r in rows]
    vols = [r[5] for r in rows]
    last, prev = closes[-1], closes[-2]
    d20 = closes[-20:]
    return {
        "date": rows[-1][0], "close": last,
        "pct": round((last - prev) / prev * 100, 2),
        "ma5": round(ma(closes, 5), 2), "ma10": round(ma(closes, 10), 2),
        "ma20": round(ma(closes, 20), 2), "ma60": round(ma(closes, 60), 2) if ma(closes, 60) else None,
        "rsi14": rsi(closes),
        "hi20": max(d20), "lo20": min(d20),
        "pos20": round((last - min(d20)) / max(max(d20) - min(d20), 1e-9) * 100),  # 20日区间位置%
        "vol_ratio": round(vols[-1] / (ma(vols, 5) or 1), 2),
    }


def glm_analysis(cfg, sym, name, a):
    if not cfg.get("LLM_API_KEY"):
        return "（未配置LLM，跳过解读）"
    prompt = (f"以下是{name}({sym})今日数据：收盘{a['close']}，涨跌{a['pct']}%，"
              f"MA5={a['ma5']} MA10={a['ma10']} MA20={a['ma20']} MA60={a['ma60']}，"
              f"RSI14={a['rsi14']}，20日区间位置{a['pos20']}%（0=最低100=最高），"
              f"量比(对5日均量)={a['vol_ratio']}。\n"
              "用中文写120-180字客观解读：趋势方向（相对均线排列）、超买超卖、"
              "量价配合、明后天值得留意什么。只描述事实与常识性技术分析，"
              "不给买卖建议，结尾自然带一句风险提示。")
    body = json.dumps({
        "model": cfg.get("LLM_MODEL", "glm-4-flash"), "temperature": 0.3, "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        cfg["LLM_BASE_URL"].rstrip("/") + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["LLM_API_KEY"]})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    u = data.get("usage") or {}
    return (data["choices"][0]["message"]["content"].strip(),
            u.get("prompt_tokens", 0), u.get("completion_tokens", 0))


def main():
    day = (datetime.utcnow() + timedelta(hours=8)).date().isoformat()
    cfg = load_env()
    quotes = tencent_quote([t[0] for t in TARGETS if t[2] == "tencent"])
    sections, texts = [], []
    pt_sum = ct_sum = 0
    for sym, name, src in TARGETS:
        try:
            rows = tencent_kline(sym) if src == "tencent" else stooq_kline(sym)
            if len(rows) < 21:
                raise ValueError("K线不足")
            a = analyze(rows)
            q = quotes.get(sym, {})
            _txt, _pt, _ct = glm_analysis(cfg, sym, name, a)
            pt_sum += _pt
            ct_sum += _ct
            head = (f"### {name}（{sym}）\n"
                    f"- 收盘 **{q.get('price', a['close'])}**（{q.get('pct', a['pct'])}%）｜{a['date']}\n"
                    f"- MA5 {a['ma5']} / MA10 {a['ma10']} / MA20 {a['ma20']}"
                    + (f" / MA60 {a['ma60']}" if a['ma60'] else "") + "\n"
                    f"- RSI14 {a['rsi14']}｜20日区间位置 {a['pos20']}%｜量比 {a['vol_ratio']}\n\n"
                    f"{_txt}\n")
            sections.append(head)
            texts.append(f"【{name}】收{q.get('price', a['close'])}({q.get('pct', a['pct'])}%) "
                         f"RSI{a['rsi14']} 区间位置{a['pos20']}% 📊{_pt + _ct}tk")
        except Exception as ex:
            sections.append(f"### {name}（{sym}）\n- 数据获取失败：{str(ex)[:80]}\n")
            texts.append(f"【{name}】数据失败:{str(ex)[:40]}")
    import tokens as tk
    if pt_sum or ct_sum:
        tk.log("股票日报", cfg.get("LLM_MODEL", "glm-4-flash"), pt_sum, ct_sum)
    tok_line = (f"\n📊 解读LLM消耗：输入{pt_sum} · 输出{ct_sum} · 合计{pt_sum + ct_sum} tokens"
                if (pt_sum or ct_sum) else "")
    md = (f"# 股票观察日报 {day}\n\n" + "\n".join(sections) + tok_line +
          "\n\n---\n*数据: 腾讯行情/stooq · 解读: GLM生成，仅供参考，非投资建议。*\n")
    out_dir = os.path.join(BASE, "reports_stocks")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{day}.md"), "w", encoding="utf-8") as f:
        f.write(md)

    hook = web.feishu_webhook()
    text = (f"【AI精选·股票日报】{day}\n" + "\n".join(texts) + tok_line +
            "\n完整技术解读见附档/网站\n（仅供参考，非投资建议）")
    if hook:
        body = json.dumps({"msg_type": "text", "content": {"text": text[:2800]}}).encode()
        req = urllib.request.Request(hook, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    print(f"stock_report: {day} done -> reports_stocks/{day}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
