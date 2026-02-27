#!/usr/bin/env python3
# tools/TokenRank1h.py
#
# Rank Binance Spot symbols by 1h % change using the last two closed 1h klines.
# Default: USDC-quoted spot symbols, top 10 by absolute % change (includes negative movers).
#
# Usage:
#   python3 tools/TokenRank1h.py
#   python3 tools/TokenRank1h.py --quote USDT --top 10 --mode abs
#   python3 tools/TokenRank1h.py --mode up
#   python3 tools/TokenRank1h.py --mode down
#
import argparse
import sys
import time
from typing import List, Tuple

import requests

BINANCE_REST = "https://api.binance.com"


def http_get_json(url: str, params: dict | None = None, timeout: float = 10.0, retries: int = 3):
    last_exc = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1.0 + i)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_exc = e
            time.sleep(0.5 + i)
    raise RuntimeError(f"GET failed: {url} params={params} err={last_exc}")


def get_spot_symbols(quote: str) -> List[str]:
    data = http_get_json(f"{BINANCE_REST}/api/v3/exchangeInfo")
    out: List[str] = []
    for s in data.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("isSpotTradingAllowed") is False:
            continue
        if s.get("quoteAsset") != quote:
            continue
        sym = s.get("symbol")
        if sym:
            out.append(sym)
    return out


def get_1h_change_pct(symbol: str) -> float | None:
    kl = http_get_json(
        f"{BINANCE_REST}/api/v3/klines",
        params={"symbol": symbol, "interval": "1h", "limit": 3},
        timeout=10.0,
        retries=3,
    )
    if not isinstance(kl, list) or len(kl) < 2:
        return None
    now_ms = int(time.time() * 1000)
    closed = [k for k in kl if int(k[6]) < now_ms]
    if len(closed) < 2:
        return None
    prev = closed[-2]
    last = closed[-1]
    try:
        prev_close = float(prev[4])
        last_close = float(last[4])
        if prev_close <= 0:
            return None
        return (last_close / prev_close - 1.0) * 100.0
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quote", default="USDC", help="Quote asset filter (default: USDC)")
    ap.add_argument("--top", type=int, default=10, help="Top N (default: 10)")
    ap.add_argument("--mode", choices=["abs", "up", "down"], default="abs", help="Sort mode: abs/up/down")
    ap.add_argument("--sleep", type=float, default=0.05, help="Sleep between requests (rate limit safety)")
    args = ap.parse_args()

    quote = args.quote.upper().strip()
    symbols = get_spot_symbols(quote)
    if not symbols:
        print(f"No symbols found for quote={quote}", file=sys.stderr)
        return 2

    rows: List[Tuple[str, float]] = []
    for i, sym in enumerate(symbols, 1):
        try:
            pct = get_1h_change_pct(sym)
            if pct is not None:
                rows.append((sym, pct))
        except Exception:
            pass

        if args.sleep > 0:
            time.sleep(args.sleep)

        if i % 200 == 0:
            print(f"scanned {i}/{len(symbols)}", file=sys.stderr)

    if not rows:
        print("No data returned.", file=sys.stderr)
        return 3

    if args.mode == "abs":
        rows.sort(key=lambda x: abs(x[1]), reverse=True)
    elif args.mode == "up":
        rows.sort(key=lambda x: x[1], reverse=True)
    else:
        rows.sort(key=lambda x: x[1])

    topn = rows[: max(1, args.top)]
    print(f"Top {len(topn)} movers (1h) quote={quote} mode={args.mode}")
    for sym, pct in topn:
        print(f"{sym}\t{pct:+.2f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
