#!/usr/bin/env python3
"""etf-radar collector — 腾讯/新浪日线行情 + 东财新闻公告 + Google News RSS 消息面。

  行情: 腾讯fqkline(主, 前复权) / 新浪日K(兜底, 不复权) -> data/market.db (symbol+date幂等)
        (原东财push2his因频繁限流于2026-11-13弃用; 数值/单位与东财一致: 手/前复权)
  消息: 东财公告+个股新闻(np-*) 与 news.google.com RSS -> data/news-YYYYMMDD.jsonl

非交易日/节假日: 库里没有当日K线属正常, analyze 阶段自行判断跳过。
全部标的行情抓取失败才返回非零(触发 run.sh 告警); 新闻失败只记状态。
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import timedelta

import common
from common import WATCHLIST, bj_now, bj_today, fetch

TX_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{lmt},qfq"
SINA_KLINE = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
              "?symbol={sym}&scale=240&ma=no&datalen={lmt}")
EM_NEWS = ("https://np-listapi.eastmoney.com/comm/web/getListInfo?"
           "client=web&mTypeAndCode={secid}&type=1&pageSize=12&req_trace=1")
EM_ANN = ("https://np-anotice-stock.eastmoney.com/api/security/ann?"
          "sr=-1&page_size=6&page_index=1&ann_type=A&client_source=web"
          "&stock_list={code}&f_node=0&s_node=0")


def tx_symbol(secid):
    """1.600150 -> sh600150; 0.159699 -> sz159699"""
    return ("sh" if secid.startswith("1.") else "sz") + secid.split(".", 1)[1]


def _klines_tx(secid, lmt):
    sym = tx_symbol(secid)
    data = json.loads(fetch(TX_KLINE.format(sym=sym, lmt=lmt), timeout=20))
    d = (data.get("data") or {}).get(sym) or {}
    rows = d.get("qfqday") or d.get("day") or []
    out = []
    for r in rows:
        if len(r) < 6:
            continue
        # 腾讯字段序: 日期,开,收,高,低,量(手) [,额 —— 个股行尾常带一个dict, 需过滤]
        amt = 0.0
        if len(r) > 6 and isinstance(r[6], str) and r[6]:
            try:
                amt = float(r[6])
            except ValueError:
                pass
        out.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                    float(r[5]), amt))
    return out


def _klines_sina(secid, lmt):
    sym = tx_symbol(secid)
    text = fetch(SINA_KLINE.format(sym=sym, lmt=lmt), timeout=20).decode("utf-8", "ignore")
    out = []
    for it in (json.loads(text) if text.startswith("[") else []):
        try:
            # 新浪: day/open/high/low/close/volume(股) —— 不复权, 仅兜底; 量/100对齐到手
            out.append((it["day"], float(it["open"]), float(it["close"]),
                        float(it["high"]), float(it["low"]), float(it["volume"]) / 100, 0.0))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_klines(secid, lmt=260):
    """日K线 -> [(date, open, close, high, low, volume, amount)]; 腾讯主源, 新浪兜底。"""
    try:
        rows = _klines_tx(secid, lmt)
        if rows:
            return rows
        print(f"kline tx empty for {secid}, fallback sina")
    except Exception as ex:
        print(f"kline tx failed {secid}: {str(ex)[:80]}, fallback sina")
    return _klines_sina(secid, lmt)


def collect_market(db):
    status, fresh = {}, 0
    today = bj_today()
    for code, name, secid, _ in WATCHLIST:
        try:
            rows = fetch_klines(secid)
            new = 0
            for r in rows:
                cur = db.execute(
                    "INSERT OR IGNORE INTO klines VALUES(?,?,?,?,?,?,?,?)",
                    (code,) + r)
                new += cur.rowcount
                if r[0] == today and cur.rowcount:
                    fresh += 1
            status[code] = {"ok": 1, "rows": len(rows), "new": new}
        except Exception as ex:
            status[code] = {"ok": 0, "rows": 0, "err": str(ex)[:120]}
        time.sleep(1)
    db.commit()
    return status, fresh


# ---------- 消息面(沿用 ai-digest 的 RSS 解析/幂等合并模式) ----------
def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(body):
    root = ET.fromstring(body)
    items = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        d = {}
        for c in el:
            cn = c.tag.rsplit("}", 1)[-1]
            if cn == "link":
                d.setdefault("link", (c.get("href") or c.text or "").strip())
            elif cn == "title":
                d["title"] = strip_html(c.text)
            elif cn in ("description", "summary", "content"):
                d.setdefault("desc", strip_html(c.text))
            elif cn in ("pubDate", "published", "updated"):
                d.setdefault("ts", (c.text or "").strip())
        if d.get("title") and d.get("link"):
            items.append(d)
    return items


def gnews_url(query):
    q = urllib.parse.quote(f"{query} when:2d")
    return (f"https://news.google.com/rss/search?q={q}"
            "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans")


def _fresh(item, days):
    """按 ts 的日期部分过滤, 只留 days 天内(含今天)的条目。"""
    d = (item.get("ts") or "")[:10]
    return d >= (bj_now().date() - timedelta(days=days)).isoformat()


def fetch_em_news(secid, code):
    """东财个股/基金新闻列表。返回近2天条目。"""
    data = json.loads(fetch(EM_NEWS.format(secid=secid), timeout=15))
    out = []
    for it in (data.get("data") or {}).get("list") or []:
        link = (it.get("Art_Url") or it.get("Art_OriginUrl") or "").replace(
            "http://", "https://")
        title = strip_html(it.get("Art_Title") or "")
        if not (link and title):
            continue
        item = {"symbol": code, "src": "em-news", "title": title[:200],
                "link": link, "desc": "", "ts": (it.get("Art_ShowTime") or "")[:16]}
        if _fresh(item, 2):
            out.append(item)
    return out


def fetch_em_ann(code):
    """东财公告(ann_type=A 仅个股; ETF/LOF无上市公司公告)。返回近3天条目。"""
    data = json.loads(fetch(EM_ANN.format(code=code), timeout=15))
    out = []
    for it in (data.get("data") or {}).get("list") or []:
        art = it.get("art_code") or ""
        title = strip_html(it.get("title") or "")
        if not (art and title):
            continue
        cols = ",".join(c.get("column_name", "") for c in (it.get("columns") or []))
        item = {"symbol": code, "src": "em-ann", "title": title[:200],
                "link": f"https://data.eastmoney.com/notices/detail/{code}/{art}.html",
                "desc": cols, "ts": (it.get("notice_date") or "")[:16]}
        if _fresh(item, 3):
            out.append(item)
    return out


def collect_news():
    today = bj_today()
    out_path = os.path.join(common.DATA, f"news-{today}.jsonl")
    seen = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["link"])
                except Exception:
                    pass
    new_items, status = [], {}
    for code, name, secid, query in WATCHLIST:
        per = []  # [(源名, 条目列表)], 公告在前(最硬的信息)
        # 1) 东财公告 —— 仅个股(沪60/深00开头; ETF/LOF无A类公告)
        if code[:2] in ("60", "00"):
            try:
                per.append(("ann", fetch_em_ann(code)))
                status[f"{code}:ann"] = {"ok": 1, "new": len(per[-1][1]), "err": ""}
            except Exception as ex:
                status[f"{code}:ann"] = {"ok": 0, "new": 0, "err": str(ex)[:120]}
        # 2) 东财个股新闻列表
        try:
            per.append(("em", fetch_em_news(secid, code)))
            status[f"{code}:em"] = {"ok": 1, "new": len(per[-1][1]), "err": ""}
        except Exception as ex:
            status[f"{code}:em"] = {"ok": 0, "new": 0, "err": str(ex)[:120]}
        # 3) Google News 聚合(近2天)
        try:
            entries = parse_feed(fetch(gnews_url(query)))[:15]
            g_items = []
            for e in entries:
                # Google News 链接是跳转壳, 标题里「 - 媒体名」去掉留正文
                title = re.sub(r" - [^-]{2,20}$", "", e["title"])
                g_items.append({"symbol": code, "src": "gnews",
                                "title": title[:200], "link": e["link"],
                                "desc": e.get("desc", "")[:300], "ts": e.get("ts", "")})
            per.append(("gnews", g_items))
            status[f"{code}:gnews"] = {"ok": 1, "new": len(g_items), "err": ""}
        except Exception as ex:
            status[f"{code}:gnews"] = {"ok": 0, "new": 0, "err": str(ex)[:120]}
        time.sleep(1)
        for _, items in per:
            for it in items:
                if it["link"] not in seen:
                    new_items.append(it)
                    seen.add(it["link"])
    with open(out_path, "a", encoding="utf-8") as f:
        for it in new_items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return status, out_path


def main():
    common.ensure_dirs()
    today = bj_today()
    db = common.market_db()
    mk_status, fresh = collect_market(db)
    nw_status, news_path = collect_news()
    db.close()

    mk_fail = [c for c, s in mk_status.items() if not s["ok"]]
    total_news = sum(s["new"] for s in nw_status.values())
    print(json.dumps({"day": today, "fresh_today": fresh,
                      "market": mk_status, "news": nw_status,
                      "news_file": news_path}, ensure_ascii=False, indent=1))
    if len(mk_fail) == len(WATCHLIST):
        print("all market fetch failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
