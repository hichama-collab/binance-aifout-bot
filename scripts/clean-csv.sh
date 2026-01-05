#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

TARGET="${1:-ALL}"

if [ "$TARGET" = "ALL" ]; then
  rm -f data/logs/*_trades.csv 2>/dev/null || true
  echo "OK: csv cleaned (ALL)"
  exit 0
fi

rm -f "data/logs/${TARGET}_trades.csv" 2>/dev/null || true
echo "OK: csv cleaned (${TARGET})"
