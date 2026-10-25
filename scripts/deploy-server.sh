#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT=/root/nia
ID="${1:?缺少版本编号}"
[[ "$ID" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || { echo '版本编号不合法'; exit 1; }
[ "$(id -u)" = 0 ] || { echo '请使用 root 部署'; exit 1; }
command -v systemctl >/dev/null || { echo '服务器必须支持 systemd'; exit 1; }
command -v flock >/dev/null || { echo '请先安装 util-linux（flock）'; exit 1; }
mkdir -p "$ROOT/releases" "$ROOT/data" "$ROOT/backups"
exec 9>"$ROOT/deploy.lock"
flock -n 9 || { echo '已有部署正在进行，请稍后重试'; exit 1; }
RELEASE="$ROOT/releases/$ID"
[ ! -e "$RELEASE" ] || { echo '版本目录已存在，停止覆盖'; exit 1; }
if [ -e "$ROOT/current" ] && [ ! -L "$ROOT/current" ]; then
  echo '/root/nia/current 已存在且不是版本链接，停止部署以免覆盖。'; exit 1
fi
if [ -f /etc/systemd/system/nia.service ] && ! grep -q 'WorkingDirectory=/root/nia/current' /etc/systemd/system/nia.service; then
  echo '发现非本脚本管理的 nia.service，请先人工确认。'; exit 1
fi
FRAGMENT="$(systemctl show nia.service --property=FragmentPath --value 2>/dev/null || true)"
if [ -n "$FRAGMENT" ] && [ "$FRAGMENT" != /etc/systemd/system/nia.service ]; then
  echo 'nia.service 名称已由其他位置的服务占用，停止部署。'; exit 1
fi
OLD="$(readlink -f "$ROOT/current" 2>/dev/null || true)"
SWITCHED=0
STOPPED=0
UNIT=/etc/systemd/system/nia.service
UNIT_BACKUP="$ROOT/backups/nia-service-$ID"
[ ! -f "$UNIT" ] || cp -p "$UNIT" "$UNIT_BACKUP"
rollback() {
  code=$?
  trap - EXIT
  if [ "$code" -ne 0 ]; then
    echo '部署失败；保留日志、新版本和数据备份供排查。'
    if [ "$SWITCHED" = 1 ]; then
      systemctl stop nia.service || true
      if [ -n "$OLD" ] && [ -d "$OLD" ]; then
        ln -s "$OLD" "$ROOT/.rollback-$ID"
        mv -Tf "$ROOT/.rollback-$ID" "$ROOT/current"
        if [ -f "$UNIT_BACKUP" ]; then cp -p "$UNIT_BACKUP" "$UNIT"; fi
        systemctl daemon-reload
        systemctl restart nia.service || true
        echo '已尝试恢复旧代码；业务数据未回滚。'
      else
        echo '首次部署未通过检查，服务已停止。'
      fi
    elif [ "$STOPPED" = 1 ] && [ -n "$OLD" ]; then
      systemctl start nia.service || true
    fi
  fi
  exit "$code"
}
trap rollback EXIT
# Do not install/upgrade OS packages: package hooks may restart unrelated services.
# Runtime packages must already exist; Python dependencies stay inside this release's venv.
PYTHON=''
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; assert sys.version_info >= (3,11)' 2>/dev/null; then PYTHON="$candidate"; break; fi
done
[ -n "$PYTHON" ] || { echo '需要 Python 3.11+，推荐 Ubuntu 24.04 或 Debian 12。'; exit 1; }
for required in ffmpeg curl ss; do
  command -v "$required" >/dev/null || { echo "缺少 $required，请先由服务器管理员安装；脚本不会修改系统软件或其他服务。"; exit 1; }
done
"$PYTHON" -c 'import venv, ensurepip' || { echo 'Python 缺少 venv/ensurepip，请先由管理员补齐。'; exit 1; }
# An existing listener must belong to this deployment's managed systemd process.
"$PYTHON" - <<'CHECK_PORT'
import re, subprocess, sys
result = subprocess.run(['ss', '-H', '-ltnp', 'sport = :5173'], capture_output=True, text=True, check=True)
listeners = result.stdout.strip()
if listeners:
    pid = subprocess.run(['systemctl', 'show', 'nia.service', '--property=MainPID', '--value'], capture_output=True, text=True, check=True).stdout.strip()
    pids = set(re.findall(r'pid=(\d+)', listeners))
    if pid == '0' or not pids or pids != {pid}:
        sys.exit('5173 端口已被其他进程占用，停止部署；不会停止或修改占用进程。')
CHECK_PORT
mkdir "$RELEASE"
tar -xzf "$ROOT/incoming/$ID/release.tar.gz" -C "$RELEASE" --no-same-owner
[ -f "$RELEASE/dist/index.html" ] && [ -f "$RELEASE/backend/app/main.py" ]
"$PYTHON" -m venv "$RELEASE/backend/.venv"
"$RELEASE/backend/.venv/bin/python" -m pip install --disable-pip-version-check -r "$RELEASE/backend/requirements.txt"
# Import/start against an isolated empty database before touching the running service.
NEXT_NIA_DATA_DIR="$RELEASE/.preflight-data" NEXT_NIA_SERVE_DIST=1 "$RELEASE/backend/.venv/bin/python" -c "import sys; sys.path.insert(0, '$RELEASE'); from backend.app.main import app; assert app"
rm -rf "$RELEASE/.preflight-data"
# Check port before stopping anything. Never kill an unrelated service.
if ! systemctl is-active --quiet nia.service; then
  "$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("0.0.0.0",5173)); s.close()'
fi
if [ -f "$UNIT" ]; then systemctl stop nia.service; fi
STOPPED=1
# SQLite files are copied only while the application is stopped. Remote PostgreSQL is not included.
tar -czf "$ROOT/backups/data-$ID.tar.gz" -C "$ROOT" data
cat > "$UNIT" <<UNIT
[Unit]
Description=NIA web application
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
WorkingDirectory=$ROOT/current
Environment=NEXT_NIA_DATA_DIR=$ROOT/data
Environment=NEXT_NIA_SERVE_DIST=1
Environment=PYTHONUNBUFFERED=1
Environment=TZ=Asia/Shanghai
ExecStart=$ROOT/current/backend/.venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 5173 --workers 1
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
UNIT
chmod 644 "$UNIT"
ln -s "$RELEASE" "$ROOT/.current-$ID"
SWITCHED=1
mv -Tf "$ROOT/.current-$ID" "$ROOT/current"
systemctl daemon-reload
systemctl enable nia.service
systemctl restart nia.service
for attempt in $(seq 1 45); do
  if curl --fail --silent --max-time 3 http://127.0.0.1:5173/api/health | "$PYTHON" -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ok"' 2>/dev/null && curl --fail --silent --max-time 3 http://127.0.0.1:5173/ -o /dev/null; then
    echo "服务已启动，版本 $ID，数据目录 $ROOT/data"
    exit 0
  fi
  sleep 2
done
echo '健康检查未通过。服务器上可运行 journalctl -u nia -n 80 排查。'
exit 1
