#!/usr/bin/env python3
"""Pick best symbols to run the bot on (Spot, public endpoints only).

Ranking aligned with the bot constraints:
- LIMIT-only -> spread + depth matter
- Entry driver = short-term momentum
- Small budget friendly -> minNotional + price cap
- Wants "zigzag" -> prioritize short-term range/volatility (so TP is reachable)
"""

import argparse
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE = "https://api.binance.com"
UA = {"User-Agent": "bot-symbol-picker/1.1"}

# Avoid obvious garbage / non-bot-friendly bases (stablecoins, fiat, etc.)
EXCLUDE_BASE = {
    "USDC","USDT","BUSD","TUSD","FDUSD","USDP","DAI",
    "EUR","EURI","GBP","BRL","TRY","RUB","UAH","BIDR",
    "PAXG","XAUT",
}

def get_json(path, params=None, timeout=10):
    r = requests.get(BASE + path, params=params or {}, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()

def build_symbol_universe(quote: str):
    exch = get_json("/api/v3/exchangeInfo")
    out = []
    for s in exch.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("isSpotTradingAllowed") is False:
            continue
        if s.get("quoteAsset") != quote:
            continue
        base = s.get("baseAsset")
        sym = s.get("symbol")
        if not base or not sym:
            continue
        if base in EXCLUDE_BASE:
            continue
        # avoid leveraged tokens patterns
        if any(x in sym for x in ("UP"+quote, "DOWN"+quote, "BULL"+quote, "BEAR"+quote)):
            continue
        out.append(sym)
    return out

def build_filters_map(quote: str):
    """Return {symbol: {tick, step, minNotional}} for quote pairs."""
    exch = get_json("/api/v3/exchangeInfo")
    m = {}
    for s in exch.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != quote:
            continue
        sym = s.get("symbol")
        tick = step = None
        min_notional = 0.0
        for f in s.get("filters", []):
            ft = f.get("filterType")
            if ft == "PRICE_FILTER":
                tick = float(f.get("tickSize", 0) or 0)
            elif ft == "LOT_SIZE":
                step = float(f.get("stepSize", 0) or 0)
            elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                # NOTIONAL exists on newer symbols; use minNotional when present
                mn = f.get("minNotional")
                if mn is not None:
                    try:
                        min_notional = float(mn)
                    except Exception:
                        pass
        if sym and tick and step:
            m[sym] = {"tick": tick, "step": step, "minNotional": float(min_notional or 0.0)}
    return m

def realized_vol_and_range(closes):
    # returns in pct
    if len(closes) < 5:
        return 0.0, 0.0
    rets = []
    for i in range(1, len(closes)):
        a = closes[i-1]
        b = closes[i]
        if a > 0:
            rets.append((b / a) - 1.0)
    if len(rets) < 4:
        return 0.0, 0.0
    mu = sum(rets) / len(rets)
    var = sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)
    vol = math.sqrt(max(0.0, var))
    cmin = min(closes)
    cmax = max(closes)
    last = closes[-1] if closes[-1] else 0.0
    rng = ((cmax - cmin) / last) if last > 0 else 0.0
    return vol, rng

def score_symbol(sym: str, quote: str, interval: str, mom_window: int, klines_limit: int,
                 max_spread: float, min_quote_vol: float, depth_levels: int,
                 budget: float, max_price: float, filters_map: dict):
    # bookTicker for spread
    bt = get_json("/api/v3/ticker/bookTicker", {"symbol": sym})
    bid = float(bt["bidPrice"])
    ask = float(bt["askPrice"])
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    spread = (ask - bid) / mid
    if spread > max_spread:
        return None

    # last price (budget filter)
    last = float(get_json("/api/v3/ticker/price", {"symbol": sym}).get("price", 0.0))
    if last <= 0:
        return None
    if max_price > 0 and last > max_price:
        return None

    # filters (minNotional)
    f = filters_map.get(sym)
    if not f:
        return None
    min_notional = float(f.get("minNotional", 0.0) or 0.0)
    if budget > 0 and min_notional > 0 and budget < min_notional:
        return None

    # 24h for liquidity
    t24 = get_json("/api/v3/ticker/24hr", {"symbol": sym})
    quote_vol = float(t24.get("quoteVolume", 0.0))
    trades = int(float(t24.get("count", 0)))
    if quote_vol < min_quote_vol:
        return None

    # order book depth (top N) for fill probability
    depth = get_json("/api/v3/depth", {"symbol": sym, "limit": min(1000, max(5, depth_levels))})
    bids = depth.get("bids", [])[:depth_levels]
    asks = depth.get("asks", [])[:depth_levels]
    depth_quote = 0.0
    for px, qty in bids + asks:
        px = float(px); qty = float(qty)
        depth_quote += px * qty

    # momentum + realized vol/range on recent klines
    kl = get_json("/api/v3/klines", {"symbol": sym, "interval": interval, "limit": klines_limit})
    closes = [float(k[4]) for k in kl if float(k[4]) > 0]
    if len(closes) < mom_window + 2:
        return None

    cur = closes[-1]
    prev = closes[-1 - mom_window]
    mom = (cur / prev) - 1.0 if prev > 0 else 0.0
    abs_mom = abs(mom)

    vol, rng = realized_vol_and_range(closes[-max(20, mom_window+2):])

    # scoring tuned for "zigzag" small-cap style:
    # - range and realized vol dominant (TP reachability)
    # - liquidity/depth helps fills
    # - penalize spread
    # - mild reward for momentum (still the bot entry driver)
    s = 0.0
    s += 3.2 * rng
    s += 2.0 * vol
    s += 1.6 * abs_mom
    s += 0.55 * math.log10(max(1.0, quote_vol))
    s += 0.35 * math.log10(max(1.0, depth_quote))
    s -= 3.8 * spread

    return {
        "symbol": sym,
        "score": s,
        "spreadPct": spread * 100.0,
        "momPct": mom * 100.0,
        "absMomPct": abs_mom * 100.0,
        "vol": vol,
        "rangePct": rng * 100.0,
        "last": last,
        "quoteVol": quote_vol,
        "trades24h": trades,
        "depthQuote": depth_quote,
        "minNotional": min_notional,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quote", default="USDC")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--interval", default="1m")
    ap.add_argument("--momWindow", type=int, default=10, help="Momentum window in candles")
    ap.add_argument("--klines", type=int, default=120, help="Klines to fetch per symbol")
    ap.add_argument("--maxSpread", type=float, default=0.006, help="Max spread as fraction (0.006=0.6%)")
    ap.add_argument("--minQuoteVol", type=float, default=750000, help="Min 24h quote volume")
    ap.add_argument("--depthLevels", type=int, default=50, help="Depth levels (sum both sides)")
    ap.add_argument("--budget", type=float, default=50.0, help="USDC budget to check minNotional")
    ap.add_argument("--maxPrice", type=float, default=5.0, help="Reject symbols with last price above this (0=off)")
    ap.add_argument("--threads", type=int, default=24)
    args = ap.parse_args()

    t0 = time.time()
    syms = build_symbol_universe(args.quote)
    filters_map = build_filters_map(args.quote)

    results = []
    with ThreadPoolExecutor(max_workers=max(4, args.threads)) as ex:
        futs = []
        for s in syms:
            futs.append(ex.submit(
                score_symbol, s, args.quote, args.interval, args.momWindow, args.klines,
                args.maxSpread, args.minQuoteVol, args.depthLevels,
                args.budget, args.maxPrice, filters_map
            ))
        for f in as_completed(futs):
            try:
                r = f.result()
                if r:
                    results.append(r)
            except Exception:
                continue

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[: max(1, args.top)]
    took = time.time() - t0
    print(f"SCAN quote={args.quote} universe={len(syms)} scored={len(results)} took={took:.2f}s "
          f"(maxPrice={args.maxPrice} budget={args.budget})")

    if not top:
        print("No symbol passed filters. Loosen --minQuoteVol/--maxSpread or raise --maxPrice.")
        return

    hdr = "RANK | SYMBOL | SCORE | SPR% | RANGE% | ABSMOM% | VOL | MINNOT | QVOL24H | DEPTHQ | TR24H | LAST"
    print(hdr)
    for i, r in enumerate(top, 1):
        print(
            f"{i:>4} | {r['symbol']:<10} | {r['score']:+.4f} | {r['spreadPct']:.3f} | "
            f"{r['rangePct']:.3f} | {r['absMomPct']:.3f} | {r['vol']:.5f} | "
            f"{r['minNotional']:.2f} | {r['quoteVol']:.0f} | {r['depthQuote']:.0f} | {r['trades24h']} | {r['last']}"
        )

if __name__ == "__main__":
    main()
