#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

TARGET="${1:-ALL}"

if [ "$TARGET" = "ALL" ]; then
  rm -f data/logs/*_trades.log data/logs/*_errors.log 2>/dev/null || true
  echo "OK: logs cleaned (ALL)"
  exit 0
fi

rm -f "data/logs/${TARGET}_trades.log" "data/logs/${TARGET}_errors.log" 2>/dev/null || true
echo "OK: logs cleaned (${TARGET})"
