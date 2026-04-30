#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../dashboard_btc_range"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "OK: btc range dashboard venv ready"
