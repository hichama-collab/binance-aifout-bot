#!/usr/bin/env python3
import os
import csv
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, jsonify

app = Flask(__name__)

BOT_LOG_DIR = Path(os.environ.get("BOT_LOG_DIR", "./data/logs"))

def load_trades_csv():
    trades = []
    for f in BOT_LOG_DIR.glob("*_trades.csv"):
        try:
            with f.open() as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    trades.append(row)
        except Exception:
            continue
    return trades

@app.route("/api/trades")
def api_trades():
    trades = load_trades_csv()
    by_symbol = {}

    for t in trades:
        sym = t.get("symbol") or ""
        if not sym:
            continue
        by_symbol.setdefault(sym, []).append(t)

    rows = []
    for sym, items in by_symbol.items():
        first_ts = min(i["ts_utc"] for i in items if "ts_utc" in i)
        last_ts = max(i["ts_utc"] for i in items if "ts_utc" in i)
        net = 0.0
        for i in items:
            try:
                net += float(i.get("pnl", 0))
            except Exception:
                pass

        rows.append({
            "symbol": sym,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "net_usdc": net,
            "trades": len(items)
        })

    return jsonify({"ok": True, "rows": rows})

@app.route("/api/wallet")
def api_wallet():
    # Placeholder wallet logic; real logic should compute USDC value
    rows = []
    MIN_USDC = 1.0

    # Example structure (replace with real wallet data)
    wallet_data = []

    for r in wallet_data:
        try:
            usdc_val = float(r.get("usdc", 0))
        except Exception:
            usdc_val = 0.0
        if usdc_val >= MIN_USDC:
            rows.append(r)

    return jsonify({"ok": True, "rows": rows})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
