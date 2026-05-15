"""
Token quality scoring based on historical bot performance per token.
Blocks structurally losing tokens from being selected.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Optional

QUALITY_FILE = Path("state/token_quality.json")
VERSION = 1


def compute_quality_score(stats: dict, min_trades: int = 3) -> float:
    """
    Score 0.0 (catastrophique) à 1.0 (excellent). Sans historique suffisant → 0.5.

    Conditions de blocage (score = 0) :
    - n_trades >= min_trades ET pnl_net_total < blockPnlThreshold
    - n_trades >= min_trades ET winrate < blockWinrateThreshold
    """
    n = stats.get("n_trades", 0)
    if n < min_trades:
        return 0.5

    pnl_total = stats.get("pnl_net_total", 0.0)
    wr = stats.get("winrate", 0.0)
    avg = stats.get("avg_pnl_net", 0.0)
    block_pnl = stats.get("block_pnl_threshold", -0.10)
    block_wr = stats.get("block_winrate_threshold", 0.20)
    min_trades_block = stats.get("min_trades_for_block", 5)

    if n >= min_trades_block and pnl_total < block_pnl:
        return 0.0
    if n >= min_trades_block and wr < block_wr:
        return 0.0

    # Base : avg_pnl_net normalisé sur [-0.05, +0.05]
    base = (avg + 0.05) / 0.10
    base = max(0.0, min(1.0, base))

    # Bonus winrate (centré sur 0.5)
    wr_bonus = (wr - 0.5) * 0.3

    return round(max(0.0, min(1.0, base + wr_bonus)), 3)


def load_quality_map(path: Path = QUALITY_FILE) -> dict:
    """Load token_quality.json. Returns empty dict if missing."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")).get("tokens", {})
    except Exception:
        pass
    return {}


def save_quality_map(tokens: dict, fee_rate: float, path: Path = QUALITY_FILE) -> None:
    """Write token_quality.json atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": VERSION,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fee_rate_used": fee_rate,
        "tokens": tokens,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def select_token(
    candidates: list,
    quality_map: dict,
    cfg,
    top_n: int = 10,
) -> Optional[str]:
    """
    candidates : tokens classés par ranking 1h (meilleur en premier).
    quality_map : token_quality.json["tokens"].

    Logique :
    - Si quality_score == 0 ET cfg.tokenQuality_respectBlocked : SKIP
    - Si quality_score < cfg.tokenQuality_minQualityScore : SKIP
    - Sinon : ranking_score_final = position_rank_score * quality_score

    Retourne le token avec le plus haut ranking_score_final.
    Fallback : token avec le plus haut quality_score parmi le top N.
    Si tous bloqués : retourne None.
    """
    enabled = bool(getattr(cfg, "tokenQuality_enabled", True))
    if not enabled or not quality_map:
        return candidates[0] if candidates else None

    min_qs = float(getattr(cfg, "tokenQuality_minQualityScore", 0.3))
    respect_blocked = bool(getattr(cfg, "tokenQuality_respectBlocked", True))

    scored = []
    n = len(candidates[:top_n])
    for rank, token in enumerate(candidates[:top_n]):
        tok_data = quality_map.get(token, {})
        qs = float(tok_data.get("quality_score", 0.5))
        if qs == 0.0 and respect_blocked:
            continue
        if qs < min_qs:
            continue
        # Position rank score: 1.0 for rank 0, decreasing
        rank_score = (n - rank) / n if n > 0 else 1.0
        final_score = rank_score * qs
        scored.append((token, final_score, qs))

    if scored:
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

    # Fallback : best quality_score among top N regardless of min_qs
    fallback = []
    for token in candidates[:top_n]:
        tok_data = quality_map.get(token, {})
        qs = float(tok_data.get("quality_score", 0.5))
        if qs == 0.0 and respect_blocked:
            continue
        fallback.append((token, qs))

    if fallback:
        fallback.sort(key=lambda x: x[1], reverse=True)
        return fallback[0][0]

    return None
