"""
Calcul des frais Binance réels et PnL net.
"""
from __future__ import annotations
import time
from typing import Optional

DEFAULT_FEE_RATE = 0.001  # 0.10% taker spot

class FeeModel:
    def __init__(self, fee_rate: float = DEFAULT_FEE_RATE, use_bnb: bool = False):
        self.base_fee_rate = fee_rate
        self.use_bnb = use_bnb
        # BNB discount: 25% reduction
        self.fee_rate = fee_rate * (0.75 if use_bnb else 1.0)
        self._cache: dict = {}
        self._cache_ttl = 3600.0

    def get_fee_rate(self, symbol: str = "") -> float:
        """Retourne le fee rate effectif. Extensible pour per-symbol via API."""
        return self.fee_rate

    def estimate_round_trip_cost(self, notional: float, symbol: str = "") -> float:
        """Coût total estimé d'un round-trip BUY+SELL en USDC."""
        rate = self.get_fee_rate(symbol)
        return notional * rate * 2.0

    def compute_net_pnl(
        self,
        buy_price: float,
        buy_qty: float,
        sell_price: float,
        sell_qty: float,
        symbol: str = "",
    ) -> dict:
        """
        Calcule le PnL NET réel d'un round-trip.
        Les frais Binance sont prélevés en USDC côté SELL, et en asset côté BUY
        (= qty reçue légèrement inférieure à qty commandée).
        On modélise: fees_buy = buy_notional * fee_rate, fees_sell = sell_notional * fee_rate
        """
        rate = self.get_fee_rate(symbol)
        buy_notional = buy_price * buy_qty
        sell_notional = sell_price * sell_qty
        fees_buy = buy_notional * rate
        fees_sell = sell_notional * rate
        gross_pnl = sell_notional - buy_notional
        net_pnl = gross_pnl - fees_buy - fees_sell
        net_pnl_pct = (net_pnl / buy_notional * 100.0) if buy_notional > 0 else 0.0
        qty_lost = (fees_buy / buy_price) if buy_price > 0 else 0.0
        return {
            "gross_pnl": round(gross_pnl, 6),
            "fees_buy": round(fees_buy, 6),
            "fees_sell": round(fees_sell, 6),
            "total_fees": round(fees_buy + fees_sell, 6),
            "net_pnl": round(net_pnl, 6),
            "net_pnl_pct": round(net_pnl_pct, 4),
            "qty_lost_to_fees": round(qty_lost, 8),
            "breakeven_pct": round((fees_buy + fees_sell) / buy_notional * 100.0, 4) if buy_notional > 0 else 0.0,
        }

    def min_move_to_profit(self, spread_pct: float = 0.0) -> float:
        """Mouvement de prix minimum (%) pour être profitable net."""
        return self.fee_rate * 2.0 + spread_pct

_default_model: Optional[FeeModel] = None

def get_default_fee_model(use_bnb: bool = False) -> FeeModel:
    global _default_model
    if _default_model is None:
        _default_model = FeeModel(use_bnb=use_bnb)
    return _default_model
