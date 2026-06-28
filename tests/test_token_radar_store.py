from services.token_radar_store import (
    add_favorite,
    get_token_detail,
    get_top_tokens,
    init_db,
    insert_snapshots,
    list_favorites,
    remove_favorite,
)


def _snapshot(symbol="BTCUSDC", score=80.0, price=65000.0):
    return {
        "created_at": "2026-06-28T00:00:00+00:00",
        "symbol": symbol,
        "price": price,
        "bid": price - 1,
        "ask": price + 1,
        "spread_pct": 0.00003,
        "volume_24h": 1000,
        "quote_volume_24h": 10_000_000,
        "change_5m_pct": 0.001,
        "change_15m_pct": 0.002,
        "change_30m_pct": 0.003,
        "change_1h_pct": 0.004,
        "change_2h_pct": 0.005,
        "change_4h_pct": 0.006,
        "change_24h_pct": 0.010,
        "change_3d_pct": 0.020,
        "change_7d_pct": 0.030,
        "high_24h": price * 1.01,
        "low_24h": price * 0.98,
        "distance_high_24h_pct": -0.01,
        "distance_low_24h_pct": 0.02,
        "momentum_score": 20,
        "liquidity_score": 15,
        "spread_score": 15,
        "trend_quality_score": 12,
        "risk_score": 18,
        "global_score": score,
        "signal": "WATCH",
        "reason": "test",
    }


def test_store_creates_db_and_reads_top_tokens(tmp_path):
    db = tmp_path / "radar.sqlite3"
    init_db(db)

    inserted = insert_snapshots([_snapshot("BTCUSDC", 80), _snapshot("ETHUSDC", 70)], db)
    rows = get_top_tokens(min_score=0, limit=10, db_path=db)

    assert inserted == 2
    assert [row["symbol"] for row in rows] == ["BTCUSDC", "ETHUSDC"]


def test_favorites_lifecycle(tmp_path):
    db = tmp_path / "radar.sqlite3"
    insert_snapshots([_snapshot("BTCUSDC", 80)], db)

    fav = add_favorite("BTC", note="watch", db_path=db)
    favorites = list_favorites(db_path=db)
    removed = remove_favorite("BTCUSDC", db_path=db)
    favorites_after = list_favorites(db_path=db)

    assert fav["symbol"] == "BTCUSDC"
    assert favorites[0]["current_price"] == 65000.0
    assert removed is True
    assert favorites_after == []


def test_token_detail_returns_latest_and_history(tmp_path):
    db = tmp_path / "radar.sqlite3"
    first = _snapshot("BTCUSDC", 60, 64000)
    second = _snapshot("BTCUSDC", 82, 65000)
    second["created_at"] = "2026-06-28T00:15:00+00:00"
    insert_snapshots([first, second], db)

    detail = get_token_detail("BTCUSDC", db_path=db)

    assert detail["latest"]["global_score"] == 82
    assert len(detail["history"]) == 2

