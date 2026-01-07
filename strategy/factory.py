from __future__ import annotations
from typing import Any
from .momentum_v1 import MomentumV1
from .reversal_v1 import ReversalV1

def getStrategy(cfg: Any, profile: Any):
    name = (getattr(cfg, "strategyName", "") or "momentum").lower()
    if name in ("reversal", "reversal_v1", "meanrevert", "mr"):
        return ReversalV1(cfg, profile)
    # default
    return MomentumV1(cfg, profile)
