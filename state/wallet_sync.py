import time
import os
import json
from pathlib import Path
from typing import Optional
from state.position import Position


_QUOTE_OR_NON_POSITION_ASSETS = {"USDC", "USDT", "BUSD", "FDUSD", "TUSD", "USDP", "DAI", "EUR"}


def _safe_book_bid(bx, symbol: str) -> float:
    try:
        t = bx.get("/api/v3/ticker/bookTicker", {"symbol": symbol}, signed=False)
        return float(t.get("bidPrice", 0.0))
    except Exception:
        return 0.0


def _safe_book_bid_map(bx) -> dict:
    try:
        rows = bx.get("/api/v3/ticker/bookTicker", signed=False)
        if not isinstance(rows, list):
            return {}
        out = {}
        for row in rows:
            try:
                sym = str(row.get("symbol", "")).upper()
                bid = float(row.get("bidPrice", 0.0) or 0.0)
            except Exception:
                continue
            if sym and bid > 0:
                out[sym] = bid
        return out
    except Exception:
        return {}


def _balance_qty(balance: dict) -> tuple[float, float, float]:
    free = float(balance.get("free", 0.0) or 0.0)
    locked = float(balance.get("locked", 0.0) or 0.0)
    return free, locked, free + locked


def _wallet_holdings(balances: list, bid_map: dict, min_notional: float) -> list:
    holdings = []
    for b in balances:
        asset = str(b.get("asset", "")).upper()
        if not asset or asset in _QUOTE_OR_NON_POSITION_ASSETS:
            continue
        try:
            free, locked, qty = _balance_qty(b)
        except Exception:
            continue
        if qty <= 0:
            continue
        symbol = f"{asset}USDC"
        bid = float(bid_map.get(symbol, 0.0) or 0.0)
        notional = qty * bid if bid > 0 else 0.0
        if notional < float(min_notional):
            continue
        holdings.append({
            "asset": asset,
            "symbol": symbol,
            "free": free,
            "locked": locked,
            "qty": qty,
            "bid": bid,
            "notional": notional,
        })
    holdings.sort(key=lambda h: h["notional"], reverse=True)
    return holdings



def _runtime_dir() -> Path:
    # Bot runtime directory (default: data/runtime relative to project root)
    p = os.getenv("BOT_RUNTIME_DIR") or "data/runtime"
    d = Path(p)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _safe_write_json(path: Path, data: object) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        # best effort
        return


def _record_flat_guard(
    now: float,
    symbol: str,
    cfg,
    reason: str,
    qty: float,
    notional: float,
) -> float:
    duration = max(
        float(getattr(cfg, "walletFlatCooldownSec", 0.0) or 0.0),
        float(getattr(cfg, "dustCooldownSec", 0.0) or 0.0),
    )
    if duration <= 0:
        return 0.0
    guard_until = now + duration
    _safe_write_json(_runtime_dir() / "wallet_flat_guard.json", {
        "ts": now,
        "until": guard_until,
        "symbol": symbol,
        "reason": reason,
        "qty": qty,
        "notional": notional,
    })
    return guard_until


def loadWalletFlatGuard(symbol: str, now: Optional[float] = None) -> dict:
    path = _runtime_dir() / "wallet_flat_guard.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    current = time.time() if now is None else float(now)
    if str(payload.get("symbol", "")).upper() != str(symbol or "").upper():
        return {}
    if float(payload.get("until", 0.0) or 0.0) <= current:
        return {}
    return payload


def _estimate_entry_from_recent_trades(bx, symbol: str, qty_now: float, step: float) -> tuple[float, str]:
    """
    Estimate the entry of the currently held quantity from recent Binance fills.

    Binance spot balances do not expose cost basis. Walking recent BUY fills from
    newest to oldest gives a practical approximation of the still-open lot after
    sells have reduced older inventory.
    """
    if qty_now <= 0:
        return 0.0, ""
    try:
        trades = bx.get("/api/v3/myTrades", {"symbol": symbol, "limit": 1000}, signed=True) or []
    except Exception:
        return 0.0, ""

    needed = float(qty_now)
    tolerance = max(float(step) * 0.5, abs(float(qty_now)) * 1e-6, 1e-12)
    qty_acc = 0.0
    cost_acc = 0.0
    try:
        ordered = sorted(trades, key=lambda t: int(t.get("time", 0)), reverse=True)
    except Exception:
        ordered = list(reversed(trades))

    for trade in ordered:
        try:
            if not bool(trade.get("isBuyer", False)):
                continue
            qty = float(trade.get("qty", 0.0) or 0.0)
            price = float(trade.get("price", 0.0) or 0.0)
        except Exception:
            continue
        if qty <= 0 or price <= 0:
            continue
        take = min(qty, needed)
        qty_acc += take
        cost_acc += take * price
        needed -= take
        if needed <= tolerance:
            break

    if qty_acc > 0 and needed <= max(tolerance, abs(qty_now) * 0.01):
        return cost_acc / qty_acc, "myTrades"
    return 0.0, ""


def _position_payload(now: float, symbol: str, pos: Optional[Position], reason: str = "") -> dict:
    if pos is None:
        return {"ts": now, "symbol": "", "qty": 0.0, "reason": reason}
    entry = float(getattr(pos, "entry", 0.0) or 0.0)
    return {
        "ts": now,
        "symbol": symbol,
        "qty": float(getattr(pos, "qty", 0.0) or 0.0),
        "entry": entry,
        "entry_price": entry,
        "cost_basis": float(getattr(pos, "cost_basis", entry) or entry),
        "entry_source": str(getattr(pos, "entry_source", "") or ""),
        "high": float(getattr(pos, "high", 0.0) or 0.0),
        "stop": float(getattr(pos, "stop", 0.0) or 0.0),
        "ts_entry": float(getattr(pos, "ts_entry", 0.0) or 0.0),
        "reason": reason,
    }


def _write_runtime_snapshot(
    now: float,
    symbol: str,
    acc: dict,
    balances: list,
    pos: Optional[Position],
    reason: str = "",
    holdings: Optional[list] = None,
) -> None:
    try:
        rt = _runtime_dir()
        _safe_write_json(rt / "account.json", {"ts": now, "symbol": symbol, "account": acc})
        _safe_write_json(rt / "wallet.json", {"ts": now, "symbol": symbol, "balances": balances})
        _safe_write_json(rt / "position.json", _position_payload(now, symbol, pos, reason))
        if holdings is not None:
            _safe_write_json(rt / "portfolio.json", {"ts": now, "symbol": symbol, "holdings": holdings})
    except Exception:
        pass

def walletSyncEvery(
    bx,
    symbol,
    pos,
    cfg,
    *,
    step,
    minNotional,
    syncState,
    intervalSec=60,
):
    free_usdc = float(syncState.get('usdc', 0.0))
    # compat: anciens états pos en dict
    if isinstance(pos, dict):
        try:
            pos = Position(
                qty=float(pos.get("qty", 0.0)),
                entry=float(pos.get("entry", 0.0)),
                high=float(pos.get("high", pos.get("entry", 0.0))),
                stop=float(pos.get("stop", 0.0)),
                ts_entry=float(pos.get("ts_entry", pos.get("time", time.time()))),
            )
        except Exception:
            pos = None

    now = time.time()
    if now < syncState.get("next", 0.0):
        return pos, syncState, {"usdc": free_usdc}
    syncState["next"] = now + intervalSec

    max_retries = int(getattr(cfg, "walletMaxRetries", 0))
    backoff = float(getattr(cfg, "walletRetryBackoffSec", 0.0))
    acc = None
    for attempt in range(max_retries + 1):
        try:
            acc = bx.get("/api/v3/account", signed=True)
            break
        except Exception:
            if attempt >= max_retries:
                return pos, syncState, {"usdc": free_usdc}
            time.sleep(backoff)

    balances = acc.get("balances", [])
    base = symbol.replace("USDC", "")
    bid_map = _safe_book_bid_map(bx)

    free_base = 0.0
    locked_base = 0.0
    free_usdc = 0.0
    for b in balances:
        if b.get("asset") == base:
            free_base = float(b.get("free", 0.0))
            locked_base = float(b.get("locked", 0.0))
        elif b.get("asset") == "USDC":
            free_usdc = float(b.get("free", 0.0))

    syncState['usdc'] = free_usdc

    qty_now = free_base + locked_base

    # Dust detection must include MIN_NOTIONAL (not only step).
    bid = float(bid_map.get(symbol, 0.0) or 0.0) or _safe_book_bid(bx, symbol)
    notional = (qty_now * bid) if (qty_now > 0 and bid > 0) else 0.0
    dust_frac = float(getattr(cfg, "dustStepFraction", 0.5))
    qty_floor = float(step) * dust_frac
    holdings = _wallet_holdings(balances, bid_map, float(minNotional))
    external_holding = next((h for h in holdings if h.get("symbol") != symbol), None)

    if qty_now <= qty_floor:
        reason = "wallet_cleared" if pos is not None else "wallet_empty"
        if external_holding is not None and pos is None:
            _write_runtime_snapshot(now, symbol, acc, balances, None, "external_symbol_found", holdings)
            return None, syncState, {
                "changed": True,
                "usdc": free_usdc,
                "reason": "external_symbol_found",
                "external_symbol": external_holding["symbol"],
                "external_asset": external_holding["asset"],
                "wallet_qty": external_holding["qty"],
                "wallet_notional": external_holding["notional"],
                "holdings": holdings,
            }
        guard_until = (
            _record_flat_guard(now, symbol, cfg, reason, qty_now, notional)
            if pos is not None
            else 0.0
        )
        _write_runtime_snapshot(now, symbol, acc, balances, None, reason, holdings)
        return None, syncState, {
            "changed": pos is not None,
            "usdc": free_usdc,
            "reason": reason,
            "wallet_qty": qty_now,
            "wallet_notional": notional,
            "entry_block_until": guard_until,
            "holdings": holdings,
        }

    is_dust = notional > 0 and notional < float(minNotional)
    if is_dust:
        _write_runtime_snapshot(now, symbol, acc, balances, None, "wallet_dust", holdings)
        if pos is not None:
            guard_until = _record_flat_guard(
                now, symbol, cfg, "wallet_dust", qty_now, notional
            )
            return None, syncState, {
                "changed": True,
                "usdc": free_usdc,
                "reason": "wallet_dust",
                "wallet_qty": qty_now,
                "wallet_notional": notional,
                "entry_block_until": guard_until,
                "holdings": holdings,
            }
        if external_holding is not None:
            return None, syncState, {
                "changed": True,
                "usdc": free_usdc,
                "reason": "external_symbol_found",
                "external_symbol": external_holding["symbol"],
                "external_asset": external_holding["asset"],
                "wallet_qty": external_holding["qty"],
                "wallet_notional": external_holding["notional"],
                "holdings": holdings,
            }
        return None, syncState, {
            "usdc": free_usdc,
            "reason": "wallet_dust",
            "wallet_qty": qty_now,
            "wallet_notional": notional,
            "holdings": holdings,
        }

    # Wallet has base asset but no local pos => adopt it immediately.
    # Prefer Binance fills for entry; fallback to current bid so the bot does not stay blind.
    if pos is None:
        entry, entry_source = _estimate_entry_from_recent_trades(bx, symbol, qty_now, float(step))
        estimated_entry = entry
        reason = "wallet_found"
        if entry > 0 and bid > 0:
            sl_pct = float(getattr(cfg, "riskPct", 0.008) or 0.008)
            if sl_pct <= 0:
                sl_pct = 0.008
            already_below_stop = bid <= (entry * (1.0 - sl_pct))
            if already_below_stop:
                entry = bid
                entry_source = f"{entry_source or 'unknown'}_market_reset"
                reason = "wallet_found_market_entry"
        if entry <= 0 and bid > 0:
            entry = bid
            estimated_entry = entry
            entry_source = "market_bid"
            reason = "wallet_found_market_entry"
        if entry <= 0:
            _write_runtime_snapshot(now, symbol, acc, balances, None, "wallet_found_entry_unknown")
            return None, syncState, {
                "changed": True,
                "usdc": free_usdc,
                "reason": "wallet_found_entry_unknown",
                "wallet_qty": qty_now,
            }
        p = Position(qty=qty_now, entry=entry, high=max(entry, bid), stop=0.0, ts_entry=time.time())
        setattr(p, "cost_basis", estimated_entry if estimated_entry > 0 else entry)
        setattr(p, "entry_source", entry_source)
        try:
            p.init_stops(cfg, getattr(cfg, "profile", None), tick=float(getattr(cfg, "tick", 0.0) or 0.0))
        except Exception:
            pass
        _write_runtime_snapshot(now, symbol, acc, balances, p, reason, holdings)
        return p, syncState, {
            "changed": True,
            "usdc": free_usdc,
            "reason": reason,
            "entry_source": entry_source,
            "cost_basis": estimated_entry if estimated_entry > 0 else entry,
            "wallet_qty": qty_now,
            "wallet_notional": notional,
            "holdings": holdings,
        }

    # pos exists: if divergence qty, resync qty
    try:
        qty_pos = float(pos.qty)
    except Exception:
        qty_pos = 0.0

    if abs(qty_now - qty_pos) > (float(step) * 0.5):
        delta = qty_now - qty_pos
        pos.qty = qty_now
        if delta > 0:
            entry, entry_source = _estimate_entry_from_recent_trades(bx, symbol, qty_now, float(step))
            if entry > 0:
                setattr(pos, "cost_basis", entry)
                setattr(pos, "entry_source", f"{entry_source}_qty_mismatch")
        _write_runtime_snapshot(now, symbol, acc, balances, pos, "qty_mismatch", holdings)
        return pos, syncState, {
            "changed": True,
            "usdc": free_usdc,
            "reason": "qty_mismatch",
            "wallet_qty": qty_now,
            "wallet_notional": notional,
            "qty_delta": delta,
            "holdings": holdings,
        }

    _write_runtime_snapshot(now, symbol, acc, balances, pos, "wallet_sync", holdings)
    return pos, syncState, {"usdc": free_usdc, "reason": "wallet_sync", "holdings": holdings}
