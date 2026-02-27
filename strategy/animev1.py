import pandas as pd
from indicators.basic import ema, rsi

KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore"
]

class AnimeV1:
    def __init__(self, cfg, profile):
        self.cfg = cfg
        self.p = profile

    def compute(self, kl1, kl5):
        df1 = pd.DataFrame(kl1, columns=KLINE_COLS)
        df5 = pd.DataFrame(kl5, columns=KLINE_COLS)

        df1["close"] = df1["close"].astype(float)
        df1["volume"] = df1["volume"].astype(float)
        df5["close"] = df5["close"].astype(float)

        df1["emaF"] = ema(df1["close"], self.p.emaFast)
        df1["emaS"] = ema(df1["close"], self.p.emaSlow)
        df1["rsi"] = rsi(df1["close"], self.p.rsiLen)

        df5["emaF"] = ema(df5["close"], self.p.emaFast)
        df5["emaS"] = ema(df5["close"], self.p.emaSlow)

        i = self.cfg.closeIdx

        emaOk1m = df1["emaF"].iloc[i] > df1["emaS"].iloc[i]
        emaOk5m = df5["emaF"].iloc[i] > df5["emaS"].iloc[i]
        rsiVal = float(df1["rsi"].iloc[i])

        volMean = float(df1["volume"].rolling(self.p.volumeWindow).mean().iloc[i])
        volLast = float(df1["volume"].iloc[i])
        volOk = (volMean > 0) and (volLast > self.p.volMult * volMean)

        return {
            "emaOk1m": emaOk1m,
            "emaOk5m": emaOk5m,
            "rsi": rsiVal,
            "volOk": volOk,
            "volMean": volMean,
            "volLast": volLast,
        }

    def entryOk(self, ind, spreadOk: bool):
        htfOk = ind["emaOk5m"] if self.p.useHtfFilter else True
        rsiOk = (self.p.rsiMin <= ind["rsi"] <= self.p.rsiMax)
        return ind["emaOk1m"] and htfOk and rsiOk and ind["volOk"] and spreadOk
