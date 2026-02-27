from __future__ import annotations
from typing import Any, Dict

class ReversalV1:
    """Entrée type 'reversal' simple: RSI bas + EMA1 ok (filtre) + spread ok.
    Objectif: prendre les retours de range quand momentum ne déclenche pas assez.
    """
    name = "reversal"

    def __init__(self, cfg: Any, profile: Any):
        self.cfg = cfg
        self.p = profile
        sp = getattr(cfg, 'strategyParams', {}) or {}
        self.rsiCushion = float(sp.get('rsiCushion', 5.0))
        self.requireEma1Ok = bool(sp.get('requireEma1Ok', True))

    def compute(self, s1: Any, s5: Any, momOk: bool, momPct: float, upRatio: float, spread: float, p1p4Ok: bool = True) -> Dict[str, Any]:
        return {
            "ema1_ok": bool(getattr(s1, "ema_ok", False)),
            "ema5_ok": bool(getattr(s5, "ema_ok", False)),
            "rsi": float(getattr(s1, "rsi", 0.0)),
            "vol_ok": bool(getattr(s1, "vol_ok", False)),
            "mom_ok": bool(momOk),
            "mom_pct": float(momPct),
            "up_ratio": float(upRatio),
            "spread": float(spread),
            "p1p4_ok": bool(p1p4Ok),
        }

    def entryOk(self, ind: Dict[str, Any], spreadOk: bool) -> bool:
        if not spreadOk:
            return False
        rsi = float(ind.get("rsi", 0.0))
        ema1_ok = bool(ind.get("ema1_ok", False))
        if self.requireEma1Ok and not ema1_ok:
            return False
        return (rsi <= (self.p.rsiMin + self.rsiCushion))
