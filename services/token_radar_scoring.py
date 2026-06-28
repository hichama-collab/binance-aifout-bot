"""Pure scoring helpers for the independent Token Radar scanner."""

from __future__ import annotations

import math
from typing import Mapping


def _num(data: Mapping, key: str, default: float = 0.0) -> float:
    try:
        value = data.get(key, default)
        if value is None:
            return default
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return default
        return value
    except Exception:
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _positive_scale(value: float, target: float) -> float:
    if target <= 0:
        return 0.0
    return _clamp(max(0.0, value) / target)


def _score_momentum(data: Mapping) -> float:
    change_5m = _num(data, "change_5m_pct")
    change_15m = _num(data, "change_15m_pct")
    change_1h = _num(data, "change_1h_pct")
    change_4h = _num(data, "change_4h_pct")
    change_24h = _num(data, "change_24h_pct")

    score = 0.0
    score += 7.0 * _positive_scale(change_15m, 0.006)
    score += 11.0 * _positive_scale(change_1h, 0.018)
    score += 10.0 * _positive_scale(change_4h, 0.045)

    if change_15m > 0 and change_1h > 0 and change_4h > 0:
        score += 2.0
    if change_5m <= -0.006:
        score -= 5.0
    if change_24h >= 0.15 and change_5m <= 0 and change_15m <= 0:
        score -= 6.0

    return round(_clamp(score, 0.0, 30.0), 3)


def _score_liquidity(data: Mapping) -> float:
    quote_volume = max(0.0, _num(data, "quote_volume_24h"))
    if quote_volume <= 0:
        return 0.0
    # Smooth log scale: 500k is usable, 100M+ is excellent.
    low = math.log10(500_000)
    high = math.log10(100_000_000)
    score = 20.0 * _clamp((math.log10(quote_volume) - low) / (high - low))
    return round(score, 3)


def _score_spread(data: Mapping) -> float:
    spread = max(0.0, _num(data, "spread_pct"))
    if spread <= 0.0005:
        return 15.0
    if spread <= 0.0010:
        return round(12.0 - ((spread - 0.0005) / 0.0005) * 3.0, 3)
    if spread <= 0.0020:
        return round(8.0 - ((spread - 0.0010) / 0.0010) * 5.0, 3)
    return round(max(0.0, 3.0 - ((spread - 0.0020) / 0.0030) * 3.0), 3)


def _score_trend_quality(data: Mapping) -> float:
    c5 = _num(data, "change_5m_pct")
    c15 = _num(data, "change_15m_pct")
    c30 = _num(data, "change_30m_pct")
    c1h = _num(data, "change_1h_pct")
    c2h = _num(data, "change_2h_pct")
    c4h = _num(data, "change_4h_pct")

    windows = [c15, c30, c1h, c2h, c4h]
    positives = sum(1 for value in windows if value > 0)
    score = positives * 2.0

    if c15 > 0 and c1h > c15 * 0.6 and c4h > c1h * 0.6:
        score += 3.0
    if c5 > 0.018 and c15 < 0.008:
        score -= 4.0
    if max(abs(v) for v in windows) > 0.10 and positives <= 2:
        score -= 3.0
    if c1h > 0 and c4h < -0.01:
        score -= 3.0

    return round(_clamp(score, 0.0, 15.0), 3)


def _score_risk(data: Mapping) -> float:
    spread = max(0.0, _num(data, "spread_pct"))
    c5 = _num(data, "change_5m_pct")
    c24 = _num(data, "change_24h_pct")
    distance_high = _num(data, "distance_high_24h_pct", -1.0)

    score = 20.0
    if distance_high > -0.002:
        score -= 6.0
    elif distance_high > -0.006:
        score -= 3.0
    if c24 > 0.18:
        score -= 6.0
    elif c24 > 0.12:
        score -= 3.0
    if c5 > 0.025:
        score -= 5.0
    elif c5 > 0.015:
        score -= 2.0
    if spread > 0.002:
        score -= 4.0
    elif spread > 0.001:
        score -= 2.0

    return round(_clamp(score, 0.0, 20.0), 3)


def classify_signal(data: Mapping, scores: Mapping) -> str:
    spread = max(0.0, _num(data, "spread_pct"))
    quote_volume = max(0.0, _num(data, "quote_volume_24h"))
    c5 = _num(data, "change_5m_pct")
    c15 = _num(data, "change_15m_pct")
    c1h = _num(data, "change_1h_pct")
    c4h = _num(data, "change_4h_pct")
    distance_high = _num(data, "distance_high_24h_pct", -1.0)
    global_score = _num(scores, "global_score")

    if spread > 0.002:
        return "SPREAD_TOO_HIGH"
    if quote_volume < 500_000:
        return "LOW_LIQUIDITY"
    if distance_high > -0.002:
        return "NEAR_HIGH_RISK"
    if c1h > 0 and c4h > 0 and c5 < 0 and global_score >= 60:
        return "PULLBACK_WATCH"
    if c15 > 0 and c1h > 0 and c4h > 0 and global_score >= 75:
        return "STRONG_MOMENTUM"
    return "WATCH"


def build_reason(data: Mapping, scores: Mapping) -> str:
    parts = []
    if _num(data, "change_1h_pct") > 0 and _num(data, "change_4h_pct") > 0:
        parts.append("momentum 1h/4h positif")
    if _num(data, "quote_volume_24h") >= 5_000_000:
        parts.append("volume correct")
    if _num(data, "spread_pct") <= 0.001:
        parts.append("spread faible")
    if _num(data, "distance_high_24h_pct", -1.0) > -0.006:
        parts.append("proche du high 24h")
    if _num(data, "change_5m_pct") < 0:
        parts.append("pullback court terme")
    if not parts:
        parts.append("signal neutre a surveiller")
    return ", ".join(parts)[:240]


def score_token(data: Mapping) -> dict:
    momentum = _score_momentum(data)
    liquidity = _score_liquidity(data)
    spread = _score_spread(data)
    trend_quality = _score_trend_quality(data)
    risk = _score_risk(data)
    global_score = round(_clamp(momentum + liquidity + spread + trend_quality + risk, 0.0, 100.0), 3)
    scores = {
        "momentum_score": momentum,
        "liquidity_score": liquidity,
        "spread_score": spread,
        "trend_quality_score": trend_quality,
        "risk_score": risk,
        "score": global_score,
        "global_score": global_score,
    }
    scores["signal"] = classify_signal(data, scores)
    scores["reason"] = build_reason(data, scores)
    return scores
