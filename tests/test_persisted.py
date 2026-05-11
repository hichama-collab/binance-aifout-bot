import time
import pytest
from pathlib import Path
import tempfile
from state.persisted import save_position, load_position, clear_position, PersistedPosition, reconcile_with_wallet


def _make_pos(symbol="TESTUSDC"):
    return PersistedPosition(
        symbol=symbol,
        entry_price=100.0,
        entry_qty=10.0,
        entry_ts=time.time() - 60,
        entry_reason="BURST",
        high_seen=101.0,
        buy_notional=1000.0,
    )


def test_save_and_load():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    pos = _make_pos()
    assert save_position(pos, path)
    loaded = load_position(path)
    assert loaded is not None
    assert loaded.symbol == "TESTUSDC"
    assert loaded.entry_price == pytest.approx(100.0)
    path.unlink(missing_ok=True)


def test_load_missing_returns_none():
    result = load_position(Path("/tmp/nonexistent_xyz.json"))
    assert result is None


def test_clear():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    save_position(_make_pos(), path)
    assert clear_position(path)
    assert not path.exists()


def test_reconcile_match():
    pos = _make_pos()
    result = reconcile_with_wallet(pos, 10.0, "TESTUSDC")
    assert result is not None
    assert result.entry_price == 100.0


def test_reconcile_external_sell():
    pos = _make_pos()
    result = reconcile_with_wallet(pos, 0.0, "TESTUSDC")
    assert result is None


def test_reconcile_no_persisted_no_wallet():
    result = reconcile_with_wallet(None, 0.0, "TESTUSDC")
    assert result is None
