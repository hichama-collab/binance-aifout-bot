"""
Règles de sortie cohérentes avec la stratégie d'entrée.
"""
from __future__ import annotations
import time
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple

from .regime import RegimeSnapshot

log = logging.getLogger("exit_rules")


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str      # "SIGNAL_INVALIDATED" | "TAKE_PROFIT" | "STOP_LOSS" | "TIMEOUT" | "PUMP_REVERSAL"
    target_price: float
    urgency: str     # "PASSIVE" (LIMIT bid) | "AGGRESSIVE" (cross spread)
    pnl_net_est: float = 0.0


def _estimate_net_pnl(entry_price: float, buy_notional: float, current_bid: float, sell_qty: float, fee_rate: float) -> float:
    sell_notional = current_bid * sell_qty
    return sell_notional - buy_notional - (buy_notional + sell_notional) * fee_rate


def evaluate_exit(
    entry_price: float,
    entry_qty: float,
    entry_ts: float,
    entry_reason: str,
    buy_notional: float,
    high_seen: float,
    ticks: List[Tuple],      # list[(ts, mid_price)]
    bid: float,
    ask: float,
    spread: float,
    regime: Optional[RegimeSnapshot],
    fee_rate: float,
    cfg,
) -> ExitDecision:
    """
    Évalue si on doit sortir de la position.
    Priorité: STOP_LOSS > PUMP_REVERSAL > SIGNAL_INVALIDATED > TAKE_PROFIT > TIMEOUT
    """
    now = time.time()
    age = max(0.0, now - entry_ts)
    pnl_net_est = _estimate_net_pnl(entry_price, buy_notional, bid, entry_qty, fee_rate)

    max_loss_pct = float(getattr(cfg, 'maxLossPct', 0.008))
    min_tp_pct = float(getattr(cfg, 'minTpPct', 0.004))
    max_hold_sec = float(getattr(cfg, 'maxHoldSec', 300))

    no_exit = ExitDecision(False, "", bid, "PASSIVE", pnl_net_est)

    # 1. STOP_LOSS HARD
    if entry_price > 0 and bid < entry_price * (1.0 - max_loss_pct):
        return ExitDecision(True, "STOP_LOSS", bid, "AGGRESSIVE", pnl_net_est)

    # 2. PUMP_REVERSAL — si on est entré pendant un pump et le prix retombe
    if regime and regime.is_pump and entry_reason in ("BURST", "BURST_TREND"):
        peak_drop = (high_seen - bid) / high_seen if high_seen > 0 else 0.0
        if peak_drop > 0.008 and bid < entry_price:  # -0.8% depuis le peak ET sous entry
            return ExitDecision(True, "PUMP_REVERSAL", bid, "AGGRESSIVE", pnl_net_est)

    # 3. SIGNAL_INVALIDATED — signal d'entrée inversé
    if ticks and len(ticks) >= 4:
        prices = [float(t[1]) for t in ticks[-4:]]
        # Entrée BURST/P_ALGO: sortir si tape descendante claire
        if prices[-1] < prices[-2] < prices[-3]:
            if pnl_net_est > 0:  # Seulement si on est en gain
                return ExitDecision(True, "SIGNAL_INVALIDATED", bid, "PASSIVE", pnl_net_est)

    # 4. TAKE_PROFIT dynamique
    if entry_price > 0:
        atr_pct = regime.atr_pct if regime else 0.003
        dynamic_tp_pct = max(min_tp_pct, fee_rate * 2 + spread + atr_pct * 0.5)
        if bid >= entry_price * (1.0 + dynamic_tp_pct):
            return ExitDecision(True, "TAKE_PROFIT", bid, "PASSIVE", pnl_net_est)

    # 5. TIMEOUT — seulement si PnL net > 0 (on ne coupe jamais en perte pour TIME)
    if age > max_hold_sec and pnl_net_est > 0:
        return ExitDecision(True, "TIMEOUT", bid, "PASSIVE", pnl_net_est)

    return no_exit
