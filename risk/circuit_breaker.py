"""
Circuit breaker : daily loss limit + consecutive losses.
"""
from __future__ import annotations
import time
import logging
import datetime
from typing import Tuple

log = logging.getLogger("circuit_breaker")


class CircuitBreaker:
    def __init__(
        self,
        daily_max_loss_usdc: float = 1.0,
        max_consecutive_losses: int = 5,
        cooldown_after_break_sec: float = 3600.0,
    ):
        self.daily_max_loss = daily_max_loss_usdc
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_after_break_sec = cooldown_after_break_sec

        self._pnl_today = 0.0
        self._consecutive_losses = 0
        self._broken_until = 0.0
        self._day_anchor: datetime.date = datetime.datetime.now(datetime.timezone.utc).date()

    def _reset_if_new_day(self) -> None:
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if today != self._day_anchor:
            self._pnl_today = 0.0
            self._consecutive_losses = 0
            self._day_anchor = today

    def record_trade(self, net_pnl: float) -> None:
        """Enregistre le résultat d'un trade. Réinitialise le compteur si nouveau jour UTC."""
        self._reset_if_new_day()
        self._pnl_today += net_pnl
        if net_pnl <= 0:
            self._consecutive_losses += 1
            log.debug(f"consecutive_losses={self._consecutive_losses} daily_pnl={self._pnl_today:.4f}")
        else:
            self._consecutive_losses = 0

    def should_block_entries(self) -> Tuple[bool, str]:
        """
        Returns (blocked, reason).
        Blocked si limite daily, trop de pertes consécutives, ou cooldown actif.
        """
        self._reset_if_new_day()
        now = time.time()

        if now < self._broken_until:
            remaining = int(self._broken_until - now)
            return True, f"cooldown actif encore {remaining}s"

        if self.daily_max_loss > 0 and self._pnl_today <= -self.daily_max_loss:
            return True, f"daily_loss_limit atteint pnl_today={self._pnl_today:.4f} limit={self.daily_max_loss:.4f}"

        if self.max_consecutive_losses > 0 and self._consecutive_losses >= self.max_consecutive_losses:
            return True, f"consecutive_losses={self._consecutive_losses} >= max={self.max_consecutive_losses}"

        return False, ""

    def trigger_cooldown(self, reason: str = "") -> None:
        """Active un cooldown de sécurité."""
        self._broken_until = time.time() + self.cooldown_after_break_sec
        log.warning(f"circuit_breaker TRIGGERED reason={reason} cooldown={self.cooldown_after_break_sec}s")

    @property
    def pnl_today(self) -> float:
        self._reset_if_new_day()
        return self._pnl_today

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses
