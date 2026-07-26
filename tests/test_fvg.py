"""HTF fair value gaps: built from completed bars only, retired once rebalanced."""

from __future__ import annotations

import unittest

from helpers import mk
from ict_bot.models import ZoneKind
from ict_bot.signals.fvg import FvgStore

HTF_STEP = 14400  # 4H


class TestFvgConstruction(unittest.TestCase):
    def test_bisi_from_a_three_bar_displacement(self):
        # bar[2].low (105) sits above bar[0].high (100) -> unfilled buy-side gap
        bars = mk([(95, 100, 94, 99), (99, 104, 98, 103), (103, 110, 105, 109)],
                  step=HTF_STEP)
        store = FvgStore()
        created = store.ingest_htf(bars)
        self.assertEqual(len(created), 1)
        zone = created[0]
        self.assertIs(zone.kind, ZoneKind.BISI)
        self.assertEqual((zone.bottom, zone.top), (100, 105))
        self.assertTrue(store.price_in_bullish(102))
        self.assertFalse(store.price_in_bullish(99))

    def test_sibi_from_a_bearish_displacement(self):
        bars = mk([(110, 112, 105, 106), (106, 107, 101, 102), (102, 103, 96, 97)],
                  step=HTF_STEP)
        store = FvgStore()
        created = store.ingest_htf(bars)
        self.assertEqual(len(created), 1)
        zone = created[0]
        self.assertIs(zone.kind, ZoneKind.SIBI)
        self.assertEqual((zone.bottom, zone.top), (103, 105))
        self.assertTrue(store.price_in_bearish(104))

    def test_no_gap_when_the_ranges_overlap(self):
        bars = mk([(95, 105, 94, 104), (104, 108, 100, 107), (107, 112, 103, 111)],
                  step=HTF_STEP)
        store = FvgStore()
        self.assertEqual(store.ingest_htf(bars), [])

    def test_two_bars_are_not_enough(self):
        store = FvgStore()
        self.assertEqual(store.ingest_htf(mk([(1, 2, 0, 1), (3, 4, 2, 3)], step=HTF_STEP)), [])

    def test_reingesting_the_same_bars_creates_nothing_new(self):
        bars = mk([(95, 100, 94, 99), (99, 104, 98, 103), (103, 110, 105, 109)],
                  step=HTF_STEP)
        store = FvgStore()
        store.ingest_htf(bars)
        store.ingest_htf(bars)
        self.assertEqual(len(store.bullish), 1)

    def test_new_bars_extend_the_set(self):
        bars = mk([(95, 100, 94, 99), (99, 104, 98, 103), (103, 110, 105, 109)],
                  step=HTF_STEP)
        store = FvgStore()
        store.ingest_htf(bars)
        more = bars + mk([(109, 118, 112, 117)], start=bars[-1].time + HTF_STEP, step=HTF_STEP)
        store.ingest_htf(more)
        self.assertEqual(len(store.bullish), 2)  # 112 > 104


class TestFvgPruning(unittest.TestCase):
    def setUp(self):
        self.store = FvgStore()
        self.store.ingest_htf(mk([(95, 100, 94, 99), (99, 104, 98, 103), (103, 110, 105, 109)],
                                 step=HTF_STEP))

    def test_a_gap_survives_a_partial_fill(self):
        self.store.prune(close=102)          # inside the gap, not through it
        self.assertEqual(len(self.store.bullish), 1)

    def test_a_gap_is_retired_once_price_closes_through_it(self):
        self.store.prune(close=99)           # below the 100 floor
        self.assertEqual(len(self.store.bullish), 0)

    def test_bearish_gaps_prune_upward(self):
        store = FvgStore()
        store.ingest_htf(mk([(110, 112, 105, 106), (106, 107, 101, 102), (102, 103, 96, 97)],
                            step=HTF_STEP))
        store.prune(close=104)
        self.assertEqual(len(store.bearish), 1)
        store.prune(close=106)
        self.assertEqual(len(store.bearish), 0)

    def test_the_store_is_bounded(self):
        store = FvgStore(max_zones=3)
        price = 100
        bars = []
        for _ in range(10):
            bars += [(price, price + 5, price - 1, price + 4),
                     (price + 4, price + 9, price + 3, price + 8),
                     (price + 8, price + 16, price + 10, price + 15)]
            price += 20
        store.ingest_htf(mk(bars, step=HTF_STEP))
        self.assertLessEqual(len(store.bullish), 3)


if __name__ == "__main__":
    unittest.main()
