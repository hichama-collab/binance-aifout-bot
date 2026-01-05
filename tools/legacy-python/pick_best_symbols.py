#!/usr/bin/env python3
import math
import statistics
from core.config import loadConfig
from exchange.binance import Binance

QUOTE = "USDC"
TOPN = 10
BEST3 = 3

def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def main():
    cfg = loadConfig()
    bx = Binance(
        cfg.apiKey,
        cfg.apiSecret,
        cfg.baseUrl,
        cfg.httpTimeout,
        cfg.httpRetries,
        cfg.httpBackoff,
    )

    exch = bx.get("/api/v3/exchangeInfo", {})
    symbols = []
    for s in exch.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != QUOTE:
            continue
        if s.get("isSpotTradingAllowed") is False:
            continue
        symbols.append(s["symbol"])

    tickers = bx.get("/api/v3/ticker/24hr", {})
    tmap = {t["symbol"]: t for t in tickers if "symbol" in t}

    scored = []
    for sym in symbols:
        t = tmap.get(sym)
        if not t:
            continue

        vol24h = safe_float(t.get("quoteVolume", 0.0))

        ob = bx.get("/api/v3/depth", {"symbol": sym, "limit": 5})
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])
        if not bids or not asks:
            continue

        bid = safe_float(bids[0][0])
        ask = safe_float(asks[0][0])
        if bid <= 0 or ask <= 0 or ask <= bid:
            continue

        spread = (ask - bid) / ask

        kl = bx.get("/api/v3/klines", {"symbol": sym, "interval": "1m", "limit": 60})
        closes = [safe_float(k[4]) for k in kl if len(k) > 4]
        if len(closes) < 20:
            continue

        rets = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
        if len(rets) < 10:
            continue

        vol1m_std = statistics.pstdev(rets) * 100.0
        score = math.log10(max(vol24h, 1.0)) + (vol1m_std * 10.0) - (spread * 1000.0)

        scored.append((score, sym, vol24h, spread * 100.0, vol1m_std))

    scored.sort(reverse=True, key=lambda x: x[0])

    print("=== TOP SYMBOLS (raw) ===")
    for score, sym, vol24h, spr_pct, vol1m_std in scored[:TOPN]:
        print(f"{sym:>12}  score={score:6.3f}  vol24h={vol24h:,.0f}  spread={spr_pct:.3f}%  vol1m_std={vol1m_std:.3f}%")

    print("\nBest 3 symbols:")
    for _, sym, *_ in scored[:BEST3]:
        print(sym)

if __name__ == "__main__":
    main()

