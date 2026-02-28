#!/usr/bin/env python3
import os
import requests

BASE_URL = "https://api.binance.com"
ROOT_PATH = "/opt/binance-aifout-bot"
SERVICE_ENV_PATH = os.path.join(ROOT_PATH, ".service.env")

# Force Europe-safe universe
QUOTE_ASSET = "USDC"
EXCLUDED = {"USDCUSDT", "USDTUSDC"}

def get_symbols_usdc_trading():
    r = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", timeout=10)
    r.raise_for_status()
    data = r.json()

    out = []
    for s in data.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != QUOTE_ASSET:
            continue
        sym = s.get("symbol")
        if not sym or sym in EXCLUDED:
            continue
        # Filter out non-ASCII / non [A-Z0-9] symbols (can break downstream + dashboard)
        if not sym.isascii():
            continue
        bad = False
        for ch in sym:
            if not ("A" <= ch <= "Z" or "0" <= ch <= "9"):
                bad = True
                break
        if bad:
            continue
        out.append(sym)
    return out

def change_30m_pct(symbol: str):
    # 30 minutes variation (1m candles, limit=30)
    r = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": "1m", "limit": 30},
        timeout=10,
    )
    r.raise_for_status()
    k = r.json()
    if not isinstance(k, list) or len(k) < 30:
        return None
    o = float(k[0][1])
    c = float(k[-1][4])
    if o <= 0:
        return None
    return (c - o) / o * 100.0

def pick_top1_positive():
    best_sym = None
    best_pct = 0.0

    for sym in get_symbols_usdc_trading():
        try:
            pct = change_30m_pct(sym)
            if pct is None:
                continue
            if pct > best_pct:
                best_pct = pct
                best_sym = sym
        except Exception:
            continue

    return best_sym, best_pct

def write_service_env(symbol: str, pct: float):
    content = f"SYMBOL={symbol}\nDRY_RUN=0\nPROFILE=major\n"
    with open(SERVICE_ENV_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"TOKEN_SELECTOR: wrote {SERVICE_ENV_PATH} SYMBOL={symbol} VAR1H={pct:.2f}% PROFILE=major DRY_RUN=0 QUOTE={QUOTE_ASSET}")

def main():
    sym, pct = pick_top1_positive()
    if not sym:
        print(f"TOKEN_SELECTOR: no positive {QUOTE_ASSET} symbol found")
        return 0
    write_service_env(sym, pct)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
