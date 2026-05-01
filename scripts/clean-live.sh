#!/usr/bin/env bash
set -euo pipefail
# Clean only live logs
cd "$(dirname "$0")/.."
rm -rf data/logs/live/main/* data/logs/live/btc_range/*
echo "Live logs cleaned"
