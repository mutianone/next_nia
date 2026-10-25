#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_ROOT/scripts/common.sh"
check_platform
check_venv
cd "$PROJECT_ROOT"
[ -f dist/index.html ] || fail '请先运行 npm run build。'
export NEXT_NIA_SERVE_DIST=1
exec backend/.venv/bin/python -m uvicorn backend.app.main:app --host "${NEXT_NIA_HOST:-127.0.0.1}" --port "${NEXT_NIA_PORT:-8080}" --workers 1
