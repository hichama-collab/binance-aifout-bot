"""
Persistance de la position sur disque — survit aux restarts.
"""
from __future__ import annotations
import json
import time
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("persisted")
_PERSISTED_PATH_DEFAULT = Path("data/runtime/persisted_position.json")


@dataclass
class PersistedPosition:
    symbol: str
    entry_price: float
    entry_qty: float
    entry_ts: float
    entry_reason: str
    high_seen: float
    buy_notional: float
    last_save_ts: float = 0.0

    def age_sec(self) -> float:
        return max(0.0, time.time() - self.entry_ts)


def save_position(pos: PersistedPosition, path: Path = _PERSISTED_PATH_DEFAULT) -> bool:
    """Écriture atomique via fichier temporaire + rename."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(pos)
        data["last_save_ts"] = time.time()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.rename(path)
        return True
    except Exception as e:
        log.error(f"save_position failed: {e}")
        return False


def load_position(path: Path = _PERSISTED_PATH_DEFAULT) -> Optional[PersistedPosition]:
    """Retourne None si pas de fichier ou JSON corrompu."""
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return PersistedPosition(
            symbol=str(data["symbol"]),
            entry_price=float(data["entry_price"]),
            entry_qty=float(data["entry_qty"]),
            entry_ts=float(data["entry_ts"]),
            entry_reason=str(data.get("entry_reason", "")),
            high_seen=float(data.get("high_seen", data["entry_price"])),
            buy_notional=float(data.get("buy_notional", data["entry_price"] * data["entry_qty"])),
            last_save_ts=float(data.get("last_save_ts", 0.0)),
        )
    except Exception as e:
        log.error(f"load_position failed: {e}")
        return None


def clear_position(path: Path = _PERSISTED_PATH_DEFAULT) -> bool:
    """Supprime le fichier de position persistée."""
    try:
        if path.exists():
            path.unlink()
        return True
    except Exception as e:
        log.error(f"clear_position failed: {e}")
        return False


def reconcile_with_wallet(
    persisted: Optional[PersistedPosition],
    wallet_qty: float,
    symbol: str,
    step: float = 1e-8,
) -> Optional[PersistedPosition]:
    """
    Réconcilie l'état persisté avec ce que dit le wallet.
    - wallet_qty ~= persisted.entry_qty → OK, garde persisted
    - wallet_qty == 0 et persisted existe → vente manuelle externe
    - wallet_qty > 0 et pas de persisted → adoption avec log d'alerte
    """
    tol = max(step * 2, 1e-6)

    if persisted is not None and wallet_qty > tol:
        if abs(wallet_qty - persisted.entry_qty) <= tol:
            return persisted
        log.warning(f"DISCREPANCY symbol={symbol} persisted_qty={persisted.entry_qty} wallet_qty={wallet_qty} — keeping persisted")
        return persisted

    if persisted is not None and wallet_qty <= tol:
        log.warning(f"WALLET_EXTERNAL_SELL symbol={symbol} — position cleared externally")
        return None

    if persisted is None and wallet_qty > tol:
        log.warning(f"ADOPTED_UNKNOWN_ENTRY symbol={symbol} qty={wallet_qty} — no entry price known")
        return None  # Ne pas adopter sans entry_price connu

    return None
