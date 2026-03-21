#!/usr/bin/env bash
set -euo pipefail

BOT_ROOT="${BOT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON="${PYTHON_BIN}"
elif [ -x "${BOT_ROOT}/.venv/bin/python3" ]; then
  PYTHON="${BOT_ROOT}/.venv/bin/python3"
elif [ -x "${BOT_ROOT}/.venv/bin/python" ]; then
  PYTHON="${BOT_ROOT}/.venv/bin/python"
else
  PYTHON="python3"
fi

export BOT_ROOT_DIR="${BOT_ROOT_DIR:-${BOT_ROOT}}"
export BOT_LOG_DIR="${BOT_LOG_DIR:-${BOT_ROOT}/data/logs}"
export TRADE_MEMORY_DB_PATH="${TRADE_MEMORY_DB_PATH:-${BOT_ROOT}/data/runtime/trade_memory.sqlite3}"

cd "${BOT_ROOT}"

"${PYTHON}" - <<'PY'
import json
from core.trade_memory import sync_trade_memory

info = sync_trade_memory()
print(json.dumps(info, ensure_ascii=True))
if not info.get("ok"):
    raise SystemExit(1)
PY
