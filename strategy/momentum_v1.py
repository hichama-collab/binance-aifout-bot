from __future__ import annotations
from typing import Any, Dict

class MomentumV1:
    """Encapsule la logique d'entrée actuelle (momentum + spread + RSI range)."""
    name = "momentum"

    def __init__(self, cfg: Any, profile: Any):
        self.cfg = cfg
        self.p = profile
        sp = getattr(cfg, 'strategyParams', {}) or {}
        self.momWindowSec = float(sp.get('momWindowSec', 8.0))
        self.momMinPct = float(sp.get('momMinPct', 0.0018))
        self.momMinUpRatio = float(sp.get('momMinUpRatio', 0.55))

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
        if not ind.get("mom_ok", False):
            return False
        # enforce thresholds (strategy-level)
        mom_pct = float(ind.get('mom_pct', 0.0))
        # Direction guard: never buy with non-positive momentum
        if mom_pct <= 0.0:
            return False
        # Micro-structure confirmation: current bid strictly above bid N ticks ago
        if not bool(ind.get('p1p4_ok', True)):
            return False
        if mom_pct < self.momMinPct:
            return False
        if float(ind.get('up_ratio', 0.0)) < self.momMinUpRatio:
            return False
        rsi = float(ind.get("rsi", 0.0))
        return (self.p.rsiMin <= rsi <= self.p.rsiMax)