import unittest
from unittest.mock import patch

import TokenProfileSelector as selector


class FlatTokenHoldTests(unittest.TestCase):
    def test_every_token_is_held_before_absolute_minimum_age(self):
        with patch.object(selector, "SELECTOR_FLAT_MIN_HOLD_MINUTES", 5.0):
            self.assertAlmostEqual(
                selector._minimum_hold_remaining_minutes(2.25),
                2.75,
            )

    def test_token_can_switch_after_absolute_minimum_age(self):
        with patch.object(selector, "SELECTOR_FLAT_MIN_HOLD_MINUTES", 5.0):
            self.assertEqual(
                selector._minimum_hold_remaining_minutes(5.0),
                0.0,
            )


class CandidateWindowTests(unittest.TestCase):
    def test_rejects_micro_move(self):
        with (
            patch.object(selector, "SELECTOR_MIN_WINDOW_PCT", 0.15),
            patch.object(selector, "SELECTOR_MAX_WINDOW_PCT", 1.8),
            patch.object(selector, "SELECTOR_MIN_MOVE_TO_SPREAD", 2.0),
        ):
            self.assertFalse(selector._candidate_window_is_eligible(0.09, 0.01))

    def test_rejects_move_too_small_for_spread(self):
        with (
            patch.object(selector, "SELECTOR_MIN_WINDOW_PCT", 0.15),
            patch.object(selector, "SELECTOR_MAX_WINDOW_PCT", 1.8),
            patch.object(selector, "SELECTOR_MIN_MOVE_TO_SPREAD", 2.0),
        ):
            self.assertFalse(selector._candidate_window_is_eligible(0.18, 0.10))

    def test_accepts_directional_move_with_edge(self):
        with (
            patch.object(selector, "SELECTOR_MIN_WINDOW_PCT", 0.15),
            patch.object(selector, "SELECTOR_MAX_WINDOW_PCT", 1.8),
            patch.object(selector, "SELECTOR_MIN_MOVE_TO_SPREAD", 2.0),
        ):
            self.assertTrue(selector._candidate_window_is_eligible(0.30, 0.05))


class RecentHighFilterTests(unittest.TestCase):
    def test_distance_from_recent_high_pct(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return [
                    [0, "100", "101", "99", "100", "0"],
                    [0, "100", "102", "99", "101", "0"],
                    [0, "101", "103", "100", "102", "0"],
                    [0, "102", "104", "101", "103", "0"],
                    [0, "103", "105", "102", "104", "0"],
                ]

        with patch.object(selector._SESSION, "get", return_value=Response()):
            self.assertAlmostEqual(
                selector.distance_from_recent_high_pct("BTCUSDC", minutes=5),
                (105.0 - 104.0) / 105.0,
            )

    def test_pick_best_rejects_candidate_too_close_to_high(self):
        ranked = [{"symbol": "BTCUSDC", "pct": 0.30, "spread_pct": 0.02, "is_toxic": False}]
        with (
            patch.object(selector, "collect_candidates", return_value=ranked),
            patch.object(selector, "rank_candidates", return_value=ranked),
            patch.object(selector, "current_direction_pct", return_value=0.10),
            patch.object(selector, "distance_from_recent_high_pct", return_value=0.0001),
            patch.object(selector, "SELECTOR_MAX_DISTANCE_FROM_5M_HIGH_PCT", 0.0020),
        ):
            chosen, _ = selector.pick_best_candidate({})

        self.assertIsNone(chosen)


if __name__ == "__main__":
    unittest.main()
