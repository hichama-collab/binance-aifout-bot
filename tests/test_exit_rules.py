import time
import pytest
from types import SimpleNamespace
from strategy.exit_rules import evaluate_exit
from strategy.regime import RegimeSnapshot
from main import (
    burst_entry_signal,
    burst_runtime_allowed,
    buy_below_sellable_notional,
    compute_entry_net_edge,
    loss_exit_allowed,
    strict_p_tape_exit_reason,
)
from state.position import Position


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


def test_protective_loss_exits_are_allowed():
    assert loss_exit_allowed("PROTECT")
    assert loss_exit_allowed("STOP")
    assert loss_exit_allowed("TRAIL")
    assert loss_exit_allowed("BURST_REVERSAL peak=1")
    assert loss_exit_allowed("BURST_FAIL age=2")
    assert loss_exit_allowed("ENTRY_GUARD_PROTECT gross=-0.1")
    assert loss_exit_allowed("ENTRY_GUARD_LOSS_CUT gross=-0.1")
    assert loss_exit_allowed("PSELL FAIL age=45")
    assert loss_exit_allowed("PSELL FAST latest=1")
    assert not loss_exit_allowed("PSELL STALE")


def test_partial_buy_below_sellable_notional_detected():
    assert buy_below_sellable_notional(0.00001, 65000.0, 5.0)
    assert not buy_below_sellable_notional(0.001, 65000.0, 5.0)


def test_strict_live_disables_burst_but_dry_run_keeps_it_available():
    assert not burst_runtime_allowed(SimpleNamespace(dryRun=False, profileName="strict"))
    assert burst_runtime_allowed(SimpleNamespace(dryRun=True, profileName="strict"))
    assert burst_runtime_allowed(SimpleNamespace(dryRun=False, profileName="aggressive"))


def test_entry_net_edge_rejects_signal_below_roundtrip_cost():
    cfg = SimpleNamespace(minProfitBufferPct=0.0, entryMinNetEdgeMult=1.20)
    edge = compute_entry_net_edge("P", 0.0012, 0.0005, cfg, fee_rate=0.001)

    assert edge["roundtrip_cost_pct"] == pytest.approx(0.0025)
    assert edge["required_edge_pct"] == pytest.approx(0.0030)
    assert edge["signal_edge_pct"] < edge["required_edge_pct"]


def test_entry_net_edge_accepts_signal_above_required_cost():
    cfg = SimpleNamespace(minProfitBufferPct=0.0, entryMinNetEdgeMult=1.20)
    edge = compute_entry_net_edge("P", 0.0035, 0.0005, cfg, fee_rate=0.001)

    assert edge["signal_edge_pct"] >= edge["required_edge_pct"]
    assert edge["expected_net_edge_pct"] == pytest.approx(0.0010)


def test_burst_signal_uses_burst_net_edge_multiplier():
    cfg = SimpleNamespace(
        burstEntryEnabled=True,
        burstLookbackTicks=3,
        burstMaxWindowSec=10.0,
        burstMinReturnPct=0.0,
        burstMinReturnVsFeeBuf=0.0,
        burstMinMoveVsSpread=0.0,
        burstMinVelocityPctPerSec=0.0,
        burstMinEfficiency=0.0,
        burstMinPressureRatio=0.0,
        burstMaxSingleDropPct=1.0,
        burstMinNetEdgeMult=1.25,
        defaultFeeRate=0.001,
        minProfitBufferPct=0.0,
    )
    now = time.time()
    low_edge_ticks = [(now, 100.0), (now + 1, 100.10), (now + 2, 100.20)]
    high_edge_ticks = [(now, 100.0), (now + 1, 100.20), (now + 2, 100.40)]

    low_ok, low_stats = burst_entry_signal(low_edge_ticks, 0.0005, cfg)
    high_ok, high_stats = burst_entry_signal(high_edge_ticks, 0.0005, cfg)

    assert low_stats["required_edge_pct"] == pytest.approx(0.003125)
    assert not low_ok
    assert high_ok
    assert high_stats["required_return_pct"] >= high_stats["required_edge_pct"]


def test_position_tp_has_minimal_net_margin_over_fees():
    cfg = SimpleNamespace(
        riskPct=0.001,
        tpPct=0.001,
        tpMinPct=0.0,
        defaultFeeRate=0.001,
        minProfitBufferPct=0.0,
        tpNetMarginPct=0.003,
        armPct=0.01,
        feeBufPct=0.001,
        trailPct=0.003,
        protectArmPct=0.0,
        protectLockPct=0.0,
        protectGivebackPct=0.0,
    )
    pos = Position(qty=1.0, entry=100.0, high=100.0, stop=0.0, ts_entry=time.time())

    pos.init_stops(cfg, SimpleNamespace(), tick=0.0001)

    assert pos._tp_pct >= 0.005 - 1e-12
