#!/usr/bin/env python3
"""
Unified logging helpers for the bot.
- Separates DRY vs LIVE logs
- Separates BTC Range V1 vs Main Bot logs
- Auto-creates subdirectories
"""

import csv
import os
import time
from datetime import datetime, timezone
from pathlib import Path


TRADE_CSV_FIELDNAMES = [
    "ts_utc", "symbol", "event", "side", "qty", "price", "reason", "pnl", "pnl_net",
    "profile", "dry_run", "spread_pct", "mom_pct", "mom_range_pct", "up_ratio",
    "rsi", "ema1_ok", "ema5_ok", "vol_ok", "bid", "ask", "mid",
    "entry_price", "p1", "p2", "p3", "p4", "entry_vs_mid_pct", "mid_vs_entry_pct",
    "notional", "min_notional", "step_size", "tick_size", "ws_age_ms",
    "roundtrip_cost_pct", "signal_edge_pct", "required_edge_pct", "expected_net_edge_pct",
    "entry_cross_spread", "entry_mode", "exit_reason", "exit_reason_raw",
    "order_id", "client_order_id", "exchange_status", "fill_latency_ms", "cancel_status",
    "fee_source", "fee_buy", "fee_sell", "commission_asset",
    "executed_qty", "quote_qty", "pnl_gross", "pnl_net_pct",
    "wallet_sync_status", "error_code", "error_msg",
]


def _resolve_log_base_dir(cfg) -> Path:
    """Resolve base log directory with mode and bot type subdirectories."""
    base = Path(getattr(cfg, "logDir", "data/logs"))

    # Subdirectory for dry vs live
    is_dry = bool(getattr(cfg, "dryRun", False))
    mode_dir = "dry" if is_dry else "live"

    # Subdirectory for bot type
    bot_type = getattr(cfg, "botType", "main")
    if bot_type not in ("main", "btc_range"):
        bot_type = "main"

    # Final path: data/logs/dry/btc_range/ or data/logs/live/main/
    final = base / mode_dir / bot_type
    final.mkdir(parents=True, exist_ok=True)
    return final


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d")


def _log_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


class LogDayContext:
    def __init__(self):
        self.day = _today()
        self._anchor = None

    def ensure_anchor_today(self):
        today = _today()
        if today != self.day:
            self.day = today
            self._anchor = None

    def clear_anchor(self):
        self._anchor = None

    def get_anchor(self):
        return self._anchor

    def set_anchor(self, val):
        self._anchor = val


def _log_path(cfg, symbol: str, suffix: str, ctx: LogDayContext) -> Path:
    base = _resolve_log_base_dir(cfg)
    day = ctx.day if ctx else _today()
    return base / f"{symbol}_{day}{suffix}"


def tradeLogger(cfg, symbol: str, ctx: LogDayContext):
    base = _resolve_log_base_dir(cfg)
    day = ctx.day if ctx else _today()
    path = base / f"{symbol}_{day}_trades.log"

    def _log(msg: str):
        ts = _log_timestamp()
        line = f"[{ts}] {msg}\n"
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    return _log


def tradeCsvLogger(cfg, symbol: str, ctx: LogDayContext):
    base = _resolve_log_base_dir(cfg)
    day = ctx.day if ctx else _today()
    path = base / f"{symbol}_{day}_trades.csv"

    def _log(row: dict):
        try:
            exists = path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TRADE_CSV_FIELDNAMES, extrasaction="ignore")
                if not exists:
                    writer.writeheader()
                writer.writerow(row)
        except Exception:
            pass

    return _log


def errorLogger(cfg, symbol: str, ctx: LogDayContext):
    base = _resolve_log_base_dir(cfg)
    day = ctx.day if ctx else _today()
    path = base / f"{symbol}_{day}_errors.log"

    def _log(label: str, exc: Exception):
        ts = _log_timestamp()
        line = f"[{ts}] ERR {label}: {exc}\n"
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    return _log


def ensureCsvHeader(cfg, symbol: str, ctx: LogDayContext):
    """Ensure CSV header exists for today's file."""
    base = _resolve_log_base_dir(cfg)
    day = ctx.day if ctx else _today()
    path = base / f"{symbol}_{day}_trades.csv"

    try:
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TRADE_CSV_FIELDNAMES)
                writer.writeheader()
    except Exception:
        pass


def local_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
