#!/usr/bin/env python3
import os
import re
import requests

BASE_URL = "https://api.binance.com"
ROOT_PATH = "/opt/binance-aifout-bot"
SERVICE_ENV_PATH = os.path.join(ROOT_PATH, ".service.env")

# Universe
QUOTE_ASSET = "USDC"
EXCLUDED = {"USDCUSDT", "USDTUSDC"}

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
        if not _is_symbol_safe(sym):
            continue
        out.append(sym)
    return out

def change_30m_pct(symbol: str):
    # 30 minutes based on 1m klines
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
    # IMPORTANT: This tool must NOT change DRY_RUN / PROFILE or any other keys.
    # It only updates SYMBOL in-place inside .service.env.
    existing_txt = ""
    try:
        existing_txt = open(SERVICE_ENV_PATH, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        existing_txt = ""

    lines = existing_txt.splitlines(True)

    def is_symbol_line(raw: str) -> bool:
        return bool(re.match(r'^\s*SYMBOL\s*=.*$', raw))

    changed = False
    new_lines = []
    for raw in lines:
        if is_symbol_line(raw):
            new_lines.append(f"SYMBOL={symbol}\n")
            changed = True
        else:
            new_lines.append(raw)

    if not changed:
        # If no SYMBOL line exists, prepend it. Do not invent DRY_RUN/PROFILE.
        new_lines = [f"SYMBOL={symbol}\n"] + new_lines

    with open(SERVICE_ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Observability only (read-only).
    env_now = _read_env_file(SERVICE_ENV_PATH)
    print(
        f"TOKEN_SELECTOR: wrote {SERVICE_ENV_PATH} "
        f"SYMBOL={symbol} VAR30M={pct:.2f}% "
        f"PROFILE={env_now.get('PROFILE','')} DRY_RUN={env_now.get('DRY_RUN','')} QUOTE={QUOTE_ASSET}"
    )

def main():
    sym, pct = pick_top1_positive()
    if not sym:
        print(f"TOKEN_SELECTOR: no positive {QUOTE_ASSET} symbol found (30m window)")
        return 0
    write_service_env(sym, pct)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
