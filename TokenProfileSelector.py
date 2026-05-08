#!/usr/bin/env python3
import os
import re
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from core.trade_memory import load_token_scores, sync_trade_memory

BASE_URL = "https://api.binance.com"
ROOT_PATH = Path((os.getenv("BOT_ROOT_DIR") or str(Path(__file__).resolve().parent)).strip()).resolve()
SERVICE_ENV_PATH = str(Path(os.getenv("BOT_SERVICE_ENV_PATH") or (ROOT_PATH / ".service.env")).resolve())
BLOCKED_SYMBOLS_PATH = Path(
    (os.getenv("BOT_BLOCKED_SYMBOLS_PATH") or str(ROOT_PATH / "data" / "blocked_symbols.txt")).strip()
).resolve()
SELECTOR_STATE_PATH = ROOT_PATH / "data" / "runtime" / "selector_state.json"
# Durée minimale sur un token avant de switcher (en minutes)
SELECTOR_MIN_HOLD_MINUTES = float(os.getenv("SELECTOR_MIN_HOLD_MINUTES", "10"))
# Amélioration minimale du score pour justifier un switch pendant la période de garde
SELECTOR_HYSTERESIS_PCT = float(os.getenv("SELECTOR_HYSTERESIS_PCT", "0.25"))
# Variation minimale du token actuel pour activer la garde (si plus flat que ça, on switche tout de suite)
SELECTOR_MIN_ACTIVE_VAR_PCT = float(os.getenv("SELECTOR_MIN_ACTIVE_VAR_PCT", "0.15"))
# Direction 1-2min minimum pour valider le candidat choisi (évite d'entrer après le move)
SELECTOR_MIN_DIRECTION_PCT = float(os.getenv("SELECTOR_MIN_DIRECTION_PCT", "-0.15"))
WINDOW_MINUTES = max(2, int(os.getenv("SELECTOR_WINDOW_MINUTES", "2")))
HTTP_TIMEOUT = 5
MAX_WORKERS = 16
SELECTOR_MAX_SPREAD_PCT = float(os.getenv("SELECTOR_MAX_SPREAD_PCT", "0.0025"))
SELECTOR_MIN_PRICE_USDC = float(os.getenv("SELECTOR_MIN_PRICE_USDC", "0.05"))
SELECTOR_MIN_QUOTE_VOLUME_USDC_24H = float(os.getenv("SELECTOR_MIN_QUOTE_VOLUME_USDC_24H", "1000000"))
SELECTOR_MIN_TRADE_COUNT_24H = int(os.getenv("SELECTOR_MIN_TRADE_COUNT_24H", "5000"))
SELECTOR_MIN_WINDOW_PCT = float(os.getenv("SELECTOR_MIN_WINDOW_PCT", "0.08"))
SELECTOR_MAX_WINDOW_PCT = float(os.getenv("SELECTOR_MAX_WINDOW_PCT", "1.80"))
SELECTOR_MAX_WINDOW_PCT_UNKNOWN = float(os.getenv("SELECTOR_MAX_WINDOW_PCT_UNKNOWN", "1.20"))
SELECTOR_MIN_24H_CHANGE_PCT = float(os.getenv("SELECTOR_MIN_24H_CHANGE_PCT", "-8.0"))
SELECTOR_MAX_24H_CHANGE_PCT = float(os.getenv("SELECTOR_MAX_24H_CHANGE_PCT", "12.0"))
SELECTOR_UNKNOWN_SCORE_PENALTY = float(os.getenv("SELECTOR_UNKNOWN_SCORE_PENALTY", "0.05"))
DEFAULT_PROFILE = (os.getenv("SELECTOR_PROFILE", "major") or "major").strip()


def _load_selector_state() -> dict:
    try:
        if SELECTOR_STATE_PATH.exists():
            return json.loads(SELECTOR_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_selector_state(state: dict) -> None:
    try:
        SELECTOR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SELECTOR_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass

# Universe
QUOTE_ASSET = "USDC"
EXCLUDED = {"USDCUSDT", "USDTUSDC"}
EXCLUDED_BASE_ASSETS = {
    "USDC", "USDT", "FDUSD", "TUSD", "USDP", "USDS", "USDE", "DAI", "PYUSD",
    "EUR", "EURC", "AEUR", "USD1",
}

_SESSION = requests.Session()


def load_blocked_symbols(path: Path) -> set[str]:
    try:
        if not path.exists():
            return set()
        blocked = set()
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            symbol = raw.strip().upper()
            if symbol:
                blocked.add(symbol)
        return blocked
    except Exception:
        return set()

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


def _base_asset(symbol: str) -> str:
    if symbol.endswith(QUOTE_ASSET):
        return symbol[:-len(QUOTE_ASSET)]
    return symbol

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


def get_market_stats_map():
    out = {}
    r = _SESSION.get(f"{BASE_URL}/api/v3/ticker/24hr", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return out
    for row in data:
        try:
            sym = str(row.get("symbol") or "")
            if not sym or not _is_symbol_safe(sym):
                continue
            out[sym] = {
                "last_price": float(row.get("lastPrice") or 0.0),
                "quote_volume_24h": float(row.get("quoteVolume") or 0.0),
                "trade_count_24h": int(row.get("count") or 0),
                "change_pct_24h": float(row.get("priceChangePercent") or 0.0),
            }
        except Exception:
            continue
    return out

def change_window_pct(symbol: str, minutes: int = WINDOW_MINUTES):
    # klines-based price change over `minutes` 1m candles
    limit = max(2, minutes)
    r = _SESSION.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": "1m", "limit": limit},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    k = r.json()
    if not isinstance(k, list) or len(k) < limit:
        return None
    o = float(k[0][1])
    c = float(k[-1][4])
    if o <= 0:
        return None
    return (c - o) / o * 100.0


def current_direction_pct(symbol: str) -> float | None:
    """1-minute price direction check — is the token still moving up right now?"""
    try:
        return change_window_pct(symbol, minutes=2)
    except Exception:
        return None

def collect_candidates(excluded_symbols: set[str] | None = None, positive_only: bool = True):
    symbols = get_symbols_usdc_trading()
    spread_map = get_spread_map()
    market_map = get_market_stats_map()
    excluded_symbols = {str(sym).strip().upper() for sym in (excluded_symbols or set()) if str(sym).strip()}
    filter_counts = {
        "blocked": 0,
        "base_excluded": 0,
        "spread": 0,
        "market": 0,
        "price": 0,
        "quote_volume": 0,
        "trade_count": 0,
        "change_24h": 0,
    }
    eligible_symbols = []
    for sym in symbols:
        if sym in excluded_symbols:
            filter_counts["blocked"] += 1
            continue
        if _base_asset(sym) in EXCLUDED_BASE_ASSETS:
            filter_counts["base_excluded"] += 1
            continue
        spread = spread_map.get(sym)
        if spread is None or spread > SELECTOR_MAX_SPREAD_PCT:
            filter_counts["spread"] += 1
            continue
        market = market_map.get(sym)
        if market is None:
            filter_counts["market"] += 1
            continue
        if float(market["last_price"]) < SELECTOR_MIN_PRICE_USDC:
            filter_counts["price"] += 1
            continue
        if float(market["quote_volume_24h"]) < SELECTOR_MIN_QUOTE_VOLUME_USDC_24H:
            filter_counts["quote_volume"] += 1
            continue
        if int(market["trade_count_24h"]) < SELECTOR_MIN_TRADE_COUNT_24H:
            filter_counts["trade_count"] += 1
            continue
        change_24h = float(market["change_pct_24h"])
        if change_24h < SELECTOR_MIN_24H_CHANGE_PCT or change_24h > SELECTOR_MAX_24H_CHANGE_PCT:
            filter_counts["change_24h"] += 1
            continue
        eligible_symbols.append(sym)

    print(
        "TOKEN_SELECTOR: universe "
        f"total={len(symbols)} eligible={len(eligible_symbols)} "
        f"reject_blocked={filter_counts['blocked']} "
        f"reject_base={filter_counts['base_excluded']} "
        f"reject_spread={filter_counts['spread']} reject_market={filter_counts['market']} "
        f"reject_price={filter_counts['price']} reject_quote_volume={filter_counts['quote_volume']} "
        f"reject_trade_count={filter_counts['trade_count']} reject_change24h={filter_counts['change_24h']} "
        f"min_window={SELECTOR_MIN_WINDOW_PCT:.2f}% max_window={SELECTOR_MAX_WINDOW_PCT:.2f}% "
        f"min_price={SELECTOR_MIN_PRICE_USDC:.4f} "
        f"min_quote_volume_24h={SELECTOR_MIN_QUOTE_VOLUME_USDC_24H:.0f} "
        f"min_trade_count_24h={SELECTOR_MIN_TRADE_COUNT_24H}"
    )
    candidates = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_symbol = {
            pool.submit(change_window_pct, sym): sym
            for sym in eligible_symbols
        }
        for future in as_completed(future_to_symbol):
            try:
                pct = future.result()
            except Exception:
                continue
            if pct is None:
                continue
            if positive_only and pct <= 0:
                continue
            if abs(pct) < SELECTOR_MIN_WINDOW_PCT:
                continue
            if abs(pct) > SELECTOR_MAX_WINDOW_PCT:
                continue
            sym = future_to_symbol[future]
            market = market_map.get(sym) or {}
            candidates.append({
                "symbol": sym,
                "pct": pct,
                "spread_pct": float(spread_map.get(sym, 0.0)) * 100.0,
                "last_price": float(market.get("last_price") or 0.0),
                "quote_volume_24h": float(market.get("quote_volume_24h") or 0.0),
                "trade_count_24h": int(market.get("trade_count_24h") or 0),
                "change_pct_24h": float(market.get("change_pct_24h") or 0.0),
            })
    candidates.sort(key=lambda item: (item["pct"], -item["spread_pct"], item["symbol"]), reverse=True)
    return candidates


def rank_candidates(candidates, score_map):
    ranked = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        memory = score_map.get(symbol, {})
        closed_trades = int(memory.get("closed_trades") or 0)
        if closed_trades <= 0 and abs(float(candidate["pct"])) > SELECTOR_MAX_WINDOW_PCT_UNKNOWN:
            continue
        history_bonus = float(memory.get("history_bonus") or 0.0)
        unknown_penalty = SELECTOR_UNKNOWN_SCORE_PENALTY if closed_trades <= 0 else 0.0
        final_score = float(candidate["pct"]) + history_bonus - unknown_penalty
        ranked.append({
            **candidate,
            "final_score": final_score,
            "history_bonus": history_bonus,
            "unknown_penalty": unknown_penalty,
            "is_toxic": bool(memory.get("is_toxic")),
            "toxic_reasons": str(memory.get("toxic_reasons") or ""),
            "closed_trades": closed_trades,
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


def pick_best_candidate(score_map, excluded_symbols: set[str] | None = None):
    candidates = collect_candidates(excluded_symbols=excluded_symbols, positive_only=True)
    if not candidates:
        return None, []
    ranked = rank_candidates(candidates, score_map)
    # Choose best candidate whose current 2-min direction is acceptable (not in free fall)
    chosen = None
    for item in ranked:
        if item.get("is_toxic"):
            continue
        dir_pct = current_direction_pct(item["symbol"])
        if dir_pct is not None and dir_pct < SELECTOR_MIN_DIRECTION_PCT:
            print(
                f"TOKEN_SELECTOR: skip {item['symbol']} direction_2m={dir_pct:.3f}% < {SELECTOR_MIN_DIRECTION_PCT}% — move exhausted"
            )
            continue
        chosen = item
        break
    if chosen is None:
        # Fallback: pick top non-toxic without direction filter
        chosen = next((item for item in ranked if not item["is_toxic"]), None)
    return chosen, ranked


def choose_fallback_candidate(score_map, excluded_symbols: set[str] | None = None):
    positive_candidates = collect_candidates(excluded_symbols=excluded_symbols, positive_only=True)
    positive_ranked = rank_candidates(positive_candidates, score_map) if positive_candidates else []
    any_candidates = collect_candidates(excluded_symbols=excluded_symbols, positive_only=False)
    any_ranked = rank_candidates(any_candidates, score_map) if any_candidates else []

    fallback = None
    fallback_mode = ""

    if any_ranked:
        fallback = next((item for item in any_ranked if not item["is_toxic"]), None)
        fallback_mode = "best_eligible_any_momentum"

    return fallback, fallback_mode, positive_ranked, any_ranked

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
            f"cand#{idx} {item['symbol']} var={item['pct']:.2f}% "
            f"bonus={item['history_bonus']:+.2f} unk_penalty={item.get('unknown_penalty', 0.0):+.2f} "
            f"score={item['final_score']:.2f} closed={item['closed_trades']} "
            f"price={item.get('last_price', 0.0):.6f} "
            f"qv24h={item.get('quote_volume_24h', 0.0):.0f} trades24h={item.get('trade_count_24h', 0)} "
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
    env_now = _read_env_file(SERVICE_ENV_PATH)
    current_symbol = (env_now.get("SYMBOL") or "").strip().upper()
    blocked_symbols = load_blocked_symbols(BLOCKED_SYMBOLS_PATH)
    current_symbol_score = score_map.get(current_symbol, {}) if current_symbol else {}
    current_symbol_blocked = current_symbol in blocked_symbols or bool(current_symbol_score.get("is_toxic"))
    if blocked_symbols:
        print(
            f"TOKEN_SELECTOR: loaded blocked symbols count={len(blocked_symbols)} "
            f"file={BLOCKED_SYMBOLS_PATH}"
        )
    chosen, ranked = pick_best_candidate(score_map, excluded_symbols=blocked_symbols)
    log_selector_memory_state(sync_info, ranked)

    if not chosen:
        if current_symbol and current_symbol_blocked:
            fallback, fallback_mode, positive_ranked, any_ranked = choose_fallback_candidate(
                score_map,
                excluded_symbols=blocked_symbols,
            )
            fallback_ranked = positive_ranked if positive_ranked else any_ranked
            if fallback_ranked:
                log_selector_memory_state(sync_info, fallback_ranked)
            if fallback:
                current_reason = current_symbol_score.get("toxic_reasons") or "blocked_symbol"
                print(
                    f"TOKEN_SELECTOR: fallback selected {fallback['symbol']} "
                    f"mode={fallback_mode} current_blocked={current_symbol} "
                    f"reason={current_reason} "
                    f"var={fallback['pct']:.2f}% toxic={int(bool(fallback.get('is_toxic')))}"
                )
                write_service_env(fallback["symbol"], fallback["pct"], DEFAULT_PROFILE)
                return 0
        print(
            f"TOKEN_SELECTOR: no eligible positive {QUOTE_ASSET} symbol found "
            f"({WINDOW_MINUTES}m window, max_spread={SELECTOR_MAX_SPREAD_PCT*100:.2f}% "
            f"min_price={SELECTOR_MIN_PRICE_USDC:.4f} "
            f"min_quote_volume_24h={SELECTOR_MIN_QUOTE_VOLUME_USDC_24H:.0f} "
            f"min_trade_count_24h={SELECTOR_MIN_TRADE_COUNT_24H})"
        )
        return 0
    raw_top = ranked[0] if ranked else None
    if raw_top and raw_top["symbol"] != chosen["symbol"] and raw_top.get("is_toxic"):
        print(
            f"TOKEN_SELECTOR: skipped toxic top candidate {raw_top['symbol']} "
            f"var={raw_top['pct']:.2f}% reasons={raw_top.get('toxic_reasons') or 'n/a'}"
        )

    # --- Garde minimale : ne switche pas si le token actuel est jeune et encore valable ---
    selector_state = _load_selector_state()
    last_switch_ts = float(selector_state.get("last_switch_ts", 0))
    last_switch_symbol = selector_state.get("last_switch_symbol", "")
    hold_age_min = (time.time() - last_switch_ts) / 60.0

    is_new_token = chosen["symbol"] != current_symbol
    # Check if current token is basically flat (dead) — bypass hold time if so
    current_in_ranked = next((c for c in ranked if c["symbol"] == current_symbol), None)
    current_var_pct = abs(float(current_in_ranked["pct"])) if current_in_ranked else 0.0
    current_is_flat = current_var_pct < SELECTOR_MIN_ACTIVE_VAR_PCT
    if current_is_flat and is_new_token:
        print(
            f"TOKEN_SELECTOR: current {current_symbol} is flat (var={current_var_pct:.2f}% < {SELECTOR_MIN_ACTIVE_VAR_PCT}%) — switching immediately"
        )
    elif (
        is_new_token
        and not current_symbol_blocked
        and current_symbol
        and hold_age_min < SELECTOR_MIN_HOLD_MINUTES
    ):
        # Seulement switcher si le gain de score est suffisant pour justifier l'interruption
        current_score = current_in_ranked["final_score"] if current_in_ranked else 0.0
        score_delta = chosen["final_score"] - current_score
        if score_delta < SELECTOR_HYSTERESIS_PCT:
            print(
                f"TOKEN_SELECTOR: keeping {current_symbol} "
                f"hold_age={hold_age_min:.1f}min < {SELECTOR_MIN_HOLD_MINUTES}min "
                f"delta={score_delta:+.3f} < {SELECTOR_HYSTERESIS_PCT} — no switch"
            )
            write_service_env(current_symbol, current_score, DEFAULT_PROFILE)
            return 0
        print(
            f"TOKEN_SELECTOR: early switch authorized "
            f"hold_age={hold_age_min:.1f}min delta={score_delta:+.3f} >= {SELECTOR_HYSTERESIS_PCT}"
        )

    print(
        f"TOKEN_SELECTOR: selected {chosen['symbol']} "
        f"var={chosen['pct']:.2f}% bonus={chosen['history_bonus']:+.2f} score={chosen['final_score']:.2f} "
        f"price={chosen.get('last_price', 0.0):.6f} "
        f"qv24h={chosen.get('quote_volume_24h', 0.0):.0f} "
        f"trades24h={chosen.get('trade_count_24h', 0)}"
    )
    write_service_env(chosen["symbol"], chosen["pct"], DEFAULT_PROFILE)
    if is_new_token or not last_switch_ts:
        _save_selector_state({
            "last_switch_ts": time.time(),
            "last_switch_symbol": chosen["symbol"],
        })
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
