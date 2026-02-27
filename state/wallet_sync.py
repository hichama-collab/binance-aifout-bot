import time
from state.position import Position


def _safe_book_bid(bx, symbol: str) -> float:
    try:
        t = bx.get("/api/v3/ticker/bookTicker", {"symbol": symbol}, signed=False)
        return float(t.get("bidPrice", 0.0))
    except Exception:
        return 0.0


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

    free_base = 0.0
    free_usdc = 0.0
    for b in balances:
        if b.get("asset") == base:
            free_base = float(b.get("free", 0.0))
        elif b.get("asset") == "USDC":
            free_usdc = float(b.get("free", 0.0))

    syncState['usdc'] = free_usdc

    qty_now = free_base

    # Dust detection must include MIN_NOTIONAL (not only step).
    bid = _safe_book_bid(bx, symbol)
    notional = (qty_now * bid) if (qty_now > 0 and bid > 0) else 0.0
    dust_frac = float(getattr(cfg, "dustStepFraction", 0.5))
    is_dust = (qty_now <= (float(step) * dust_frac)) or (notional > 0 and notional < float(minNotional))

    if is_dust:
        if pos is not None:
            return None, syncState, {"changed": True, "usdc": free_usdc, "reason": "wallet_dust"}
        return None, syncState, {"usdc": free_usdc, "reason": "wallet_dust"}

    # Wallet has base asset but no local pos => adopt qty, but keep entry=0 (untracked).
    # Main loop will treat entry<=0 as "untracked" and will liquidate when possible.
    if pos is None:
    # placeholder pos (entry=0). main loop will adopt current bid as entry and init stops.
        p = Position(qty=qty_now, entry=0.0, high=0.0, stop=0.0, ts_entry=time.time())
        return p, syncState, {"changed": True, "usdc": free_usdc, "reason": "wallet_found"}

    # pos exists: if divergence qty, resync qty
    try:
        qty_pos = float(pos.qty)
    except Exception:
        qty_pos = 0.0

    if abs(qty_now - qty_pos) > (float(step) * 0.5):
        pos.qty = qty_now
        return pos, syncState, {"changed": True, "usdc": free_usdc, "reason": "qty_mismatch"}

    return pos, syncState, {"usdc": free_usdc}