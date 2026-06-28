from services.token_radar_scoring import score_token


def _base(**kwargs):
    data = {
        "quote_volume_24h": 20_000_000,
        "spread_pct": 0.0004,
        "change_5m_pct": 0.001,
        "change_15m_pct": 0.004,
        "change_30m_pct": 0.006,
        "change_1h_pct": 0.015,
        "change_2h_pct": 0.025,
        "change_4h_pct": 0.040,
        "change_24h_pct": 0.05,
        "distance_high_24h_pct": -0.03,
    }
    data.update(kwargs)
    return data


def test_clean_momentum_scores_high():
    score = score_token(_base())

    assert score["global_score"] >= 70
    assert score["signal"] in {"WATCH", "STRONG_MOMENTUM"}


def test_near_high_and_excessive_pump_penalizes_risk():
    score = score_token(_base(change_5m_pct=0.03, change_24h_pct=0.22, distance_high_24h_pct=-0.0005))

    assert score["risk_score"] < 10
    assert score["signal"] == "NEAR_HIGH_RISK"


def test_wide_spread_has_low_spread_score():
    score = score_token(_base(spread_pct=0.003))

    assert score["spread_score"] <= 3
    assert score["signal"] == "SPREAD_TOO_HIGH"


def test_low_volume_has_low_liquidity_score():
    score = score_token(_base(quote_volume_24h=50_000))

    assert score["liquidity_score"] <= 1
    assert score["signal"] == "LOW_LIQUIDITY"


def test_global_score_is_bounded():
    low = score_token(_base(quote_volume_24h=0, spread_pct=0.05, change_24h_pct=2.0))
    high = score_token(_base(change_15m_pct=1.0, change_1h_pct=1.0, change_4h_pct=1.0))

    assert 0 <= low["global_score"] <= 100
    assert 0 <= high["global_score"] <= 100

