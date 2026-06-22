"""Token performance rankings for the dashboard."""

from __future__ import annotations

from typing import Iterable, Mapping, Optional


def _as_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_token_rankings(
    rows: Iterable[Mapping],
    fx: Optional[float] = None,
    limit: int = 5,
) -> dict:
    """Aggregate closed trades and return distinct best/worst token groups."""
    aggregates: dict[str, dict] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        pnl = _as_float(row.get("pnl", row.get("pnl_usdc", 0.0)))
        item = aggregates.setdefault(
            symbol,
            {
                "symbol": symbol,
                "pnl_usdc": 0.0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "last_ts": 0.0,
            },
        )
        item["pnl_usdc"] += pnl
        item["trades"] += 1
        item["wins"] += int(pnl > 0)
        item["losses"] += int(pnl < 0)
        item["last_ts"] = max(item["last_ts"], _as_float(row.get("ts", row.get("ts_epoch", 0))))

    ranked = []
    for item in aggregates.values():
        trades = int(item["trades"])
        pnl_usdc = round(float(item["pnl_usdc"]), 4)
        ranked.append(
            {
                **item,
                "pnl_usdc": pnl_usdc,
                "pnl_eur": round(pnl_usdc * fx, 4) if fx is not None else None,
                "winrate": round(item["wins"] / trades, 4) if trades else 0.0,
            }
        )

    if not ranked:
        return {"top": [], "bottom": []}

    group_size = min(max(1, int(limit)), max(1, len(ranked) // 2))
    top = sorted(
        ranked,
        key=lambda row: (row["pnl_usdc"], row["winrate"], row["trades"], row["symbol"]),
        reverse=True,
    )[:group_size]
    top_symbols = {row["symbol"] for row in top}
    bottom = [
        row
        for row in sorted(
            ranked,
            key=lambda row: (row["pnl_usdc"], -row["trades"], row["symbol"]),
        )
        if row["symbol"] not in top_symbols
    ][:group_size]

    return {"top": top, "bottom": bottom}
