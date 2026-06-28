#!/usr/bin/env python3
"""One-shot public Binance scanner for Token Radar."""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.token_radar_scoring import score_token
from services.token_radar_store import finish_scan_run, insert_snapshots, resolve_db_path, start_scan_run


BASE_URL = os.getenv("TOKEN_RADAR_BASE_URL", "https://api.binance.com").rstrip("/")
QUOTE = os.getenv("TOKEN_RADAR_QUOTE", "USDC").upper()
LIMIT = max(1, int(os.getenv("TOKEN_RADAR_LIMIT", "80")))
MIN_QUOTE_VOLUME_24H = float(os.getenv("TOKEN_RADAR_MIN_QUOTE_VOLUME_24H", "500000"))
MAX_SPREAD_PCT = float(os.getenv("TOKEN_RADAR_MAX_SPREAD_PCT", "0.002"))
HTTP_TIMEOUT = float(os.getenv("TOKEN_RADAR_HTTP_TIMEOUT", "10"))
MAX_WORKERS = max(1, int(os.getenv("TOKEN_RADAR_MAX_WORKERS", "8")))
EXCLUDED_BASE_ASSETS = {
    item.strip().upper()
    for item in os.getenv(
        "TOKEN_RADAR_EXCLUDE_BASES",
        "USDC,USDT,FDUSD,TUSD,USDP,DAI,USD1,EUR,TRY,BRL",
    ).split(",")
    if item.strip()
}


def _float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _public_get(session: requests.Session, path: str, params: dict | None = None):
    url = f"{BASE_URL}{path}"
    last_exc = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, params=params or {}, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                time.sleep(0.35 * attempt)
    raise RuntimeError(f"GET {path} failed: {last_exc}")


def _is_excluded_base(base_asset: str) -> bool:
    base = str(base_asset or "").upper()
    return base in EXCLUDED_BASE_ASSETS or base.endswith(("UP", "DOWN", "BULL", "BEAR"))


def _load_symbols(session: requests.Session) -> set[str]:
    data = _public_get(session, "/api/v3/exchangeInfo")
    symbols = set()
    for row in data.get("symbols", []):
        symbol = str(row.get("symbol") or "").upper()
        base_asset = str(row.get("baseAsset") or "").upper()
        if not symbol.endswith(QUOTE):
            continue
        if row.get("quoteAsset") and str(row.get("quoteAsset")).upper() != QUOTE:
            continue
        if row.get("status") != "TRADING":
            continue
        if row.get("isSpotTradingAllowed") is False:
            continue
        if _is_excluded_base(base_asset):
            continue
        symbols.add(symbol)
    return symbols


def _market_maps(session: requests.Session) -> tuple[dict, dict]:
    tickers = _public_get(session, "/api/v3/ticker/24hr")
    books = _public_get(session, "/api/v3/ticker/bookTicker")
    ticker_map = {str(row.get("symbol") or "").upper(): row for row in tickers if isinstance(row, dict)}
    book_map = {str(row.get("symbol") or "").upper(): row for row in books if isinstance(row, dict)}
    return ticker_map, book_map


def _change_from_klines(klines: list, bars: int) -> float | None:
    if not isinstance(klines, list) or len(klines) < bars:
        return None
    first_open = _float(klines[-bars][1])
    last_close = _float(klines[-1][4])
    if first_open <= 0:
        return None
    return (last_close - first_open) / first_open


def _fetch_klines(session: requests.Session, symbol: str, interval: str, limit: int) -> list:
    data = _public_get(
        session,
        "/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": int(limit)},
    )
    return data if isinstance(data, list) else []


def _build_base_candidate(symbol: str, ticker: dict, book: dict) -> dict | None:
    bid = _float(book.get("bidPrice"))
    ask = _float(book.get("askPrice"))
    price = _float(ticker.get("lastPrice")) or ((bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0)
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else price
    if price <= 0 or mid <= 0 or bid <= 0 or ask <= 0:
        return None
    spread = (ask - bid) / mid
    quote_volume = _float(ticker.get("quoteVolume"))
    if quote_volume < MIN_QUOTE_VOLUME_24H:
        return None
    if spread > MAX_SPREAD_PCT:
        return None

    high_24h = _float(ticker.get("highPrice"))
    low_24h = _float(ticker.get("lowPrice"))
    distance_high = ((price - high_24h) / high_24h) if high_24h > 0 else None
    distance_low = ((price - low_24h) / low_24h) if low_24h > 0 else None
    return {
        "symbol": symbol,
        "price": price,
        "bid": bid,
        "ask": ask,
        "spread_pct": spread,
        "volume_24h": _float(ticker.get("volume")),
        "quote_volume_24h": quote_volume,
        "change_24h_pct": _float(ticker.get("priceChangePercent")) / 100.0,
        "high_24h": high_24h,
        "low_24h": low_24h,
        "distance_high_24h_pct": distance_high,
        "distance_low_24h_pct": distance_low,
    }


def _enrich_candidate(symbol: str, base: dict) -> dict:
    session = requests.Session()
    one_min = _fetch_klines(session, symbol, "1m", 240)
    one_hour = _fetch_klines(session, symbol, "1h", 168)

    changes = {
        "change_5m_pct": _change_from_klines(one_min, 5),
        "change_15m_pct": _change_from_klines(one_min, 15),
        "change_30m_pct": _change_from_klines(one_min, 30),
        "change_1h_pct": _change_from_klines(one_min, 60),
        "change_2h_pct": _change_from_klines(one_min, 120),
        "change_4h_pct": _change_from_klines(one_min, 240),
        "change_3d_pct": _change_from_klines(one_hour, 72),
        "change_7d_pct": _change_from_klines(one_hour, 168),
    }
    if len(one_hour) >= 24:
        changes["change_24h_pct"] = _change_from_klines(one_hour, 24)
    row = {**base, **{k: (0.0 if v is None else v) for k, v in changes.items()}}
    row.update(score_token(row))
    return row


def _has_positive_variation(row: dict) -> bool:
    return any(
        (row.get(key) or 0.0) > 0.0
        for key in ("change_5m_pct", "change_15m_pct", "change_1h_pct", "change_24h_pct")
    )


def scan() -> list[dict]:
    session = requests.Session()
    symbols = _load_symbols(session)
    ticker_map, book_map = _market_maps(session)
    candidates = []
    for symbol in symbols:
        ticker = ticker_map.get(symbol)
        book = book_map.get(symbol)
        if not ticker or not book:
            continue
        candidate = _build_base_candidate(symbol, ticker, book)
        if candidate:
            candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            item["quote_volume_24h"],
            abs(item.get("change_24h_pct") or 0.0),
            item["symbol"],
        ),
        reverse=True,
    )
    selected = candidates[:LIMIT]
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshots = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_item = {pool.submit(_enrich_candidate, item["symbol"], item): item for item in selected}
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                row = future.result()
                if not _has_positive_variation(row):
                    continue
                row["created_at"] = created_at
                snapshots.append(row)
            except Exception as exc:
                print(f"TOKEN_RADAR: skip {item['symbol']} err={type(exc).__name__}:{exc}", file=sys.stderr)
    snapshots.sort(key=lambda item: (item["score"], item["symbol"]), reverse=True)
    return snapshots


def main() -> int:
    db_path = resolve_db_path()
    run_id = start_scan_run(db_path)
    snapshots = []
    try:
        snapshots = scan()
        inserted = insert_snapshots(snapshots, db_path)
        finish_scan_run(run_id, status="ok", scanned_count=len(snapshots), inserted_count=inserted, db_path=db_path)
        print(f"TOKEN_RADAR: db={db_path} scanned={len(snapshots)} inserted={inserted}")
        for row in snapshots[:10]:
            print(f"{row['symbol']} score={row['score']:.2f} signal={row['signal']} price={row['price']:.8g}")
        return 0
    except Exception as exc:
        finish_scan_run(
            run_id,
            status="error",
            scanned_count=len(snapshots),
            inserted_count=0,
            error=f"{type(exc).__name__}: {exc}",
            db_path=db_path,
        )
        print(f"TOKEN_RADAR: ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
