#!/usr/bin/env bash
# etf-radar 服务器端安装(在VPS上以root运行, 幂等)
# 1) systemd: etf-radar-web.service(常驻,127.0.0.1:8891)
#             etf-radar.service/.timer(工作日 08:15 UTC = 北京16:15, Persistent补跑)
# 2) Caddy: etf.example.com -> 127.0.0.1:8891 (先备份->validate->reload)
# 3) sentinel whitelist + --learn (铁律: 新增units必须learn, 否则巡检误报)
set -euo pipefail
DST=/opt/etf-radar
DOMAIN=etf.example.com
PORT=8891

cd "$DST"
if [ ! -f config.env ]; then
  echo "ERROR: $DST/config.env 不存在。先: cp config.env.example config.env 并填写" >&2
  exit 1
fi
if [ ! -f web_config.json ]; then
  echo "ERROR: web口令未初始化。先: python3 $DST/web.py init <口令>" >&2
  exit 1
fi
echo "[0/5] config ok"

cat > /etc/systemd/system/etf-radar-web.service <<EOF
[Unit]
Description=etf-radar web dashboard (127.0.0.1:$PORT)
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $DST/web.py
WorkingDirectory=$DST
Environment=WEB_BIND=127.0.0.1
Environment=WEB_PORT=$PORT
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
MemoryMax=64M
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DST
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/etf-radar.service <<EOF
[Unit]
Description=etf-radar daily pipeline (collect->analyze->report->mail)
After=network-online.target

[Service]
Type=oneshot
ExecStart=$DST/run.sh
WorkingDirectory=$DST
Environment=PYTHONUNBUFFERED=1
PrivateTmp=true
NoNewPrivileges=true
EOF

cat > /etc/systemd/system/etf-radar.timer <<EOF
[Unit]
Description=etf-radar morning report pipeline (Mon-Fri 00:30 UTC = Beijing 08:30)

[Timer]
OnCalendar=Mon..Fri *-*-* 00:30:00 UTC
Persistent=true
Unit=etf-radar.service

[Install]
WantedBy=timers.target
EOF

# 周报: 周六 8:40 北京(覆盖完整一周)
cat > /etc/systemd/system/etf-radar-weekly.service <<EOF
[Unit]
Description=etf-radar weekly report (report+mail+push)

[Service]
Type=oneshot
ExecStart=$DST/weekly.sh
WorkingDirectory=$DST
Environment=PYTHONUNBUFFERED=1
PrivateTmp=true
NoNewPrivileges=true
EOF

cat > /etc/systemd/system/etf-radar-weekly.timer <<EOF
[Unit]
Description=etf-radar weekly report (Sat 00:40 UTC = Beijing 08:40)

[Timer]
OnCalendar=Sat *-*-* 00:40:00 UTC
Persistent=false
Unit=etf-radar-weekly.service

[Install]
WantedBy=timers.target
EOF

# 盘中速报: 交易时段每小时(北京 10/11/13/14/15 = UTC 02/03/05/06/07)
cat > /etc/systemd/system/etf-radar-intraday.service <<EOF
[Unit]
Description=etf-radar intraday hourly quote push

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $DST/intraday.py
WorkingDirectory=$DST
Environment=PYTHONUNBUFFERED=1
PrivateTmp=true
NoNewPrivileges=true
EOF

cat > /etc/systemd/system/etf-radar-intraday.timer <<EOF
[Unit]
Description=etf-radar intraday push (Beijing 10/11/13/14/15 on trading days)

[Timer]
OnCalendar=Mon..Fri *-*-* 02:00:00 UTC
OnCalendar=Mon..Fri *-*-* 03:00:00 UTC
OnCalendar=Mon..Fri *-*-* 05:00:00 UTC
OnCalendar=Mon..Fri *-*-* 06:00:00 UTC
OnCalendar=Mon..Fri *-*-* 07:00:00 UTC
Unit=etf-radar-intraday.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now etf-radar-web.service
systemctl enable --now etf-radar.timer
systemctl enable --now etf-radar-weekly.timer
systemctl enable --now etf-radar-intraday.timer
echo "[1/5] systemd units enabled"

# 首次部署: 拉齐历史数据 + 回测基线 (已有库则幂等增量)
/usr/bin/python3 "$DST/collect.py"
/usr/bin/python3 "$DST/analyze.py" --backtest >/dev/null && echo "[2/5] backtest baseline saved"

if ! grep -q "$DOMAIN" /etc/caddy/Caddyfile; then
  cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-etf
  cat >> /etc/caddy/Caddyfile <<EOF

http://$DOMAIN {
    reverse_proxy 127.0.0.1:$PORT
}
$DOMAIN {
    reverse_proxy 127.0.0.1:$PORT
}
EOF
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  systemctl reload caddy
  echo "[3/5] caddy vhost added + reloaded"
else
  echo "[3/5] caddy vhost already present, skip"
fi

# 铁律: 新增systemd units后必须learn, 否则sentinel巡检误报
if [ -x /opt/sentinel/bin/zombie-scan.sh ]; then
  for u in etf-radar-web.service etf-radar.service etf-radar-weekly.service etf-radar-intraday.service; do
    grep -qx "/etc/systemd/system/$u" /opt/sentinel/state/whitelist.units 2>/dev/null \
      || echo "/etc/systemd/system/$u" >> /opt/sentinel/state/whitelist.units
  done
  bash /opt/sentinel/bin/zombie-scan.sh --learn && echo "[4/5] sentinel learned"
else
  echo "[4/5] WARN: sentinel not found, skip learn"
fi

sleep 3
systemctl is-active etf-radar-web && echo "[5/5] web active on 127.0.0.1:$PORT"
echo "INSTALL_DONE"
echo "提醒: DNS 加 A 记录 $DOMAIN -> YOUR_SERVER_IP 后, https://$DOMAIN 生效"
echo "首跑: systemctl start etf-radar  (或等最近一个工作日 16:15 北京)"
