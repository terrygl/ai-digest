#!/usr/bin/env python3
"""etf-radar reporter — 生成日报/周报 markdown 到 reports/。

  日报 etf-YYYY-MM-DD.md : 基于 data/analysis-YYYY-MM-DD.json (找不到则不生成)
  周报 weekly-YYYY-MM-DD.md (--weekly): 基于 market.db 最近5个交易日 + predictions

md 格式刻意只用标题/列表/链接 —— web 与邮件的 md2html 渲染器不支持表格。
"""
import json
import os
import re
import sys
import urllib.request

import common
from common import WATCHLIST, WATCH, bj_today, load_cfg

DISCLAIMER = ("> ⚠️ 本报告为规则+消息面模型的统计输出，仅供个人研究参考，"
              "不构成任何投资建议；预测不保证正确，据此操作风险自负。")


def pct(x, sign=True):
    s = "+" if (sign and x >= 0) else ""
    return f"{s}{x:.2f}%"


def latest_analysis():
    """最新一份分析(按文件名日期取最大), 分析日期=其数据收盘日。"""
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


def llm_overview(analysis, cfg, close_date):
    """可选: 让LLM把全部信号串成一段150字晨报综述; 失败返回空。"""
    if not cfg.get("LLM_API_KEY"):
        return ""
    lines = []
    for code, s in analysis["symbols"].items():
        news = s.get("news") or {}
        sent = news.get("sent")
        sent_s = f"情绪{sent:+.2f}" if sent is not None else "无消息面"
        lines.append(f"{s['name']}({code}) 昨收{pct(s['pct'])} 信号{s['signal']} "
                     f"看涨概率{s['prob_up']}% {sent_s}")
    body = json.dumps({
        "model": cfg.get("LLM_MODEL", "glm-4-flash"),
        "temperature": 0.3,
        "max_tokens": 3000,  # glm-5.3推理模型: 预算须覆盖思考(约1k)+150字输出
        "messages": [
            {"role": "system", "content": "你是严谨的市场观察员，语气克制，不做保证性表述。"},
            {"role": "user", "content":
                f"昨收盘({close_date})的各标的信号数据如下，今日开盘在即：\n"
                + "\n".join(lines)
                + "\n\n写一段150字以内的中文晨报综述：概括昨日强弱分化与隔夜消息，"
                  "点出今日最值得注意的1-2个信号；不得给出买卖建议，不得虚构数字。"}]
    }).encode()
    url = cfg.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/") \
        + "/chat/completions"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.get('LLM_API_KEY', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:  # 推理模型响应更慢
            data = json.loads(r.read())
        common.log_tokens("overview", data.get("usage") or {})
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def daily(db):
    D, a = latest_analysis()
    if not a or not a.get("symbols"):
        print("report: no analysis, skip daily")
        return None
    today = bj_today()  # 发送日(晨报当日) = 预测目标日
    cfg = load_cfg()
    out = [f"# ETF趋势雷达 日报 {today}", "",
           f"> 数据截至 **{D} 收盘**，预测针对今日开盘后的走势。", "",
           DISCLAIMER, ""]

    overview = llm_overview(a, cfg, D)
    if overview:
        out += ["## 综述", "", overview, ""]

    out += ["## 速览", "",
            "| 标的 | 昨收 | 昨日涨跌 | 预测价 | 预测涨跌 | 今日区间 | 信号 | 看涨概率 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for code, s in a["symbols"].items():
        out.append(f"| {s['name']}({code}) | {s['close']:.3f} | {pct(s['pct'])}"
                   f" | **{s['pred_close']:.3f}** | {pct(s['pred_ret'])}"
                   f" | {s['band_low']:.3f}~{s['band_high']:.3f}"
                   f" | {s['signal']} | {s['prob_up']}% |")
    out.append("")

    for code, s in a["symbols"].items():
        f = s["features"]
        out += [f"## {s['name']}（{code}）", "",
                f"- 昨收 **{s['close']:.3f}**（前日 {s['prev_close']:.3f}，"
                f"涨跌 **{pct(s['pct'])}**）",
                (f"- 技术面：5日动量 {pct(f['mom5']*100)}、20日动量 {pct(f['mom20']*100)}、"
                 f"RSI14 {f['rsi14']:.1f}、量比 {f['vol_ratio']:.2f}、"
                 f"MA20偏离 {pct(f['ma20_dev']*100)}、ATR {f['atr_pct']*100:.2f}%、"
                 f"连涨跌 {f['streak']}天、20日位置 {f['pos20']*100:.0f}%")]
        news = s.get("news") or {}
        if news.get("n"):
            sent = news.get("sent")
            sent_s = f"{sent:+.2f}" if sent is not None else "-"
            out.append(f"- 消息面：{news['n']}条评分，情绪 **{sent_s}**")
            for it in news.get("items", [])[:5]:
                mark = "〔公告〕" if it.get("src") == "em-ann" else ""
                out.append(f"  - {mark}[{it['title']}]({it['link']})"
                           f"（{it['tag']} {it['score']:+.1f}）")
        else:
            out.append("- 消息面：今日无评分新闻")
        if s.get("llm_view"):
            out.append(f"- 模型观点：{s['llm_view']}")
        h = s.get("hit20") or {}
        hit_s = (f"｜近{h['n']}日方向胜率 {h['dir']*100:.0f}%、区间命中 {h['band']*100:.0f}%"
                 if h.get("n") else "")
        out.append(f"- 今日预测：**{s['pred_close']:.3f}（{pct(s['pred_ret'])}）**"
                   f"｜区间 {s['band_low']:.3f}~{s['band_high']:.3f}"
                   f"｜信号 **{s['signal']}**｜看涨概率 {s['prob_up']}%{hit_s}")
        reasons = s.get("reasons") or {}
        if reasons.get("up"):
            out.append(f"- ▲ 看涨因素：{'；'.join(reasons['up'])}")
        if reasons.get("down"):
            out.append(f"- ▼ 看跌因素：{'；'.join(reasons['down'])}")
        out.append("")

    # 复盘: 最近一批已验证的预测(预测作出日=收盘日, 实际=其下一交易日收盘)
    last = db.execute("SELECT DISTINCT date FROM predictions WHERE actual_close IS NOT NULL"
                      " ORDER BY date DESC LIMIT 1").fetchone()
    if last:
        rows = db.execute("SELECT symbol, close, pred_close, actual_close, hit_dir,"
                          " hit_band FROM predictions WHERE date=? AND"
                          " actual_close IS NOT NULL ORDER BY symbol",
                          (last[0],)).fetchall()
        adates = set()
        for sym, close, pred, actual, hd, hb in rows:
            ad = db.execute("SELECT MIN(date) FROM klines WHERE symbol=? AND date>?",
                            (sym, last[0])).fetchone()[0]
            if ad:
                adates.add(ad)
        ad_s = "/".join(sorted(adates)) or "?"
        out += [f"## 预测复盘（{last[0]} 收盘作出 → {ad_s} 实际）", "",
                "| 标的 | 预测价 | 实际 | 误差 | 方向 | 区间 |",
                "| --- | --- | --- | --- | --- | --- |"]
        for sym, close, pred, actual, hd, hb in rows:
            ret = (actual / close - 1) * 100
            err = ((actual / pred - 1) * 100) if pred else None
            err_s = f"{err:+.2f}%" if err is not None else "-"
            out.append(f"| {WATCH[sym][1]} | {pred:.3f} | {actual:.3f}（{pct(ret)}）"
                       f" | {err_s} | {'✓' if hd else '✗'} |"
                       f" {'命中' if hb else '未中'} |")
        out.append("")

    out += ["---", ""]
    ts = common.token_stats(day=today)
    if ts["calls"]:
        out.append(f"*今日LLM用量：{ts['calls']} 次调用 · {ts['total']:,} tokens"
                   f"（输入 {ts['prompt']:,} / 输出 {ts['completion']:,}）*")
        out.append("")
    out += ["*模型：动量0.40 / RSI 0.15 / 量能0.10 / MA20偏离0.05 / 消息面0.30；"
            "看涨概率=50+18×score（截断40~60）；区间=昨收±1.5×ATR"
            "（历史覆盖率93%）；预测价=区间内按看涨概率定位。*"]
    path = os.path.join(common.REPORTS, f"etf-{today}.md")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(out) + "\n")
    return path


def weekly(db):
    # 最近5个交易日(以任意标的的最新K线日为准)
    dates = [r[0] for r in db.execute(
        "SELECT DISTINCT date FROM klines ORDER BY date DESC LIMIT 5").fetchall()][::-1]
    if len(dates) < 2:
        print("report: not enough kline history for weekly")
        return None
    day = dates[-1]
    out = [f"# ETF趋势雷达 周报 {dates[0]} ~ {day}", "", DISCLAIMER, ""]

    out += ["## 本周行情", ""]
    for code, name, _, _ in WATCHLIST:
        rows = db.execute("SELECT date,close FROM klines WHERE symbol=? AND date>=?"
                          " ORDER BY date", (code, dates[0])).fetchall()
        if len(rows) < 2:
            continue
        first, last = rows[0][1], rows[-1][1]
        wmin = min(r[1] for r in rows)
        wmax = max(r[1] for r in rows)
        out.append(f"- **{name}**（{code}）：周涨跌 **{pct((last/first-1)*100)}**"
                   f"（{first:.3f} → {last:.3f}），周内最低 {wmin:.3f} / 最高 {wmax:.3f}，"
                   f"本周{len(rows)}个交易日")
    out.append("")

    # 本周信号回顾
    out += [f"## 本周信号回顾（{dates[0]} 起）", ""]
    rows = db.execute("SELECT symbol, date, close, prob_up, signal, actual_close, hit_dir"
                      " FROM predictions WHERE date>=? ORDER BY date, symbol",
                      (dates[0],)).fetchall()
    cur = None
    for sym, d, close, prob, sig, actual, hd in rows:
        if d != cur:
            cur = d
            out.append(f"**{d}**")
        if actual is None:
            out.append(f"- {WATCH[sym][1]}：{sig}（看涨{prob}%）→ 待验证")
        else:
            ret = (actual / close - 1) * 100
            out.append(f"- {WATCH[sym][1]}：{sig}（看涨{prob}%）→ 实际 {pct(ret)} "
                       f"{'✓' if hd else '✗'}")
    out.append("")

    # 胜率追踪
    out += ["## 胜率追踪", ""]
    tot_n = tot_d = tot_b = 0
    for code, name, _, _ in WATCHLIST:
        h = db.execute("SELECT hit_dir, hit_band FROM predictions WHERE symbol=? AND"
                       " hit_dir IS NOT NULL ORDER BY date DESC LIMIT 20",
                       (code,)).fetchall()
        if not h:
            continue
        n = len(h)
        d = sum(r[0] for r in h) / n
        b = sum(r[1] for r in h) / n
        tot_n += n
        tot_d += sum(r[0] for r in h)
        tot_b += sum(r[1] for r in h)
        out.append(f"- {name}：近{n}次 方向胜率 {d*100:.0f}%、区间命中 {b*100:.0f}%")
    if tot_n:
        out.append(f"- **合计**：近{tot_n}次 方向胜率 {tot_d/tot_n*100:.0f}%、"
                   f"区间命中 {tot_b/tot_n*100:.0f}%")
    bt_path = os.path.join(common.STATE, "backtest.json")
    if os.path.exists(bt_path):
        bt = json.load(open(bt_path, encoding="utf-8"))
        o = bt.get("overall") or {}
        if o.get("n"):
            out.append(f"- 历史回测（纯技术面，n={o['n']}）：方向 {o['dir']*100:.0f}%、"
                       f"区间 {o['band']*100:.0f}%")
    out.append("")

    # 下周参考区间(最新收盘 ± 2.5×ATR)
    out += ["## 下周波动参考（最新收盘 ± 2.5×ATR，非目标价）", ""]
    for code, name, _, _ in WATCHLIST:
        rows = db.execute("SELECT close FROM klines WHERE symbol=? ORDER BY date DESC LIMIT 30",
                          (code,)).fetchall()
        if len(rows) < 15:
            continue
        closes = [r[0] for r in rows][::-1]
        # 用最近预测里存的ATR特征, 没有则粗算
        feat = db.execute("SELECT features FROM predictions WHERE symbol=?"
                          " ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        if feat:
            try:
                atr_pct = json.loads(feat[0]).get("atr_pct")
            except Exception:
                atr_pct = None
        else:
            atr_pct = None
        if not atr_pct:
            atr_pct = (max(closes) - min(closes)) / closes[-1] / 5
        c = closes[-1]
        out.append(f"- {name}（{code}）：{c*(1-2.5*atr_pct):.3f} ~ {c*(1+2.5*atr_pct):.3f}"
                   f"（现价 {c:.3f}，ATR {atr_pct*100:.2f}%）")
    out.append("")

    ts = common.token_stats(since=dates[0], upto=day)
    if ts["calls"]:
        out += ["## 本周LLM用量", "",
                f"- 调用 {ts['calls']} 次 · 共 {ts['total']:,} tokens"
                f"（输入 {ts['prompt']:,} / 输出 {ts['completion']:,}）", ""]

    out += ["---", "", "*周报为日度信号的聚合统计，同样不构成投资建议。*"]
    path = os.path.join(common.REPORTS, f"weekly-{day}.md")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(out) + "\n")
    return path


def main():
    common.ensure_dirs()
    db = common.market_db()
    made = []
    p = daily(db)
    if p:
        made.append(p)
    if "--weekly" in sys.argv:
        p = weekly(db)
        if p:
            made.append(p)
    db.close()
    print("report: " + ("; ".join(made) if made else "nothing generated"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
