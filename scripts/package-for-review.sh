#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-"$ROOT/../binance-aifout-bot-review.zip"}"

rm -f "$OUT"
cd "$ROOT"

zip -r "$OUT" . \
  -x ".git/*" \
  -x ".env" \
  -x ".service.env" \
  -x ".btc_range.env" \
  -x ".btc_range_dash.env" \
  -x "dashboard/botdash.env" \
  -x "config/.service.env" \
  -x "data/runtime/*" \
  -x "data/logs/*" \
  -x "logs/*" \
  -x "__pycache__/*" \
  -x "*/__pycache__/*" \
  -x ".pytest_cache/*" \
  -x "venv/*" \
  -x ".venv/*" \
  -x "*.pyc" \
  -x "*.pyo"

echo "Created $OUT"
