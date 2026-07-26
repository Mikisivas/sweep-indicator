"""End-to-end: the same engine, wired through fills, costs and metrics."""

from __future__ import annotations

import unittest

from helpers import BULL_ROWS, mk
from ict_bot.backtest import Backtester, offline_spec
from ict_bot.config import Config
from ict_bot.models import Direction


def cfg(**strategy) -> Config:
    c = Config()
    c.strategy.swing_len = 2
    c.strategy.max_wait = 5
    c.strategy.chain_max = 10
    c.strategy.min_rr = 0.0            # the fixtures are tiny; R:R is not what we test here
    for key, value in strategy.items():
        setattr(c.strategy, key, value)
    c.risk.risk_pct = 1.0
    c.backtest.start_equity = 10_000
    c.backtest.spread_points = 0
    c.backtest.slippage_points = 0
    c.backtest.commission_per_lot = 0
    c.backtest.apply_session_filter = False
    c.backtest.apply_news_filter = False
    c.news.enabled = False
    return c


def run(rows, config=None):
    config = config or cfg()
    bars = mk(rows)
    tester = Backtester(config, spec=offline_spec(config))
    return tester.run(bars, [], 14400)


class TestEntryModel(unittest.TestCase):
    """NO RETEST: the fill is the confirmation bar's close, not the order block."""

    def test_trade_is_entered_on_the_confirmation_close(self):
        rows = BULL_ROWS + [(108.5, 109, 108, 109)]
        report = run(rows)
        self.assertEqual(len(report.trades), 1)
        trade = report.trades[0]
        self.assertIs(trade.direction, Direction.LONG)
        self.assertEqual(trade.entry, 108.5)                  # bar 8 close
        self.assertEqual(trade.entry_time, mk(rows)[8].time)
        self.assertEqual(trade.stop, 99.0)

    def test_price_never_returned_to_the_order_block(self):
        """Proof the retest rule would have missed this trade entirely."""
        rows = BULL_ROWS + [(108.5, 109, 108, 109)]
        bars = mk(rows)
        order_block = (108.0 + 99.0) / 2
        after_entry = bars[9:]
        self.assertTrue(all(b.low > order_block for b in after_entry))
        self.assertEqual(len(run(rows).trades), 1)

    def test_costs_are_charged_against_us(self):
        config = cfg()
        config.backtest.spread_points = 20     # 0.20 at point 0.01
        config.backtest.slippage_points = 10   # 0.10
        report = Backtester(config, spec=offline_spec(config)).run(
            mk(BULL_ROWS + [(108.5, 109, 108, 109)]), [], 14400)
        self.assertAlmostEqual(report.trades[0].entry, 108.8, places=6)


class TestExits(unittest.TestCase):
    def test_target_hit(self):
        report = run(BULL_ROWS + [(108.5, 109, 108, 109)])
        trade = report.trades[0]
        self.assertEqual(trade.outcome, "tp")
        self.assertEqual(trade.exit_price, 109.0)             # the opposing pivot high
        self.assertGreater(trade.profit, 0)

    def test_stop_hit(self):
        report = run(BULL_ROWS + [(108.5, 108.6, 98, 99)])
        trade = report.trades[0]
        self.assertEqual(trade.outcome, "sl")
        self.assertEqual(trade.exit_price, 99.0)
        self.assertLess(trade.profit, 0)
        self.assertAlmostEqual(trade.r_multiple, -1.0, places=2)

    def test_ambiguous_bar_assumes_the_stop_first(self):
        """One bar covering both levels must never be scored as a winner."""
        report = run(BULL_ROWS + [(108.5, 115, 95, 100)])
        self.assertEqual(report.trades[0].outcome, "sl")

    def test_open_trade_is_marked_out_at_the_last_close(self):
        report = run(BULL_ROWS + [(108.5, 108.8, 108, 108.6)])
        self.assertEqual(report.trades[0].outcome, "eod")

    def test_no_exit_check_on_the_entry_bar_itself(self):
        """Entry is at that bar's close, so its own range cannot stop us out."""
        rows = list(BULL_ROWS)
        rows[8] = (101, 108.5, 98.5, 108.5)   # low pierces the stop before the close
        report = run(rows + [(108.5, 109, 108, 109)])
        self.assertEqual(len(report.trades), 1)
        self.assertEqual(report.trades[0].outcome, "tp")


class TestSizingAndMetrics(unittest.TestCase):
    def test_position_is_sized_to_one_percent_of_equity(self):
        report = run(BULL_ROWS + [(108.5, 108.6, 98, 99)])
        trade = report.trades[0]
        risk_distance = 108.5 - 99.0                       # 9.5 index points
        # UT100 offline spec: 1 lot = 1 USD per point, so 100 USD / 9.5 = 10.52 lots
        self.assertAlmostEqual(trade.volume, 10.52)
        self.assertAlmostEqual(abs(trade.profit), risk_distance * trade.volume, places=6)
        self.assertLessEqual(abs(trade.profit), 100.0)     # never more than the budget

    def test_report_metrics(self):
        report = run(BULL_ROWS + [(108.5, 109, 108, 109)])
        self.assertEqual(report.sweeps, 1)
        self.assertEqual(report.confirmations, 1)
        self.assertEqual(report.win_rate, 100.0)
        self.assertEqual(report.profit_factor, float("inf"))
        self.assertGreater(report.end_equity, report.start_equity)
        self.assertIn("BACKTEST SUMMARY", report.summary())

    def test_drawdown_is_measured_peak_to_trough(self):
        report = run(BULL_ROWS + [(108.5, 108.6, 98, 99)])
        dd_abs, dd_pct = report.max_drawdown
        self.assertGreater(dd_abs, 0)
        self.assertLess(dd_pct, 2.0)

    def test_blocked_confirmations_are_counted_with_a_reason(self):
        config = cfg()
        config.strategy.min_rr = 5.0                       # nothing can pass this
        report = Backtester(config, spec=offline_spec(config)).run(
            mk(BULL_ROWS + [(108.5, 109, 108, 109)]), [], 14400)
        self.assertEqual(len(report.trades), 0)
        self.assertEqual(report.skipped, {"rr_below_min": 1})


class TestNoSignalCases(unittest.TestCase):
    def test_flat_market_produces_nothing(self):
        rows = [(100, 100.5, 99.5, 100)] * 60
        report = run(rows)
        self.assertEqual(report.trades, [])
        self.assertEqual(report.confirmations, 0)

    def test_invalidated_setups_never_trade(self):
        hovering = [(101, 105, 100, 102)] * 8               # never closes above 108
        report = run(BULL_ROWS[:8] + hovering)
        self.assertEqual(report.trades, [])
        self.assertEqual(report.sweeps, 1)
        self.assertEqual(report.invalidations, 1)


if __name__ == "__main__":
    unittest.main()
