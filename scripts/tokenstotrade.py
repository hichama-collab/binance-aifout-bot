#!/usr/bin/env python3
"""Propose the best tokens to trade based on recent CSV logs.

The script expects the same CSV format used by ``review_latest.py`` and ranks
symbols by total PnL recorded on ``SELL_FILLED`` events. It prints the top N
symbols with win rate and average PnL details so you can copy/paste the
recommendation directly to your team.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass
class TokenStats:
    symbol: str
    trades: int = 0
    trades_with_pnl: int = 0
    total_pnl: float = 0.0
    wins: int = 0
    losses: int = 0

    def record_trade(self, pnl: Optional[float]) -> None:
        self.trades += 1
        if pnl is None:
            return
        self.trades_with_pnl += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
        elif pnl < 0:
            self.losses += 1

    @property
    def average_pnl(self) -> float:
        return self.total_pnl / self.trades_with_pnl if self.trades_with_pnl else 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades_with_pnl * 100) if self.trades_with_pnl else 0.0


# --- helpers ---------------------------------------------------------------


def parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def gather_csv_files(path: Path) -> List[Path]:
    """Return CSV files, walking directories recursively."""

    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"No such file or directory: {path}")

    csv_files = sorted(p for p in path.rglob("*.csv") if p.is_file())
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in directory tree: {path}")
    return csv_files


# --- core logic ------------------------------------------------------------


def load_token_stats(csv_files: Iterable[Path]) -> Dict[str, TokenStats]:
    stats: Dict[str, TokenStats] = {}

    for csv_path in csv_files:
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("event", "").strip() != "SELL_FILLED":
                    continue

                symbol = row.get("symbol", "").strip() or "(unknown)"
                pnl = parse_float(row.get("pnl"))

                token_stat = stats.setdefault(symbol, TokenStats(symbol=symbol))
                token_stat.record_trade(pnl)

    return stats


def select_top_tokens(stats: Dict[str, TokenStats], top_n: int) -> List[TokenStats]:
    ranked = sorted(
        stats.values(),
        key=lambda s: (s.total_pnl, s.win_rate, s.trades_with_pnl),
        reverse=True,
    )
    return ranked[:top_n]


def format_report(tokens: List[TokenStats]) -> str:
    if not tokens:
        return "Aucun trade enregistré : impossible de proposer des tokens."

    lines = ["# Tokens recommandés pour le bot", ""]
    for idx, token in enumerate(tokens, start=1):
        lines.append(
            "- "
            f"#{idx} {token.symbol}: total PnL={token.total_pnl:.6f}, "
            f"avg PnL/trade={token.average_pnl:.6f}, win rate={token.win_rate:.1f}%, "
            f"trades comptés={token.trades_with_pnl}/{token.trades}"
        )
    lines.append("")
    lines.append("Copie/colle cette liste dans ton brief développeur.")
    return "\n".join(lines)


# --- CLI -------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classe les tokens par PnL et renvoie les meilleurs candidats."
    )
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path("shared_logs"),
        help="CSV ou dossier contenant les logs à analyser (par défaut: shared_logs)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="Nombre de tokens à recommander (par défaut: 3)",
    )
    args = parser.parse_args()

    csv_files = gather_csv_files(args.path)
    stats = load_token_stats(csv_files)
    top_tokens = select_top_tokens(stats, args.top)

    print(format_report(top_tokens))


if __name__ == "__main__":
    main()
