from __future__ import annotations

import time
from dataclasses import dataclass
import pandas as pd

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
    trendOk: bool  # NEW: filtre de tendance
    atr: float     # NEW: ATR pour stop dynamique

@dataclass(frozen=True)
class TradePlan:
    entryZone: float
    targetPrice: float
    stopPrice: float
    rewardRisk: float
    entryPrice: float  # NEW: prix d'entrée estimé pour calcul du stop

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
    initialStop: float = 0.0  # NEW: pour trailing stop
    maxReached: float = 0.0   # NEW: pour trailing stop


def _atr(highs, lows, closes, period=14):
    """Calculate Average True Range"""
    if len(closes) < period + 1:
        return (max(highs) - min(lows)) / len(highs) if highs else 0.0

    tr_list = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        c_prev = closes[i-1]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_list.append(tr)

    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else 0.0

    atr_vals = []
    atr_vals.append(sum(tr_list[:period]) / period)
    for i in range(period, len(tr_list)):
        atr_vals.append((atr_vals[-1] * (period - 1) + tr_list[i]) / period)

    return atr_vals[-1] if atr_vals else 0.0


def _ema(series, period):
    """Calculate EMA"""
    return series.ewm(span=period, adjust=False).mean()


def _trend_filter(klines, cfg):
    """
    NEW: Filtre de tendance global.
    Ne trade le range que si la tendance est neutre ou haussière.
    """
    try:
        # Use context window for trend analysis
        context_window = max(20, int(getattr(cfg, "contextWindowBars", 72)))
        if len(klines) < context_window:
            return True, "INSUFFICIENT_DATA"

        ctx = klines[-context_window:]
        closes = pd.Series([float(row[4]) for row in ctx])
        highs = [float(row[2]) for row in ctx]
        lows = [float(row[3]) for row in ctx]

        if len(closes) < 50:
            return True, "INSUFFICIENT_DATA"

        ema20 = _ema(closes, 20).iloc[-1]
        ema50 = _ema(closes, 50).iloc[-1]
        last_close = closes.iloc[-1]

        # Trend strength
        trend_pct = (last_close - ema50) / ema50 if ema50 > 0 else 0

        # Max allowed trend against position (we're long-only)
        max_trend_against = float(getattr(cfg, "trendMaxAgainstPct", 0.015) or 0.015)

        if trend_pct < -max_trend_against:
            return False, f"STRONG_DOWNTREND {trend_pct*100:.2f}%"

        # Also check if price is below EMA20 (short-term bearish)
        if last_close < ema20 * 0.995 and trend_pct < -0.005:
            return False, f"BELOW_EMA20 {trend_pct*100:.2f}%"

        return True, f"TREND_OK {trend_pct*100:.2f}%"
    except Exception:
        return True, "TREND_FILTER_ERROR"


def build_range_snapshot(klines: list, cfg) -> RangeSnapshot:
    range_window = max(4, int(getattr(cfg, "rangeWindowBars", 24)))
    context_window = max(range_window, int(getattr(cfg, "contextWindowBars", 72)))
    if len(klines) < context_window:
        raise ValueError("not enough klines")

    ctx = klines[-context_window:]
    work = klines[-range_window:]

    lows = [float(row[3]) for row in work]
    highs = [float(row[2]) for row in work]
    closes_ctx = [float(row[4]) for row in ctx]
    highs_ctx = [float(row[2]) for row in ctx]
    lows_ctx = [float(row[3]) for row in ctx]

    low = min(lows)
    high = max(highs)
    if low <= 0 or high <= low:
        raise ValueError("invalid range")

    mid = (low + high) / 2.0
    width = high - low
    range_pct = width / low

    first_close = closes_ctx[0]
    last_close = closes_ctx[-1]
    if first_close <= 0:
        drift_pct = 0.0
    else:
        drift_pct = (last_close - first_close) / first_close

    # NEW: Calculate ATR
    atr_val = _atr(highs_ctx, lows_ctx, closes_ctx, 14)

    # NEW: Trend filter
    trend_ok, trend_reason = _trend_filter(klines, cfg)

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
        trendOk=trend_ok,
        atr=atr_val,
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
    # NEW: Trend filter
    if not snapshot.trendOk:
        return False, "TREND_FILTER_FAIL"
    return True, "RANGE_OK"


def build_trade_plan(snapshot: RangeSnapshot, bid: float, cfg) -> TradePlan:
    span = snapshot.width

    # MODIFIED: Entry zone plus bas pour meilleur R:R
    entry_zone = snapshot.low + (span * float(getattr(cfg, "buyZoneFrac", 0.15)))
    target = snapshot.low + (span * float(getattr(cfg, "targetZoneFrac", 0.80)))

    # MODIFIED: Stop basé sur l'ATR ou le range, le plus serré des deux
    stop_range = snapshot.low - (span * float(getattr(cfg, "stopRangeFrac", 0.08)))
    stop_atr = bid - (snapshot.atr * float(getattr(cfg, "atrStopMult", 1.5)))

    # Use the tighter stop (higher price for long)
    stop = max(stop_range, stop_atr)

    # Ensure stop is below entry
    min_stop_distance = bid * float(getattr(cfg, "minStopDistancePct", 0.003))
    if bid - stop < min_stop_distance:
        stop = bid - min_stop_distance

    risk = max(1e-12, bid - stop)
    reward = max(0.0, target - bid)
    reward_risk = reward / risk

    return TradePlan(
        entryZone=entry_zone,
        targetPrice=target,
        stopPrice=stop,
        rewardRisk=reward_risk,
        entryPrice=bid,
    )


def rebound_ok(bid_ticks: list[tuple[float, float]], cfg) -> tuple[bool, float, int]:
    # MODIFIED: Plus de confirmation nécessaire
    lookback = max(2, int(getattr(cfg, "reboundConfirmTicks", 5) or 5))
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

    # MODIFIED: Besoin de plus de ticks haussiers
    min_up_ratio = float(getattr(cfg, "reboundMinUpRatio", 0.6) or 0.6)
    ok = (
        rebound_pct >= float(getattr(cfg, "reboundMinPct", 0.0) or 0.0)
        and up_steps >= max(2, int(lookback * min_up_ratio))
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
    if bid > pos.maxReached:
        pos.maxReached = bid

    span = max(pos.rangeHigh - pos.rangeLow, pos.entry * 0.0005)

    # MODIFIED: Protection plus tôt et plus agressive
    trigger = pos.entry + (span * float(getattr(cfg, "protectActivateFrac", 0.25)))

    # MODIFIED: Trailing stop basé sur le max atteint
    trail_pct = float(getattr(cfg, "trailStopPct", 0.0) or 0.0)
    if trail_pct > 0 and pos.maxReached > pos.entry:
        trail_stop = pos.maxReached * (1 - trail_pct)
        if trail_stop > pos.stop:
            pos.stop = trail_stop

    lock_stop = pos.entry + (span * float(getattr(cfg, "protectLockFrac", 0.05)))

    if not pos.protectArmed and pos.high >= trigger:
        pos.protectArmed = True

    if pos.protectArmed and lock_stop > pos.stop:
        pos.stop = lock_stop

    if bid <= pos.stop:
        return "STOP"
    if bid >= pos.target:
        return "TARGET"

    age = max(0.0, time.time() - pos.tsEntry)

    # MODIFIED: Time stop plus court
    max_hold = float(getattr(cfg, "maxHoldSec", 0.0) or 0.0)
    if max_hold > 0 and age >= max_hold:
        return "TIME"

    progress = max(0.0, (pos.high - pos.entry) / span)
    stale_after = float(getattr(cfg, "staleAfterSec", 0.0) or 0.0)
    stale_min_progress = float(getattr(cfg, "staleMinProgressFrac", 0.0) or 0.0)
    if stale_after > 0 and age >= stale_after and progress < stale_min_progress:
        return "STALE"

    return None
