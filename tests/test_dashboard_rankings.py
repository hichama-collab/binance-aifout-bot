import unittest

from dashboard.rankings import build_token_rankings


class DashboardRankingsTests(unittest.TestCase):
    def test_builds_distinct_best_and_worst_groups(self):
        rows = [
            {"symbol": "BTCUSDC", "pnl": 0.30, "ts": 1},
            {"symbol": "BTCUSDC", "pnl": -0.10, "ts": 2},
            {"symbol": "ETHUSDC", "pnl": -0.40, "ts": 3},
            {"symbol": "SUIUSDC", "pnl": 0.05, "ts": 4},
            {"symbol": "INJUSDC", "pnl": -0.20, "ts": 5},
        ]

        result = build_token_rankings(rows, fx=0.9, limit=2)

        self.assertEqual([row["symbol"] for row in result["top"]], ["BTCUSDC", "SUIUSDC"])
        self.assertEqual([row["symbol"] for row in result["bottom"]], ["ETHUSDC", "INJUSDC"])
        self.assertEqual(result["top"][0]["trades"], 2)
        self.assertEqual(result["top"][0]["winrate"], 0.5)
        self.assertEqual(result["top"][0]["pnl_eur"], 0.18)

    def test_single_token_is_not_duplicated(self):
        result = build_token_rankings([{"symbol": "BTCUSDC", "pnl_usdc": -0.1}], limit=5)

        self.assertEqual(len(result["top"]), 1)
        self.assertEqual(result["bottom"], [])


if __name__ == "__main__":
    unittest.main()
