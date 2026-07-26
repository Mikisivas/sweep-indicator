"""The live path end to end, against a fake terminal.

Covers what unit tests cannot: warm-up placing no orders, a confirmation on a new
closed bar turning into a real order_send, and every gate (news, spread, kill
switch, exposure) actually vetoing it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fake_mt5 import FakeMt5, h4_rows, rows_from
from helpers import BULL_ROWS
from ict_bot import runner as runner_module
from ict_bot.config import Config
from ict_bot.mt5_client import session as session_module
from ict_bot.news import CalendarEvent, NewsFilter
from ict_bot.runner import LiveRunner


class StaticSource:
    def __init__(self, events):
        self.events = events

    def fetch(self):
        return self.events


def build_config(tmp: str) -> Config:
    cfg = Config()
    cfg.mt5.symbol = "UT100"
    cfg.mt5.server_utc_offset_hours = 0
    cfg.mt5.history_bars = 100
    cfg.mt5.htf_history_bars = 10
    cfg.strategy.swing_len = 2
    cfg.strategy.max_wait = 5
    cfg.strategy.chain_max = 10
    cfg.strategy.min_rr = 0.0
    cfg.risk.risk_pct = 1.0
    cfg.news.enabled = False
    cfg.runtime.mode = "paper"
    cfg.runtime.state_file = os.path.join(tmp, "state.json")
    cfg.runtime.signals_csv = os.path.join(tmp, "signals.csv")
    cfg.runtime.log_file = ""
    return cfg


class RunnerHarness(unittest.TestCase):
    """Warm up through the sweep (bars 0-7), then close the CISD bar (bar 8)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.cfg = build_config(self.tmp)
        # bars 0..7 closed, plus a forming bar the feed must discard
        self.fake = FakeMt5(rows_from(BULL_ROWS[:8] + [(101, 102, 100, 101)]), h4_rows())
        self._real_api = session_module.mt5_api
        session_module.mt5_api = lambda: self.fake
        self.addCleanup(self._restore)

    def _restore(self):
        session_module.mt5_api = self._real_api
        self._tmp.cleanup()

    def start_runner(self) -> LiveRunner:
        bot = LiveRunner(self.cfg)
        bot.start()
        return bot

    def close_the_cisd_bar(self):
        """Replace the forming bar with the real bar 8, plus a new forming bar."""
        self.fake.m15 = rows_from(BULL_ROWS[:9] + [(108.5, 109, 108, 108.7)])


class TestWarmUp(RunnerHarness):
    def test_warm_up_places_no_orders(self):
        bot = self.start_runner()
        self.assertEqual(self.fake.requests, [])
        self.assertIsNotNone(bot.engine.bull, "the sweep should be armed after replay")
        bot.stop()

    def test_watermark_starts_at_the_last_closed_bar(self):
        bot = self.start_runner()
        self.assertEqual(bot.watermark, self.fake.m15[-2]["time"])
        bot.stop()

    def test_forming_bar_is_never_fed_to_the_engine(self):
        bot = self.start_runner()
        self.assertEqual(bot.engine.bars[-1].time, self.fake.m15[-2]["time"])
        bot.stop()

    def test_paper_mode_refuses_a_real_account(self):
        self.fake.trade_mode = FakeMt5.ACCOUNT_TRADE_MODE_REAL
        with self.assertRaises(RuntimeError) as ctx:
            self.start_runner()
        self.assertIn("REAL account", str(ctx.exception))


class TestEntry(RunnerHarness):
    def test_confirmation_sends_a_market_order_immediately(self):
        bot = self.start_runner()
        self.close_the_cisd_bar()
        bot.tick_once()

        deals = self.fake.deals()
        self.assertEqual(len(deals), 1, "expected exactly one market order")
        request = deals[0]
        self.assertEqual(request["type"], FakeMt5.ORDER_TYPE_BUY)
        self.assertEqual(request["price"], self.fake.ask)      # market, not the order block
        self.assertEqual(request["sl"], 99.0)                  # the protected swept low
        self.assertEqual(request["tp"], 109.0)                 # opposing liquidity
        self.assertEqual(request["magic"], self.cfg.mt5.magic)
        self.assertEqual(request["type_filling"], FakeMt5.ORDER_FILLING_FOK)
        bot.stop()

    def test_it_does_not_wait_for_a_retest(self):
        """Entry is above the order block, which price never returned to."""
        bot = self.start_runner()
        order_block = (108.0 + 99.0) / 2
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertGreater(self.fake.deals()[0]["price"], order_block)
        bot.stop()

    def test_volume_is_one_percent_of_equity_over_the_stop_distance(self):
        bot = self.start_runner()
        self.close_the_cisd_bar()
        bot.tick_once()
        # equity 10,000 -> 100 USD risk; stop distance 108.60 - 99.00 = 9.60
        self.assertAlmostEqual(self.fake.deals()[0]["volume"], 10.41, places=2)
        bot.stop()

    def test_sizing_works_even_though_tick_value_reads_zero(self):
        """UT100's panel reports tick value 0; order_calc_profit must carry it."""
        bot = self.start_runner()
        self.assertEqual(bot.session.spec.tick_value, 0.0)
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertTrue(self.fake.deals())
        bot.stop()

    def test_the_bar_is_watermarked_so_it_cannot_fire_twice(self):
        bot = self.start_runner()
        self.close_the_cisd_bar()
        bot.tick_once()
        bot.tick_once()
        self.assertEqual(len(self.fake.deals()), 1)
        bot.stop()

    def test_signal_journal_records_the_entry(self):
        bot = self.start_runner()
        self.close_the_cisd_bar()
        bot.tick_once()
        bot.stop()
        with open(self.cfg.runtime.signals_csv, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("confirmed", content)
        self.assertIn("entered", content)


class TestGates(RunnerHarness):
    def _blocked(self, bot) -> str:
        with open(self.cfg.runtime.signals_csv, encoding="utf-8") as fh:
            rows = [line for line in fh if ",blocked," in line]
        return rows[0] if rows else ""

    def test_news_blackout_stops_the_entry(self):
        self.cfg.news.enabled = True
        bot = LiveRunner(self.cfg)
        now = datetime.now(timezone.utc)
        bot.news = NewsFilter(self.cfg.news, source=StaticSource([
            CalendarEvent(now + timedelta(minutes=2), "USD", "high", "CPI m/m")]))
        bot.start()
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertEqual(self.fake.deals(), [], "no order may be sent inside the blackout")
        self.assertIn("news blackout", self._blocked(bot))
        bot.stop()

    def test_an_event_outside_the_window_does_not_block(self):
        self.cfg.news.enabled = True
        bot = LiveRunner(self.cfg)
        now = datetime.now(timezone.utc)
        bot.news = NewsFilter(self.cfg.news, source=StaticSource([
            CalendarEvent(now + timedelta(minutes=30), "USD", "high", "CPI m/m")]))
        bot.start()
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertEqual(len(self.fake.deals()), 1)
        bot.stop()

    def test_a_missing_calendar_blocks_by_default(self):
        self.cfg.news.enabled = True
        self.cfg.news.path = os.path.join(self.tmp, "nope.csv")
        bot = self.start_runner()
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertEqual(self.fake.deals(), [])
        self.assertIn("fail safe", self._blocked(bot))
        bot.stop()

    def test_wide_spread_blocks_the_entry(self):
        self.cfg.risk.max_spread_points = 5
        self.fake.ask = self.fake.bid + 1.0          # 100 points at point 0.01
        bot = self.start_runner()
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertEqual(self.fake.deals(), [])
        self.assertIn("spread", self._blocked(bot))
        bot.stop()

    def test_an_existing_position_blocks_a_second_entry(self):
        bot = self.start_runner()
        self.fake.positions.append(type("P", (), {
            "ticket": 1, "type": FakeMt5.POSITION_TYPE_BUY, "volume": 1.0,
            "price_open": 100.0, "sl": 99.0, "tp": 110.0, "profit": 0.0,
            "time": 0, "magic": self.cfg.mt5.magic, "comment": "",
        })())
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertEqual(self.fake.deals(), [])
        bot.stop()

    def test_the_kill_switch_blocks_the_entry(self):
        bot = self.start_runner()
        bot.risk.book.halted = True
        bot.risk.book.halt_reason = "down 3.10% today"
        bot.risk.book.day = bot.session.server_now().date()
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertEqual(self.fake.deals(), [])
        self.assertIn("kill switch", self._blocked(bot))
        bot.stop()

    def test_min_rr_blocks_a_poor_setup(self):
        self.cfg.strategy.min_rr = 3.0
        bot = self.start_runner()
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertEqual(self.fake.deals(), [])
        self.assertIn("R:R", self._blocked(bot))
        bot.stop()

    def test_positions_from_other_bots_are_ignored(self):
        """A foreign magic number must not count towards our exposure."""
        bot = self.start_runner()
        self.fake.positions.append(type("P", (), {
            "ticket": 2, "type": FakeMt5.POSITION_TYPE_BUY, "volume": 1.0,
            "price_open": 100.0, "sl": 0.0, "tp": 0.0, "profit": 0.0,
            "time": 0, "magic": 111111, "comment": "someone else",
        })())
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertEqual(len(self.fake.deals()), 1)
        bot.stop()


class TestFailures(RunnerHarness):
    def test_a_rejected_order_is_logged_not_retried_forever(self):
        self.fake.forced_retcode = 10019          # not enough money
        bot = self.start_runner()
        self.close_the_cisd_bar()
        bot.tick_once()
        self.assertEqual(len(self.fake.deals()), 1, "a hard rejection must not be retried")
        with open(self.cfg.runtime.signals_csv, encoding="utf-8") as fh:
            self.assertIn("order_failed", fh.read())
        bot.stop()

    def test_state_survives_a_restart_without_re_entering(self):
        bot = self.start_runner()
        self.close_the_cisd_bar()
        bot.tick_once()
        bot.stop()
        self.assertEqual(len(self.fake.deals()), 1)

        again = LiveRunner(self.cfg)
        again.start()
        again.tick_once()
        self.assertEqual(len(self.fake.deals()), 1, "restart must not re-enter the same setup")
        again.stop()

    def test_stop_is_safe_when_start_never_ran(self):
        LiveRunner(self.cfg).stop()      # must not raise


if __name__ == "__main__":
    unittest.main()
