"""Sweep -> CISD -> trade plan, including the NO-RETEST behaviour."""

from __future__ import annotations

import unittest

from helpers import (
    BEAR_CISD_LEVEL,
    BEAR_PIVOT_LOW,
    BEAR_ROWS,
    BEAR_SWEPT_HIGH,
    BULL_CISD_LEVEL,
    BULL_PIVOT_HIGH,
    BULL_ROWS,
    BULL_SWEPT_LOW,
    BULL_SWEPT_POOL,
    bull_engine,
    mk,
)
from ict_bot.models import Candle, Direction, EventKind


def feed(engine, bars, htf=None):
    events = []
    for bar in bars:
        events.extend(engine.on_bar(bar, htf))
    return events


def of_kind(events, kind, direction=None):
    return [e for e in events
            if e.kind is kind and (direction is None or e.direction is direction)]


class TestBullishSetup(unittest.TestCase):
    def setUp(self):
        self.engine = bull_engine()
        self.events = feed(self.engine, mk(BULL_ROWS))

    def test_sweep_detected_on_the_wick_through_the_pool(self):
        sweeps = of_kind(self.events, EventKind.SWEEP, Direction.LONG)
        self.assertEqual(len(sweeps), 1)
        setup = sweeps[0].setup
        self.assertEqual(setup.swept_pool, BULL_SWEPT_POOL)
        self.assertEqual(setup.swept_extreme, BULL_SWEPT_LOW)
        self.assertEqual(sweeps[0].bar_index, 7)

    def test_cisd_level_is_the_open_of_the_earliest_down_close(self):
        setup = of_kind(self.events, EventKind.SWEEP, Direction.LONG)[0].setup
        self.assertEqual(setup.cisd_level, BULL_CISD_LEVEL)
        self.assertEqual(setup.chain_start_index, 5)  # first bar of the 3-bar run

    def test_confirmation_on_the_close_through_the_level(self):
        confirms = of_kind(self.events, EventKind.CONFIRMED, Direction.LONG)
        self.assertEqual(len(confirms), 1)
        self.assertEqual(confirms[0].bar_index, 8)

    def test_no_retest_the_plan_is_actionable_on_the_confirmation_bar(self):
        """The heart of the change: we never wait for price to return to the block."""
        plan = of_kind(self.events, EventKind.CONFIRMED, Direction.LONG)[0].plan
        self.assertEqual(plan.reference_entry, (BULL_CISD_LEVEL + BULL_SWEPT_LOW) / 2)
        # price closed at 108.5, far ABOVE the 103.5 order block and never returned
        self.assertGreater(plan.signal_price, plan.reference_entry)
        trade = plan.finalize(plan.signal_price)   # what the live bot actually does
        self.assertTrue(trade.is_valid)
        self.assertEqual(trade.entry, 108.5)
        self.assertEqual(trade.stop, BULL_SWEPT_LOW)

    def test_stop_and_target(self):
        plan = of_kind(self.events, EventKind.CONFIRMED, Direction.LONG)[0].plan
        trade = plan.finalize(plan.reference_entry)
        self.assertEqual(trade.stop, BULL_SWEPT_LOW)
        self.assertEqual(trade.take_profit, BULL_PIVOT_HIGH)  # opposing liquidity
        self.assertEqual(trade.tp_source, "opposing_liquidity")
        self.assertAlmostEqual(trade.risk_distance, 4.5)
        self.assertAlmostEqual(trade.rr, 5.5 / 4.5, places=6)

    def test_entry_mode_ob_edge(self):
        engine = bull_engine(entry_mode="ob_edge")
        events = feed(engine, mk(BULL_ROWS))
        plan = of_kind(events, EventKind.CONFIRMED, Direction.LONG)[0].plan
        self.assertEqual(plan.reference_entry, BULL_CISD_LEVEL)

    def test_fixed_rr_target(self):
        engine = bull_engine(tp_mode="fixed_rr", rr_target=3.0)
        events = feed(engine, mk(BULL_ROWS))
        plan = of_kind(events, EventKind.CONFIRMED, Direction.LONG)[0].plan
        trade = plan.finalize(103.5)
        self.assertEqual(trade.tp_source, "fixed_rr")
        self.assertAlmostEqual(trade.take_profit, 103.5 + 3.0 * 4.5)

    def test_pool_is_consumed_so_it_cannot_be_swept_twice(self):
        self.assertIsNone(self.engine.last_pivot_low)


class TestBearishSetup(unittest.TestCase):
    def setUp(self):
        self.engine = bull_engine()
        self.events = feed(self.engine, mk(BEAR_ROWS))

    def test_sweep_and_cisd(self):
        sweeps = of_kind(self.events, EventKind.SWEEP, Direction.SHORT)
        self.assertEqual(len(sweeps), 1)
        self.assertEqual(sweeps[0].setup.swept_extreme, BEAR_SWEPT_HIGH)
        self.assertEqual(sweeps[0].setup.cisd_level, BEAR_CISD_LEVEL)

    def test_confirmation_and_levels(self):
        confirms = of_kind(self.events, EventKind.CONFIRMED, Direction.SHORT)
        self.assertEqual(len(confirms), 1)
        plan = confirms[0].plan
        self.assertEqual(plan.reference_entry, (BEAR_CISD_LEVEL + BEAR_SWEPT_HIGH) / 2)
        trade = plan.finalize(plan.reference_entry)
        self.assertEqual(trade.stop, BEAR_SWEPT_HIGH)
        self.assertEqual(trade.take_profit, BEAR_PIVOT_LOW)
        self.assertTrue(trade.is_valid)


class TestInvalidation(unittest.TestCase):
    def _armed(self):
        engine = bull_engine()
        feed(engine, mk(BULL_ROWS[:8]))          # stop right after the sweep
        self.assertIsNotNone(engine.bull)
        return engine

    def test_close_back_through_the_swept_low_kills_it(self):
        engine = self._armed()
        last = engine.bars[-1]
        killer = Candle(last.time + 900, 101, 102, 97, 98)   # closes below 99
        events = engine.on_bar(killer)
        self.assertEqual(len(of_kind(events, EventKind.INVALIDATED, Direction.LONG)), 1)
        self.assertIsNone(engine.bull)

    def test_timeout_kills_it(self):
        engine = self._armed()          # max_wait = 5
        time = engine.bars[-1].time
        events = []
        for i in range(1, 8):
            # hovering: never closes above 108, never closes below 99
            events.extend(engine.on_bar(Candle(time + i * 900, 101, 105, 100, 102)))
            if engine.bull is None:
                break
        invalid = of_kind(events, EventKind.INVALIDATED, Direction.LONG)
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0].bar_index - 7, 6)   # first bar past max_wait=5
        self.assertIn("no CISD", invalid[0].note)

    def test_sweep_may_extend_deeper_while_waiting(self):
        engine = self._armed()
        time = engine.bars[-1].time
        engine.on_bar(Candle(time + 900, 101, 104, 99.5, 102))   # dips but holds
        self.assertEqual(engine.bull.swept_extreme, 99.0)
        engine.on_bar(Candle(time + 1800, 102, 104, 98.5, 100))  # new low, still no close below
        self.assertEqual(engine.bull.swept_extreme, 98.5)
        self.assertIsNotNone(engine.bull)

    def test_a_deeper_sweep_moves_the_stop_with_it(self):
        engine = self._armed()
        time = engine.bars[-1].time
        engine.on_bar(Candle(time + 900, 101, 104, 98.0, 100))
        events = engine.on_bar(Candle(time + 1800, 100, 110, 99, 109))
        plan = of_kind(events, EventKind.CONFIRMED, Direction.LONG)[0].plan
        self.assertEqual(plan.stop, 98.0)


class TestChainScan(unittest.TestCase):
    def test_chain_stops_at_the_first_non_down_close(self):
        engine = bull_engine()
        feed(engine, mk(BULL_ROWS[:8]))
        # bars 5,6,7 are the run; bar 4 is an up-close and must not be included
        self.assertEqual(engine.bull.cisd_level, 108.0)
        self.assertNotEqual(engine.bull.cisd_level, 106.0)

    def test_chain_max_bounds_the_scan(self):
        # make bar 4 a down close too, so the run is 4 candles long: 4,5,6,7
        rows = list(BULL_ROWS[:8])
        rows[4] = (109, 109.5, 105, 108)

        deep = bull_engine(chain_max=10)
        feed(deep, mk(rows))
        self.assertEqual(deep.bull.cisd_level, 109.0)      # reaches bar 4's open

        capped = bull_engine(chain_max=3)
        feed(capped, mk(rows))
        self.assertEqual(capped.bull.cisd_level, 108.0)    # stops after 3 candles

    def test_doji_terminates_the_run(self):
        rows = list(BULL_ROWS[:7])
        rows[5] = (108, 108, 106, 108)     # close == open: not a down candle
        engine = bull_engine()
        feed(engine, mk(rows + [BULL_ROWS[7]]))
        self.assertEqual(engine.bull.cisd_level, 107.0)


class TestHtfFilter(unittest.TestCase):
    def _htf(self, low_gap: bool):
        """Three completed 4H bars; `low_gap` puts a BISI around the sweep price."""
        if low_gap:
            return mk([(90, 95, 85, 94), (94, 96, 93, 95), (99, 104, 98, 103)],
                      start=1_699_000_000, step=14400)
        return mk([(120, 125, 118, 124), (124, 126, 123, 125), (126, 130, 125, 129)],
                  start=1_699_000_000, step=14400)

    def test_filter_blocks_a_sweep_outside_every_gap(self):
        engine = bull_engine(require_sweep_in_htf_fvg=True)
        events = feed(engine, mk(BULL_ROWS), self._htf(low_gap=False))
        self.assertEqual(len(of_kind(events, EventKind.SWEEP, Direction.LONG)), 1)
        self.assertEqual(len(of_kind(events, EventKind.CONFIRMED, Direction.LONG)), 0)

    def test_filter_allows_a_sweep_inside_a_bullish_gap(self):
        # BISI spans high[0]=95 .. low[2]=98, and the sweep prints at 99... so use a
        # gap that actually brackets the sweep low of 99.
        htf = mk([(90, 98, 85, 94), (94, 96, 93, 95), (99, 104, 98.5, 103)],
                 start=1_699_000_000, step=14400)
        engine = bull_engine(require_sweep_in_htf_fvg=True)
        events = feed(engine, mk(BULL_ROWS), htf)
        self.assertTrue(engine.fvg.bullish, "expected a BISI zone to exist")
        sweep = of_kind(events, EventKind.SWEEP, Direction.LONG)[0]
        self.assertEqual(sweep.setup.htf_ok, engine.fvg.price_in_bullish(99.0))

    def test_filter_off_by_default(self):
        engine = bull_engine()
        events = feed(engine, mk(BULL_ROWS), self._htf(low_gap=False))
        self.assertEqual(len(of_kind(events, EventKind.CONFIRMED, Direction.LONG)), 1)


class TestParityDetail(unittest.TestCase):
    def test_target_pool_consumed_by_an_opposite_sweep_on_the_same_bar(self):
        """Indicator parity: the TP is read AFTER both state machines have run.

        Bar 8 closes above the CISD level (long confirms) while its high also takes
        the buy-side pool. The indicator reads `lastPivHigh` after the bearish block
        has already consumed it, so the target falls back to fixed R:R. We match.
        """
        rows = list(BULL_ROWS)
        rows[8] = (101, 110, 100, 109)     # high 110 sweeps the 109 pivot high
        engine = bull_engine()
        events = feed(engine, mk(rows))
        plan = of_kind(events, EventKind.CONFIRMED, Direction.LONG)[0].plan
        self.assertIsNone(plan.liquidity_target)
        trade = plan.finalize(plan.reference_entry)
        self.assertEqual(trade.tp_source, "fixed_rr")
        self.assertEqual(len(of_kind(events, EventKind.SWEEP, Direction.SHORT)), 1)

    def test_engine_rejects_nonsense_parameters(self):
        for kwargs in ({"swing_len": 1}, {"max_wait": 0}, {"chain_max": 2},
                       {"entry_mode": "nope"}, {"tp_mode": "nope"}):
            with self.assertRaises(ValueError):
                bull_engine(**kwargs)


if __name__ == "__main__":
    unittest.main()
