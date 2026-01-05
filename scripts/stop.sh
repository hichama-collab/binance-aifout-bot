#!/usr/bin/env bash
set -euo pipefail

pkill -f "python3 main.py" 2>/dev/null || true
echo "OK: stopped (if running)"
