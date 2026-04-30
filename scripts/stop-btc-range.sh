#!/usr/bin/env bash
set -euo pipefail

pkill -f "python3 -m btc_range_v1.main" 2>/dev/null || true
echo "OK: btc_range_v1 stopped (if running)"
