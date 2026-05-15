"""
Position Dynamics — maxiprice, trailing stop, breakeven escape.

Tracks price movements since entry and provides two exit mechanisms:
- Trailing stop: locks gains after a significant rise then retrace
- Breakeven escape: exits at breakeven after a partial retrace
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class PositionDynamics:
    entry_price: float
    entry_ts: float
    maxiprice: float = 0.0
    miniprice: float = 0.0
    max_gain_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    current_drawdown_from_peak_pct: float = 0.0
    has_been_above_entry: bool = False
    has_been_below_entry: bool = False
    n_updates: int = 0


def init_dynamics(entry_price: float, entry_ts: float, current_bid: float) -> PositionDynamics:
    """Call immediately after BUY_FILLED."""
    gain = (current_bid - entry_price) / entry_price if entry_price > 0 else 0.0
    dd = max(0.0, (entry_price - current_bid) / entry_price) if entry_price > 0 else 0.0
    return PositionDynamics(
        entry_price=entry_price,
        entry_ts=entry_ts,
        maxiprice=current_bid,
        miniprice=current_bid,
        max_gain_pct=gain,
        max_drawdown_pct=dd,
        current_drawdown_from_peak_pct=0.0,
        has_been_above_entry=current_bid > entry_price,
        has_been_below_entry=current_bid < entry_price,
        n_updates=1,
    )


def update_dynamics(dyn: PositionDynamics, current_bid: float) -> PositionDynamics:
    """Call every tick while in position."""
    if current_bid > dyn.maxiprice:
        dyn.maxiprice = current_bid
    if dyn.miniprice == 0.0 or current_bid < dyn.miniprice:
        dyn.miniprice = current_bid

    if dyn.entry_price > 0:
        gain_pct = (current_bid - dyn.entry_price) / dyn.entry_price
        dd_from_entry = max(0.0, (dyn.entry_price - current_bid) / dyn.entry_price)
    else:
        gain_pct = dd_from_entry = 0.0

    dd_from_peak = (
        max(0.0, (dyn.maxiprice - current_bid) / dyn.maxiprice)
        if dyn.maxiprice > 0 else 0.0
    )

    if gain_pct > dyn.max_gain_pct:
        dyn.max_gain_pct = gain_pct
    if dd_from_entry > dyn.max_drawdown_pct:
        dyn.max_drawdown_pct = dd_from_entry

    dyn.current_drawdown_from_peak_pct = dd_from_peak

    if current_bid > dyn.entry_price:
        dyn.has_been_above_entry = True
    if current_bid < dyn.entry_price:
        dyn.has_been_below_entry = True

    dyn.n_updates += 1
    return dyn


def check_trailing_stop(
    dyn: PositionDynamics,
    current_bid: float,
    fee_rate: float,
    trailing_drawdown_pct: float = 0.004,
    min_gain_arming_pct: float = 0.005,
) -> tuple[bool, str]:
    """
    Returns (should_exit, reason).
    Arms when max_gain >= min_gain_arming_pct. Triggers when drawdown
    from peak >= trailing_drawdown_pct.
    """
    if dyn.max_gain_pct < min_gain_arming_pct:
        return False, f"trailing_not_armed max_gain={dyn.max_gain_pct*100:.3f}%"

    if dyn.current_drawdown_from_peak_pct < trailing_drawdown_pct:
        return False, (
            f"no_trailing_trigger drawdown={dyn.current_drawdown_from_peak_pct*100:.3f}%"
            f" < threshold={trailing_drawdown_pct*100:.3f}%"
        )

    return True, (
        f"TRAILING_STOP peak={dyn.maxiprice:.8f} "
        f"max_gain={dyn.max_gain_pct*100:.3f}% "
        f"drawdown_from_peak={dyn.current_drawdown_from_peak_pct*100:.3f}% "
        f"bid={current_bid:.8f}"
    )


def check_breakeven_escape(
    dyn: PositionDynamics,
    current_bid: float,
    fee_rate: float,
    min_gain_arming_pct: float = 0.003,
    buffer_pct: float = 0.0005,
) -> tuple[bool, str]:
    """
    Returns (should_exit, reason).
    Arms when max_gain >= min_gain_arming_pct. Exits at breakeven
    when >= 50% of the peak gain has been retraced.
    """
    if dyn.max_gain_pct < min_gain_arming_pct:
        return False, f"breakeven_not_armed max_gain={dyn.max_gain_pct*100:.3f}%"

    breakeven_target = dyn.entry_price * (1.0 + 2.0 * fee_rate + buffer_pct)

    if current_bid < breakeven_target:
        return False, (
            f"below_breakeven_target bid={current_bid:.8f} target={breakeven_target:.8f}"
        )

    retraced_ratio = (
        dyn.current_drawdown_from_peak_pct / dyn.max_gain_pct
        if dyn.max_gain_pct > 0 else 0.0
    )
    if retraced_ratio < 0.5:
        return False, f"retraced_only_{retraced_ratio*100:.0f}%_of_peak"

    return True, (
        f"BREAKEVEN_ESCAPE peak={dyn.maxiprice:.8f} "
        f"max_gain={dyn.max_gain_pct*100:.3f}% "
        f"target={breakeven_target:.8f} bid={current_bid:.8f} "
        f"retraced={retraced_ratio*100:.0f}%"
    )


# ── Persistence ──────────────────────────────────────────────────────────────

def save_dynamics(dyn: PositionDynamics, path: Path) -> None:
    """Save atomically. Called every tick."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(dyn), indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def load_dynamics(path: Path) -> Optional[PositionDynamics]:
    """Load from disk. Returns None if missing or corrupt."""
    try:
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            return PositionDynamics(**d)
    except Exception:
        pass
    return None


def clear_dynamics(path: Path) -> None:
    """Remove dynamics file after position is closed."""
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass
