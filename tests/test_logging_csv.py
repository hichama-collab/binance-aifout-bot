import csv
from types import SimpleNamespace

from core.logging import LogDayContext, TRADE_CSV_FIELDNAMES, tradeCsvLogger


def test_trade_csv_accepts_new_diagnostic_columns(tmp_path):
    cfg = SimpleNamespace(logDir=tmp_path, dryRun=True, botType="main")
    ctx = LogDayContext()
    log_csv = tradeCsvLogger(cfg, "BTCUSDC", ctx)

    log_csv({
        "ts_utc": "2026-06-27T00:00:00Z",
        "symbol": "BTCUSDC",
        "event": "ORDER_FINAL",
        "side": "BUY",
        "order_id": 123,
        "client_order_id": "aifout_buy_btcusdc_1",
        "exchange_status": "FILLED",
        "fee_source": "exchange",
        "fee_buy": 0.001,
        "commission_asset": "BTC",
        "roundtrip_cost_pct": 0.25,
        "signal_edge_pct": 0.35,
        "required_edge_pct": 0.30,
        "expected_net_edge_pct": 0.10,
        "entry_cross_spread": 1,
        "entry_mode": "P",
        "unexpected_extra": "ignored",
    })

    path = next((tmp_path / "dry" / "main").glob("BTCUSDC_*_trades.csv"))
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        row = next(reader)

    assert "order_id" in reader.fieldnames
    assert "fee_source" in reader.fieldnames
    assert "roundtrip_cost_pct" in reader.fieldnames
    assert "expected_net_edge_pct" in reader.fieldnames
    assert "entry_cross_spread" in reader.fieldnames
    assert set(TRADE_CSV_FIELDNAMES).issubset(set(reader.fieldnames))
    assert row["event"] == "ORDER_FINAL"
    assert row["client_order_id"] == "aifout_buy_btcusdc_1"
    assert row["entry_mode"] == "P"
