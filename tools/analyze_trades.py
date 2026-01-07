#!/usr/bin/env python3
# tools/analyze_trades.py
# Analyse rapide de data/trades.csv (winrate, pnl, raisons, durées)
import csv, sys, statistics
from collections import Counter, defaultdict
from datetime import datetime

def to_float(x):
    try: return float(x)
    except: return None

def parse_ts(s):
    # accepte epoch seconds/ms ou iso
    if not s: return None
    s = s.strip()
    if s.isdigit():
        v = int(s)
        if v > 10_000_000_000:  # ms
            v = v/1000.0
        return datetime.utcfromtimestamp(v)
    # iso fallback
    try:
        return datetime.fromisoformat(s.replace("Z","+00:00")).replace(tzinfo=None)
    except:
        return None

def main(path):
    rows = []
    with open(path, newline='') as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)

    if not rows:
        print("EMPTY")
        return 2

    # SELL rows are the only ones with realized pnl in most schemas
    sell = [x for x in rows if (x.get("event","").upper().startswith("SELL")) or (x.get("side","").upper()=="SELL")]
    pnl_vals = []
    reasons = Counter()
    by_symbol = defaultdict(list)

    # duration if matching trade_id or position_id present
    buys = {}
    for x in rows:
        ev = (x.get("event","") or "").upper()
        if ev.startswith("BUY") or (x.get("side","") or "").upper()=="BUY":
            key = x.get("trade_id") or x.get("position_id") or x.get("orderId") or x.get("buy_order_id")
            if key:
                buys[key] = x

    durations = []
    for x in sell:
        pnl = to_float(x.get("pnl") or x.get("pnl_usdc") or x.get("pnl_quote"))
        if pnl is not None:
            pnl_vals.append(pnl)
            by_symbol[x.get("symbol","?")].append(pnl)

        reason = (x.get("reason") or x.get("exit_reason") or x.get("exit") or "").upper() or "UNKNOWN"
        reasons[reason] += 1

        key = x.get("trade_id") or x.get("position_id") or x.get("orderId") or x.get("sell_order_id")
        b = buys.get(key)
        if b:
            t0 = parse_ts(b.get("ts") or b.get("timestamp") or b.get("time"))
            t1 = parse_ts(x.get("ts") or x.get("timestamp") or x.get("time"))
            if t0 and t1:
                durations.append((t1-t0).total_seconds())

    def q(p, arr):
        if not arr: return None
        arr2 = sorted(arr)
        k = int(round((len(arr2)-1)*p))
        return arr2[k]

    print(f"ROWS={len(rows)} SELL_ROWS={len(sell)}")
    if pnl_vals:
        wins = sum(1 for v in pnl_vals if v > 0)
        winrate = wins/len(pnl_vals)*100.0
        print(f"PNL_SUM={sum(pnl_vals):.4f} PNL_AVG={statistics.mean(pnl_vals):.6f} WINRATE={winrate:.1f}%")
        print(f"PNL_MIN={min(pnl_vals):.6f} PNL_P10={q(0.10,pnl_vals):.6f} PNL_P50={q(0.50,pnl_vals):.6f} PNL_P90={q(0.90,pnl_vals):.6f} PNL_MAX={max(pnl_vals):.6f}")
    else:
        print("NO_PNL_VALUES_FOUND (check csv columns: pnl/pnl_usdc/pnl_quote)")

    if reasons:
        print("EXIT_REASONS:")
        for k,v in reasons.most_common():
            print(f"  {k}: {v}")

    if durations:
        print(f"HOLD_SEC_AVG={statistics.mean(durations):.1f} HOLD_P50={q(0.50,durations):.1f} HOLD_P90={q(0.90,durations):.1f} HOLD_MAX={max(durations):.1f}")
    else:
        print("NO_HOLD_DURATIONS (need shared key: trade_id/position_id/orderId)")

    if by_symbol:
        print("BY_SYMBOL_PNL_SUM_TOP:")
        sums = sorted(((sym, sum(vals), len(vals)) for sym,vals in by_symbol.items()), key=lambda t: t[1], reverse=True)
        for sym, s, n in sums[:10]:
            print(f"  {sym}: sum={s:.4f} n={n}")

    return 0

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv)>1 else "data/trades.csv"
    raise SystemExit(main(path))
