"""Pivot detection: the liquidity pools everything else hangs off."""

from __future__ import annotations

import unittest

from helpers import mk
from ict_bot.signals.pivots import confirmed_pivot_high, confirmed_pivot_low


class TestPivots(unittest.TestCase):
    def test_pivot_low_confirms_strength_bars_late(self):
        bars = mk([
            (110, 111, 108, 109),
            (109, 110, 106, 107),
            (107, 108, 100, 104),   # the pivot
            (104, 107, 103, 106),
            (106, 109, 105, 108),   # confirmation bar
        ])
        # Not confirmable until both wings exist.
        self.assertIsNone(confirmed_pivot_low(bars[:4], 2))
        self.assertEqual(confirmed_pivot_low(bars, 2), 100)

    def test_pivot_high(self):
        bars = mk([
            (90, 92, 89, 91),
            (91, 94, 90, 93),
            (93, 100, 92, 96),      # the pivot
            (96, 97, 93, 94),
            (94, 95, 91, 92),
        ])
        self.assertEqual(confirmed_pivot_high(bars, 2), 100)

    def test_flat_bottom_is_not_a_pivot(self):
        """A double bottom leaves the level intact - it is not a swing point."""
        bars = mk([
            (110, 111, 108, 109),
            (109, 110, 100, 107),   # equal low
            (107, 108, 100, 104),   # centre, same low
            (104, 107, 103, 106),
            (106, 109, 105, 108),
        ])
        self.assertIsNone(confirmed_pivot_low(bars, 2))

    def test_insufficient_history(self):
        bars = mk([(1, 2, 0.5, 1.5)] * 4)
        self.assertIsNone(confirmed_pivot_low(bars, 2))
        self.assertIsNone(confirmed_pivot_high(bars, 2))

    def test_strength_scales_the_window(self):
        rows = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(5)]      # rising
        rows += [(105, 106, 104, 105)]                                       # the high
        rows += [(104 - i, 105 - i, 103 - i, 104 - i) for i in range(5)]     # falling
        bars = mk(rows)
        # centre must sit `strength` bars from the end for confirmation
        self.assertEqual(confirmed_pivot_high(bars[:7], 1), 106)
        self.assertIsNone(confirmed_pivot_high(bars, 1))


if __name__ == "__main__":
    unittest.main()
