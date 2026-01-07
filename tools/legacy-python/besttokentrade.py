#!/usr/bin/env python3
# besttoken.py
# Binance Spot scanner: trouve le meilleur token à trader (USDC par défaut)
# Public endpoints only (pas besoin API key)

import argparse
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE = "https://api.binance.com"
UA = {"User-Agent": "besttoken/1.0"}

# Exclusions "inutiles" pour un bot de scalping spot sur USDC
EXCLUDE = {
  "FDUSDUSDC", "USDTUSDC", "USDCUSDC", "TUSDUSDC", "DAIUSDC",
  "EURUSDC", "TRYUSDC", "BRLUSDC", "GBPUSDC"
}

def get(url, params=None, timeout=8):
  r = requests.get(url, params=params, timeout=timeout, headers=UA)
  r.raise_for_status()
  return r.json()

def safe_float(x, d=0.0):
  try:
    return float(x)
  except Exception:
    return d

def safe_int(x, d=0):
  try:
    return int(x)
  except Exception:
    return d

def fetch_exchangeinfo():
  return get(f"{BASE}/api/v3/exchangeInfo")

def fetch_24h():
  return get(f"{BASE}/api/v3/ticker/24hr")

def fetch_book(symbol, limit=20):
  return get(f"{BASE}/api/v3/depth", {"symbol": symbol, "limit": limit})

def fetch_klines(symbol, interval="1m", limit=120):
  return get(f"{BASE}/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})

def sum_levels(levels, n=10):
  s = 0.0
  for p, q in levels[:n]:
    s += safe_float(p) * safe_float(q)
  return s

def compute_range_vol(klines):
  # proxy volatilité: moyenne (high-low)/close sur ~60 bougies
  if not klines:
    return 0.0
  acc = 0.0
  cnt = 0
  for k in klines[-60:]:
    high = safe_float(k[2])
    low = safe_float(k[3])
    close = safe_float(k[4])
    if close > 0:
      acc += (high - low) / close
      cnt += 1
  return acc / cnt if cnt else 0.0

def score_symbol(m, args):
  sym = m["symbol"]

  if sym in EXCLUDE:
    return None
  if args.excludeMajors and sym in {"BTCUSDC", "ETHUSDC", "BNBUSDC"}:
    return None

  bid = safe_float(m.get("bidPrice"))
  ask = safe_float(m.get("askPrice"))
  last = safe_float(m.get("lastPrice"))
  if bid <= 0 or ask <= 0 or last <= 0:
    return None

  spread = (ask - bid) / ask
  if spread > args.maxSpread:
    return None

  quoteVol = safe_float(m.get("quoteVolume"))
  trades = safe_int(m.get("count"))
  if quoteVol < args.minQuoteVol:
    return None
  if trades < args.minTrades:
    return None

  try:
    book = fetch_book(sym, limit=20)
    kl = fetch_klines(sym, interval=args.klineInterval, limit=args.klineLimit)
  except Exception:
    return None

  bids = book.get("bids", [])
  asks = book.get("asks", [])
  if not bids or not asks:
    return None

  depthBid = sum_levels(bids, n=args.depthLevels)
  depthAsk = sum_levels(asks, n=args.depthLevels)
  depth = min(depthBid, depthAsk)
  if depth < args.minDepth:
    return None

  volRange = compute_range_vol(kl)
  if volRange < args.minVol or volRange > args.maxVol:
    return None

  # Score: liquide + actif + depth, spread bas, vol proche cible
  liq = math.log10(1.0 + quoteVol)
  act = math.log10(1.0 + trades)
  dep = math.log10(1.0 + depth)

  spr = max(1e-9, spread)
  sprPenalty = -math.log10(spr)

  v = max(0.0, volRange)
  vScore = 1.0 - min(1.0, abs(v - args.targetVol) / max(1e-9, args.targetVol))
  vScore = max(0.0, min(1.0, vScore))

  score = (
    args.wLiq * liq +
    args.wAct * act +
    args.wDep * dep +
    args.wSpr * sprPenalty +
    args.wVol * (10.0 * vScore)
  )

  return {
    "symbol": sym,
    "score": score,
    "spreadPct": spread * 100.0,
    "last": last,
    "quoteVol": quoteVol,
    "trades": trades,
    "depthUSDC": depth,
    "rangeVol": volRange,
  }

def main():
  p = argparse.ArgumentParser(description="Binance Spot: trouve le meilleur token à trader")
  p.add_argument("--quote", default="USDC", help="quote asset")
  p.add_argument("--top", type=int, default=15, help="top N")
  p.add_argument("--maxSpread", type=float, default=0.006, help="spread max (0.006=0.6%)")
  p.add_argument("--minQuoteVol", type=float, default=1500000.0, help="min quoteVolume 24h")
  p.add_argument("--minTrades", type=int, default=15000, help="min nb trades 24h")
  p.add_argument("--workers", type=int, default=10, help="threads")
  p.add_argument("--klineInterval", default="1m", help="interval bougies")
  p.add_argument("--klineLimit", type=int, default=120, help="nb bougies")
  p.add_argument("--depthLevels", type=int, default=10, help="niveaux book pour depth")
  p.add_argument("--minDepth", type=float, default=8000.0, help="min depth USDC (min(bid,ask) sur N niveaux)")
  p.add_argument("--minVol", type=float, default=0.0008, help="vol min (range moyen)")
  p.add_argument("--maxVol", type=float, default=0.0060, help="vol max (range moyen)")
  p.add_argument("--targetVol", type=float, default=0.0018, help="vol cible (range moyen)")
  p.add_argument("--excludeMajors", action="store_true", help="exclure BTC/ETH/BNB")

  p.add_argument("--wLiq", type=float, default=2.4)
  p.add_argument("--wAct", type=float, default=1.8)
  p.add_argument("--wDep", type=float, default=1.6)
  p.add_argument("--wSpr", type=float, default=2.0)
  p.add_argument("--wVol", type=float, default=1.2)
  args = p.parse_args()

  info = fetch_exchangeinfo()
  allowed = set()
  for s in info.get("symbols", []):
    if s.get("status") != "TRADING":
      continue
    if s.get("isSpotTradingAllowed") is not True:
      continue
    if s.get("quoteAsset") != args.quote:
      continue
    allowed.add(s["symbol"])

  tickers = fetch_24h()
  candidates = [t for t in tickers if t.get("symbol") in allowed]

  pre = []
  for t in candidates:
    sym = t.get("symbol")
    if not sym:
      continue
    if sym in EXCLUDE:
      continue
    if args.excludeMajors and sym in {"BTCUSDC", "ETHUSDC", "BNBUSDC"}:
      continue

    bid = safe_float(t.get("bidPrice"))
    ask = safe_float(t.get("askPrice"))
    if bid <= 0 or ask <= 0:
      continue

    spread = (ask - bid) / ask
    if spread > args.maxSpread:
      continue

    qv = safe_float(t.get("quoteVolume"))
    tr = safe_int(t.get("count"))
    if qv < args.minQuoteVol or tr < args.minTrades:
      continue

    pre.append(t)

  if not pre:
    print("Aucun candidat avec ces filtres. Baisse --minQuoteVol/--minTrades ou augmente --maxSpread.")
    return 1

  results = []
  started = time.time()
  with ThreadPoolExecutor(max_workers=args.workers) as ex:
    futs = [ex.submit(score_symbol, t, args) for t in pre]
    for f in as_completed(futs):
      r = f.result()
      if r:
        results.append(r)

  results.sort(key=lambda x: x["score"], reverse=True)
  took = time.time() - started

  topn = results[: max(1, args.top)]
  print(f"SCAN quote={args.quote} candidates={len(pre)} scored={len(results)} took={took:.2f}s")
  print("RANK | SYMBOL | SCORE | SPREAD% | LAST | QUOTEVOL24H | TRADES24H | DEPTH(USDC) | RANGEVOL")
  for i, r in enumerate(topn, 1):
    print(
      f"{i:>4} | {r['symbol']:<9} | {r['score']:.3f} | {r['spreadPct']:.3f} | {r['last']:.8g} | "
      f"{r['quoteVol']:.0f} | {r['trades']} | {r['depthUSDC']:.0f} | {r['rangeVol']:.5f}"
    )

  best = topn[0]
  print("\nBEST", best["symbol"])
  return 0

if __name__ == "__main__":
  raise SystemExit(main())

