#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

TARGET="${1:-ALL}"

if [ "$TARGET" = "ALL" ]; then
  find data/logs -type f -name "*_trades.csv" -delete 2>/dev/null || true
  echo "OK: csv cleaned (ALL)"
  exit 0
fi

find data/logs -type f -name "${TARGET}_*_trades.csv" -delete 2>/dev/null || true
echo "OK: csv cleaned (${TARGET})"
