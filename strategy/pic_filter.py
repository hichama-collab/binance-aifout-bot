"""
Pic filter — bloque les entrées en achat au sommet local.

Si le bid actuel est trop proche du maximum des ticks sur la fenêtre lookback,
on refuse l'entrée : le mouvement est probablement épuisé.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class PicCheck:
    is_near_peak: bool
    distance_from_peak_pct: float
    peak_lookback_s: int
    peak_price: float
    reason: str


def check_near_peak(
    ticks: list,
    bid: float,
    lookback_seconds: int = 180,
    peak_threshold_pct: float = 0.001,
) -> PicCheck:
    """
    Regarde le max des mid prices sur la fenêtre lookback_seconds.
    Si bid actuel est à moins de peak_threshold_pct du peak → is_near_peak=True.

    ticks : list[(ts_seconds, mid_price)]
    bid   : prix actuel
    peak_threshold_pct : 0.001 = 0.1% sous le peak → near peak
    """
    if not ticks:
        return PicCheck(False, 0.0, lookback_seconds, 0.0, "no_ticks")

    now = ticks[-1][0]
    cutoff = now - lookback_seconds
    window = [float(p) for t, p in ticks if t >= cutoff]

    if len(window) < 5:
        return PicCheck(False, 0.0, lookback_seconds, 0.0, "insufficient_data")

    peak = max(window)
    if peak <= 0:
        return PicCheck(False, 0.0, lookback_seconds, peak, "invalid_peak")

    distance_pct = (peak - bid) / peak
    is_near = distance_pct < peak_threshold_pct

    return PicCheck(
        is_near_peak=is_near,
        distance_from_peak_pct=round(distance_pct, 6),
        peak_lookback_s=lookback_seconds,
        peak_price=peak,
        reason="near_peak" if is_near else "ok",
    )
