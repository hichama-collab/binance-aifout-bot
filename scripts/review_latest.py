#!/usr/bin/env python3
"""Summarize a trade log CSV.

The expected CSV header is:
```
ts_utc,symbol,event,side,qty,price,reason,pnl,profile,dry_run,spread_pct,mom_pct,up_ratio,rsi,ema1_ok,ema5_ok,vol_ok
```

The script prints a concise Markdown report with totals and basic statistics.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional


def parse_float(value: str) -> Optional[float]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def summarize_trades(path: Path) -> str:
    event_counts: Counter[str] = Counter()
    side_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    profile_counts: Counter[str] = Counter()
    pnl_values: List[float] = []
    pnl_by_reason: Dict[str, List[float]] = defaultdict(list)
    spread_values: List[float] = []

    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        event = row.get("event", "").strip() or "(unknown)"
        event_counts[event] += 1

        side = row.get("side", "").strip()
        if side:
            side_counts[side] += 1

        reason = row.get("reason", "").strip() or "(none)"
        reason_counts[reason] += 1

        profile = row.get("profile", "").strip()
        if profile:
            profile_counts[profile] += 1

        pnl = parse_float(row.get("pnl"))
        if pnl is not None:
            pnl_values.append(pnl)
            pnl_by_reason[reason].append(pnl)

        spread = parse_float(row.get("spread_pct"))
        if spread is not None:
            spread_values.append(spread)

    total_events = len(rows)
    total_trades = event_counts.get("SELL_FILLED", 0)
    positive_trades = sum(1 for value in pnl_values if value > 0)
    negative_trades = sum(1 for value in pnl_values if value < 0)
    win_rate = (positive_trades / len(pnl_values) * 100) if pnl_values else 0.0

    def render_counter(counter: Counter[str]) -> str:
        if not counter:
            return "(none)"
        parts = [f"- {key}: {counter[key]}" for key in sorted(counter)]
        return "\n".join(parts)

    def render_pnl_breakdown() -> str:
        if not pnl_by_reason:
            return "(none)"
        lines: List[str] = []
        for reason in sorted(pnl_by_reason):
            values = pnl_by_reason[reason]
            lines.append(
                "- "
                f"{reason}: count={len(values)}, total={sum(values):.6f}, "
                f"avg={mean(values):.6f}, wins={sum(1 for v in values if v > 0)}, "
                f"losses={sum(1 for v in values if v < 0)}"
            )
        return "\n".join(lines)

    lines = [
        "# Trade Log Review",
        f"- File: `{path}`",
        f"- Total events: **{total_events}**",
        f"- Unique symbols: {len({row['symbol'] for row in rows})}",
        f"- Profiles: {', '.join(sorted(profile_counts)) if profile_counts else '(none)'}",
        "",
        "## Event breakdown",
        render_counter(event_counts),
        "",
        "## Side breakdown",
        render_counter(side_counts),
        "",
        "## Reason breakdown",
        render_counter(reason_counts),
        "",
        "## PnL summary (SELL_FILLED rows)",
        f"- Trades recorded: {total_trades}",
        f"- Trades with PnL: {len(pnl_values)}",
        f"- Total PnL: {sum(pnl_values):.6f}",
        f"- Average PnL: {mean(pnl_values):.6f}" if pnl_values else "- Average PnL: n/a",
        f"- Positive trades: {positive_trades}",
        f"- Negative trades: {negative_trades}",
        f"- Win rate: {win_rate:.1f}%",
        "",
        "### PnL by reason",
        render_pnl_breakdown(),
        "",
        "## Spread percentage",
        f"- Observations: {len(spread_values)}",
        f"- Average spread %: {mean(spread_values):.6f}" if spread_values else "- Average spread %: n/a",
    ]

    return "\n".join(lines) + "\n"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize trade log CSV files.")
    parser.add_argument(
        "path",
        type=Path,
        help="CSV file to summarize or directory containing CSV files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional directory to write one Markdown report per CSV",
    )
    parser.add_argument(
        "--combined-output",
        type=Path,
        help="Optional path to write the combined Markdown report",
    )
    args = parser.parse_args()

    csv_files = gather_csv_files(args.path)

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    reports: List[str] = []
    for csv_file in csv_files:
        report = summarize_trades(csv_file)
        reports.append(report)

        if args.output_dir:
            output_path = args.output_dir / f"{csv_file.stem}.md"
            output_path.write_text(report)

    combined_report = "\n".join(reports)

    if args.combined_output:
        args.combined_output.write_text(combined_report)

    print(combined_report)


if __name__ == "__main__":
    main()
