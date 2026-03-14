#!/usr/bin/env python3
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE_URL = "https://api.binance.com"
ROOT_PATH = "/opt/binance-aifout-bot"
SERVICE_ENV_PATH = os.path.join(ROOT_PATH, ".service.env")
WINDOW_MINUTES = 10
HTTP_TIMEOUT = 5
MAX_WORKERS = 16
SELECTOR_MAX_SPREAD_PCT = float(os.getenv("SELECTOR_MAX_SPREAD_PCT", "0.0025"))
DEFAULT_PROFILE = (os.getenv("SELECTOR_PROFILE", "major") or "major").strip()

# Universe
QUOTE_ASSET = "USDC"
EXCLUDED = {"USDCUSDT", "USDTUSDC"}

_SESSION = requests.Session()

def _read_env_file(path: str) -> dict:
    out = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except Exception:
        pass
    return out

def _is_symbol_safe(sym: str) -> bool:
    # Exclude any non-ASCII symbols (e.g. Chinese characters) and empty values.
    # Binance symbols we want are plain ASCII like ABCUSDC.
    return bool(sym) and sym.isascii()

def get_symbols_usdc_trading():
    r = _SESSION.get(f"{BASE_URL}/api/v3/exchangeInfo", timeout=HTTP_TIMEOUT)
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
        if not _is_symbol_safe(sym):
            continue
        out.append(sym)
    return sorted(out)

def get_spread_map():
    out = {}
    r = _SESSION.get(f"{BASE_URL}/api/v3/ticker/bookTicker", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return out
    for row in data:
        try:
            sym = str(row.get("symbol") or "")
            if not sym or not _is_symbol_safe(sym):
                continue
            bid = float(row.get("bidPrice") or 0.0)
            ask = float(row.get("askPrice") or 0.0)
            if bid <= 0 or ask <= 0:
                continue
            out[sym] = (ask - bid) / bid
        except Exception:
            continue
    return out

def change_window_pct(symbol: str):
    # WINDOW_MINUTES based on 1m klines
    r = _SESSION.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": "1m", "limit": WINDOW_MINUTES},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    k = r.json()
    if not isinstance(k, list) or len(k) < WINDOW_MINUTES:
        return None
    o = float(k[0][1])
    c = float(k[-1][4])
    if o <= 0:
        return None
    return (c - o) / o * 100.0

def pick_top1_positive():
    best_sym = None
    best_pct = 0.0
    symbols = get_symbols_usdc_trading()
    spread_map = get_spread_map()
    symbols = [
        sym for sym in symbols
        if spread_map.get(sym) is not None and spread_map[sym] <= SELECTOR_MAX_SPREAD_PCT
    ]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_symbol = {
            pool.submit(change_window_pct, sym): sym
            for sym in symbols
        }
        for future in as_completed(future_to_symbol):
            try:
                pct = future.result()
            except Exception:
                continue
            if pct is None:
                continue
            if pct > best_pct:
                best_pct = pct
                best_sym = future_to_symbol[future]

    return best_sym, best_pct

def write_service_env(symbol: str, pct: float, profile: str):
    # IMPORTANT: This tool must NOT change DRY_RUN or unrelated keys.
    # It updates SYMBOL and PROFILE in-place inside .service.env.
    existing_txt = ""
    try:
        existing_txt = open(SERVICE_ENV_PATH, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        existing_txt = ""

    lines = existing_txt.splitlines(True)

    def is_symbol_line(raw: str) -> bool:
        return bool(re.match(r'^\s*SYMBOL\s*=.*$', raw))

    def is_profile_line(raw: str) -> bool:
        return bool(re.match(r'^\s*PROFILE\s*=.*$', raw))

    changed = False
    profile_changed = False
    new_lines = []
    for raw in lines:
        if is_symbol_line(raw):
            new_lines.append(f"SYMBOL={symbol}\n")
            changed = True
        elif is_profile_line(raw):
            new_lines.append(f"PROFILE={profile}\n")
            profile_changed = True
        else:
            new_lines.append(raw)

    if not changed:
        # If no SYMBOL line exists, prepend it.
        new_lines = [f"SYMBOL={symbol}\n"] + new_lines
    if not profile_changed:
        new_lines = [f"PROFILE={profile}\n"] + new_lines

    with open(SERVICE_ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Observability only (read-only).
    env_now = _read_env_file(SERVICE_ENV_PATH)
    print(
        f"TOKEN_SELECTOR: wrote {SERVICE_ENV_PATH} "
        f"SYMBOL={symbol} VAR{WINDOW_MINUTES}M={pct:.2f}% MAX_SPREAD={SELECTOR_MAX_SPREAD_PCT*100:.2f}% "
        f"PROFILE={env_now.get('PROFILE', profile)} DRY_RUN={env_now.get('DRY_RUN','')} QUOTE={QUOTE_ASSET}"
    )

def main():
    sym, pct = pick_top1_positive()
    if not sym:
        print(
            f"TOKEN_SELECTOR: no positive {QUOTE_ASSET} symbol found "
            f"({WINDOW_MINUTES}m window, max_spread={SELECTOR_MAX_SPREAD_PCT*100:.2f}%)"
        )
        return 0
    write_service_env(sym, pct, DEFAULT_PROFILE)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
