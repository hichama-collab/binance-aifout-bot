#!/usr/bin/env python3
# tools/TokenRank1hTable.py

import argparse
import time
import requests

BINANCE_REST = "https://api.binance.com"


def http_get_json(url, params=None, timeout=10.0, retries=3):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1 + i)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(0.5 + i)
    raise RuntimeError(last)


def get_symbols(quote):
    data = http_get_json(f"{BINANCE_REST}/api/v3/exchangeInfo")
    out = []
    for s in data["symbols"]:
        if s["status"] == "TRADING" and s["quoteAsset"] == quote and s.get("isSpotTradingAllowed", True):
            out.append(s["symbol"])
    return out


def get_last_2h_closes(symbol):
    kl = http_get_json(
        f"{BINANCE_REST}/api/v3/klines",
        params={"symbol": symbol, "interval": "1h", "limit": 3},
    )
    now = int(time.time() * 1000)
    closed = [k for k in kl if int(k[6]) < now]
    if len(closed) < 2:
        return None
    prev, last = closed[-2], closed[-1]
    return float(prev[4]), float(last[4])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quote", default="USDC")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--sleep", type=float, default=0.05)
    args = ap.parse_args()

    rows = []
    for sym in get_symbols(args.quote):
        try:
            p1, p2 = get_last_2h_closes(sym)
            var = (p2 / p1 - 1) * 100
            rows.append((sym, p1, p2, var))
        except Exception:
            pass
        time.sleep(args.sleep)

    rows.sort(key=lambda x: abs(x[3]), reverse=True)
    rows = rows[: args.top]

    col = 15

    print(f"{'TOKEN':<{col}}{'H-1':<{col}}{'H':<{col}}{'VAR%':<{col}}")
    print("-" * (col * 4))

    for sym, p1, p2, v in rows:
        print(
            f"{sym:<{col}}"
            f"{p1:<{col}.6f}"
            f"{p2:<{col}.6f}"
            f"{v:+<{col}.2f}"
        )


if __name__ == "__main__":
    main()

