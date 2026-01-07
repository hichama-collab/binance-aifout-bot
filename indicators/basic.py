from decimal import Decimal, ROUND_DOWN
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0)
    l = -d.clip(upper=0)
    rs = g.rolling(n).mean() / l.rolling(n).mean()
    return 100 - (100 / (1 + rs))


def dec(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def fmt(x, quantum=Decimal("0.00000001")) -> str:
    d = dec(x)
    q = dec(quantum)
    return str(d.quantize(q, rounding=ROUND_DOWN).normalize())


class Signals:
    def __init__(self, ema_ok, rsi, vol_ok):
        self.ema_ok = ema_ok
        self.rsi = rsi
        self.vol_ok = vol_ok


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
    vol_ok = v1.iloc[-1] > v1.mean() * profile.volMult

    return (
        Signals(ema1_ok, rsi1, vol_ok),
        Signals(ema5_ok, rsi1, vol_ok)
    )

