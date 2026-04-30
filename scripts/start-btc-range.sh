#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SYMBOL="${1:-${BTC_RANGE_SYMBOL:-BTCUSDC}}"

# shellcheck disable=SC1091
source .venv/bin/activate

export BTC_RANGE_SYMBOL="${SYMBOL}"
export BTC_RANGE_PROFILE="${BTC_RANGE_PROFILE:-default}"

exec python3 -m btc_range_v1.main
