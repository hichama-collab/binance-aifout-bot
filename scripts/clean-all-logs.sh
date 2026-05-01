#!/usr/bin/env bash
set -euo pipefail
# Clean ALL logs (dry + live + main + btc)
cd "$(dirname "$0")/.."
rm -rf data/logs/*
echo "All logs cleaned"
