from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RangeSnapshot:
    low: float
    high: float
    mid: float
    width: float
    rangePct: float
    driftPct: float
    lastClose: float
    barCount: int
    timeframe: str


@dataclass(frozen=True)
class TradePlan:
    entryZone: float
    targetPrice: float
    stopPrice: float
    rewardRisk: float


@dataclass
class PositionState:
    qty: float
    entry: float
    stop: float
    target: float
    rangeLow: float
    rangeHigh: float
    rangeMid: float
    tsEntry: float
    high: float
    protectArmed: bool = False


def build_range_snapshot(klines: list, cfg) -> RangeSnapshot:
    range_window = max(4, int(getattr(cfg, "rangeWindowBars", 24)))
    context_window = max(range_window, int(getattr(cfg, "contextWindowBars", 72)))
    if len(klines) < context_window:
        raise ValueError("not enough klines")

    ctx = klines[-context_window:]
    work = klines[-range_window:]

    lows = [float(row[3]) for row in work]
    highs = [float(row[2]) for row in work]
    closes = [float(row[4]) for row in ctx]

    low = min(lows)
    high = max(highs)
    if low <= 0 or high <= low:
        raise ValueError("invalid range")

    mid = (low + high) / 2.0
    width = high - low
    range_pct = width / low

    first_close = closes[0]
    last_close = closes[-1]
    if first_close <= 0:
        drift_pct = 0.0
    else:
        drift_pct = (last_close - first_close) / first_close

    return RangeSnapshot(
        low=low,
        high=high,
        mid=mid,
        width=width,
        rangePct=range_pct,
        driftPct=drift_pct,
        lastClose=last_close,
        barCount=len(work),
        timeframe=str(getattr(cfg, "rangeTimeframe", "5m")),
    )


def range_market_ok(snapshot: RangeSnapshot, cfg) -> tuple[bool, str]:
    min_range = float(getattr(cfg, "minRangePct", 0.0) or 0.0)
    max_range = float(getattr(cfg, "maxRangePct", 1.0) or 1.0)
    max_drift = float(getattr(cfg, "trendMaxDriftPct", 1.0) or 1.0)

    if snapshot.rangePct < min_range:
        return False, f"RANGE_SMALL {snapshot.rangePct*100:.3f}%<{min_range*100:.3f}%"
    if snapshot.rangePct > max_range:
        return False, f"RANGE_WIDE {snapshot.rangePct*100:.3f}%>{max_range*100:.3f}%"
    if abs(snapshot.driftPct) > max_drift:
        return False, f"TREND_STRONG {snapshot.driftPct*100:.3f}%>{max_drift*100:.3f}%"
    return True, "RANGE_OK"


def build_trade_plan(snapshot: RangeSnapshot, bid: float, cfg) -> TradePlan:
    span = snapshot.width
    entry_zone = snapshot.low + (span * float(getattr(cfg, "buyZoneFrac", 0.20)))
    target = snapshot.low + (span * float(getattr(cfg, "targetZoneFrac", 0.80)))
    stop = snapshot.low - (span * float(getattr(cfg, "stopRangeFrac", 0.12)))
    risk = max(1e-12, bid - stop)
    reward = max(0.0, target - bid)
    reward_risk = reward / risk
    return TradePlan(
        entryZone=entry_zone,
        targetPrice=target,
        stopPrice=stop,
        rewardRisk=reward_risk,
    )


def rebound_ok(bid_ticks: list[tuple[float, float]], cfg) -> tuple[bool, float, int]:
    lookback = max(2, int(getattr(cfg, "reboundConfirmTicks", 3) or 3))
    if len(bid_ticks) < lookback:
        return False, 0.0, 0

    recent = [float(px) for _, px in bid_ticks[-lookback:]]
    low = min(recent)
    last = recent[-1]
    if low <= 0:
        return False, 0.0, 0

    rebound_pct = (last - low) / low
    up_steps = 0
    for idx in range(1, len(recent)):
        if recent[idx] >= recent[idx - 1]:
            up_steps += 1

    ok = (
        rebound_pct >= float(getattr(cfg, "reboundMinPct", 0.0) or 0.0)
        and up_steps >= (lookback - 1)
    )
    return ok, rebound_pct, up_steps


def entry_signal(snapshot: RangeSnapshot, bid: float, spread: float, bid_ticks: list[tuple[float, float]], cfg):
    ok, reason = range_market_ok(snapshot, cfg)
    if not ok:
        return False, reason, None, 0.0

    max_spread = float(getattr(cfg, "spreadMaxPct", 1.0) or 1.0)
    if spread > max_spread:
        return False, f"SPREAD {spread*100:.4f}%>{max_spread*100:.4f}%", None, 0.0

    plan = build_trade_plan(snapshot, bid, cfg)
    if bid > plan.entryZone:
        return False, f"ABOVE_BUY_ZONE bid={bid:.2f} zone={plan.entryZone:.2f}", plan, 0.0

    reb_ok, rebound_pct, up_steps = rebound_ok(bid_ticks, cfg)
    if not reb_ok:
        return False, f"NO_REBOUND up_steps={up_steps} rebound={rebound_pct*100:.4f}%", plan, rebound_pct

    min_rr = float(getattr(cfg, "minRewardRisk", 0.0) or 0.0)
    if plan.rewardRisk < min_rr:
        return False, f"RR {plan.rewardRisk:.2f}<{min_rr:.2f}", plan, rebound_pct

    return True, "BUY_ZONE_REBOUND", plan, rebound_pct


def update_position(pos: PositionState, bid: float, cfg) -> str | None:
    if bid <= 0:
        return None

    if bid > pos.high:
        pos.high = bid

    span = max(pos.rangeHigh - pos.rangeLow, pos.entry * 0.0005)
    trigger = pos.entry + (span * float(getattr(cfg, "protectActivateFrac", 0.35)))
    lock_stop = pos.entry + (span * float(getattr(cfg, "protectLockFrac", 0.10)))

    if not pos.protectArmed and pos.high >= trigger:
        pos.protectArmed = True

    if pos.protectArmed and lock_stop > pos.stop:
        pos.stop = lock_stop

    if bid <= pos.stop:
        return "STOP"
    if bid >= pos.target:
        return "TARGET"

    age = max(0.0, time.time() - pos.tsEntry)
    if age >= float(getattr(cfg, "maxHoldSec", 0.0) or 0.0):
        return "TIME"

    progress = max(0.0, (pos.high - pos.entry) / span)
    stale_after = float(getattr(cfg, "staleAfterSec", 0.0) or 0.0)
    stale_min_progress = float(getattr(cfg, "staleMinProgressFrac", 0.0) or 0.0)
    if stale_after > 0 and age >= stale_after and progress < stale_min_progress:
        return "STALE"

    return None
