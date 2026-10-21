#!/usr/bin/env bash
# Shared checks for macOS and Linux. Source this file from another script.

fail() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

check_platform() {
  case "$(uname -s)" in
    Darwin|Linux) ;;
    *) fail '当前脚本仅支持 macOS 和 Linux。' ;;
  esac
}

check_node() {
  command -v node >/dev/null 2>&1 || fail '未找到 Node.js，请先安装 20.19+ 的 20.x 版本或 22.12 及以上版本。'
  command -v npm >/dev/null 2>&1 || fail '未找到 npm，请检查 Node.js 安装。'
  node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22 ? 0 : 1)' \
    || fail "当前 Node.js $(node --version) 不满足要求，请使用 20.19+ 的 20.x 版本或 22.12 及以上版本；不支持 21.x 和 22.0–22.11。"
}

find_python() {
  local candidate
  for candidate in python3 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      return
    fi
  done
  fail '未找到 Python 3.11 或更高版本。请安装 Python 后重新运行。'
}

check_venv() {
  [ -x "$PROJECT_ROOT/backend/.venv/bin/python" ] || fail '未找到后端虚拟环境，请先运行 bash scripts/setup.sh。'
  "$PROJECT_ROOT/backend/.venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || fail '现有虚拟环境的 Python 版本低于 3.11，请使用合适版本重新创建 backend/.venv。'
}
