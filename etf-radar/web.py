#!/usr/bin/env python3
"""etf-radar web — 口令保护的仪表盘 (默认 127.0.0.1:8891, Caddy 反代 etf.example.com)。

  WEB_BIND/WEB_PORT 环境变量覆盖(config.env 里的值由 systemd 注入或默认)。
  口令: `python3 web.py init <口令>` 写入 web_config.json(secret/salt/hash, 600)
  安全: PBKDF2 / HMAC签名cookie / 登录限速5次每10分钟 / 爆破推飞书 / 输出全转义
  数据: data/analysis-*.json(最新) + data/market.db + state/backtest.json + reports/

mailer.py 复用本文件的 md2html / send_mail。
"""
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import smtplib
import sys
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import common
from common import WATCH, load_cfg

CFG_PATH = os.path.join(common.BASE, "web_config.json")
PAGE = re.compile(r"^(etf|weekly)-[\w.-]+\.md$")
SESSION_TTL = 30 * 86400
MAX_BODY = 64 * 1024

CSS = """
:root{--ink:#26272b;--paper:#f7f6f3;--card:#fff;--line:#e6e3dd;--accent:#0e7490;
      --muted:#8b8a86;--dark:#1f2a30;--up:#c0392b;--dn:#1e8449;--gold:#b4531f}
*{box-sizing:border-box}
body{margin:0;font:16px/1.7 -apple-system,"Segoe UI",Roboto,"PingFang SC",
     "Hiragino Sans GB","Microsoft YaHei",sans-serif;color:var(--ink);background:var(--paper)}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
.navbar{position:sticky;top:0;z-index:20;background:var(--dark);color:#fff;display:flex;
     align-items:center;justify-content:space-between;gap:1rem;padding:.65rem 1.3rem;
     box-shadow:0 1px 10px rgba(0,0,0,.18)}
.navbar .brand{color:#fff;font-weight:800;letter-spacing:.08em;font-size:1.02rem;
     border-bottom:2px solid var(--accent);padding-bottom:.15rem}
.navbar nav{display:flex;gap:1.25rem;align-items:center;font-size:.93rem}
.navbar nav a{color:#d9dce2}
main{max-width:1080px;margin:1.6rem auto 3rem;padding:0 1rem}
.hero{background:linear-gradient(180deg,#22333c,var(--dark));color:#fff;border-radius:12px;
     padding:1.5rem 1.6rem;margin-bottom:1.4rem}
.hero h1{margin:0;font-size:1.5rem;letter-spacing:.06em}
.hero .meta{color:#aab4bb;font-size:.88rem;margin-top:.4rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:1rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
     padding:1.05rem 1.2rem;transition:box-shadow .15s}
.card:hover{box-shadow:0 5px 16px rgba(0,0,0,.06)}
.card h3{margin:0 0 .55rem;font-size:1.05rem;display:flex;justify-content:space-between;
     align-items:baseline;gap:.5rem}
.code{font-family:ui-monospace,Consolas,monospace;font-size:.78rem;color:var(--muted)}
.price{font-size:1.6rem;font-weight:800;font-variant-numeric:tabular-nums}
.up{color:var(--up)} .dn{color:var(--dn)} .flat{color:var(--muted)}
.badge{display:inline-block;border-radius:99px;padding:.14rem .8rem;font-size:.85rem;
     font-weight:700;color:#fff;vertical-align:middle}
.b-long{background:var(--up)} .b-short{background:var(--dn)} .b-mid{background:#7a828a}
.chip{display:inline-block;border:1px solid var(--line);border-radius:99px;
     padding:.05rem .6rem;font-size:.8rem;color:var(--muted);margin-right:.3rem}
.chip.ann{color:var(--gold);border-color:var(--gold)}
.kv{display:flex;justify-content:space-between;font-size:.9rem;padding:.12rem 0;
     border-bottom:1px dashed #efece6}
.kv:last-child{border-bottom:none}
.kv b{font-variant-numeric:tabular-nums}
svg.spark{display:block;margin-top:.55rem}
.twrap{overflow-x:auto}
.why{font-size:.84rem;line-height:1.55;margin-top:.35rem;color:#4a4b4f}
.why .up,.why .dn{font-weight:700}
.whybox{margin-top:.5rem;border-top:1px dashed #efece6;padding-top:.45rem}
section h2{font-size:1.15rem;border-left:4px solid var(--accent);padding-left:.6rem;
     margin:2rem 0 .8rem}
table.t{width:100%;border-collapse:collapse;background:var(--card);
     border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:.92rem}
table.t td,table.t th{border-bottom:1px solid var(--line);padding:.5rem .6rem;text-align:left}
table.t th{background:#f0eee9;font-size:.85rem;color:var(--muted)}
table.t tr:last-child td{border-bottom:none}
.news{background:var(--card);border:1px solid var(--line);border-radius:12px;
     padding:.9rem 1.2rem;margin-bottom:.8rem}
.news li{margin:.3rem 0}
.tag-pos{color:var(--up);font-weight:700} .tag-neg{color:var(--dn);font-weight:700}
.auth-card{max-width:420px;margin:3rem auto}
.banner{border-radius:10px;padding:.8rem 1.1rem;margin-bottom:1rem;font-size:.95rem}
.banner-bad{background:#fbeeee;border:1px solid #f0d4d4}
input[type=password]{width:100%;padding:.62rem .8rem;border:1px solid var(--line);
     border-radius:8px;font-size:15px}
.btn{display:inline-block;background:var(--dark);color:#fff;border:none;border-radius:99px;
     padding:.55rem 1.5rem;font-size:.95rem;cursor:pointer}
article.md{background:var(--card);border:1px solid var(--line);border-radius:12px;
     padding:1.6rem 1.9rem;max-width:860px;margin:0 auto}
article.md h1{font-size:1.5rem} article.md h2{font-size:1.2rem}
article.md h1,article.md h2,article.md h3{line-height:1.4;margin:1.4em 0 .5em}
article.md blockquote{border-left:4px solid var(--gold);background:#faf6ef;
     margin:.8em 0;padding:.5em 1em;color:#6b5f4e}
article.md table{border-collapse:collapse;width:100%;margin:1em 0}
article.md th,article.md td{border:1px solid var(--line);padding:.4rem .55rem;
     font-size:.92rem;text-align:left}
article.md th{background:#f0eee9;font-size:.85rem;color:var(--muted)}
footer{background:var(--dark);color:#9aa0aa;text-align:center;padding:1.5rem 1rem;
     font-size:.82rem;margin-top:3rem}
footer a{color:#c9ccd3}
.rpt{background:var(--card);border:1px solid var(--line);border-radius:12px;
     padding:.9rem 1.2rem;margin-bottom:.7rem;display:flex;justify-content:space-between;
     align-items:center;gap:1rem}
.date-chip{font-family:ui-monospace,Consolas,monospace;font-size:.78rem;color:var(--muted);
     border:1px solid var(--line);border-radius:99px;padding:.12rem .6rem;white-space:nowrap}
@media(max-width:560px){.hero h1{font-size:1.2rem} main{padding:0 .6rem}
     .card{padding:.9rem 1rem}}
"""

# ---------- 口令/会话 (沿用 ai-digest web 的成熟模式) ----------
def pbkdf2(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()


def check_pw(pw, salt, want):
    return hmac.compare_digest(pbkdf2(pw, salt), want)


def sign(secret, who, exp):
    return hmac.new(secret.encode(), f"{who}:{exp}".encode(), hashlib.sha256).hexdigest()


def make_cookie(secret, ttl=SESSION_TTL):
    exp = str(int(time.time()) + ttl)
    return f"radar|{exp}.{sign(secret, 'radar', exp)}"


def cookie_ok(secret, val):
    try:
        payload, sig = val.rsplit(".", 1)
        who, exp = payload.rsplit("|", 1)
        return (who == "radar" and int(exp) > time.time()
                and hmac.compare_digest(sig, sign(secret, who, exp)))
    except Exception:
        return False


def load_web_cfg():
    d = {"secret": "", "salt": "", "hash": ""}
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            d.update(json.load(f))
    return d


# ---------- Markdown 渲染(先转义再变换, 同 ai-digest) ----------
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
CODE = re.compile(r"`([^`]+)`")


def _inline(s):
    s = LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    s = BOLD.sub(r"<b>\1</b>", s)
    return CODE.sub(r"<code>\1</code>", s)


def md2html(text):
    out, para, lst, tbl = [], [], [None], [False]

    def fp():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def fl():
        if lst[0]:
            out.append(f"</{lst[0]}>")
            lst[0] = None

    def ft():
        if tbl[0]:
            out.append("</table>")
            tbl[0] = False

    for raw in text.splitlines():
        s = html.escape(raw.rstrip()).strip()
        if tbl[0] and not s.startswith("|"):
            ft()
        if not s:
            fp(), fl()
            continue
        # 管道表格: | a | b | 行; |---|---| 为表头分隔(跳过)
        if s.startswith("|") and s.count("|") >= 2:
            fp(), fl()
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if not tbl[0]:
                out.append("<table>")
                tbl[0] = True
                out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", s)
        if m:
            fp(), fl()
            out.append(f"<h{len(m.group(1))}>{_inline(m.group(2))}</h{len(m.group(1))}>")
            continue
        if re.match(r"^[-=*]{3,}$", s):
            fp(), fl()
            out.append("<hr>")
            continue
        m = re.match(r"^[-*]\s+(.*)", s)
        if m:
            fp()
            if lst[0] != "ul":
                fl(), out.append("<ul>")
                lst[0] = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        m = re.match(r"^\d+[.、]\s*(.*)", s)
        if m:
            fp()
            if lst[0] != "ol":
                fl(), out.append("<ol>")
                lst[0] = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        if s.startswith(">"):
            fp(), fl()
            out.append(f"<blockquote>{_inline(s.lstrip('> '))}</blockquote>")
            continue
        para.append(s)
    fp(), fl(), ft()
    return "\n".join(out)


# ---------- 邮件(mailer 复用; SMTP 配置在 config.env) ----------
def send_mail(cfg, to, subject, html_body):
    if not cfg.get("SMTP_HOST"):
        return False, "smtp未配置"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = cfg.get("SMTP_FROM") or cfg["SMTP_USER"]
        msg["To"] = to
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        port = int(cfg.get("SMTP_PORT") or 465)
        if port == 465:
            s = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, timeout=20)
        else:
            s = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=20)
            s.starttls()
        with s:
            s.login(cfg["SMTP_USER"], cfg["SMTP_PASS"])
            s.sendmail(msg["From"], [to], msg.as_string())
        return True, ""
    except Exception as ex:
        return False, str(ex)[:150]


# ---------- 仪表盘数据 ----------
def latest_analysis():
    best, best_d = None, ""
    if os.path.isdir(common.DATA):
        for f in os.listdir(common.DATA):
            m = re.match(r"analysis-(\d{4}-\d{2}-\d{2})\.json$", f)
            if m and m.group(1) > best_d:
                best_d = m.group(1)
                best = f
    if not best:
        return None
    try:
        with open(os.path.join(common.DATA, best), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def sparksvg(closes, w=250, h=46):
    """60日收盘迷你走势图(内联SVG, 无CDN)。"""
    if len(closes) < 2:
        return ""
    cs = closes[-60:]
    lo, hi = min(cs), max(cs)
    rng = (hi - lo) or 1e-9
    pts = []
    for i, c in enumerate(cs):
        x = i / (len(cs) - 1) * (w - 6) + 3
        y = h - 6 - (c - lo) / rng * (h - 12)
        pts.append(f"{x:.1f},{y:.1f}")
    color = "var(--up)" if cs[-1] >= cs[0] else "var(--dn)"
    lx, ly = pts[-1].split(",")
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="近{len(cs)}日走势">'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" '
            f'stroke-width="1.6" stroke-linejoin="round"/>'
            f'<circle cx="{lx}" cy="{ly}" r="2.6" fill="{color}"/></svg>')


def kline_closes(db, code, n=60):
    return [r[0] for r in db.execute(
        "SELECT close FROM klines WHERE symbol=? ORDER BY date DESC LIMIT ?",
        (code, n)).fetchall()][::-1]


def pct_cls(p):
    return "up" if p > 0 else ("dn" if p < 0 else "flat")


def fmt_pct(p, sign=True):
    s = "+" if (sign and p >= 0) else ""
    return f"{s}{p:.2f}%"


class Handler(BaseHTTPRequestHandler):
    server_version = "etf-radar-web/1.0"
    wcfg = {}
    env_cfg = {}

    def client_ip(self):
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0]

    def log_message(self, fmt, *a):
        print(time.strftime("%F %T"), self.client_ip(), fmt % a)

    # ---------- 输出 ----------
    def send_body(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, to, set_cookie=None):
        self.send_response(302)
        self.send_header("Location", to)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def page(self, title, body, authed=False):
        right = ('<a href="/">仪表盘</a><a href="/reports">报告</a>'
                 '<a href="/logout">退出</a>') if authed else ""
        head = f'<span class="brand">ETF趋势雷达</span><nav>{right}</nav>'
        return ("<!doctype html><html lang=zh><head><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>{html.escape(title)} · ETF趋势雷达</title>"
                f"<style>{CSS}</style></head><body>"
                f"<header class='navbar'>{head}</header>"
                f"<main>{body}</main>"
                "<footer>etf-radar · 本站内容为量化规则+消息面模型的统计输出，"
                "不构成投资建议，请独立决策 · Powered by ZCode</footer></body></html>")

    # ---------- 会话 ----------
    def authed(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        v = c["radar_auth"].value if "radar_auth" in c else None
        return bool(v and cookie_ok(self.wcfg["secret"], v))

    def read_form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return parse_qs(self.rfile.read(min(n, MAX_BODY)).decode("utf-8", "ignore")) \
            if 0 < n <= MAX_BODY else {}

    # 限速与爆破告警(飞书机器人关键词「日报」已包含)
    def rate_ok(self):
        st = LOGIN_FAILS.get(self.client_ip(), [0, 0])
        return time.time() >= st[1]

    def rate_fail(self):
        ip = self.client_ip()
        st = LOGIN_FAILS.get(ip, [0, 0])
        st[0] += 1
        LOGIN_FAILS[ip] = [st[0], time.time() + 600] if st[0] >= 5 else st
        time.sleep(0.3)
        if st[0] >= 3 and time.time() - ALERT_TS.get(ip, 0) > 1800:
            ALERT_TS[ip] = time.time()
            locked = "已锁定10分钟" if time.time() < LOGIN_FAILS[ip][1] else "临近锁定"
            common.feishu_alert(
                "【ETF雷达日报·安全告警】⚠️ 网站遭口令爆破\n"
                f"来源IP: {ip}\n累计失败: {st[0]} 次\n状态: {locked}\n"
                f"时间(UTC): {time.strftime('%F %T')}", self.env_cfg)

    # ================= 路由 =================
    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p == "/robots.txt":
            self.send_body(200, "User-agent: *\nDisallow: /\n", "text/plain")
        elif p == "/login":
            self.send_body(200, self.page("登录", LOGIN_HTML))
        elif p == "/logout":
            self.redirect("/login", "radar_auth=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        elif not self.authed():
            self.redirect("/login")
        elif p == "/":
            self.send_body(200, self.page("仪表盘", self.dashboard(), True))
        elif p == "/reports":
            self.send_body(200, self.page("报告", self.reports_page(), True))
        elif p.startswith("/r/"):
            self.show_report(p.split("/", 2)[2])
        else:
            self.send_body(404, self.page("404", '<div class="card">页面不存在</div>', True))

    def do_POST(self):
        p = urlparse(self.path).path
        if p != "/login":
            self.redirect("/login")
            return
        if not self.rate_ok():
            self.send_body(429, self.page("锁定", LOCKED_HTML))
            return
        form = self.read_form()
        pw = (form.get("pw") or [""])[0]
        if check_pw(pw, self.wcfg["salt"], self.wcfg["hash"]):
            LOGIN_FAILS.pop(self.client_ip(), None)
            self.redirect("/", "radar_auth=" + make_cookie(self.wcfg["secret"])
                          + "; Path=/; HttpOnly; SameSite=Strict")
        else:
            self.rate_fail()
            self.send_body(401, self.page("登录", LOGIN_HTML_FAIL))

    # ---------- 页面 ----------
    def dashboard(self):
        a = latest_analysis()
        if not a:
            return ('<div class="card">还没有分析数据。服务部署后首个交易日 8:30（北京）'
                    '自动生成，或先手动跑一次 run.sh。</div>')
        db = common.market_db()
        note = f' ｜ {html.escape(a["note"])}' if a.get("note") else ""
        hero = (f'<div class="hero"><h1>{a["date"]} 收盘信号</h1>'
                f'<div class="meta">生成于 {html.escape(a.get("generated_at",""))}{note}'
                f' ｜ 晨报每交易日 8:30 更新</div></div>')

        # 预测总览表
        trs = []
        for code, s in a["symbols"].items():
            p = s["pct"]
            pr = s.get("pred_ret")
            badge = {"偏多": "b-long", "偏空": "b-short"}.get(s["signal"], "b-mid")
            pred = f"{s['pred_close']:.3f}" if s.get("pred_close") is not None else "-"
            prs = f"{pr:+.2f}%" if pr is not None else "-"
            trs.append(
                f"<tr><td>{html.escape(s['name'])} <span class='code'>{code}</span></td>"
                f"<td><b>{s['close']:.3f}</b></td>"
                f"<td class='{pct_cls(p)}'>{fmt_pct(p)}</td>"
                f"<td><span class='badge {badge}'>{s['signal']}</span></td>"
                f"<td><b>{pred}</b></td>"
                f"<td class='{pct_cls(pr) if pr is not None else 'flat'}'>{prs}</td>"
                f"<td>{s['band_low']:.3f}~{s['band_high']:.3f}</td>"
                f"<td>{s['prob_up']}%</td></tr>")
        overview = ('<section><h2>预测总览</h2><div class="twrap"><table class="t">'
                    '<tr><th>标的</th><th>收盘</th><th>今日</th><th>信号</th>'
                    '<th>预测价(今日)</th><th>预测涨跌</th><th>参考区间</th><th>看涨概率</th></tr>'
                    + "".join(trs) + "</table></div></section>")

        cards = []
        for code, s in a["symbols"].items():
            p = s["pct"]
            badge = {"偏多": "b-long", "偏空": "b-short"}.get(s["signal"], "b-mid")
            news = s.get("news") or {}
            sent = news.get("sent")
            sent_html = (f'<span class="kv"><span>消息面情绪</span>'
                         f'<b class="{pct_cls(sent)}">{sent:+.2f}（{news.get("n",0)}条）</b></span>'
                         if sent is not None else
                         '<span class="kv"><span>消息面情绪</span><b class="flat">无</b></span>')
            pr = s.get("pred_ret")
            pred_html = (f'<div class="kv"><span>预测价(今日)</span>'
                         f'<b class="{pct_cls(pr)}">{s["pred_close"]:.3f}'
                         f'（{fmt_pct(pr)}）</b></div>') \
                if s.get("pred_close") is not None else ""
            r = s.get("reasons") or {}
            why = ""
            if r.get("up"):
                why += ('<div class="why"><span class="up">▲ 看涨：</span>'
                        + html.escape("；".join(r["up"])) + "</div>")
            if r.get("down"):
                why += ('<div class="why"><span class="dn">▼ 看跌：</span>'
                        + html.escape("；".join(r["down"])) + "</div>")
            why_html = f'<div class="whybox">{why}</div>' if why else ""
            h = s.get("hit20") or {}
            hit_html = (f'方向胜率 {h["dir"]*100:.0f}% · 区间 {h["band"]*100:.0f}%（n={h["n"]}）'
                        if h.get("n") else "暂无验证样本")
            view = (f'<div class="kv" style="align-items:flex-start"><span>模型观点</span>'
                    f'<b style="font-weight:500;text-align:right;max-width:60%">'
                    f'{html.escape(s["llm_view"])}</b></div>') if s.get("llm_view") else ""
            cards.append(
                f'<div class="card"><h3><span>{html.escape(s["name"])}'
                f'<span class="code"> {code}</span></span>'
                f'<span class="badge {badge}">{s["signal"]}</span></h3>'
                f'<div class="price {pct_cls(p)}">{s["close"]:.3f} '
                f'<span style="font-size:.95rem">{fmt_pct(p)}</span></div>'
                + sparksvg(kline_closes(db, code)) +
                f'<div class="kv"><span>看涨概率</span><b>{s["prob_up"]}%</b></div>'
                + pred_html +
                f'<div class="kv"><span>预测区间</span>'
                f'<b>{s["band_low"]:.3f} ~ {s["band_high"]:.3f}</b></div>'
                f'{sent_html}{view}{why_html}'
                f'<div class="kv"><span>近20日胜率</span>'
                f'<b style="font-weight:500">{hit_html}</b></div></div>')
        grid = '<div class="grid">' + "".join(cards) + "</div>"

        # 今日消息
        news_sec = []
        for code, s in a["symbols"].items():
            items = (s.get("news") or {}).get("items") or []
            if not items:
                continue
            lis = []
            for it in items[:5]:
                cls = "tag-pos" if it["score"] > 0 else ("tag-neg" if it["score"] < 0 else "flat")
                ann = ('<span class="chip ann">公告</span>'
                       if it.get("src") == "em-ann" else "")
                lis.append(f'<li>{ann}<a href="{html.escape(it["link"])}" target="_blank" '
                           f'rel="noopener">{html.escape(it["title"])}</a> '
                           f'<span class="{cls}">{it["tag"]} {it["score"]:+.1f}</span></li>')
            news_sec.append(f'<div class="news"><b>{html.escape(s["name"])}（{code}）</b>'
                            + "<ul>" + "".join(lis) + "</ul></div>")
        news_html = ("<section><h2>今日消息面</h2>" + "".join(news_sec) + "</section>"
                     if news_sec else "")

        # 信号复盘表(最近15条已验证)
        rows = db.execute("SELECT symbol, date, close, pred_close, prob_up, signal,"
                          " actual_close, hit_dir, hit_band FROM predictions"
                          " WHERE actual_close IS NOT NULL"
                          " ORDER BY date DESC, symbol LIMIT 15").fetchall()
        trs = []
        for sym, d, close, pred, prob, sig, actual, hd, hb in rows:
            ret = (actual / close - 1) * 100
            cls = "up" if hd else "dn"
            pred_s = f"{pred:.3f}" if pred is not None else "-"
            trs.append(f"<tr><td>{d}</td><td>{html.escape(WATCH[sym][1])}</td>"
                       f"<td>{pred_s}</td><td>{sig}</td><td>{prob}%</td>"
                       f'<td class="{pct_cls(ret)}">{fmt_pct(ret)}</td>'
                       f'<td class="{cls}">{"✓" if hd else "✗"}</td>'
                       f"<td>{'命中' if hb else '未中'}</td></tr>")
        review_html = ("<section><h2>信号复盘（最近验证）</h2>"
                       '<div class="twrap"><table class="t"><tr><th>日期</th><th>标的</th>'
                       '<th>预测价</th><th>信号</th><th>看涨概率</th><th>实际</th>'
                       '<th>方向</th><th>区间</th></tr>'
                       + "".join(trs) + "</table></div></section>") if trs else ""

        # 回测
        bt_html = ""
        btp = os.path.join(common.STATE, "backtest.json")
        if os.path.exists(btp):
            try:
                bt = json.load(open(btp, encoding="utf-8"))
                o = bt.get("overall") or {}
                if o.get("n"):
                    tds = (f"<tr><td>全体标的</td><td>{o['n']}</td>"
                           f"<td>{o['dir']*100:.1f}%</td><td>{o['band']*100:.1f}%</td></tr>")
                    for code, r in (bt.get("symbols") or {}).items():
                        tds += (f"<tr><td>{html.escape(WATCH[code][1])}</td><td>{r['n']}</td>"
                                f"<td>{r['dir']*100:.1f}%</td><td>{r['band']*100:.1f}%</td></tr>")
                    bt_html = ("<section><h2>历史回测（纯技术面 walk-forward）</h2>"
                               '<table class="t"><tr><th>标的</th><th>样本</th>'
                               '<th>方向胜率</th><th>区间命中</th></tr>' + tds + "</table>"
                               '<p style="font-size:.85rem;color:var(--muted)">'
                               + html.escape(bt.get("note", "")) + "</p></section>")
            except Exception:
                pass
        db.close()
        return hero + overview + grid + news_html + review_html + bt_html

    def reports_page(self):
        files = []
        if os.path.isdir(common.REPORTS):
            for f in sorted(os.listdir(common.REPORTS), reverse=True):
                if PAGE.match(f):
                    kind = "周报" if f.startswith("weekly") else "日报"
                    files.append(f'<div class="rpt"><a href="/r/{f}">'
                                 f'{kind} {f[:-3].split("-",1)[1]}</a>'
                                 f'<span class="date-chip">{f}</span></div>')
        body = "".join(files[:100]) or '<div class="card">还没有报告</div>'
        return '<section><h2>报告归档</h2>' + body + "</section>"

    def show_report(self, name):
        name = os.path.basename(name)
        if not PAGE.match(name):
            self.send_body(404, self.page("404", '<div class="card">文件名非法</div>', True))
            return
        full = os.path.join(common.REPORTS, name)
        if not os.path.exists(full):
            self.send_body(404, self.page("404", '<div class="card">不存在</div>', True))
            return
        text = open(full, encoding="utf-8").read()
        self.send_body(200, self.page(name[:-3],
                                      f'<article class="md">{md2html(text)}</article>', True))


LOGIN_HTML = ('<div class="card auth-card"><h2 style="margin-top:0">ETF趋势雷达 · 登录</h2>'
              '<form method=post action="/login">'
              '<p><input type=password name=pw required placeholder="访问口令" autofocus></p>'
              '<p><button class="btn" style="width:100%">进入</button></p></form></div>')
LOGIN_HTML_FAIL = ('<div class="card auth-card"><h2 style="margin-top:0">ETF趋势雷达 · 登录</h2>'
                   '<div class="banner banner-bad">口令错误</div>'
                   '<form method=post action="/login">'
                   '<p><input type=password name=pw required placeholder="访问口令"></p>'
                   '<p><button class="btn" style="width:100%">进入</button></p></form></div>')
LOCKED_HTML = ('<div class="card auth-card"><div class="banner banner-bad">'
               '尝试过多，10分钟后再试</div></div>')

LOGIN_FAILS = {}  # ip -> [count, lock_until]
ALERT_TS = {}     # ip -> 上次告警时间(30分钟节流)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "init":
        pw = sys.argv[2]
        cfg = load_web_cfg()
        if not cfg["secret"]:
            cfg["secret"] = secrets.token_hex(32)
        cfg["salt"] = secrets.token_hex(16)
        cfg["hash"] = pbkdf2(pw, cfg["salt"])
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.chmod(CFG_PATH, 0o600)
        print("init: ok")
        return
    Handler.wcfg = load_web_cfg()
    if not Handler.wcfg.get("hash"):
        sys.exit("web_config.json 未初始化: 先运行 python3 web.py init <口令>")
    Handler.env_cfg = load_cfg()
    common.ensure_dirs()
    bind = os.environ.get("WEB_BIND", Handler.env_cfg.get("WEB_BIND", "127.0.0.1"))
    port = int(os.environ.get("WEB_PORT", Handler.env_cfg.get("WEB_PORT", "8891")))
    srv = ThreadingHTTPServer((bind, port), Handler)
    print(f"etf-radar web on {bind}:{port}  base={common.BASE}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
