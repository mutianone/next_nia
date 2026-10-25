#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$#" -eq 1 ] && [ "${1:-}" = "--reset" ]; then
  "$PROJECT_ROOT/backend/.venv/bin/python" "$PROJECT_ROOT/scripts/presentation-reset.py"
elif [ "$#" -gt 0 ]; then
  printf '用法：bash scripts/presentation.sh [--reset]\n' >&2
  exit 1
fi
# Compatibility launcher: selects the populated workspace only. All AI uses real providers.
export NEXT_NIA_PRESENTATION=1
export NEXT_NIA_DATA_DIR="$PROJECT_ROOT/backend/presentation-data"
exec bash "$PROJECT_ROOT/scripts/dev.sh"
