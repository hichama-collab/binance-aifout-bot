import unittest

from strategy.pic_filter import PicCheck, should_block_near_peak


def _near_peak():
    return PicCheck(
        is_near_peak=True,
        distance_from_peak_pct=0.0,
        peak_lookback_s=180,
        peak_price=100.0,
        reason="near_peak",
    )


class PicFilterEntryModeTests(unittest.TestCase):
    def test_regular_p_entry_stays_blocked_near_peak(self):
        self.assertTrue(should_block_near_peak("P", _near_peak()))

    def test_short_burst_stays_blocked_near_peak(self):
        self.assertTrue(should_block_near_peak("BURST", _near_peak()))


if __name__ == "__main__":
    unittest.main()
