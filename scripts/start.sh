#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SYMBOL="${1:-}"
if [ -z "$SYMBOL" ]; then
  if [ -f "symbol" ]; then
    SYMBOL="$(head -n 1 symbol | tr -d ' \t\r\n')"
  fi
fi

if [ -z "$SYMBOL" ]; then
  echo "Usage: ./scripts/start.sh SYMBOL"
  echo "Or put SYMBOL in ./symbol then run ./scripts/start.sh"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

PROFILE="${PROFILE:-strict}"
STRATEGY="${STRATEGY:-momentum}"

export PROFILE
export STRATEGY

exec python3 main.py "$SYMBOL"
