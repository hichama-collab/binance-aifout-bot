import time
from indicators.basic import fmt


def _fake_order(symbol: str, side: str, qty: float, price: float, orderId: int):
    return {
        "symbol": symbol,
        "side": side,
        "orderId": orderId,
        "status": "FILLED",
        "executedQty": str(qty),
        "cummulativeQuoteQty": str(qty * price),
    }


def placeLimit(bx, symbol: str, side: str, qty: float, price: float, stepQ, tickQ, dryRun: bool = False):
    if dryRun:
        oid = int(time.time() * 1000) % 10_000_000_000
        return {"orderId": oid, "status": "NEW", "symbol": symbol, "side": side, "price": price, "origQty": qty}

    params = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTC",
        "quantity": fmt(qty, stepQ),
        "price": fmt(price, tickQ),
    }
    return bx.post("/api/v3/order", params)


def getOrder(bx, symbol: str, orderId: int):
    return bx.get("/api/v3/order", {"symbol": symbol, "orderId": orderId}, signed=True)


def cancelOrder(bx, symbol: str, orderId: int):
    return bx.delete("/api/v3/order", {"symbol": symbol, "orderId": orderId})


def waitFillOrCancel(
    bx,
    symbol: str,
    orderId: int,
    ttl: float,
    poll: float,
    *,
    dryRun: bool = False,
    side: str = "",
    qty: float = 0.0,
    price: float = 0.0,
    maxRestRetries: int = 3,
    restBackoffSec: float = 0.2,
):
    if dryRun:
        return True, _fake_order(symbol, side, qty, price, orderId)

    def _executed_qty(order: dict) -> float:
        try:
            return float(order.get("executedQty", 0.0) or 0.0)
        except Exception:
            return 0.0

    t0 = time.time()
    nextLog = t0
    poll_failures = 0
    while time.time() - t0 < ttl:
        try:
            o = getOrder(bx, symbol, orderId)
            poll_failures = 0
        except Exception as e:
            poll_failures += 1
            print('ORDER_POLL_FAIL', symbol, orderId, type(e).__name__, str(e), "retry", poll_failures)
            if poll_failures >= maxRestRetries:
                print("ORDER_POLL_GIVEUP", symbol, orderId, "retries", poll_failures)
                break
            time.sleep(max(poll, restBackoffSec))
            continue

        st = o.get("status")
        if st == "FILLED":
            return True, o
        if st in ("CANCELED", "REJECTED", "EXPIRED"):
            return _executed_qty(o) > 0.0, o

        now = time.time()
        if now >= nextLog:
            print("ORDER_WAIT", symbol, orderId, st, "t", round(now - t0, 2))
            nextLog = now + 1.0

        time.sleep(poll)

    for attempt in range(1, maxRestRetries + 1):
        try:
            cancelOrder(bx, symbol, orderId)
            break
        except Exception as e:
            print("ORDER_CANCEL_FAIL", symbol, orderId, type(e).__name__, str(e), "retry", attempt)
            time.sleep(restBackoffSec)

    try:
        o = getOrder(bx, symbol, orderId)
    except Exception as e:
        print('ORDER_FINAL_POLL_FAIL', symbol, orderId, type(e).__name__, str(e))
        o = {'symbol': symbol, 'orderId': orderId, 'status': 'UNKNOWN'}
    return (o.get("status") == "FILLED") or (_executed_qty(o) > 0.0), o
