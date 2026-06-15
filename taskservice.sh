#!/bin/bash

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_bot_status() {
  echo "== systemd =="
  sudo systemctl status binance-aifout-bot.service --no-pager

  echo
  echo "== runtime =="
  if [ -f "$ROOT_DIR/data/runtime/bot_status.json" ]; then
    python3 - "$ROOT_DIR/data/runtime/bot_status.json" <<'PY'
import json
import sys
import time

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

ts = float(data.get("ts") or 0)
age = max(0.0, time.time() - ts) if ts else None
print(f"symbol={data.get('symbol', '')} state={data.get('state', '')} age_sec={age:.1f}" if age is not None else "age_sec=NA")
print(f"bid={data.get('bid', '')} ask={data.get('ask', '')} spread_pct={data.get('spread_pct', '')}")
print(f"mom_pct={data.get('mom_pct', '')} range_pct={data.get('mom_range_pct', '')} up_ratio={data.get('up_ratio', '')}")
PY
  else
    echo "missing data/runtime/bot_status.json"
  fi

  echo
  echo "== selected token =="
  if [ -f "$ROOT_DIR/.service.env" ]; then
    cat "$ROOT_DIR/.service.env"
  else
    echo "missing .service.env"
  fi

  echo
  echo "== position =="
  if [ -f "$ROOT_DIR/data/runtime/position.json" ]; then
    cat "$ROOT_DIR/data/runtime/position.json"
    echo
  else
    echo "missing data/runtime/position.json"
  fi

  echo
  echo "== latest bot log =="
  latest_log="$(find "$ROOT_DIR/data/logs/live/main" -type f -name '*_trades.log' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
  if [ -n "$latest_log" ] && [ -f "$latest_log" ]; then
    stat -c '%y %s %n' "$latest_log"
    tail -20 "$latest_log"
  else
    echo "no trade log found"
  fi

  echo
  echo "== recent errors =="
  journalctl -u binance-aifout-bot.service --since "15 minutes ago" --no-pager \
    | grep -Ei "traceback|exception|error|failed|critical" || true
}

case "$1" in
  1)
    journalctl -u token-profile-selector.service -n 20 --no-pager
    ;;
  2)
    show_bot_status
    ;;
  3)
    sudo systemctl start binance-aifout-bot.service
    ;;
  4)
    sudo systemctl stop binance-aifout-bot.service
    ;;
  5)
    sudo systemctl status botdash.service --no-pager
    ;;
  6)
    sudo systemctl stop botdash.service
    ;;
  7)
    sudo systemctl start botdash.service
    ;;
  *)
    echo "Usage: $0 {1|2|3|4|5|6|7}"
    echo "  1 selector logs"
    echo "  2 bot status + runtime + latest bot log"
    echo "  3 start bot"
    echo "  4 stop bot"
    echo "  5 dashboard status"
    echo "  6 stop dashboard"
    echo "  7 start dashboard"
    exit 1
    ;;
esac
