#!/usr/bin/env python3
"""ai-digest web v2 — 前台(订阅站) + 后台(管理) 双模式。

  MODE=front python3 web.py   # 前台: 8800 公开阅读+注册/登录/订阅
  MODE=admin python3 web.py   # 后台: 8801 专用管理(无注册)

配置 web_config.json(服务器本地,永不下发):
  { secret, salt, hash(管理员口令), site_url,
    smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from }
用户库: users.db (sqlite: email/口令散列/验证与订阅状态/退订token)

安全: PBKDF2 / HMAC会话cookie / 登录限速 / 输出转义 / 文件名白名单 / 注册邮箱格式校验
"""
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import smtplib
import sqlite3
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(BASE, "reports")
CFG_PATH = os.path.join(BASE, "web_config.json")
DB_PATH = os.path.join(BASE, "users.db")
MODE = os.environ.get("MODE", "front")
PORT = int(os.environ.get("WEB_PORT", "8800" if MODE == "front" else "8801"))
SESSION_TTL = 30 * 86400
MAX_BODY = 2 * 1024 * 1024
PAGE = re.compile(r"^[\w.-]+\.md$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

# ============ 模板:Newsletter 编辑风(自绘,无CDN依赖) ============
CSS = """
:root{--ink:#26272b;--paper:#f7f6f3;--card:#fff;--line:#e6e3dd;--accent:#b4531f;
      --muted:#8b8a86;--dark:#20232a;--ok:#15803d;--bad:#b91c1c}
*{box-sizing:border-box}
body{margin:0;font:16px/1.75 -apple-system,"Segoe UI",Roboto,"PingFang SC",
     "Hiragino Sans GB","Microsoft YaHei",sans-serif;color:var(--ink);background:var(--paper)}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
.navbar{position:sticky;top:0;z-index:20;background:var(--dark);color:#fff;display:flex;
     align-items:center;justify-content:space-between;gap:1rem;padding:.65rem 1.3rem;
     box-shadow:0 1px 10px rgba(0,0,0,.18)}
.navbar .brand{color:#fff;font-weight:800;letter-spacing:.08em;font-size:1.02rem;
     border-bottom:2px solid var(--accent);padding-bottom:.15rem}
.navbar nav{display:flex;gap:1.25rem;align-items:center;font-size:.93rem;flex-wrap:wrap}
.navbar nav a{color:#d9dce2}
.navbar nav a:hover{color:#fff;text-decoration:none}
.pill{background:var(--accent);color:#fff;border-radius:99px;padding:.32rem .95rem;font-weight:600}
.pill:hover{opacity:.88;text-decoration:none;color:#fff}
.hero{background:linear-gradient(180deg,#262a33,var(--dark));color:#fff;text-align:center;
     padding:3.1rem 1rem 3.3rem;border-bottom:3px solid var(--accent)}
.hero .big{font-size:2.15rem;font-weight:800;letter-spacing:.1em}
.hero .tag{color:#b9bcc4;margin-top:.7rem;font-size:.98rem}
main{max-width:760px;margin:2.2rem auto 3rem;padding:0 1rem}
.wide{max-width:980px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:1.15rem 1.4rem;margin-bottom:1rem;transition:box-shadow .15s}
.card:hover{box-shadow:0 5px 16px rgba(0,0,0,.06)}
.row{display:flex;justify-content:space-between;align-items:center;gap:1rem}
.date-chip{font-family:ui-monospace,Consolas,monospace;font-size:.78rem;color:var(--muted);
     border:1px solid var(--line);border-radius:99px;padding:.12rem .6rem;white-space:nowrap}
article{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:2rem 2.2rem}
article h1,article h2,article h3{font-family:Georgia,"Times New Roman","Songti SC",SimSun,serif;
     line-height:1.4;margin:1.6em 0 .6em}
article h1:first-child{margin-top:0;font-size:1.7rem}
article h2{font-size:1.3rem;border-left:4px solid var(--accent);padding-left:.6rem}
article h3{font-size:1.08rem}
article p{margin:.8em 0} article li{margin:.3em 0}
article code{background:#f0eee9;padding:.1em .4em;border-radius:4px;font-size:.92em}
hr{border:none;border-top:1px solid var(--line);margin:1.6rem 0}
.btn{display:inline-block;background:var(--dark);color:#fff;border:none;border-radius:99px;
     padding:.55rem 1.5rem;font-size:.95rem;cursor:pointer}
.btn:hover{opacity:.88;text-decoration:none}
.btn-accent{background:var(--accent)}
.btn-line{background:transparent;color:var(--ink);border:1px solid var(--line)}
.btn-danger{background:var(--bad)}
input[type=text],input[type=email],input[type=password]{
     width:100%;padding:.62rem .8rem;border:1px solid var(--line);border-radius:8px;
     font-size:15px;background:#fff}
textarea{width:100%;min-height:62vh;font-family:ui-monospace,Consolas,monospace;font-size:14px;
     padding:.9rem;box-sizing:border-box;border:1px solid var(--line);border-radius:8px}
.auth-card{max-width:440px;margin:0 auto}
.meta{color:var(--muted);font-size:.88rem}
.err{color:var(--bad)} .ok{color:var(--ok)}
.banner{border-radius:10px;padding:.8rem 1.1rem;margin-bottom:1.1rem;font-size:.95rem}
.banner-ok{background:#ecf7ef;border:1px solid #cde7d5}
.banner-bad{background:#fbeeee;border:1px solid #f0d4d4}
.banner-info{background:#eef3fb;border:1px solid #d4dff0}
form.inline{display:inline}
table.t{width:100%;border-collapse:collapse}
table.t td,table.t th{border-bottom:1px solid var(--line);padding:.55rem .4rem;text-align:left}
footer{background:var(--dark);color:#9aa0aa;text-align:center;padding:1.7rem 1rem;font-size:.85rem}
footer a{color:#c9ccd3}
@media(max-width:560px){article{padding:1.3rem 1.1rem}.hero .big{font-size:1.5rem}
.navbar{padding:.55rem .9rem}.navbar nav{gap:.9rem;font-size:.88rem}}
/* 左侧日期菜单 + 右侧内容 的双栏布局 */
.layout{display:flex;gap:1.2rem;align-items:flex-start}
.sidebar{width:200px;flex-shrink:0;position:sticky;top:64px;max-height:calc(100vh - 84px);overflow-y:auto}
.sidebar a{display:block;padding:.42rem .6rem;border-radius:8px;color:var(--ink);
     font-family:ui-monospace,Consolas,monospace;font-size:.86rem;line-height:1.4}
.sidebar a:hover{background:#efedea;text-decoration:none}
.sidebar a.on{background:var(--dark);color:#fff}
.main-col{flex:1;min-width:0}
@media(max-width:760px){.layout{flex-direction:column}
.sidebar{width:100%;position:static;max-height:none;display:flex;flex-wrap:wrap;gap:.3rem}
.sidebar a{display:inline-block}}
"""


def load_cfg():
    d = {"secret": "", "salt": "", "hash": "", "site_url": "http://localhost:%d" % PORT,
         "smtp_host": "", "smtp_port": "465", "smtp_user": "", "smtp_pass": "", "smtp_from": "",
         "front_host": "", "admin_host": ""}
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            d.update(json.load(f))
    return d


# ---------- 口令/会话 ----------
def pbkdf2(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()


def check_pw(pw, salt, want):
    return hmac.compare_digest(pbkdf2(pw, salt), want)


def sign(secret, who, exp):
    return hmac.new(secret.encode(), f"{who}:{exp}".encode(), hashlib.sha256).hexdigest()


def make_cookie(secret, who, ttl=SESSION_TTL):
    exp = str(int(time.time()) + ttl)
    return f"{who}|{exp}.{sign(secret, who, exp)}"


def cookie_ok(secret, val, who_prefix):
    try:
        payload, sig = val.rsplit(".", 1)
        who, exp = payload.rsplit("|", 1)  # who内可含"."(邮箱)，故用|分隔
        return (who.startswith(who_prefix) and int(exp) > time.time()
                and hmac.compare_digest(sig, sign(secret, who, exp)))
    except Exception:
        return False


# ---------- Markdown渲染(先转义再变换) ----------
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
CODE = re.compile(r"`([^`]+)`")


def _inline(s):
    s = LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    s = BOLD.sub(r"<b>\1</b>", s)
    return CODE.sub(r"<code>\1</code>", s)


def md2html(text):
    out, para, lst = [], [], [None]

    def fp():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def fl():
        if lst[0]:
            out.append(f"</{lst[0]}>")
            lst[0] = None

    for raw in text.splitlines():
        s = html.escape(raw.rstrip()).strip()
        if not s:
            fp(), fl()
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
        para.append(s)
    fp(), fl()
    return "\n".join(out)


def list_reports():
    if not os.path.isdir(REPORTS):
        return []
    out = []
    for f in sorted(os.listdir(REPORTS), reverse=True):
        if not PAGE.match(f):
            continue
        title = f[:-3]
        try:
            with open(os.path.join(REPORTS, f), encoding="utf-8", errors="ignore") as fh:
                first = fh.readline().lstrip("# ").strip()
                if first:
                    title = first
        except OSError:
            pass
        out.append((f, title))
    return out


def safe_path(name):
    name = os.path.basename(name or "")
    return name if PAGE.match(name) else None


# ---------- 用户库 ----------
def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS users("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,"
              "salt TEXT NOT NULL, hash TEXT NOT NULL, token TEXT NOT NULL,"
              "verified INTEGER DEFAULT 0, subscribed INTEGER DEFAULT 1, created TEXT)")
    return c


def send_mail(cfg, to, subject, html_body):
    if not cfg.get("smtp_host"):
        return False, "smtp未配置"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = cfg.get("smtp_from") or cfg["smtp_user"]
        msg["To"] = to
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        port = int(cfg.get("smtp_port") or 465)
        if port == 465:
            s = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=20)
        else:
            s = smtplib.SMTP(cfg["smtp_host"], port, timeout=20)
            s.starttls()
        with s:
            s.login(cfg["smtp_user"], cfg["smtp_pass"])
            s.sendmail(msg["From"], [to], msg.as_string())
        return True, ""
    except Exception as ex:
        return False, str(ex)[:150]


LOGIN_FAILS = {}  # ip -> [count, lock_until]
ADMIN_ALERT = {}   # ip -> 上次告警时间戳（30分钟节流，防告警轰炸）


def feishu_webhook():
    """复用 ai-digest config.env 里的飞书机器人（消息需含关键词「日报」）。"""
    try:
        with open(os.path.join(BASE, "config.env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("FEISHU_WEBHOOK="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def feishu_alert(text):
    """异步推飞书（不阻塞web响应）；无webhook则只记日志。"""
    hook = feishu_webhook()
    if not hook:
        print("alert(feishu): no webhook, skip:", text.replace("\n", " ")[:100])
        return

    def _send():
        try:
            body = json.dumps({"msg_type": "text", "content": {"text": text[:2800]}}).encode()
            req = urllib.request.Request(hook, data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read()
            print("alert(feishu): sent ->", text.splitlines()[0])
        except Exception as ex:
            print("alert(feishu): failed:", ex)

    threading.Thread(target=_send, daemon=True).start()


# ---------- 访问统计 ----------
STATS_DB = os.path.join(BASE, "stats.db")
BOT_UA = re.compile(r"bot|spider|crawler|curl|wget|headless|python-requests|monitor|uptime", re.I)


def stats_hit(day, hour, path, ip, mode, status, ua):
    """每请求一行落库；任何失败都不影响正常服务。"""
    if path == "/robots.txt" or status in (403, 429):  # 跳过扫描噪声
        return
    try:
        c = sqlite3.connect(STATS_DB, timeout=3)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS hits("
                  "day TEXT, hour INT, path TEXT, ip TEXT, mode TEXT, status INT, bot INT)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_hits_day ON hits(day)")
        c.execute("INSERT INTO hits VALUES(?,?,?,?,?,?,?)",
                  (day, hour, path[:80], ip, mode, status,
                   1 if BOT_UA.search(ua or "") else 0))
        c.commit()
        c.close()
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "ai-digest-web/2.0"
    cfg = {}
    mode = "front"

    def log_request(self, code='-', size='-'):
        try:
            now = datetime.utcnow() + timedelta(hours=8)
            stats_hit(now.date().isoformat(), now.hour, self.path.split("?")[0],
                      self.client_ip(), self.rmode(),
                      code if isinstance(code, int) else 0,
                      self.headers.get("User-Agent", ""))
        except Exception:
            pass
        super().log_request(code, size)

    def client_ip(self):
        """Caddy反代后取真实IP（服务仅绑定127.0.0.1，XFF可信）。"""
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
        self.send_header("Cache-Control", "no-cache")  # 日报每日更新，禁浏览器旧缓存
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

    def page(self, title, body, user=None, admin=False, hero=""):
        # 经典顶栏式：品牌居左，登录/订阅居右上
        if self.rmode() == "admin":
            right = ('<a href="/admin">内容</a><a href="/admin/stats">📈 统计</a>'
                     '<a href="/logout">退出</a>' if admin
                     else '<a href="/login">登录</a>')
            head = f'<span class="brand">AI科技精选 · 管理</span><nav>{right}</nav>'
        else:
            if user:
                right = (f'<a href="/me">我的订阅</a>'
                         f'<a href="/me" class="pill">{html.escape(user)}</a>'
                         '<a href="/logout">退出</a>')
            else:
                right = ('<a href="/register" class="pill">免费订阅</a>'
                         '<a href="/login">登录</a>')
            head = f'<a class="brand" href="/">AI科技精选</a><nav><a href="/">日报</a>{right}</nav>'
        return ("<!doctype html><html lang=zh><head><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>{html.escape(title)} · AI科技精选</title>"
                f"<style>{CSS}</style></head><body>"
                f"<header class='navbar'>{head}</header>{hero}"
                f"<main class='wide'>{body}</main>"
                "<footer>ai-digest pipeline · <a href=/unsub>退订</a> · Powered by ZCode</footer>"
                "</body></html>")

    # ---------- 会话 ----------
    def _cookie(self, name):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return c[name].value if name in c else None

    def cur_user(self):
        v = self._cookie("dgst_user")
        if v and cookie_ok(self.cfg["secret"], v, "user:"):
            return v.rsplit(".", 1)[0].rsplit("|", 1)[0][5:]  # 去掉|exp和sig → user:<email>
        return None

    def cur_admin(self):
        v = self._cookie("dgst_auth")
        return bool(v and cookie_ok(self.cfg["secret"], v, "admin"))

    def read_form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return parse_qs(self.rfile.read(min(n, MAX_BODY)).decode("utf-8", "ignore")) if 0 < n <= MAX_BODY else {}

    def rate_ok(self):
        ip = self.client_ip()
        st = LOGIN_FAILS.get(ip, [0, 0])
        return time.time() >= st[1]

    def rate_fail(self):
        ip = self.client_ip()
        st = LOGIN_FAILS.get(ip, [0, 0])
        st[0] += 1
        LOGIN_FAILS[ip] = [st[0], time.time() + 600] if st[0] >= 5 else st
        time.sleep(0.3)

    # ---- vhost路由（MODE=vhost：80端口按Host分流，仅域名放行）----
    def _host(self):
        return (self.headers.get("Host") or "").split(":")[0].strip().lower()

    def host_allowed(self):
        if self.mode != "vhost":
            return True
        h = self._host()
        fh = (self.cfg.get("front_host") or "").lower()
        ah = (self.cfg.get("admin_host") or "").lower()
        apex = fh[4:] if fh.startswith("www.") else ""
        if apex and h == apex:                      # 裸域名 → www
            self.redirect("http://" + fh + self.path)
            return False
        if h not in (fh, ah):                       # IP/陌生域名 → 拒绝
            self.send_body(403, self.page("403", "<div class='card'><b>403</b> · "
                                         "本站仅限域名访问</div>"))
            return False
        return True

    def rmode(self):
        """本次请求的归属：front / admin。"""
        if self.mode != "vhost":
            return self.mode
        return "admin" if self._host() == (self.cfg.get("admin_host") or "").lower() else "front"

    # ================= GET =================
    def do_GET(self):
        if not self.host_allowed():
            return
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p == "/robots.txt":
            self.send_body(200, "User-agent: *\nDisallow: /me\nDisallow: /verify\nDisallow: /unsub\n", "text/plain")
        elif self.rmode() == "admin":
            self.admin_get(p, q)
        else:
            self.front_get(p, q)

    # ================= POST =================
    def do_POST(self):
        if not self.host_allowed():
            return
        p = urlparse(self.path).path
        if self.rmode() == "admin":
            self.admin_post(p)
        else:
            self.front_post(p)

    # ---------- 日期侧栏（前台/后台共用）----------
    def dates_nav(self, active_file, link_fn):
        items = []
        for f, _t in list_reports()[:200]:
            cls = " class='on'" if f == active_file else ""
            items.append(f"<a href='{link_fn(f)}'{cls}>{f[:-3]}</a>")
        if not items:
            items.append("<span class='meta'>暂无报告</span>")
        return ("<nav class='sidebar card' style='padding:.55rem'>"
                f"<div class='meta' style='padding:.3rem .6rem .5rem'>📅 历史精选</div>"
                f"{''.join(items)}</nav>")

    @staticmethod
    def _read_report(f):
        with open(os.path.join(REPORTS, f), encoding="utf-8") as fh:
            return fh.read()

    # ---------- 前台路由 ----------
    def front_get(self, p, q):
        user = self.cur_user()
        if p == "/" or p.startswith("/r/") or p.startswith("/raw/"):
            files = list_reports()
            # 选中日期：/?d=YYYY-MM-DD（裸日期）或 /r/YYYY-MM-DD.md，缺省=最新一期
            d = (q.get("d") or [""])[0]
            if not d and (p.startswith("/r/") or p.startswith("/raw/")):
                d = p.split("/", 2)[2]
            # 先补.md再过白名单校验（safe_path要求*.md结尾，裸日期会误杀）
            if d and re.fullmatch(r"[\w.-]+", d):
                want = safe_path(d if d.endswith(".md") else d + ".md")
            else:
                want = None
            active = want if want and os.path.exists(os.path.join(REPORTS, want)) \
                else (files[0][0] if files else None)
            if p.startswith("/raw/"):
                if active:
                    self.send_body(200, self._read_report(active), "text/plain; charset=utf-8")
                else:
                    self.send_body(404, self.page("404", '<div class="card">不存在</div>', user))
                return
            if active:
                content = f"<article>{md2html(self._read_report(active))}</article>"
            else:
                content = '<div class="card meta">还没有报告，明早7:30见</div>'
            body = (f"<div class='layout'>{self.dates_nav(active, lambda f: '/?d=' + f[:-3])}"
                    f"<div class='main-col'>{content}</div></div>")
            self.send_body(200, self.page(active[:-3] if active else "首页", body, user))
        elif p == "/register":
            self.send_body(200, self.page("订阅", self.auth_form("register"), user))
        elif p == "/login":
            self.send_body(200, self.page("登录", self.auth_form("login"), user))
        elif p == "/logout":
            self.redirect("/", "dgst_user=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        elif p == "/me":
            if not user:
                self.redirect("/login")
                return
            c = db()
            r = c.execute("SELECT verified, subscribed FROM users WHERE email=?", (user,)).fetchone()
            c.close()
            if not r:
                self.redirect("/logout")
                return
            ver = ('<div class="banner banner-ok">邮箱已验证 ✓</div>' if r[0] else
                   '<div class="banner banner-info">邮箱未验证：邮件通道配置后点击验证链接即可，'
                   '未验证不影响浏览，只影响每日邮件</div>')
            sub = ('<div class="banner banner-ok">已订阅每日邮件 ✓ '
                   '<form class=inline method=post action="/toggle"><button class="btn btn-line">暂停订阅</button></form></div>'
                   if r[1] else
                   '<div class="banner banner-info">订阅已暂停 '
                   '<form class=inline method=post action="/toggle"><button class="btn btn-accent">恢复订阅</button></form></div>')
            body = (f'<div class="card auth-card"><h2 style="margin:0 0 .5rem">{html.escape(user)}</h2>'
                    f'{ver}{sub}<p class="meta">注册即代表同意接收每日AI日报邮件，可随时暂停或'
                    f'<a href="/unsub">彻底退订</a>。</p></div>')
            self.send_body(200, self.page("我的订阅", body, user))
        elif p == "/verify":
            tok = (q.get("t") or [""])[0]
            c = db()
            c.execute("UPDATE users SET verified=1 WHERE token=? AND verified=0", (tok,))
            n, c.total_changes and True or True
            c.commit()
            c.close()
            self.redirect("/me")
        elif p == "/unsub":
            tok = (q.get("t") or [""])[0]
            body = '<div class="card auth-card"><h2>退订</h2>'
            if tok:
                c = db()
                c.execute("UPDATE users SET subscribed=0 WHERE token=?", (tok,))
                c.commit()
                c.close()
                body += '<div class="banner banner-ok">已退订，不再发送邮件。随时可登录重新订阅。</div>'
            else:
                body += ('<p class="meta">请使用邮件里的退订链接，或登录后在'
                         '<a href=/me>我的订阅</a>中暂停。</p>')
            body += "</div>"
            self.send_body(200, self.page("退订", body, self.cur_user()))
        else:
            self.send_body(404, self.page("404", '<div class="card">页面不存在</div>', user))

    def auth_form(self, kind, err="", ok=""):
        if kind == "register":
            return (f'<div class="card auth-card"><h2 style="margin-top:0">邮箱订阅</h2>{err}{ok}'
                    '<form method=post action="/register">'
                    '<p><input type=email name=email required placeholder="你的邮箱"></p>'
                    '<p><input type=password name=pw required minlength=6 placeholder="设置密码(≥6位)"></p>'
                    '<p><input type=password name=pw2 required placeholder="再输入一次"></p>'
                    '<p><button class="btn btn-accent" style="width:100%">注册并订阅每日日报</button></p>'
                    '<p class="meta">已有账号？<a href=/login>登录</a></p></form></div>')
        return (f'<div class="card auth-card"><h2 style="margin-top:0">登录</h2>{err}{ok}'
                '<form method=post action="/login">'
                '<p><input type=email name=email required placeholder="邮箱"></p>'
                '<p><input type=password name=pw required placeholder="密码"></p>'
                '<p><button class="btn" style="width:100%">登录</button></p>'
                '<p class="meta">还没订阅？<a href=/register>免费注册</a></p></form></div>')

    # ---------- 后台路由 ----------
    def admin_get(self, p, q):
        a = self.cur_admin()
        if p == "/login":
            self.send_body(200, self.page("管理登录", (
                '<div class="card auth-card"><h2 style="margin-top:0">管理登录</h2>'
                '<form method=post action="/login">'
                '<p><input type=password name=pw required placeholder="管理密码" autofocus></p>'
                '<p><button class="btn" style="width:100%">进入后台</button></p>'
                '</form></div>')))
        elif p == "/logout":
            self.redirect("/login", "dgst_auth=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        elif p == "/" and a:
            self.redirect("/admin")
        elif p == "/admin/stats" and a:
            self.admin_stats(q)
        elif p == "/admin" and a:
            f = safe_path((q.get("f") or [""])[0])
            files = list_reports()
            if not f and files:
                f = files[0][0]  # 缺省选中最新一期
            site = self.cfg.get("site_url", "").rstrip("/")
            if f and os.path.exists(os.path.join(REPORTS, f)):
                content = html.escape(open(os.path.join(REPORTS, f), encoding="utf-8").read())
                main = (f'<div class="row" style="margin-bottom:.6rem">'
                        f'<h2 style="margin:0">📝 {html.escape(f)}</h2>'
                        f'<div><a class="meta" href="{site}/r/{f}">前台查看</a></div></div>'
                        '<form method=post action="/admin/save">'
                        f'<input type=hidden name=f value="{html.escape(f)}">'
                        f'<textarea name=content>{content}</textarea>'
                        '<p><button class="btn" type=submit>保存</button></p></form>'
                        f'<form class=inline method=post action="/admin/delete" '
                        f'onsubmit="return confirm(\'确认删除 {html.escape(f)} ？\')">'
                        f'<input type=hidden name=f value="{html.escape(f)}">'
                        '<button class="btn btn-danger" type=submit>删除</button></form>')
            else:
                main = ('<div class="card"><h2 style="margin-top:0">新建页面</h2>'
                        '<form method=get action="/admin" class="row">'
                        '<input type=text name=f placeholder="页面名，如 2026-12-25.md 或 about.md" style="flex:1">'
                        '<button class="btn" type=submit>创建</button></form></div>')
            body = (f"<div class='layout'>"
                    f"{self.dates_nav(f, lambda x: '/admin?f=' + x)}"
                    f"<div class='main-col'>{main}</div></div>")
            self.send_body(200, self.page("后台", body, admin=True))
        elif p == "/admin":
            self.redirect("/login")
        else:
            self.send_body(404, self.page("404", '<div class="card">页面不存在</div>', admin=a))

    def front_post(self, p):
        form = self.read_form()
        if p == "/register":
            email = (form.get("email") or [""])[0].strip().lower()
            pw, pw2 = (form.get("pw") or [""])[0], (form.get("pw2") or [""])[0]
            err = ""
            if not EMAIL_RE.match(email):
                err = '<div class="banner banner-bad">邮箱格式不对</div>'
            elif len(pw) < 6:
                err = '<div class="banner banner-bad">密码至少6位</div>'
            elif pw != pw2:
                err = '<div class="banner banner-bad">两次密码不一致</div>'
            if not err:
                c = db()
                try:
                    salt = secrets.token_hex(16)
                    tok = secrets.token_hex(16)
                    c.execute("INSERT INTO users(email,salt,hash,token,created) VALUES(?,?,?,?,?)",
                              (email, salt, pbkdf2(pw, salt), tok, time.strftime("%F %T")))
                    c.commit()
                    c.close()
                    if self.cfg.get("smtp_host"):
                        link = self.cfg["site_url"].rstrip("/") + "/verify?t=" + tok
                        ok, msg = send_mail(self.cfg, email, "【AI科技精选】验证你的邮箱",
                                             f'<p>点击验证，之后每天早间收到AI日报：</p><p><a href="{link}">{link}</a></p>')
                        page = ('<div class="banner banner-ok">注册成功！请到邮箱点击验证链接完成订阅。</div>'
                                if ok else '<div class="banner banner-bad">注册成功，但验证邮件发送失败：' + html.escape(msg) + '</div>')
                        self.send_body(200, self.page("注册", '<div class="card auth-card"><h2>注册</h2>' + page + '</div>'))
                    else:
                        self.send_body(200, self.page("注册", '<div class="card auth-card"><h2>注册</h2>'
                                             '<div class="banner banner-info">注册成功（邮件通道尚未配置，'
                                             '订阅会在配置后自动生效，可先登录浏览）。</div></div>'))
                    return
                except sqlite3.IntegrityError:
                    err = '<div class="banner banner-bad">该邮箱已注册，请直接登录</div>'
                finally:
                    try:
                        c.close()
                    except Exception:
                        pass
            self.send_body(200, self.page("订阅", self.auth_form("register", err)))
        elif p == "/login":
            if not self.rate_ok():
                self.send_body(429, self.page("锁定", '<div class="card auth-card"><div class="banner banner-bad">尝试过多，10分钟后再试</div></div>'))
                return
            email = (form.get("email") or [""])[0].strip().lower()
            pw = (form.get("pw") or [""])[0]
            c = db()
            r = c.execute("SELECT salt, hash FROM users WHERE email=?", (email,)).fetchone()
            c.close()
            if r and check_pw(pw, r[0], r[1]):
                LOGIN_FAILS.pop(self.client_ip(), None)
                self.redirect("/me", "dgst_user=" + make_cookie(self.cfg["secret"], "user:" + email)
                              + "; Path=/; HttpOnly; SameSite=Strict")
            else:
                self.rate_fail()
                self.send_body(401, self.page("登录", self.auth_form(
                    "login", '<div class="banner banner-bad">邮箱或密码错误</div>')))
        elif p == "/logout":
            self.redirect("/", "dgst_user=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        elif p == "/toggle":
            user = self.cur_user()
            if not user:
                self.redirect("/login")
                return
            c = db()
            cur = c.execute("SELECT subscribed FROM users WHERE email=?", (user,)).fetchone()
            c.execute("UPDATE users SET subscribed=? WHERE email=?", (0 if cur and cur[0] else 1, user))
            c.commit()
            c.close()
            self.redirect("/me")
        else:
            self.send_body(404, self.page("404", '<div class="card">页面不存在</div>'))

    def admin_stats(self, q):
        """后台访问统计：近14天总览 + 单日详情(?d=YYYY-MM-DD)。"""
        d = (q.get("d") or [""])[0]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            files = list_reports()
            d = files[0][0][:-3] if files else (datetime.utcnow() + timedelta(hours=8)).date().isoformat()
        c = sqlite3.connect(STATS_DB)
        try:
            overview = c.execute(
                "SELECT day, COUNT(*), COUNT(DISTINCT ip), SUM(bot) FROM hits "
                "GROUP BY day ORDER BY day DESC LIMIT 14").fetchall()
            ov_rows = "".join(
                f"<tr><td><a href='/admin/stats?d={r[0]}'>{r[0]}</a></td>"
                f"<td>{r[1]}</td><td>{r[2]}</td><td class=meta>{r[3] or 0}</td></tr>"
                for r in overview) or "<tr><td colspan=4 class=meta>暂无数据</td></tr>"
            tot, uv, bots, valid = c.execute(
                "SELECT COUNT(*), COUNT(DISTINCT ip), SUM(bot), "
                "SUM(CASE WHEN status<400 THEN 1 ELSE 0 END) FROM hits WHERE day=?", (d,)).fetchone()
            top = c.execute(
                "SELECT path, COUNT(*) n, COUNT(DISTINCT ip) FROM hits "
                "WHERE day=? AND status<400 GROUP BY path ORDER BY n DESC LIMIT 12", (d,)).fetchall()
            top_rows = "".join(
                f"<tr><td>{html.escape(t[0])}</td><td>{t[1]}</td><td>{t[2]}</td></tr>"
                for t in top) or "<tr><td colspan=3 class=meta>暂无</td></tr>"
            hours = dict(c.execute(
                "SELECT hour, COUNT(*) FROM hits WHERE day=? AND status<400 GROUP BY hour", (d,)).fetchall())
            peak_h = max(hours, key=hours.get) if hours else "-"
            fm, am = c.execute(
                "SELECT SUM(CASE WHEN mode='front' THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN mode='admin' THEN 1 ELSE 0 END) FROM hits WHERE day=? AND status<400",
                (d,)).fetchone()
            bar = "".join(
                f"<td style='vertical-align:bottom;text-align:center'>"
                f"<div style='width:14px;height:{min(hours.get(h, 0) * 4, 90)}px;"
                f"background:var(--accent);margin:0 auto;border-radius:3px'></div>"
                f"<div class=meta style='font-size:.68rem'>{h}</div></td>"
                for h in range(24))
            body = (f"<h2>📈 访问统计 · {d}</h2>"
                    f"<div class='card row'><div><b>{tot or 0}</b><div class=meta>总请求</div></div>"
                    f"<div><b>{valid or 0}</b><div class=meta>有效访问</div></div>"
                    f"<div><b>{uv or 0}</b><div class=meta>独立访客</div></div>"
                    f"<div><b>{bots or 0}</b><div class=meta>爬虫</div></div>"
                    f"<div><b>{fm or 0}/{am or 0}</b><div class=meta>前台/后台</div></div>"
                    f"<div><b>{peak_h}点</b><div class=meta>峰值时段</div></div></div>"
                    "<div class='card'><b>24小时分布</b><table style='width:100%'>"
                    f"<tr>{bar}</tr></table></div>"
                    "<div class='card'><b>热门路径 TOP12（当日有效访问）</b>"
                    f"<table class='t'><tr><th>路径</th><th>次数</th><th>访客</th></tr>{top_rows}</table></div>"
                    "<div class='card'><b>近14天总览</b>"
                    "<table class='t'><tr><th>日期</th><th>请求</th><th>UV</th><th>爬虫</th></tr>"
                    f"{ov_rows}</table></div>")
        finally:
            c.close()
        self.send_body(200, self.page("访问统计", body, admin=True))

    def admin_post(self, p):
        form = self.read_form()
        if p == "/login":
            if not self.rate_ok():
                self.send_body(429, self.page("锁定", '<div class="card auth-card"><div class="banner banner-bad">尝试过多，10分钟后再试</div></div>'))
                return
            if check_pw((form.get("pw") or [""])[0], self.cfg["salt"], self.cfg["hash"]):
                LOGIN_FAILS.pop(self.client_ip(), None)
                self.redirect("/admin", "dgst_auth=" + make_cookie(self.cfg["secret"], "admin", 7 * 86400)
                              + "; Path=/; HttpOnly; SameSite=Strict")
            else:
                self.rate_fail()
                ip = self.client_ip()
                st = LOGIN_FAILS.get(ip, [0, 0])
                # 撞库告警：累计失败≥3次即推飞书，同IP 30分钟最多一次
                if st[0] >= 3 and time.time() - ADMIN_ALERT.get(ip, 0) > 1800:
                    ADMIN_ALERT[ip] = time.time()
                    locked = "已自动锁定10分钟" if time.time() < st[1] else "临近锁定(5次/10分钟)"
                    feishu_alert(
                        "【AI精选·安全日报】⚠️ 管理后台遭密码爆破\n"
                        f"来源IP: {ip}\n"
                        f"累计失败: {st[0]} 次\n"
                        f"当前状态: {locked}\n"
                        f"时间(UTC): {time.strftime('%F %T')}\n"
                        "— ai-digest web 守护")
                self.send_body(401, self.page("管理登录", '<div class="card auth-card">'
                                              '<div class="banner banner-bad">密码错误</div></div>'))
        elif p == "/admin/save":
            if not self.cur_admin():
                self.redirect("/login")
                return
            name = safe_path((form.get("f") or [""])[0])
            content = (form.get("content") or [""])[0]
            if not name:
                self.send_body(400, self.page("错误", '<div class="card banner-bad">文件名非法（*.md，字母数字-_点）</div>', admin=True))
                return
            with open(os.path.join(REPORTS, name), "w", encoding="utf-8") as fh:
                fh.write(content)
            self.redirect("/admin?f=" + name)
        elif p == "/admin/delete":
            if not self.cur_admin():
                self.redirect("/login")
                return
            name = safe_path((form.get("f") or [""])[0])
            if name and os.path.exists(os.path.join(REPORTS, name)):
                os.remove(os.path.join(REPORTS, name))
            self.redirect("/admin")
        else:
            self.send_body(404, self.page("404", '<div class="card">页面不存在</div>'))


def main():
    if len(sys.argv) >= 3 and sys.argv[1] in ("init", "set-password"):
        mode, pw = sys.argv[1], sys.argv[2]
        cfg = load_cfg()
        if mode == "init" and os.path.exists(CFG_PATH):
            sys.exit("已初始化；改密码用 set-password")
        if mode == "init":
            cfg["secret"] = secrets.token_hex(32)
        salt = secrets.token_hex(16)
        cfg.update(salt=salt, hash=pbkdf2(pw, salt))
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.chmod(CFG_PATH, 0o600)
        print(f"{mode}: ok")
        return
    Handler.cfg = load_cfg()
    Handler.mode = MODE
    os.makedirs(REPORTS, exist_ok=True)
    if MODE == "front":
        db().close()  # 建库
    srv = ThreadingHTTPServer((os.environ.get("WEB_BIND", "0.0.0.0"), PORT), Handler)
    print(f"ai-digest web[{MODE}] on :{PORT}  reports={REPORTS}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
