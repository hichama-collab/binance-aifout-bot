from tools.token_radar_scan import _has_positive_variation, _is_excluded_base


def test_excludes_stable_and_leveraged_bases():
    assert _is_excluded_base("FDUSD") is True
    assert _is_excluded_base("USD1") is True
    assert _is_excluded_base("BTCUP") is True
    assert _is_excluded_base("SYN") is False


def test_positive_variation_filter():
    assert _has_positive_variation({"change_1h_pct": 0.01}) is True
    assert _has_positive_variation({"change_5m_pct": -0.01, "change_1h_pct": 0.0}) is False
