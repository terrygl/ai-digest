#!/usr/bin/env python3
"""token台账：记录每次LLM调用消耗，支持按日/按周统计。

用法:
  python3 tokens.py            # 今日各任务消耗
  python3 tokens.py day 2026-11-12
  python3 tokens.py week       # 近7天逐日+总计（周报用）
"""
import os
import sqlite3
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "tokens.db")


def _now():
    return datetime.utcnow() + timedelta(hours=8)


def log(task, model, pt, ct):
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS tokens("
              "day TEXT, task TEXT, model TEXT, pt INT, ct INT, ts TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tok_day ON tokens(day)")
    n = _now()
    c.execute("INSERT INTO tokens VALUES(?,?,?,?,?,?)",
              (n.date().isoformat(), task, model, pt, ct, n.strftime("%T")))
    c.commit()
    c.close()


def day_totals(day=None):
    day = day or _now().date().isoformat()
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT task, model, SUM(pt), SUM(ct) FROM tokens "
                     "WHERE day=? GROUP BY task", (day,)).fetchall()
    c.close()
    return rows


def week_totals():
    start = (_now() - timedelta(days=6)).date().isoformat()
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT day, SUM(pt), SUM(ct) FROM tokens "
                     "WHERE day>=? GROUP BY day ORDER BY day", (start,)).fetchall()
    tot = c.execute("SELECT SUM(pt), SUM(ct), COUNT(*) FROM tokens WHERE day>=?",
                    (start,)).fetchone()
    c.close()
    return rows, tot


def fmt_day(rows):
    if not rows:
        return "今日无LLM消耗记录"
    out = []
    for task, model, pt, ct in rows:
        out.append(f"{task}({model}): 输入{pt} 输出{ct} 合计{(pt or 0)+(ct or 0)}")
    return "\n".join(out)


def fmt_week():
    rows, tot = week_totals()
    if not rows:
        return "近7天无LLM消耗记录"
    lines = [f"{d}: 输入{p} 输出{c} 合计{(p or 0)+(c or 0)}" for d, p, c in rows]
    tp, tc, n = tot
    lines.append(f"— 7天合计: 输入{tp or 0} 输出{tc or 0} 总计{(tp or 0)+(tc or 0)} tokens（{n}次调用）")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] == "week":
        print(fmt_week())
    elif a and a[0] == "day" and len(a) > 1:
        print(fmt_day(day_totals(a[1])))
    else:
        print(fmt_day(day_totals()))
