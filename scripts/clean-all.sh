#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/logs-clean.sh "${1:-ALL}"
./scripts/csv-clean.sh "${1:-ALL}"
