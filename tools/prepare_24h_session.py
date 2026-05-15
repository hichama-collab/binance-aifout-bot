"""
Prépare une session 24h de test du bot.

Vérifie l'état, affiche la config active, et propose le reset stats.
NE démarre PAS le bot automatiquement.

Usage:
  python3 tools/prepare_24h_session.py
"""
from __future__ import annotations
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SERVICE_ENV = ROOT / ".service.env"
DB_PATH = ROOT / "data" / "runtime" / "trade_memory.sqlite3"
QUALITY_FILE = ROOT / "state" / "token_quality.json"
RUNTIME_DIR = ROOT / "data" / "runtime"


def _read_env(path: Path) -> dict:
    result = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


def _check_wallet_clean():
    wallet_path = RUNTIME_DIR / "wallet.json"
    if not wallet_path.exists():
        return True, "wallet.json absent (sera rafraîchi au démarrage)"
    try:
        w = json.loads(wallet_path.read_text())
        balances = w.get("balances", [])
        non_usdc = [(b["asset"], float(b.get("free", 0))) for b in balances
                    if b["asset"] not in ("USDC", "BNB") and float(b.get("free", 0)) > 0.001]
        if non_usdc:
            return False, f"Résidus non-USDC détectés : {non_usdc}"
        return True, "Wallet propre (USDC + BNB uniquement)"
    except Exception as e:
        return True, f"wallet.json illisible ({e}) — sera rafraîchi"


def _get_db_stats():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), SUM(pnl_usdc), AVG(pnl_usdc) FROM closed_trades")
        row = cur.fetchone()
        conn.close()
        return {"trades": row[0] or 0, "pnl_total": round(row[1] or 0.0, 4), "pnl_avg": round(row[2] or 0.0, 4)}
    except Exception:
        return {"trades": 0, "pnl_total": 0.0, "pnl_avg": 0.0}


def _load_quality_map():
    try:
        if QUALITY_FILE.exists():
            data = json.loads(QUALITY_FILE.read_text())
            return data.get("tokens", {})
    except Exception:
        pass
    return {}


def main():
    print("=" * 65)
    print("PRÉPARER SESSION 24H — Binance AiFout Bot")
    print("=" * 65)

    # 1. Check .service.env
    print("\n── Config .service.env ──")
    env = _read_env(SERVICE_ENV)
    if not env:
        print("  ⚠️  .service.env introuvable ou vide !")
    else:
        print(f"  SYMBOL  : {env.get('SYMBOL', 'NON DÉFINI')}")
        print(f"  PROFILE : {env.get('PROFILE', 'NON DÉFINI')}")
        print(f"  DRY_RUN : {env.get('DRY_RUN', '0')}")

    # 2. Check wallet
    print("\n── Wallet ──")
    ok, msg = _check_wallet_clean()
    prefix = "  ✓" if ok else "  ⚠️ "
    print(f"{prefix} {msg}")

    # 3. DB stats
    print("\n── Base de données trades ──")
    stats = _get_db_stats()
    print(f"  Trades : {stats['trades']}")
    print(f"  PnL net total : {stats['pnl_total']:+.4f} USDC")
    print(f"  PnL net moyen : {stats['pnl_avg']:+.4f} USDC/trade")

    # 4. Token quality map
    print("\n── Token Quality Map ──")
    if not QUALITY_FILE.exists():
        print("  ⚠️  token_quality.json absent — rebuild recommandé")
        print("     → python3 tools/rebuild_token_quality.py")
    else:
        qmap = _load_quality_map()
        blocked = [(s, t) for s, t in qmap.items() if t.get("quality_score", 1.0) == 0.0]
        low = [(s, t) for s, t in qmap.items() if 0.0 < t.get("quality_score", 1.0) < 0.4]
        print(f"  Tokens connus : {len(qmap)}")
        print(f"  Bloqués (score=0)  : {[s for s, _ in blocked]}")
        print(f"  Faibles (score<0.4): {[s for s, _ in low]}")
        import time
        age_s = time.time() - QUALITY_FILE.stat().st_mtime
        age_min = int(age_s / 60)
        print(f"  Dernière mise à jour : il y a {age_min} min")

    # 5. Mods actives
    print("\n── Modifications V5 actives ──")
    try:
        import importlib.util
        mods = [
            ("strategy.pic_filter", "Pic Filter"),
            ("strategy.position_dynamics", "Position Dynamics + Trailing + Breakeven"),
            ("state.token_quality", "Token Quality Score"),
        ]
        for mod, label in mods:
            spec = importlib.util.find_spec(mod)
            status = "✓" if spec else "✗ MANQUANT"
            print(f"  {status} {label}")
    except Exception:
        pass

    # 6. Décision reset
    print("\n── Reset DB stats ──")
    if stats["trades"] == 0:
        print("  ✓ DB déjà vide — prête pour la session 24h")
    else:
        print(f"  La DB contient {stats['trades']} trades ({stats['pnl_total']:+.4f} USDC)")
        do_reset = input("  Voulez-vous archiver et réinitialiser la DB ? (yes/no) : ").strip().lower()
        if do_reset == "yes":
            subprocess.run([sys.executable, str(ROOT / "tools" / "reset_stats.py")])
        else:
            print("  DB conservée.")

    # 7. Confirmation finale
    print("\n" + "=" * 65)
    print("RÉCAPITULATIF")
    print("=" * 65)
    env = _read_env(SERVICE_ENV)
    print(f"  Token    : {env.get('SYMBOL', '?')}")
    print(f"  Profile  : {env.get('PROFILE', '?')}")
    print(f"  Dry run  : {'OUI ✓' if env.get('DRY_RUN','0') != '0' else 'NON — ARGENT RÉEL ⚠️'}")
    print()
    print("Pour démarrer le bot :")
    print("  sudo systemctl restart binance-aifout-bot.service")
    print("  sudo journalctl -fu binance-aifout-bot.service")
    print()
    go = input("Confirmer que tout est OK pour lancer la session 24h ? (yes/no) : ").strip().lower()
    if go == "yes":
        print("\n✓ GO pour 24h ! Lance le bot avec systemctl.")
    else:
        print("\nSession annulée.")


if __name__ == "__main__":
    main()
