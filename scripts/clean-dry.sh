#!/usr/bin/env bash
set -euo pipefail
# Clean only dry-run logs
cd "$(dirname "$0")/.."
rm -rf data/logs/dry/main/* data/logs/dry/btc_range/*
echo "Dry-run logs cleaned"
