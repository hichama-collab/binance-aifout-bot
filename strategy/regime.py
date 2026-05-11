"""
Détection du régime de marché: TREND / RANGE / PUMP / UNKNOWN.
Utilise les klines 1m Binance (format API standard).
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List


@dataclass
class RegimeSnapshot:
    label: str          # "TREND" | "RANGE" | "PUMP" | "UNKNOWN"
    confidence: float   # 0.0 à 1.0
    atr_pct: float      # ATR sur N bars en % du prix
    range_width_pct: float  # (high-low)/low sur la fenêtre
    velocity_1m_pct: float  # variation 1 minute en %
    velocity_5m_pct: float  # variation 5 minutes en %
    is_pump: bool


def _safe_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _compute_atr(klines: list, window: int) -> float:
    """ATR simplifié sur les klines récentes."""
    if len(klines) < 2:
        return 0.0
    trs = []
    for i in range(max(1, len(klines) - window), len(klines)):
        h = _safe_float(klines[i][2])
        l = _safe_float(klines[i][3])
        c_prev = _safe_float(klines[i-1][4])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if not trs:
        return 0.0
    return sum(trs) / len(trs)


def detect_regime(
    klines_1m: List,
    window_bars: int = 20,
    pump_threshold_pct: float = 0.015,   # 1.5% en 1 bar = pump
    trend_min_atr_pct: float = 0.003,    # ATR > 0.3% = tendanciel
    range_max_atr_pct: float = 0.0015,   # ATR < 0.15% = range
) -> RegimeSnapshot:
    """
    Détecte le régime de marché actuel depuis les klines 1m.
    """
    if not klines_1m or len(klines_1m) < 3:
        return RegimeSnapshot("UNKNOWN", 0.0, 0.0, 0.0, 0.0, 0.0, False)

    recent = klines_1m[-min(window_bars, len(klines_1m)):]
    closes = [_safe_float(k[4]) for k in recent]
    highs  = [_safe_float(k[2]) for k in recent]
    lows   = [_safe_float(k[3]) for k in recent]

    if not closes or closes[-1] <= 0:
        return RegimeSnapshot("UNKNOWN", 0.0, 0.0, 0.0, 0.0, 0.0, False)

    ref_price = closes[-1]

    # ATR en %
    atr_abs = _compute_atr(klines_1m, window_bars)
    atr_pct = atr_abs / ref_price if ref_price > 0 else 0.0

    # Range width
    h_max = max(highs) if highs else ref_price
    l_min = min(lows) if lows else ref_price
    range_width_pct = (h_max - l_min) / l_min if l_min > 0 else 0.0

    # Velocity 1m (dernier bar)
    close_prev_1m = _safe_float(klines_1m[-2][4]) if len(klines_1m) >= 2 else ref_price
    velocity_1m_pct = (ref_price - close_prev_1m) / close_prev_1m if close_prev_1m > 0 else 0.0

    # Velocity 5m
    close_prev_5m = _safe_float(klines_1m[-6][4]) if len(klines_1m) >= 6 else closes[0]
    velocity_5m_pct = (ref_price - close_prev_5m) / close_prev_5m if close_prev_5m > 0 else 0.0

    is_pump = (
        abs(velocity_1m_pct) > pump_threshold_pct or
        abs(velocity_5m_pct) > pump_threshold_pct * 3
    )

    if is_pump:
        confidence = min(1.0, max(abs(velocity_1m_pct), abs(velocity_5m_pct) / 3) / pump_threshold_pct)
        return RegimeSnapshot("PUMP", confidence, atr_pct, range_width_pct, velocity_1m_pct, velocity_5m_pct, True)

    if atr_pct >= trend_min_atr_pct:
        # Vérifier direction claire (slope des closes)
        n = len(closes)
        if n >= 4:
            first_half = sum(closes[:n//2]) / (n//2)
            second_half = sum(closes[n//2:]) / (n - n//2)
            slope_ok = second_half > first_half * 1.001 or second_half < first_half * 0.999
        else:
            slope_ok = True
        confidence = min(1.0, (atr_pct - trend_min_atr_pct) / trend_min_atr_pct)
        if slope_ok:
            return RegimeSnapshot("TREND", confidence, atr_pct, range_width_pct, velocity_1m_pct, velocity_5m_pct, False)

    if atr_pct <= range_max_atr_pct:
        confidence = min(1.0, (range_max_atr_pct - atr_pct) / range_max_atr_pct) if range_max_atr_pct > 0 else 0.0
        return RegimeSnapshot("RANGE", confidence, atr_pct, range_width_pct, velocity_1m_pct, velocity_5m_pct, False)

    return RegimeSnapshot("UNKNOWN", 0.3, atr_pct, range_width_pct, velocity_1m_pct, velocity_5m_pct, False)
