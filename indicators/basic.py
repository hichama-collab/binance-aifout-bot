from decimal import Decimal, ROUND_DOWN
import pandas as pd

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0)
    l = (-d).clip(lower=0)
    avg_gain = g.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = l.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    out = out.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return out

def atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, n: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    prev_close = closes.shift(1)
    tr1 = highs - lows
    tr2 = (highs - prev_close).abs()
    tr3 = (lows - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False, min_periods=n).mean()

def dec(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))

def fmt(x, quantum=Decimal("0.00000001")) -> str:
    d = dec(x)
    q = dec(quantum)
    s = format(d.quantize(q, rounding=ROUND_DOWN), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("", "-0"):
        s = "0"
    return s

class Signals:
    def __init__(self, ema_ok, rsi, vol_ok):
        self.ema_ok = ema_ok
        self.rsi = rsi
        self.vol_ok = vol_ok

class MarketContext:
    def __init__(self, ret_1m, ret_3m, ret_5m, range_5m, ret_5m_kline):
        self.ret_1m = ret_1m
        self.ret_3m = ret_3m
        self.ret_5m = ret_5m
        self.range_5m = range_5m
        self.ret_5m_kline = ret_5m_kline

def _klines(bx, symbol, interval, limit):
    return bx.get("/api/v3/klines", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    })

def computeSignals(bx, symbol, profile):
    k1 = _klines(bx, symbol, "1m", 50)
    k5 = _klines(bx, symbol, "5m", 50)

    c1 = pd.Series([float(x[4]) for x in k1])
    c5 = pd.Series([float(x[4]) for x in k5])
    v1 = pd.Series([float(x[5]) for x in k1])

    ema1_ok = ema(c1, profile.emaFast).iloc[-1] > ema(c1, profile.emaSlow).iloc[-1]
    ema5_ok = ema(c5, profile.emaFast).iloc[-1] > ema(c5, profile.emaSlow).iloc[-1]

    rsi1 = float(rsi(c1).iloc[-1])
    rsi5 = float(rsi(c5).iloc[-1])
    vol_mult = float(getattr(profile, "volMult", 0.0) or 0.0)
    vol_ok = True if vol_mult <= 0 else (v1.iloc[-1] > v1.mean() * vol_mult)

    return (
        Signals(ema1_ok, rsi1, vol_ok),
        Signals(ema5_ok, rsi5, vol_ok)
    )

def computeMarketContext(bx, symbol):
    k1 = _klines(bx, symbol, "1m", 8)
    k5 = _klines(bx, symbol, "5m", 4)

    c1 = [float(x[4]) for x in k1]
    c5 = [float(x[4]) for x in k5]

    def _ret(series, lookback):
        if len(series) <= lookback:
            return 0.0
        start = float(series[-(lookback + 1)])
        end = float(series[-1])
        if start <= 0:
            return 0.0
        return (end - start) / start

    recent_5m = c1[-6:] if len(c1) >= 6 else c1
    low_5m = min(recent_5m) if recent_5m else 0.0
    high_5m = max(recent_5m) if recent_5m else 0.0
    range_5m = ((high_5m - low_5m) / low_5m) if low_5m > 0 else 0.0

    return MarketContext(
        ret_1m=_ret(c1, 1),
        ret_3m=_ret(c1, 3),
        ret_5m=_ret(c1, 5),
        range_5m=range_5m,
        ret_5m_kline=_ret(c5, 1),
    )

def computeTrendState(klines, fast=20, slow=50):
    """Analyze trend state from klines. Returns: (trend_ok, trend_pct, ema_fast, ema_slow)"""
    if len(klines) < slow + 5:
        return True, 0.0, 0.0, 0.0

    closes = pd.Series([float(k[4]) for k in klines])
    ema_fast = ema(closes, fast).iloc[-1]
    ema_slow = ema(closes, slow).iloc[-1]
    last_close = closes.iloc[-1]

    trend_pct = (last_close - ema_slow) / ema_slow if ema_slow > 0 else 0
    trend_ok = last_close >= ema_slow * 0.985 or trend_pct >= -0.01

    return trend_ok, trend_pct, ema_fast, ema_slow
