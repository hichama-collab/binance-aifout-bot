import time
from indicators.basic import fmt


class OrderStateUnknown(RuntimeError):
    """Raised when Binance order final state cannot be proven."""


def _client_order_id(side: str, symbol: str) -> str:
    return f"aifout_{str(side).lower()}_{str(symbol).lower()}_{int(time.time() * 1000)}"


def _fake_order(symbol: str, side: str, qty: float, price: float, orderId: int):
    client_order_id = _client_order_id(side, symbol)
    return {
        "symbol": symbol,
        "side": side,
        "orderId": orderId,
        "clientOrderId": client_order_id,
        "status": "FILLED",
        "executedQty": str(qty),
        "cummulativeQuoteQty": str(qty * price),
        "fills": [],
    }


def placeLimit(bx, symbol: str, side: str, qty: float, price: float, stepQ, tickQ, dryRun: bool = False):
    client_order_id = _client_order_id(side, symbol)
    if dryRun:
        oid = int(time.time() * 1000) % 10_000_000_000
        return {
            "orderId": oid,
            "clientOrderId": client_order_id,
            "status": "NEW",
            "symbol": symbol,
            "side": side,
            "price": price,
            "origQty": qty,
            "fills": [],
        }

    params = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTC",
        "quantity": fmt(qty, stepQ),
        "price": fmt(price, tickQ),
        "newClientOrderId": client_order_id,
        "newOrderRespType": "FULL",
    }
    return bx.post("/api/v3/order", params)


def getOrder(bx, symbol: str, orderId: int):
    return bx.get("/api/v3/order", {"symbol": symbol, "orderId": orderId}, signed=True)


def cancelOrder(bx, symbol: str, orderId: int):
    return bx.delete("/api/v3/order", {"symbol": symbol, "orderId": orderId})


def openOrders(bx, symbol: str):
    return bx.get("/api/v3/openOrders", {"symbol": symbol}, signed=True)


def order_fee_summary(order: dict, fallback_qty: float = 0.0, fallback_quote: float = 0.0) -> dict:
    fills = order.get("fills") if isinstance(order, dict) else None
    fee_total = 0.0
    assets = []
    if isinstance(fills, list):
        for fill in fills:
            try:
                fee = float(fill.get("commission", 0.0) or 0.0)
            except Exception:
                fee = 0.0
            asset = str(fill.get("commissionAsset", "") or "").strip()
            fee_total += fee
            if asset and asset not in assets:
                assets.append(asset)

    try:
        executed_qty = float(order.get("executedQty", fallback_qty) or 0.0)
    except Exception:
        executed_qty = float(fallback_qty or 0.0)
    try:
        quote_qty = float(order.get("cummulativeQuoteQty", fallback_quote) or 0.0)
    except Exception:
        quote_qty = float(fallback_quote or 0.0)

    return {
        "fee_source": "exchange" if fills else "estimated",
        "fee": fee_total,
        "commission_asset": ",".join(assets),
        "executed_qty": executed_qty,
        "quote_qty": quote_qty,
    }


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

    cancel_status = ""
    for attempt in range(1, maxRestRetries + 1):
        try:
            cancel_result = cancelOrder(bx, symbol, orderId)
            cancel_status = str(cancel_result.get("status", "CANCEL_SENT")) if isinstance(cancel_result, dict) else "CANCEL_SENT"
            break
        except Exception as e:
            cancel_status = f"ERROR:{type(e).__name__}"
            print("ORDER_CANCEL_FAIL", symbol, orderId, type(e).__name__, str(e), "retry", attempt)
            time.sleep(restBackoffSec)

    try:
        o = getOrder(bx, symbol, orderId)
    except Exception as e:
        print('ORDER_FINAL_POLL_FAIL', symbol, orderId, type(e).__name__, str(e))
        try:
            opens = openOrders(bx, symbol)
            open_count = len(opens) if isinstance(opens, list) else -1
        except Exception as open_exc:
            open_count = -1
            print("ORDER_OPEN_ORDERS_CHECK_FAIL", symbol, orderId, type(open_exc).__name__, str(open_exc))
        raise OrderStateUnknown(
            f"ORDER_STATE_UNKNOWN symbol={symbol} orderId={orderId} side={side} "
            f"cancel_status={cancel_status or 'UNKNOWN'} open_orders={open_count}"
        ) from e

    st = str(o.get("status", "") or "")
    exec_qty = _executed_qty(o)
    if st == "UNKNOWN":
        raise OrderStateUnknown(
            f"ORDER_STATE_UNKNOWN symbol={symbol} orderId={orderId} side={side} "
            f"cancel_status={cancel_status or 'UNKNOWN'}"
        )
    if st not in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
        try:
            opens = openOrders(bx, symbol)
            open_count = len(opens) if isinstance(opens, list) else -1
        except Exception as open_exc:
            open_count = -1
            print("ORDER_OPEN_ORDERS_CHECK_FAIL", symbol, orderId, type(open_exc).__name__, str(open_exc))
        if open_count != 0:
            raise OrderStateUnknown(
                f"ORDER_STATE_UNKNOWN symbol={symbol} orderId={orderId} side={side} "
                f"exchange_status={st} cancel_status={cancel_status or 'UNKNOWN'} open_orders={open_count}"
            )
        if exec_qty <= 0.0:
            raise OrderStateUnknown(
                f"ORDER_STATE_UNKNOWN symbol={symbol} orderId={orderId} side={side} "
                f"exchange_status={st} cancel_status={cancel_status or 'UNKNOWN'} open_orders=0"
            )
    return (st == "FILLED") or (exec_qty > 0.0), o
