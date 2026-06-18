import time
import pytest
from strategy.exit_rules import evaluate_exit
from strategy.regime import RegimeSnapshot
from main import strict_p_tape_exit_reason


def _make_cfg(**kwargs):
    class Cfg:
        pass
    c = Cfg()
    defaults = {"maxLossPct": 0.008, "minTpPct": 0.004, "maxHoldSec": 300}
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(c, k, v)
    return c


def _make_ticks(prices):
    now = time.time()
    return [(now - (len(prices)-i)*0.5, p) for i, p in enumerate(prices)]


def _regime(label="TREND"):
    return RegimeSnapshot(label, 0.7, 0.003, 0.01, 0.002, 0.003, label == "PUMP")


def test_stop_loss_triggered():
    cfg = _make_cfg(maxLossPct=0.008)
    ticks = _make_ticks([100.0, 100.0, 100.0, 99.0])
    result = evaluate_exit(100.0, 10.0, time.time()-10, "BURST", 1000.0, 100.5,
                           ticks, 99.1, 99.15, 0.0005, _regime(), 0.001, cfg)
    assert result.should_exit
    assert result.reason == "STOP_LOSS"
    assert result.urgency == "AGGRESSIVE"


def test_take_profit_triggered():
    cfg = _make_cfg(minTpPct=0.004)
    ticks = _make_ticks([100.0, 100.2, 100.4, 100.6])
    result = evaluate_exit(100.0, 10.0, time.time()-10, "BURST", 1000.0, 100.6,
                           ticks, 100.6, 100.61, 0.0003, _regime(), 0.001, cfg)
    assert result.should_exit
    assert result.reason == "TAKE_PROFIT"


def test_timeout_no_exit_if_pnl_negative():
    cfg = _make_cfg(maxHoldSec=10)
    ticks = _make_ticks([100.0, 99.9, 99.9, 99.9])
    result = evaluate_exit(100.0, 10.0, time.time()-20, "BURST", 1000.0, 100.0,
                           ticks, 99.8, 99.82, 0.0003, _regime(), 0.001, cfg)
    # PnL négatif → pas de TIMEOUT exit
    assert not result.should_exit or result.reason == "STOP_LOSS"


def test_not_exit_in_gain_no_signal():
    cfg = _make_cfg()
    ticks = _make_ticks([100.0, 100.2, 100.3, 100.35])
    result = evaluate_exit(100.0, 10.0, time.time()-5, "BURST", 1000.0, 100.35,
                           ticks, 100.3, 100.31, 0.0003, _regime(), 0.001, cfg)
    assert not result.should_exit


def test_strict_p_tape_exit_uses_p1_as_newest_point():
    reason = strict_p_tape_exit_reason(99.0, 100.0, 101.0, 102.0)
    assert reason is not None
    assert reason.startswith("PSELL_DIRECT_TAPE")


def test_strict_p_tape_exit_does_not_trigger_on_rising_prices():
    assert strict_p_tape_exit_reason(102.0, 101.0, 100.0, 99.0) is None


def test_strict_p_tape_exit_requires_strict_decrease():
    assert strict_p_tape_exit_reason(99.0, 100.0, 100.0, 102.0) is None
    assert strict_p_tape_exit_reason(99.0, None, 101.0, 102.0) is None
