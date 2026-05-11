#!/usr/bin/env python3
"""
PnL Audit — calcule le vrai PnL net (avec frais) depuis les CSV de trades.

Usage:
    python3 tools/pnl_audit.py [logs_dir] [--fee-rate 0.001]
    python3 tools/pnl_audit.py data/logs/live/main
    python3 tools/pnl_audit.py data/logs/live/main --fee-rate 0.00075  # BNB discount
"""
import sys
import csv
import argparse
import statistics
from pathlib import Path


def audit(logs_dir: Path, fee_rate: float = 0.001) -> dict:
    trade_details = []
    entry_reasons: dict = {}
    exit_reasons: dict = {}
    open_buy = None

    csv_files = sorted(logs_dir.rglob("*_trades.csv"))
    if not csv_files:
        print(f"No *_trades.csv found in {logs_dir}")
        return {}

    for csv_file in csv_files:
        symbol = csv_file.stem.split("_trades")[0]
        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                event = row.get("event", "")
                if event == "BUY_FILLED":
                    try:
                        open_buy = {
                            "symbol": symbol,
                            "price": float(row["price"]),
                            "qty": float(row["qty"]),
                            "notional": float(row["qty"]) * float(row["price"]),
                            "reason": row.get("reason", ""),
                        }
                        r = open_buy["reason"].split()[0] if open_buy["reason"] else "?"
                        entry_reasons[r] = entry_reasons.get(r, 0) + 1
                    except Exception:
                        pass
                elif event == "SELL_FILLED" and open_buy:
                    try:
                        sell_price = float(row["price"])
                        sell_qty = float(row["qty"])
                        sell_notional = sell_qty * sell_price
                        buy_notional = open_buy["notional"]
                        fees = (buy_notional + sell_notional) * fee_rate
                        pnl_real = sell_notional - buy_notional - fees
                        pnl_logged = float(row["pnl"]) if row.get("pnl") else 0.0
                        delta_pct = (sell_price - open_buy["price"]) / open_buy["price"] * 100

                        r = row.get("reason", "?").split()[0]
                        exit_reasons[r] = exit_reasons.get(r, 0) + 1
                        trade_details.append({
                            "symbol": open_buy["symbol"],
                            "buy_price": open_buy["price"],
                            "sell_price": sell_price,
                            "qty": sell_qty,
                            "buy_notional": buy_notional,
                            "sell_notional": sell_notional,
                            "fees": fees,
                            "pnl_real": pnl_real,
                            "pnl_logged": pnl_logged,
                            "delta_pct": delta_pct,
                            "exit_reason": r,
                            "entry_reason": open_buy["reason"].split()[0] if open_buy["reason"] else "?",
                        })
                        open_buy = None
                    except Exception:
                        open_buy = None

    if not trade_details:
        print("No completed trades found.")
        return {}

    n = len(trade_details)
    total_pnl_real = sum(t["pnl_real"] for t in trade_details)
    total_pnl_logged = sum(t["pnl_logged"] for t in trade_details)
    total_volume = sum(t["buy_notional"] + t["sell_notional"] for t in trade_details)
    total_fees = sum(t["fees"] for t in trade_details)
    n_wins = sum(1 for t in trade_details if t["pnl_real"] > 0)
    deltas = [t["delta_pct"] for t in trade_details]
    below_threshold = sum(1 for d in deltas if d < fee_rate * 2 * 100)

    # Per-symbol
    sym_pnl: dict = {}
    for t in trade_details:
        sym_pnl.setdefault(t["symbol"], 0.0)
        sym_pnl[t["symbol"]] += t["pnl_real"]
    ranked = sorted(sym_pnl.items(), key=lambda x: -x[1])

    print("=" * 60)
    print(f"PnL AUDIT  (fee_rate={fee_rate*100:.3f}% per leg)")
    print("=" * 60)
    print(f"Trades         : {n}")
    print(f"Volume total   : {total_volume:.2f} USDC")
    print(f"Fees totaux    : {total_fees:.4f} USDC ({total_fees/total_volume*100:.3f}% du volume)")
    print(f"PnL logge bot  : {total_pnl_logged:+.4f} USDC")
    print(f"PnL REEL       : {total_pnl_real:+.4f} USDC")
    print(f"Ecart log/reel : {total_pnl_real - total_pnl_logged:+.4f} USDC")
    print(f"Winrate reel   : {n_wins}/{n} = {n_wins/n*100:.1f}%")
    print(f"PnL moyen/trade: {total_pnl_real/n:+.4f} USDC")
    print(f"Avg buy size   : {sum(t['buy_notional'] for t in trade_details)/n:.2f} USDC")
    print()
    print(f"Delta prix:")
    print(f"  Min : {min(deltas):+.4f}%")
    print(f"  Max : {max(deltas):+.4f}%")
    print(f"  Moy : {statistics.mean(deltas):+.4f}%")
    print(f"  < breakeven ({fee_rate*200:.2f}%): {below_threshold}/{n} = {below_threshold/n*100:.0f}%")
    print()
    print("Raisons entree:")
    for r, c in sorted(entry_reasons.items(), key=lambda x: -x[1]):
        print(f"  {r:25s} {c:4d}")
    print()
    print("Raisons sortie:")
    for r, c in sorted(exit_reasons.items(), key=lambda x: -x[1]):
        print(f"  {r:25s} {c:4d}")
    print()
    print("Top tokens (PnL reel):")
    for sym, pnl in ranked[:5]:
        cnt = sum(1 for t in trade_details if t["symbol"] == sym)
        print(f"  {sym:15s} {cnt:3d} trades  {pnl:+.4f} USDC")
    print()
    print("Flop tokens:")
    for sym, pnl in ranked[-5:]:
        cnt = sum(1 for t in trade_details if t["symbol"] == sym)
        print(f"  {sym:15s} {cnt:3d} trades  {pnl:+.4f} USDC")

    return {
        "n": n, "total_pnl_real": total_pnl_real, "total_fees": total_fees,
        "winrate": n_wins / n, "avg_pnl": total_pnl_real / n,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PnL Audit tool")
    parser.add_argument("logs_dir", nargs="?", default="data/logs/live/main",
                        help="Path to logs directory containing *_trades.csv")
    parser.add_argument("--fee-rate", type=float, default=0.001,
                        help="Fee rate per leg (default: 0.001 = 0.1%%)")
    args = parser.parse_args()

    path = Path(args.logs_dir)
    if not path.exists():
        print(f"Directory not found: {path}")
        sys.exit(1)

    audit(path, fee_rate=args.fee_rate)
