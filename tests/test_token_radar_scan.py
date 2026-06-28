from tools.token_radar_scan import _build_base_candidate, _is_excluded_base


def test_excludes_stable_and_leveraged_bases():
    assert _is_excluded_base("FDUSD") is True
    assert _is_excluded_base("USD1") is True
    assert _is_excluded_base("USDE") is True
    assert _is_excluded_base("BFUSD") is True
    assert _is_excluded_base("BTCUP") is True
    assert _is_excluded_base("SYN") is False


def test_base_candidate_does_not_cut_on_volume_or_spread():
    ticker = {
        "lastPrice": "1.0",
        "quoteVolume": "10",
        "volume": "10",
        "priceChangePercent": "-5",
        "highPrice": "1.2",
        "lowPrice": "0.8",
    }
    book = {"bidPrice": "0.90", "askPrice": "1.10"}

    row = _build_base_candidate("TESTUSDC", ticker, book)

    assert row is not None
    assert row["quote_volume_24h"] == 10
    assert row["spread_pct"] > 0.1
