#!/usr/bin/env python3
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from core.trade_memory import load_token_scores, sync_trade_memory

BASE_URL = "https://api.binance.com"
ROOT_PATH = Path((os.getenv("BOT_ROOT_DIR") or str(Path(__file__).resolve().parent)).strip()).resolve()
SERVICE_ENV_PATH = str(Path(os.getenv("BOT_SERVICE_ENV_PATH") or (ROOT_PATH / ".service.env")).resolve())
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

def collect_positive_candidates():
    symbols = get_symbols_usdc_trading()
    spread_map = get_spread_map()
    symbols = [
        sym for sym in symbols
        if spread_map.get(sym) is not None and spread_map[sym] <= SELECTOR_MAX_SPREAD_PCT
    ]
    candidates = []

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
            if pct is None or pct <= 0:
                continue
            sym = future_to_symbol[future]
            candidates.append({
                "symbol": sym,
                "pct": pct,
                "spread_pct": float(spread_map.get(sym, 0.0)) * 100.0,
            })
    candidates.sort(key=lambda item: (item["pct"], -item["spread_pct"], item["symbol"]), reverse=True)
    return candidates


def rank_candidates(candidates, score_map):
    ranked = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        memory = score_map.get(symbol, {})
        history_bonus = float(memory.get("history_bonus") or 0.0)
        final_score = float(candidate["pct"]) + history_bonus
        ranked.append({
            **candidate,
            "final_score": final_score,
            "history_bonus": history_bonus,
            "is_toxic": bool(memory.get("is_toxic")),
            "toxic_reasons": str(memory.get("toxic_reasons") or ""),
            "closed_trades": int(memory.get("closed_trades") or 0),
            "winrate_5": memory.get("winrate_5"),
            "pnl_usdc_5": memory.get("pnl_usdc_5"),
            "avg_pnl_pct_5": memory.get("avg_pnl_pct_5"),
        })
    ranked.sort(
        key=lambda item: (
            1 if item["is_toxic"] else 0,
            -(item["final_score"]),
            -(item["pct"]),
            item["symbol"],
        )
    )
    return ranked


def pick_best_candidate(score_map):
    candidates = collect_positive_candidates()
    if not candidates:
        return None, []
    ranked = rank_candidates(candidates, score_map)
    chosen = next((item for item in ranked if not item["is_toxic"]), None)
    return chosen, ranked

def log_selector_memory_state(sync_info, ranked):
    print(
        "TOKEN_SELECTOR: memory "
        f"db={sync_info.get('db_path','')} scanned_files={sync_info.get('scanned_files',0)} "
        f"skipped_files={sync_info.get('skipped_files',0)} imported_closed={sync_info.get('imported_closed_trades',0)} "
        f"scored_tokens={sync_info.get('scored_tokens',0)}"
    )
    for idx, item in enumerate(ranked[:5], start=1):
        winrate = item.get("winrate_5")
        pnl_usdc_5 = item.get("pnl_usdc_5")
        toxic = f" toxic={item['toxic_reasons']}" if item.get("is_toxic") else ""
        print(
            "TOKEN_SELECTOR: "
            f"cand#{idx} {item['symbol']} var={item['pct']:.2f}% bonus={item['history_bonus']:+.2f} "
            f"score={item['final_score']:.2f} closed={item['closed_trades']} "
            f"pnl5={0.0 if pnl_usdc_5 is None else float(pnl_usdc_5):+.4f} "
            f"win5={'--' if winrate is None else f'{float(winrate):.1f}%'}"
            f"{toxic}"
        )

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
    sync_info = sync_trade_memory()
    score_map = load_token_scores()
    chosen, ranked = pick_best_candidate(score_map)
    log_selector_memory_state(sync_info, ranked)

    if not chosen:
        print(
            f"TOKEN_SELECTOR: no eligible positive {QUOTE_ASSET} symbol found "
            f"({WINDOW_MINUTES}m window, max_spread={SELECTOR_MAX_SPREAD_PCT*100:.2f}%)"
        )
        return 0
    raw_top = ranked[0] if ranked else None
    if raw_top and raw_top["symbol"] != chosen["symbol"] and raw_top.get("is_toxic"):
        print(
            f"TOKEN_SELECTOR: skipped toxic top candidate {raw_top['symbol']} "
            f"var={raw_top['pct']:.2f}% reasons={raw_top.get('toxic_reasons') or 'n/a'}"
        )
    print(
        f"TOKEN_SELECTOR: selected {chosen['symbol']} "
        f"var={chosen['pct']:.2f}% bonus={chosen['history_bonus']:+.2f} score={chosen['final_score']:.2f}"
    )
    write_service_env(chosen["symbol"], chosen["pct"], DEFAULT_PROFILE)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
