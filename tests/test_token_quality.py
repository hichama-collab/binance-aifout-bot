"""Tests for state/token_quality.py"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state.token_quality import compute_quality_score, select_token


def _stats(n, pnl_total, winrate, avg_pnl=None, min_trades_block=5, block_pnl=-0.10, block_wr=0.20):
    if avg_pnl is None:
        avg_pnl = pnl_total / n if n > 0 else 0.0
    return {
        "n_trades": n,
        "pnl_net_total": pnl_total,
        "winrate": winrate,
        "avg_pnl_net": avg_pnl,
        "min_trades": 3,
        "min_trades_for_block": min_trades_block,
        "block_pnl_threshold": block_pnl,
        "block_winrate_threshold": block_wr,
    }


class TestComputeQualityScore:
    def test_blocked_high_loss(self):
        # 8 trades, pnl_net_total=-0.38 → score 0.0
        s = _stats(n=8, pnl_total=-0.38, winrate=0.25)
        assert compute_quality_score(s) == 0.0

    def test_blocked_low_winrate(self):
        # 5 trades, winrate < 0.20 → score 0.0
        s = _stats(n=5, pnl_total=-0.05, winrate=0.10)
        assert compute_quality_score(s) == 0.0

    def test_neutral_few_trades(self):
        # 2 trades → score 0.5 (insufficient history)
        s = _stats(n=2, pnl_total=-0.05, winrate=0.5)
        assert compute_quality_score(s) == 0.5

    def test_good_score(self):
        # 5 trades, avg +0.02, wr 0.6 → score > 0.7
        s = _stats(n=5, pnl_total=0.10, winrate=0.6, avg_pnl=0.02)
        score = compute_quality_score(s)
        assert score > 0.7, f"Expected > 0.7, got {score}"

    def test_score_range(self):
        # Score always in [0, 1]
        for n in [1, 3, 5, 10]:
            for pnl in [-0.5, -0.05, 0.0, 0.05, 0.5]:
                for wr in [0.0, 0.25, 0.5, 0.75, 1.0]:
                    s = _stats(n=n, pnl_total=pnl, winrate=wr)
                    score = compute_quality_score(s)
                    assert 0.0 <= score <= 1.0, f"Score out of range: {score} for n={n} pnl={pnl} wr={wr}"

    def test_not_blocked_below_min_trades(self):
        # 4 trades with terrible pnl — below min_trades_block=5 → not blocked
        s = _stats(n=4, pnl_total=-0.50, winrate=0.0, min_trades_block=5)
        score = compute_quality_score(s)
        assert score != 0.0  # should be 0.5 (neutral, < min_trades)


class TestSelectToken:
    def _cfg(self, min_qs=0.3, respect_blocked=True, enabled=True):
        class Cfg:
            tokenQuality_enabled = enabled
            tokenQuality_minQualityScore = min_qs
            tokenQuality_respectBlocked = respect_blocked
        return Cfg()

    def test_skip_blocked(self):
        candidates = ["SAHARAUSDC", "UNIUSDC", "BTCUSDC"]
        quality_map = {
            "SAHARAUSDC": {"quality_score": 0.0},
            "UNIUSDC": {"quality_score": 0.6},
            "BTCUSDC": {"quality_score": 0.5},
        }
        result = select_token(candidates, quality_map, self._cfg())
        assert result != "SAHARAUSDC"
        assert result == "UNIUSDC"  # best among non-blocked

    def test_all_blocked_returns_none(self):
        candidates = ["SAHARAUSDC", "GUNUSDC"]
        quality_map = {
            "SAHARAUSDC": {"quality_score": 0.0},
            "GUNUSDC": {"quality_score": 0.0},
        }
        result = select_token(candidates, quality_map, self._cfg())
        assert result is None

    def test_no_quality_map_returns_first(self):
        candidates = ["UNIUSDC", "BTCUSDC"]
        result = select_token(candidates, {}, self._cfg())
        assert result == "UNIUSDC"

    def test_disabled_returns_first(self):
        candidates = ["SAHARAUSDC", "UNIUSDC"]
        quality_map = {"SAHARAUSDC": {"quality_score": 0.0}}
        result = select_token(candidates, quality_map, self._cfg(enabled=False))
        assert result == "SAHARAUSDC"  # quality filter bypassed

    def test_rank_by_quality_x_rank(self):
        # Candidate 1 is ranked #1 (best momentum) but quality 0.4
        # Candidate 2 is ranked #2 but quality 0.9
        # Final: rank_score_1=1.0*0.4=0.4, rank_score_2=0.5*0.9=0.45 → candidate 2 wins
        candidates = ["LOW_QUALITY", "HIGH_QUALITY"]
        quality_map = {
            "LOW_QUALITY": {"quality_score": 0.4},
            "HIGH_QUALITY": {"quality_score": 0.9},
        }
        result = select_token(candidates, quality_map, self._cfg(min_qs=0.3))
        assert result == "HIGH_QUALITY"
