import time
from state.position import Position


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
        return pos, syncState, None

    syncState["next"] = now + intervalSec

    try:
        acc = bx.get("/api/v3/account", signed=True)
    except Exception:
        return pos, syncState, None

    balances = acc.get("balances", [])
    base = symbol.replace("USDC", "")

    free_base = 0.0
    free_usdc = 0.0
    for b in balances:
        if b.get("asset") == base:
            free_base = float(b.get("free", 0.0))
        elif b.get("asset") == "USDC":
            free_usdc = float(b.get("free", 0.0))

    qty_now = free_base

    # IMPORTANT: considérer la poussière comme vide (évite ré-adoption après SELL)
    if qty_now <= (float(step) * 0.5):
        if pos is not None:
            return None, syncState, {"changed": True, "usdc": free_usdc, "reason": "wallet_empty"}
        return None, syncState, None

    # Wallet a du base asset mais pas de pos locale => placeholder (entry=0)
    if pos is None:
        p = Position(qty=qty_now, entry=0.0, high=0.0, stop=0.0, ts_entry=time.time())
        return p, syncState, {"changed": True, "usdc": free_usdc, "reason": "wallet_found"}

    # pos existe: si divergence qty, resync qty
    try:
        qty_pos = float(pos.qty)
    except Exception:
        qty_pos = 0.0

    if abs(qty_now - qty_pos) > (float(step) * 0.5):
        pos.qty = qty_now
        return pos, syncState, {"changed": True, "usdc": free_usdc, "reason": "qty_mismatch"}

    return pos, syncState, None
