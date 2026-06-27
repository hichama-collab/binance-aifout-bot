import pytest
from requests import HTTPError

from exchange.binance import Binance, BinanceApiError


class FakeResponse:
    status_code = 400
    text = '{"code":-1013,"msg":"Filter failure: MIN_NOTIONAL"}'

    def json(self):
        return {"code": -1013, "msg": "Filter failure: MIN_NOTIONAL"}


def test_binance_http_error_is_typed_when_code_present():
    bx = object.__new__(Binance)
    err = HTTPError(response=FakeResponse())

    with pytest.raises(BinanceApiError) as caught:
        bx._raise_api_error(err, "POST", "/api/v3/order")

    assert caught.value.code == -1013
    assert "MIN_NOTIONAL" in caught.value.msg


def test_retryable_api_codes():
    assert Binance._retryable_api_code(-1003)
    assert Binance._retryable_api_code("-1021")
    assert not Binance._retryable_api_code(-1013)
