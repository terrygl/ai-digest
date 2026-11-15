#!/usr/bin/env python3
"""etf-radar analyzer — 技术指标 + LLM消息面情绪 + 综合信号。

流程:
  1) 从 market.db 读各标的K线; 全部无当日新K线(节假日)则跳过
  2) 回填历史预测的实际结果(hit_dir/hit_band) —— 胜率可问责
  3) 计算技术特征(动量/RSI/量比/MA20偏离/ATR/连涨跌/20日位置)
  4) LLM对当日新闻逐条打分(-1~+1)并按标的聚合(无LLM_API_KEY则降级纯技术面)
  5) 加权得 score∈[-1,1] → prob_up(40~60) + 明日区间(收盘±1.5×ATR) + 信号
  6) 写 data/analysis-YYYYMMDD.json + predictions 表

信号权重(可调): 动量0.40 RSI0.15 量能0.10 偏离0.05 消息面0.30; 无消息面时按0.70归一。
用法: python3 analyze.py [--backtest]
"""
import difflib
import json
import math
import os
import re
import sys
import time
import urllib.request

import common
from common import WATCHLIST, WATCH, bj_today, load_cfg

# ---- 信号权重 ----
W_MOM5, W_MOM20, W_RSI, W_VOL, W_DEV, W_NEWS = 0.25, 0.15, 0.15, 0.10, 0.05, 0.30
W_TECH = W_MOM5 + W_MOM20 + W_RSI + W_VOL + W_DEV  # 0.70
SIGNAL_TH = 0.12  # |score| 超过此值才给方向


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def load_klines(db):
    out = {}
    for code, _, _, _ in WATCHLIST:
        out[code] = db.execute(
            "SELECT date,open,close,high,low,volume,amount FROM klines"
            " WHERE symbol=? ORDER BY date", (code,)).fetchall()
    return out


def features(rows):
    """rows[-1] 为最新交易日。返回特征字典或 None(历史不足)。"""
    if len(rows) < 25:
        return None
    c = [r[2] for r in rows]
    v = [r[5] for r in rows]
    h, l = [r[3] for r in rows], [r[4] for r in rows]
    day_ret = c[-1] / c[-2] - 1
    mom5 = c[-1] / c[-6] - 1
    mom20 = c[-1] / c[-21] - 1
    # RSI14 (Wilder)
    gains, losses = [], []
    for i in range(len(c) - 14, len(c)):
        ch = c[i] - c[i - 1]
        gains.append(max(ch, 0))
        losses.append(max(-ch, 0))
    ag, al = sum(gains) / 14, sum(losses) / 14
    rsi = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    # ATR14 (Wilder平滑的简化: 14日TR均值)
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
           for i in range(len(c) - 14, len(c))]
    atr_pct = sum(trs) / 14 / c[-1]
    vol_ratio = (sum(v[-5:]) / 5) / max(sum(v[-20:]) / 20, 1e-9)
    ma20_dev = c[-1] / (sum(c[-20:]) / 20) - 1
    # 连涨/连跌天数
    streak = 0
    for i in range(len(c) - 1, 0, -1):
        d = 1 if c[i] > c[i - 1] else (-1 if c[i] < c[i - 1] else 0)
        if d == 0 or (streak != 0 and d != (1 if streak > 0 else -1)):
            break
        streak += d
    w20 = c[-20:]
    pos20 = (c[-1] - min(w20)) / max(max(w20) - min(w20), 1e-9)
    return {"day_ret": day_ret, "mom5": mom5, "mom20": mom20, "rsi14": rsi,
            "atr_pct": atr_pct, "vol_ratio": vol_ratio, "ma20_dev": ma20_dev,
            "streak": streak, "pos20": pos20}


def tech_score(f):
    """技术面得分(无消息面), 各分量∈[-1,1]加权和。

    返回 (score, 分量明细, 驱动因子[(贡献, 中文说明)]), 贡献=权重×分量, 用于归因排序。
    """
    c_mom5 = math.tanh(f["mom5"] * 12)
    c_mom20 = math.tanh(f["mom20"] * 6)
    c_rsi = clip((f["rsi14"] - 50) / 25, -1, 1)
    rsi_note = ""
    if f["rsi14"] >= 78:
        c_rsi -= 0.4    # 超买: 动量衰减
        rsi_note = "（超买）"
    elif f["rsi14"] <= 22:
        c_rsi += 0.4    # 超卖: 反弹动能
        rsi_note = "（超卖）"
    c_vol = (1 if f["day_ret"] >= 0 else -1) * clip(f["vol_ratio"] - 1.2, 0, 1)
    c_dev = -clip(f["ma20_dev"] * 8, -1, 1)  # 过度偏离温和看回归
    score = W_MOM5 * c_mom5 + W_MOM20 * c_mom20 + W_RSI * c_rsi \
        + W_VOL * c_vol + W_DEV * c_dev
    comps = {"mom5": round(c_mom5, 3), "mom20": round(c_mom20, 3),
             "rsi": round(c_rsi, 3), "vol": round(c_vol, 3), "dev": round(c_dev, 3)}
    p = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    drivers = [
        (W_MOM5 * c_mom5, f"近5日{p(f['mom5'])}"),
        (W_MOM20 * c_mom20, f"近20日{p(f['mom20'])}"),
        (W_RSI * c_rsi, f"RSI {f['rsi14']:.0f}{rsi_note}"),
    ]
    if c_vol:
        drivers.append((W_VOL * c_vol, f"量比{f['vol_ratio']:.2f}，"
                        f"放量{'助涨' if f['day_ret'] >= 0 else '助跌'}"))
    drivers.append((W_DEV * c_dev, f"偏离MA20 {p(f['ma20_dev'])}"))
    if abs(f["streak"]) >= 3:
        drivers.append((0.01 if f["streak"] > 0 else -0.01,
                        f"连涨{f['streak']}天" if f["streak"] > 0
                        else f"连跌{-f['streak']}天"))
    return score, comps, drivers


# ---------- LLM 消息面情绪 ----------
SRC_LABEL = {"em-ann": "公告", "em-news": "个股新闻", "gnews": "搜索新闻"}
SRC_ORDER = {"em-ann": 0, "em-news": 1, "gnews": 2}


def _llm_score_symbol(code, items, cfg):
    """单个标的一次独立LLM调用(条目按 公告->个股->搜索 排序, 最多25条)。

    拆单标的的原因: 5个标的~100+条合一次调用时, glm-4-flash 会漏答后续标的。
    返回 {"scores":[float], "view":str, "items":[{title,link,score,tag,src}]}。
    """
    refs, blocks = [], []
    name = WATCH[code][1]
    for i, it in enumerate(sorted(items, key=lambda x: SRC_ORDER.get(x.get("src"), 3))[:25], 1):
        refs.append((i, it))
        label = SRC_LABEL.get(it.get("src"), "新闻")
        blocks.append(f"[{i}]（{label}）标题：{it['title']}\n"
                      f"    摘要：{it['desc'][:150] or '（无）'}")

    body = json.dumps({
        "model": cfg.get("LLM_MODEL", "glm-4-flash"),
        "temperature": 0.2,
        "max_tokens": 6000,  # glm-5.3是推理模型: 思考tokens计入completion, 预算须覆盖思考+输出
        "messages": [
            {"role": "system", "content":
                "你是量化团队的消息面分析师。只输出JSON，不要任何其他文字。"},
            {"role": "user", "content":
                f"以下是{bj_today()}【{name}（{code}）】的当日信息（编号已给, 含多类来源）。"
                f"判断每条对{name}下一交易日涨跌方向的影响，输出严格JSON：\n"
                '{"scores":[{"n":1,"score":0.6,"tag":"利好"},...],'
                '"view":"一句话(30字内)多空观点"}\n'
                "规则：score取{-1,-0.6,-0.3,0,0.3,0.6,1}，利好为正利空为负；"
                "（公告）条目来自交易所/公司公告，信息最硬，重大公告(业绩/订单/分红/"
                "增减持)应给足幅度；与该标的无关的条目给0；"
                "tag∈{利好,利空,中性,无关}；只基于给出的标题与摘要，不虚构。\n\n"
                + "\n".join(blocks)}]}).encode()
    url = cfg.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/") \
        + "/chat/completions"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.get('LLM_API_KEY', '')}"})
    with urllib.request.urlopen(req, timeout=240) as r:  # 推理模型响应更慢
        data = json.loads(r.read())
    text = data["choices"][0]["message"]["content"].strip()
    if not text:
        raise ValueError(f"empty content (finish={data['choices'][0].get('finish_reason')})")
    m = re.search(r"\{.*\}", text, re.S)  # 容错: 剥掉可能的```json围栏
    obj = json.loads(m.group(0))
    common.log_tokens(f"sentiment:{code}", data.get("usage") or {})
    by_i = {}
    for s in obj.get("scores", []):
        try:
            by_i[int(s["n"])] = (clip(float(s.get("score", 0)), -1, 1), str(s.get("tag", "")))
        except (KeyError, TypeError, ValueError):
            continue

    d = {"scores": [], "items": [], "view": str(obj.get("view") or "")[:80]}
    for i, it in refs:
        if i in by_i:
            sc, tag = by_i[i]
            d["scores"].append(sc)
            d["items"].append({"title": it["title"], "link": it["link"],
                               "score": sc, "tag": tag, "src": it.get("src", "")})
    return d


def call_llm_scores(news_by_code, cfg):
    """逐标的调用, 单标的失败不影响其他(该标的降级纯技术面)。"""
    out = {}
    for code, items in news_by_code.items():
        if not items:
            continue
        try:
            out[code] = _llm_score_symbol(code, items, cfg)
        except Exception as ex:
            print(f"llm sentiment failed for {code}: {str(ex)[:120]}")
        time.sleep(1)
    return out


def agg_sent(scores_list):
    """均值情绪; 条数<3按条数收缩(证据不足不加满权重)。"""
    if not scores_list:
        return None
    mean = sum(scores_list) / len(scores_list)
    return mean * min(len(scores_list) / 3, 1)


def load_news(today):
    p = os.path.join(common.DATA, f"news-{today}.jsonl")
    if not os.path.exists(p):
        return {}
    by_code = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                it = json.loads(line)
            except Exception:
                continue
            if it.get("symbol") in WATCH:
                by_code.setdefault(it["symbol"], []).append(it)
    return by_code


# ---------- 预测回填与胜率 ----------
def backfill(db, klines):
    """给尚无实际值的预测补 actual_close/hit_dir/hit_band。"""
    rows = db.execute("SELECT symbol, date, close, prob_up, band_low, band_high"
                      " FROM predictions WHERE actual_close IS NULL").fetchall()
    fixed = 0
    for sym, d, close, prob, lo, hi in rows:
        nxt = [r for r in klines.get(sym, []) if r[0] > d]
        if not nxt:
            continue
        actual = nxt[0][2]
        hit_dir = int((prob > 50) == (actual > close))
        hit_band = int(lo <= actual <= hi)
        db.execute("UPDATE predictions SET actual_close=?, hit_dir=?, hit_band=?"
                   " WHERE symbol=? AND date=?", (actual, hit_dir, hit_band, sym, d))
        fixed += 1
    if fixed:
        db.commit()
    return fixed


def hit_stats(db, code, limit=20):
    rows = db.execute("SELECT hit_dir, hit_band FROM predictions WHERE symbol=?"
                      " AND hit_dir IS NOT NULL ORDER BY date DESC LIMIT ?",
                      (code, limit)).fetchall()
    if not rows:
        return {"n": 0, "dir": None, "band": None}
    n = len(rows)
    return {"n": n, "dir": round(sum(r[0] for r in rows) / n, 3),
            "band": round(sum(r[1] for r in rows) / n, 3)}


# ---------- 回测(纯技术面, walk-forward) ----------
def backtest(klines):
    result, tot_n, tot_dir, tot_band = {}, 0, 0, 0
    for code, rows in klines.items():
        n = d_hit = b_hit = 0
        for t in range(25, len(rows) - 1):
            f = features(rows[:t + 1])
            if not f:
                continue
            s, _, _ = tech_score(f)
            close = rows[t][2]
            lo, hi = close * (1 - 1.5 * f["atr_pct"]), close * (1 + 1.5 * f["atr_pct"])
            actual = rows[t + 1][2]
            n += 1
            d_hit += int((s > 0) == (actual > close))
            b_hit += int(lo <= actual <= hi)
        if n:
            result[code] = {"n": n, "dir": round(d_hit / n, 3), "band": round(b_hit / n, 3)}
            tot_n += n
            tot_dir += d_hit
            tot_band += b_hit
    overall = ({"n": tot_n, "dir": round(tot_dir / tot_n, 3), "band": round(tot_band / tot_n, 3)}
               if tot_n else {"n": 0, "dir": None, "band": None})
    return {"overall": overall, "symbols": result}


# ---------- 主流程 ----------
def main():
    common.ensure_dirs()
    cfg = load_cfg()
    db = common.market_db()
    klines = load_klines(db)
    today = bj_today()

    if "--backtest" in sys.argv:
        bt = backtest(klines)
        bt["generated"] = today
        bt["note"] = "纯技术面规则的历史walk-forward回测(不含消息面), 仅供参考"
        with open(os.path.join(common.STATE, "backtest.json"), "w", encoding="utf-8") as f:
            json.dump(bt, f, ensure_ascii=False, indent=1)
        print(json.dumps(bt, ensure_ascii=False, indent=1))
        db.close()
        return 0

    fixed = backfill(db, klines)

    # 晨报模式: 分析对象=最新收盘日D(上一交易日), 预测目标=下一交易日。
    # 逐标的幂等: 已有当日预测的标的跳过 —— 支持中途新增标的时只为新标的补分析。
    closes = [rows[-1][0] for rows in klines.values() if rows]
    if not closes:
        print("no klines at all, skip")
        db.close()
        return 0
    D = max(closes)
    fresh = [c for c, rows in klines.items()
             if rows and rows[-1][0] == D
             and not db.execute("SELECT 1 FROM predictions WHERE symbol=? AND date=?",
                                (c, D)).fetchone()]
    if not fresh:
        print(f"close-date {D} all analyzed, skip; backfilled={fixed}")
        db.close()
        return 0

    # 消息面用"今天"(发送日)的新闻文件 —— 晨报价值就在隔夜消息
    news = load_news(today)
    llm, llm_note = {}, ""
    if cfg.get("LLM_API_KEY") and news:
        llm = call_llm_scores(news, cfg)
        wanted = [c for c, its in news.items() if its]
        missed = [c for c in wanted if c not in llm]
        if wanted and len(missed) == len(wanted):
            llm_note = "LLM情绪评分全部失败，本次纯技术面"
        elif missed:
            llm_note = "部分标的情绪评分失败(转纯技术面): " + ",".join(missed)
    if not cfg.get("LLM_API_KEY"):
        llm_note = "未配置LLM_API_KEY，纯技术面"

    analysis = {"date": D, "asof": today,
                "generated_at": common.bj_now().strftime("%F %T"),
                "symbols": {}, "note": llm_note}
    for code in fresh:
        rows = klines[code]
        f = features(rows)
        if not f:
            continue
        name = WATCH[code][1]
        close, prev = rows[-1][2], rows[-2][2]
        tech, comps, drivers = tech_score(f)
        sent, sent_n, scored_items, view = None, 0, [], ""
        if code in llm:
            d = llm[code]
            sent = agg_sent(d["scores"])
            sent_n = len(d["scores"])
            view = d.get("view", "")
            scored_items = d["items"]
        score = tech / W_TECH if sent is None else tech + W_NEWS * sent
        prob_up = clip(50 + 18 * score, 40, 60)
        lo, hi = close * (1 - 1.5 * f["atr_pct"]), close * (1 + 1.5 * f["atr_pct"])
        signal = "偏多" if score >= SIGNAL_TH else ("偏空" if score <= -SIGNAL_TH else "中性")
        # 预测价: 区间内按看涨概率定位(lo+p×(hi-lo))。
        # 不用"score×ATR×k": 回测最小二乘校准出k≈0.02, 即历史上涨跌幅度几乎不可预测,
        # 强行线性映射只会输出≈现价; 概率定位与展示的区间/概率自洽, 不引入虚假精度。
        pred_close = lo + (prob_up / 100.0) * (hi - lo)
        pred_ret = pred_close / close - 1
        # 归因: 技术面驱动(按贡献排序) + 高分消息(同质标题去重, 如多条ETF资金流报道)
        drivers.sort(key=lambda d: -abs(d[0]))
        up = [t for c, t in drivers if c >= 0.02 and t]
        down = [t for c, t in drivers if c <= -0.02 and t]
        picked_titles = []
        for it in sorted(scored_items, key=lambda x: -abs(x["score"])):
            if not it["score"] or len(picked_titles) >= 4:
                continue
            if any(difflib.SequenceMatcher(None, it["title"], t).ratio() > 0.55
                   for t in picked_titles):
                continue
            picked_titles.append(it["title"])
            mark = "〔公告〕" if it.get("src") == "em-ann" else ""
            txt = f"{mark}「{it['title'][:26]}」（{it['tag']}{it['score']:+.1f}）"
            (up if it["score"] > 0 else down).append(txt)
        reasons = {"up": up[:3], "down": down[:3]}
        analysis["symbols"][code] = {
            "name": name, "close": close, "prev_close": prev,
            "pct": round((close / prev - 1) * 100, 2),
            "signal": signal, "score": round(score, 3), "prob_up": round(prob_up, 1),
            "pred_close": round(pred_close, 3), "pred_ret": round(pred_ret * 100, 2),
            "band_low": round(lo, 3), "band_high": round(hi, 3),
            "features": {k: round(v, 4) if isinstance(v, float) else v
                         for k, v in f.items()},
            "tech_comps": comps,
            "news": {"sent": None if sent is None else round(sent, 3),
                     "n": sent_n, "items": scored_items[:6]},
            "llm_view": view,
            "reasons": reasons,
            "hit20": hit_stats(db, code),
        }
        db.execute("INSERT OR REPLACE INTO predictions"
                   "(symbol,date,close,prob_up,band_low,band_high,signal,score,"
                   "sent,sent_n,features,pred_close) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (code, D, close, round(prob_up, 1), lo, hi, signal, round(score, 3),
                    sent, sent_n, json.dumps(analysis["symbols"][code]["features"],
                                             ensure_ascii=False), round(pred_close, 3)))
    db.commit()
    db.close()

    out = os.path.join(common.DATA, f"analysis-{D}.json")
    # 与已有分析合并(本次可能只补了新增标的), 并按WATCHLIST顺序输出
    if os.path.exists(out):
        try:
            prev = (json.load(open(out, encoding="utf-8")) or {}).get("symbols") or {}
            prev.update(analysis["symbols"])
            order = {c: i for i, (c, _, _, _) in enumerate(WATCHLIST)}
            analysis["symbols"] = dict(sorted(prev.items(),
                                              key=lambda kv: order.get(kv[0], 99)))
        except Exception:
            pass
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(analysis, fp, ensure_ascii=False, indent=1)
    print(json.dumps({"close_date": D, "asof": today, "symbols": list(analysis["symbols"]),
                      "backfilled": fixed, "analysis": out, "note": llm_note},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
