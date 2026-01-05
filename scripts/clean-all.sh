#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/clean-logs.sh "${1:-ALL}"
./scripts/clean-csv.sh "${1:-ALL}"
