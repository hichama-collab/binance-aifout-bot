"""
Archive les anciennes stats et démarre une BDD propre.

Actions :
  - Archive data/runtime/trade_memory.sqlite3 → data/runtime/archive/
  - Réinitialise la base (crée une nouvelle DB vide avec le même schéma)
  - NE TOUCHE PAS aux CSV trades historiques
  - Logue un événement RESET dans dashboard.log

Usage:
  python3 tools/reset_stats.py
"""
from __future__ import annotations
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "runtime" / "trade_memory.sqlite3"
ARCHIVE_DIR = ROOT / "data" / "runtime" / "archive"
LOG_PATH = ROOT / "data" / "logs" / "dashboard.log"


def _log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    line = f"[{ts}] RESET {msg}"
    print(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _get_schema(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name")
    schema = "\n".join(row[0] for row in cur.fetchall() if row[0])
    conn.close()
    return schema


def _get_stats(db_path: Path) -> dict:
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), SUM(pnl_usdc) FROM closed_trades")
        row = cur.fetchone()
        conn.close()
        return {"trades": row[0] or 0, "pnl_total": round(row[1] or 0.0, 4)}
    except Exception:
        return {"trades": 0, "pnl_total": 0.0}


def main():
    print("=" * 60)
    print("RESET STATS — Binance AiFout Bot")
    print("=" * 60)

    if not DB_PATH.exists():
        print(f"[WARN] DB not found at {DB_PATH} — nothing to reset.")
        sys.exit(0)

    stats = _get_stats(DB_PATH)
    print(f"\nÉtat actuel de la DB :")
    print(f"  Trades enregistrés : {stats['trades']}")
    print(f"  PnL net total      : {stats['pnl_total']:+.4f} USDC")
    print(f"\nCette opération va :")
    print(f"  1. Archiver {DB_PATH.name} → {ARCHIVE_DIR}/")
    print(f"  2. Créer une nouvelle DB vide (même schéma)")
    print(f"  3. Les CSV historiques ne seront PAS modifiés")
    print()
    confirm = input("Tapez 'yes' pour confirmer : ").strip().lower()
    if confirm != "yes":
        print("Annulé.")
        sys.exit(0)

    # Archive
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts_str = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    archive_path = ARCHIVE_DIR / f"trade_memory_pre_reset_{ts_str}.sqlite3"
    shutil.copy2(str(DB_PATH), str(archive_path))
    print(f"\n✓ Archivé → {archive_path}")

    # Get schema before deleting
    schema = _get_schema(DB_PATH)

    # Remove old DB
    DB_PATH.unlink()

    # Create fresh DB with same schema
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    for stmt in schema.split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                cur.execute(stmt)
            except Exception as e:
                print(f"  [WARN] schema stmt failed: {e}")
    conn.commit()
    conn.close()
    print(f"✓ Nouvelle DB créée : {DB_PATH}")

    _log(f"DB reset — {stats['trades']} trades archivés → {archive_path.name}")
    print(f"\n✓ Reset terminé. Relancez le bot pour démarrer une session propre.")


if __name__ == "__main__":
    main()
