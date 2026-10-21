#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$PROJECT_ROOT/scripts/common.sh"

check_platform
check_node
check_venv
cd "$PROJECT_ROOT"

[ -f node_modules/vite/bin/vite.js ] || fail '未找到 Vite，请先运行 bash scripts/setup.sh。'
backend/.venv/bin/python -c 'import fastapi, uvicorn, sqlalchemy, psycopg, httpx, pypdf, python_multipart' \
  || fail '后端依赖不完整，请重新运行 bash scripts/setup.sh。'

# Check before launching either service. Never stop an existing process.
backend/.venv/bin/python - <<'PY'
import socket
import sys
import errno

for port in (8000, 5173):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                print(f"错误：127.0.0.1:{port} 已被占用，请先检查并自行停止对应服务。", file=sys.stderr)
            else:
                print(f"错误：无法监听本地端口 {port}，请检查系统或沙箱网络权限（{error.strerror}）。", file=sys.stderr)
            sys.exit(1)
PY

backend_pid=''
frontend_pid=''
cleanup() {
  trap - EXIT INT TERM
  printf '\n正在停止本次启动的服务……\n'
  for service_pid in "$frontend_pid" "$backend_pid"; do
    if [ -n "$service_pid" ] && kill -0 "$service_pid" 2>/dev/null; then
      kill -TERM "$service_pid" 2>/dev/null || true
    fi
  done
  for service_pid in "$frontend_pid" "$backend_pid"; do
    if [ -n "$service_pid" ]; then
      wait "$service_pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

backend/.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 &
backend_pid=$!

if ! backend/.venv/bin/python - <<'PY'
import time
import urllib.error
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(50):
    try:
        with opener.open("http://127.0.0.1:8000/api/health", timeout=1) as response:
            if response.status == 200:
                raise SystemExit(0)
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(0.2)
raise SystemExit(1)
PY
then
  fail '后端健康检查未通过，请查看上方日志。正式环境还需检查 PostgreSQL 连接和数据库权限。'
fi

node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort &
frontend_pid=$!

printf '\n本地预览：http://127.0.0.1:5173\n后端健康：http://127.0.0.1:8000/api/health\n按 Ctrl+C 停止本次启动的前后端服务。\n\n'

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done

fail '有服务退出，已停止本次启动的另一项服务。请检查上方日志后重新启动。'
