"""
Rebuild state/token_quality.json from historical trade data.

Sources (in priority order):
  1. data/runtime/trade_memory.sqlite3  (closed_trades table)
  2. data/logs/**/*_trades.csv          (including inside .tar.gz archives)

Idempotent — safe to run multiple times.
Usage:
  python3 tools/rebuild_token_quality.py
  python3 tools/rebuild_token_quality.py --fee-rate 0.001 --min-trades 3 --out state/token_quality.json
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import sqlite3
import tarfile
import time
from collections import defaultdict
from pathlib import Path

# Project root = parent of tools/
ROOT = Path(__file__).resolve().parent.parent

DEFAULT_FEE_RATE = 0.001
DEFAULT_MIN_TRADES = 3
DEFAULT_MIN_TRADES_BLOCK = 5
DEFAULT_BLOCK_PNL = -0.10
DEFAULT_BLOCK_WR = 0.20
DEFAULT_OUT = ROOT / "state" / "token_quality.json"


def _compute_score(stats: dict) -> float:
    from state.token_quality import compute_quality_score
    return compute_quality_score(stats, min_trades=stats.get("min_trades", DEFAULT_MIN_TRADES))


def _load_from_sqlite(db_path: Path, fee_rate: float) -> dict[str, list[float]]:
    """Returns {symbol: [pnl_net, pnl_net, ...]}. pnl_usdc in DB is already net."""
    trades: dict[str, list[float]] = defaultdict(list)
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT symbol, pnl_usdc FROM closed_trades ORDER BY ts_epoch ASC")
        for symbol, pnl in cur.fetchall():
            if symbol:
                trades[symbol].append(float(pnl or 0.0))
        conn.close()
    except Exception as e:
        print(f"  [WARN] SQLite read failed: {e}")
    return trades


def _parse_csv_rows(reader, fee_rate: float) -> dict[str, list[float]]:
    """Parse CSV DictReader rows → {symbol: [pnl_net]}."""
    trades: dict[str, list[float]] = defaultdict(list)
    for row in reader:
        symbol = (row.get("symbol") or "").strip()
        if not symbol:
            continue
        # Prefer pnl_net if present, else use pnl (already net per REVIEW_CODE.md)
        raw = row.get("pnl_net") or row.get("pnl") or ""
        try:
            pnl = float(raw)
            trades[symbol].append(pnl)
        except (ValueError, TypeError):
            pass
    return trades


def _load_from_csvs(logs_dir: Path, fee_rate: float) -> dict[str, list[float]]:
    """Scan logs_dir recursively for *_trades.csv and *.tar.gz."""
    trades: dict[str, list[float]] = defaultdict(list)
    csv_files = list(logs_dir.rglob("*_trades.csv"))
    tar_files = list(logs_dir.rglob("*.tar.gz"))

    for csv_path in csv_files:
        try:
            with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for sym, pnls in _parse_csv_rows(reader, fee_rate).items():
                    trades[sym].extend(pnls)
        except Exception as e:
            print(f"  [WARN] CSV {csv_path.name}: {e}")

    for tar_path in tar_files:
        try:
            with tarfile.open(tar_path, "r:gz") as tf:
                for member in tf.getmembers():
                    if member.name.endswith("_trades.csv"):
                        f = tf.extractfile(member)
                        if f:
                            text = f.read().decode("utf-8", errors="replace")
                            reader = csv.DictReader(io.StringIO(text))
                            for sym, pnls in _parse_csv_rows(reader, fee_rate).items():
                                trades[sym].extend(pnls)
        except Exception as e:
            print(f"  [WARN] TAR {tar_path.name}: {e}")

    return trades


def _merge(a: dict, b: dict) -> dict:
    """Merge two {symbol: [pnl]} dicts, deduplicate by length (prefer longer)."""
    merged: dict[str, list[float]] = defaultdict(list)
    for sym in set(a) | set(b):
        combined = list(a.get(sym, [])) + list(b.get(sym, []))
        merged[sym] = combined
    return merged


def build_quality_map(
    trades: dict[str, list[float]],
    fee_rate: float,
    min_trades: int,
    min_trades_block: int,
    block_pnl: float,
    block_wr: float,
) -> dict:
    tokens = {}
    for symbol, pnls in trades.items():
        if not pnls:
            continue
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        pnl_total = round(sum(pnls), 6)
        winrate = round(len(wins) / n, 4) if n > 0 else 0.0
        avg_pnl = round(pnl_total / n, 6) if n > 0 else 0.0
        last30 = pnls[-30:]
        pnl_last30 = round(sum(last30), 6)

        stats = {
            "n_trades": n,
            "pnl_net_total": pnl_total,
            "winrate": winrate,
            "avg_pnl_net": avg_pnl,
            "last_30_trades_pnl_net": pnl_last30,
            "min_trades": min_trades,
            "min_trades_for_block": min_trades_block,
            "block_pnl_threshold": block_pnl,
            "block_winrate_threshold": block_wr,
        }
        score = _compute_score(stats)
        block_reason = None
        if score == 0.0 and n >= min_trades_block:
            if pnl_total < block_pnl:
                block_reason = f"n>={min_trades_block} and pnl_net_total<{block_pnl}"
            elif winrate < block_wr:
                block_reason = f"n>={min_trades_block} and winrate<{block_wr}"

        tokens[symbol] = {
            "n_trades": n,
            "pnl_net_total": pnl_total,
            "winrate": winrate,
            "avg_pnl_net": avg_pnl,
            "last_30_trades_pnl_net": pnl_last30,
            "quality_score": score,
            "blocked_until_ts": None,
            "block_reason": block_reason,
        }
    return tokens


def main():
    parser = argparse.ArgumentParser(description="Rebuild token_quality.json")
    parser.add_argument("--fee-rate", type=float, default=DEFAULT_FEE_RATE)
    parser.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES)
    parser.add_argument("--min-trades-block", type=int, default=DEFAULT_MIN_TRADES_BLOCK)
    parser.add_argument("--block-pnl", type=float, default=DEFAULT_BLOCK_PNL)
    parser.add_argument("--block-wr", type=float, default=DEFAULT_BLOCK_WR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    import sys
    sys.path.insert(0, str(ROOT))

    print(f"[rebuild_token_quality] fee_rate={args.fee_rate} min_trades={args.min_trades}")

    db_path = ROOT / "data" / "runtime" / "trade_memory.sqlite3"
    logs_dir = ROOT / "data" / "logs"

    trades: dict[str, list[float]] = defaultdict(list)

    if db_path.exists():
        print(f"  Loading from SQLite: {db_path}")
        db_trades = _load_from_sqlite(db_path, args.fee_rate)
        trades = _merge(trades, db_trades)
        print(f"  → {sum(len(v) for v in db_trades.values())} trades from {len(db_trades)} tokens")

    if logs_dir.exists():
        print(f"  Scanning CSV/TAR in: {logs_dir}")
        csv_trades = _load_from_csvs(logs_dir, args.fee_rate)
        if csv_trades:
            trades = _merge(trades, csv_trades)
            print(f"  → {sum(len(v) for v in csv_trades.values())} trades from CSV/TAR")

    if not trades:
        print("[WARN] No trade data found. Writing empty quality map.")

    tokens = build_quality_map(
        trades,
        fee_rate=args.fee_rate,
        min_trades=args.min_trades,
        min_trades_block=args.min_trades_block,
        block_pnl=args.block_pnl,
        block_wr=args.block_wr,
    )

    from state.token_quality import save_quality_map
    save_quality_map(tokens, fee_rate=args.fee_rate, path=args.out)

    blocked = [s for s, t in tokens.items() if t["quality_score"] == 0.0]
    print(f"\n[DONE] {len(tokens)} tokens scored → {args.out}")
    print(f"  Blocked ({len(blocked)}): {blocked}")
    for sym, t in sorted(tokens.items(), key=lambda x: x[1]["quality_score"]):
        flag = "🔴 BLOCKED" if t["quality_score"] == 0.0 else ("🟡" if t["quality_score"] < 0.4 else "🟢")
        print(f"  {flag} {sym:20s}  n={t['n_trades']:3d}  wr={t['winrate']:.0%}  "
              f"pnl={t['pnl_net_total']:+.4f}  score={t['quality_score']:.3f}")


if __name__ == "__main__":
    main()
