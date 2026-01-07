# strategy/basic_v1.py
# Stratégie générique Spot – LIMIT only
# Objectif: zéro modif à chaque token. Tout est piloté par le profile quand possible.
# - Trend: EMA1m (optionnel EMA5m)
# - Reversal: RSI <= rsiMin (du profile) + optionnel pxAboveEma1
# - Volume: soft (si vol NOK -> on autorise uniquement trend)

import pandas as pd
from indicators.basic import ema, rsi

KLINE_COLS = [
  "open_time", "open", "high", "low", "close", "volume",
  "close_time", "quote_volume", "trades",
  "taker_buy_base", "taker_buy_quote", "ignore"
]

class BasicV1:
  def __init__(self, profile):
    self.p = profile

    # Fallbacks robustes (si le profile n’a pas ces champs)
    self.volWindow = int(getattr(self.p, "volWindow", 20))
    self.rsiLen = int(getattr(self.p, "rsiLen", 14))

    # Par défaut: HTF OFF (sinon tu bloques trop souvent)
    self.useHtfFilter = bool(getattr(self.p, "useHtfFilter", False))

    # Seuil reversal = rsiMin du profile (strict: 40) + petit cushion configurable
    self.rsiCushion = float(getattr(self.p, "rsiCushion", 5.0))  # 5 => 40 devient 45
    self.requirePxAboveEma1 = bool(getattr(self.p, "requirePxAboveEma1", True))

  def compute(self, kl1, kl5):
    df1 = pd.DataFrame(kl1, columns=KLINE_COLS)
    df5 = pd.DataFrame(kl5, columns=KLINE_COLS)

    df1["close"] = df1["close"].astype(float)
    df1["volume"] = df1["volume"].astype(float)
    df5["close"] = df5["close"].astype(float)

    # EMA 1m
    df1["emaF"] = ema(df1["close"], int(self.p.emaFast))
    df1["emaS"] = ema(df1["close"], int(self.p.emaSlow))

    # EMA 5m
    df5["emaF"] = ema(df5["close"], int(self.p.emaFast))
    df5["emaS"] = ema(df5["close"], int(self.p.emaSlow))

    # RSI 1m
    df1["rsi"] = rsi(df1["close"], self.rsiLen)

    i = len(df1) - 1
    j = len(df5) - 1

    emaOk1m = float(df1["emaF"].iloc[i]) > float(df1["emaS"].iloc[i])
    emaOk5m = float(df5["emaF"].iloc[j]) > float(df5["emaS"].iloc[j])

    rsiVal = float(df1["rsi"].iloc[i]) if pd.notna(df1["rsi"].iloc[i]) else 50.0

    px = float(df1["close"].iloc[i])
    ema1 = float(df1["emaF"].iloc[i])

    # Volume soft
    w = min(self.volWindow, len(df1))
    volMean = float(df1["volume"].tail(w).mean()) if w > 0 else 0.0
    volLast = float(df1["volume"].iloc[i])
    volMult = float(getattr(self.p, "volMult", 1.0))
    volOk = (volMean > 0.0) and (volLast > volMult * volMean)

    return {
      "emaOk1m": emaOk1m,
      "emaOk5m": emaOk5m,
      "rsi": rsiVal,
      "volOk": volOk,
      "volMean": volMean,
      "volLast": volLast,
      "px": px,
      "ema1": ema1,
      "pxAboveEma1": px > ema1,
    }

  def entryOk(self, ind, spreadOk: bool):
    if not spreadOk:
      return False

    ema1Ok = bool(ind.get("emaOk1m", False))
    ema5Ok = bool(ind.get("emaOk5m", False))
    volOk = bool(ind.get("volOk", True))
    rsiVal = float(ind.get("rsi", 50.0))
    pxAboveEma1 = bool(ind.get("pxAboveEma1", False))

    # Trend
    trend = ema1Ok and (ema5Ok if self.useHtfFilter else True)

    # Reversal: piloté par profile.rsiMin + cushion
    rsiMin = float(getattr(self.p, "rsiMin", 40.0))
    rsiThr = rsiMin + self.rsiCushion  # strict=40 -> 45 par défaut
    if self.requirePxAboveEma1:
      reversal = (rsiVal <= rsiThr) and pxAboveEma1
    else:
      reversal = (rsiVal <= rsiThr)

    # Volume faible: uniquement trend (évite reversal sur carnet fin)
    if not volOk:
      return trend

    return trend or reversal

