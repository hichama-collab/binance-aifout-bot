#!/usr/bin/env python3
import math
import statistics
from core.config import loadConfig
from exchange.binance import Binance

QUOTE = "USDC"
LIMIT = 60

def f(x, d=0.0):
    try:
        return float(x)
    except:
        return d

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
    symbols = [
        s["symbol"]
        for s in exch.get("symbols", [])
        if s.get("status") == "TRADING"
        and s.get("quoteAsset") == QUOTE
        and s.get("isSpotTradingAllowed") is not False
    ]

    tickers = bx.get("/api/v3/ticker/24hr", {})
    tmap = {t["symbol"]: t for t in tickers if "symbol" in t}

    best = None
    bestScore = -1e9

    for sym in symbols:
        t = tmap.get(sym)
        if not t:
            continue

        vol24h = f(t.get("quoteVolume"))
        if vol24h < 500_000:
            continue

        ob = bx.get("/api/v3/depth", {"symbol": sym, "limit": 5})
        bids, asks = ob.get("bids", []), ob.get("asks", [])
        if not bids or not asks:
            continue

        bid, ask = f(bids[0][0]), f(asks[0][0])
        if bid <= 0 or ask <= bid:
            continue

        spread = (ask - bid) / ask
        if spread > 0.003:
            continue

        kl = bx.get("/api/v3/klines", {"symbol": sym, "interval": "1m", "limit": LIMIT})
        closes = [f(k[4]) for k in kl if len(k) > 4]
        if len(closes) < 30:
            continue

        rets = [(closes[i] - closes[i-1]) / closes[i-1]
                for i in range(1, len(closes)) if closes[i-1] > 0]

        if len(rets) < 10:
            continue

        vol1m = statistics.pstdev(rets) * 100.0

        score = (
            math.log10(vol24h)
            + (vol1m * 12.0)
            - (spread * 1200.0)
        )

        if score > bestScore:
            bestScore = score
            best = sym

    if not best:
        raise RuntimeError("Aucun token valide")

    print(best)

if __name__ == "__main__":
    main()

