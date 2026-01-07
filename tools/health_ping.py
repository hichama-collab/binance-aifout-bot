#!/usr/bin/env python3
# tools/health_ping.py
# Check simple "is bot alive" signals for cron/systemd: last log activity + last error + last trade.
import os, sys, time, csv
from pathlib import Path

def tail(path, n=30):
    try:
        with open(path, "r", errors="ignore") as f:
            return f.readlines()[-n:]
    except FileNotFoundError:
        return []

def last_csv_row(path):
    try:
        with open(path, newline='') as f:
            r = csv.DictReader(f)
            last = None
            for row in r:
                last = row
            return last
    except FileNotFoundError:
        return None

def main():
    trades_csv = Path("data/trades.csv")
    trades_log = Path("data/trades.log")
    errors_log = Path("data/errors.log")

    now = time.time()
    # Liveness: trades.log mtime or trades.csv mtime within 120s
    mt = None
    for p in (trades_log, trades_csv):
        if p.exists():
            mt = p.stat().st_mtime if mt is None else max(mt, p.stat().st_mtime)

    stale = True if mt is None else (now - mt > 120)

    last_trade = last_csv_row(trades_csv) if trades_csv.exists() else None
    last_err_lines = tail(errors_log, 8)

    print("HEALTH")
    print(f"stale={stale} last_activity_age_sec={(now-mt):.1f}" if mt else "stale=True last_activity=NONE")
    if last_trade:
        ev = last_trade.get("event") or last_trade.get("side") or "?"
        sym = last_trade.get("symbol","?")
        ts = last_trade.get("ts") or last_trade.get("timestamp") or ""
        pnl = last_trade.get("pnl") or last_trade.get("pnl_usdc") or ""
        print(f"last_trade event={ev} symbol={sym} ts={ts} pnl={pnl}")
    else:
        print("last_trade NONE")

    if last_err_lines:
        print("last_errors_tail:")
        for line in last_err_lines:
            print(line.rstrip())
    else:
        print("last_errors_tail EMPTY")

    # exit code: 0 ok, 2 stale
    return 2 if stale else 0

if __name__ == "__main__":
    raise SystemExit(main())
