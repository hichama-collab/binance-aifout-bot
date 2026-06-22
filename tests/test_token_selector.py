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


if __name__ == "__main__":
    unittest.main()
