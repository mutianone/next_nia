#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$PROJECT_ROOT/scripts/common.sh"

check_platform
check_node
find_python
cd "$PROJECT_ROOT"

[ -f package.json ] || fail '项目根目录缺少 package.json。'
[ -f backend/requirements.txt ] || fail '缺少 backend/requirements.txt。'

if [ ! -d backend/.venv ]; then
  printf '正在创建后端虚拟环境……\n'
  "$PYTHON_BIN" -m venv backend/.venv
fi
check_venv

printf '正在安装 Python 依赖……\n'
backend/.venv/bin/python -m pip install -r backend/requirements.txt

printf '正在安装前端依赖……\n'
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi

printf '\n依赖安装完成。运行 bash scripts/dev.sh 启动本地预览。\n'
