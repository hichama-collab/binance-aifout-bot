# auto-extracted from main.py
from __future__ import annotations
from decimal import Decimal, ROUND_DOWN
from typing import Any, Tuple

def quantDown(x: float, q: Decimal) -> float:
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_DOWN))

def usdcFree(bx):
    acc = bx.get("/api/v3/account", signed=True)
    bal = next((b for b in acc["balances"] if b["asset"] == "USDC"), None)
    return float(bal["free"]) if bal else 0.0

