import pytest

from execution.orders import OrderStateUnknown, order_fee_summary, placeLimit, waitFillOrCancel


class FakeBinance:
    def __init__(self):
        self.posts = []
        self.deleted = []

    def post(self, path, params):
        self.posts.append((path, params))
        return {"orderId": 123, "clientOrderId": params["newClientOrderId"], "status": "NEW"}

    def get(self, path, params=None, signed=False):
        if path == "/api/v3/order":
            raise RuntimeError("status unavailable")
        if path == "/api/v3/openOrders":
            return [{"orderId": 123}]
        raise AssertionError(path)

    def delete(self, path, params):
        self.deleted.append((path, params))
        raise RuntimeError("cancel unavailable")


class FakeOpenAfterCancel:
    def get(self, path, params=None, signed=False):
        if path == "/api/v3/order":
            return {"orderId": 123, "status": "NEW", "executedQty": "0"}
        if path == "/api/v3/openOrders":
            return [{"orderId": 123}]
        raise AssertionError(path)

    def delete(self, path, params):
        return {"orderId": 123, "status": "CANCEL_SENT"}


def test_place_limit_sets_client_order_id_and_full_response():
    bx = FakeBinance()

    order = placeLimit(bx, "BTCUSDC", "BUY", 0.001, 65000.0, 0.000001, 0.01)

    params = bx.posts[0][1]
    assert order["clientOrderId"].startswith("aifout_buy_btcusdc_")
    assert params["newClientOrderId"] == order["clientOrderId"]
    assert params["newOrderRespType"] == "FULL"


def test_wait_fill_or_cancel_raises_on_unknown_final_state():
    bx = FakeBinance()

    with pytest.raises(OrderStateUnknown, match="ORDER_STATE_UNKNOWN"):
        waitFillOrCancel(
            bx,
            "BTCUSDC",
            123,
            ttl=0.0,
            poll=0.0,
            side="BUY",
            qty=0.001,
            price=65000.0,
            maxRestRetries=1,
            restBackoffSec=0.0,
        )


def test_wait_fill_or_cancel_raises_if_order_still_open_after_cancel():
    with pytest.raises(OrderStateUnknown, match="exchange_status=NEW"):
        waitFillOrCancel(
            FakeOpenAfterCancel(),
            "BTCUSDC",
            123,
            ttl=0.0,
            poll=0.0,
            side="BUY",
            qty=0.001,
            price=65000.0,
            maxRestRetries=1,
            restBackoffSec=0.0,
        )


def test_order_fee_summary_uses_exchange_fills_when_available():
    order = {
        "executedQty": "2",
        "cummulativeQuoteQty": "20",
        "fills": [
            {"commission": "0.001", "commissionAsset": "BTC"},
            {"commission": "0.002", "commissionAsset": "BTC"},
        ],
    }

    fees = order_fee_summary(order)

    assert fees["fee_source"] == "exchange"
    assert fees["fee"] == pytest.approx(0.003)
    assert fees["commission_asset"] == "BTC"
    assert fees["executed_qty"] == 2.0
    assert fees["quote_qty"] == 20.0


def test_order_fee_summary_marks_estimated_without_fills():
    fees = order_fee_summary({"executedQty": "1", "cummulativeQuoteQty": "10"})

    assert fees["fee_source"] == "estimated"
    assert fees["commission_asset"] == ""
