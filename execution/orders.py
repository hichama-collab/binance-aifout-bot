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


def waitFillOrCancel(bx, symbol: str, orderId: int, ttl: float, poll: float, *, dryRun: bool = False, side: str = "", qty: float = 0.0, price: float = 0.0):
    if dryRun:
        return True, _fake_order(symbol, side, qty, price, orderId)

    t0 = time.time()
    nextLog = t0
    while time.time() - t0 < ttl:
        o = getOrder(bx, symbol, orderId)
        st = o.get("status")
        if st == "FILLED":
            return True, o
        if st in ("CANCELED", "REJECTED", "EXPIRED"):
            return False, o

        now = time.time()
        if now >= nextLog:
            print("ORDER_WAIT", symbol, orderId, st, "t", round(now - t0, 2))
            nextLog = now + 1.0

        time.sleep(poll)

    try:
        cancelOrder(bx, symbol, orderId)
    except Exception:
        pass

    o = getOrder(bx, symbol, orderId)
    return (o.get("status") == "FILLED"), o
