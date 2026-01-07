# auto-extracted from main.py
from __future__ import annotations
from decimal import Decimal
from typing import Any, Tuple

def initSymbol(bx, symbol: str):
    ex = bx.get("/api/v3/exchangeInfo")
    s = next((x for x in ex["symbols"] if x["symbol"] == symbol), None)
    if not s:
        die(f"Symbol introuvable: {symbol}")
    if s["status"] != "TRADING":
        die(f"Symbol status={s['status']}")

    f = {x["filterType"]: x for x in s["filters"]}
    tick = float(f["PRICE_FILTER"]["tickSize"])
    step = float(f["LOT_SIZE"]["stepSize"])
    tickQ = Decimal(str(tick))
    stepQ = Decimal(str(step))
    nf = f.get("NOTIONAL") or f.get("MIN_NOTIONAL")
    minNotional = float(nf.get("minNotional", 10))
    return tick, step, tickQ, stepQ, minNotional

