import pytest
from strategy.regime import detect_regime, RegimeSnapshot

def _make_klines(prices: list, base_h_offset=0.005, base_l_offset=0.005) -> list:
    """Génère des klines synthétiques depuis une liste de prix de clôture."""
    klines = []
    for i, p in enumerate(prices):
        o = prices[i-1] if i > 0 else p
        h = p * (1 + base_h_offset)
        l = p * (1 - base_l_offset)
        klines.append([i*60000, o, h, l, p, 1000, (i+1)*60000, 1000*p, 100, 500, 500*p, 0])
    return klines

def test_trend_detected():
    prices = [1.0 + i * 0.01 for i in range(25)]  # Hausse linéaire forte
    klines = _make_klines(prices, 0.008, 0.002)
    regime = detect_regime(klines)
    assert regime.label == "TREND"
    assert regime.confidence > 0.5

def test_range_detected():
    import math
    prices = [1.0 + 0.001 * math.sin(i * 0.5) for i in range(25)]
    # Use very small H/L offsets so ATR stays well below range_max_atr_pct (0.0015)
    # ATR ≈ price * 0.0002 * 2 = ~0.0004 → confidence = (0.0015 - 0.0004) / 0.0015 ≈ 0.73
    klines = _make_klines(prices, 0.0002, 0.0002)
    regime = detect_regime(klines)
    assert regime.label == "RANGE"
    assert regime.confidence > 0.5

def test_pump_detected():
    prices = [1.0] * 20 + [1.02]  # Spike +2% sur le dernier bar
    klines = _make_klines(prices)
    regime = detect_regime(klines)
    assert regime.label == "PUMP"
    assert regime.is_pump is True

def test_unknown_on_empty():
    regime = detect_regime([])
    assert regime.label == "UNKNOWN"

def test_unknown_noisy():
    import random; random.seed(42)
    prices = [1.0 + random.uniform(-0.002, 0.002) for _ in range(25)]
    klines = _make_klines(prices, 0.001, 0.001)
    regime = detect_regime(klines)
    # En noise sans tendance → UNKNOWN ou RANGE
    assert regime.label in ("UNKNOWN", "RANGE")
