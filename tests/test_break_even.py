"""Break-even: once a trade reaches +1.5R, the stop moves to entry.

The scenario is the standard bullish fixture: filled at 108.60 with the stop at
99.00, so one R is 9.60 points and +1.5R is a bid of 123.00.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from fake_mt5 import FakeMt5, h4_rows, rows_from
from helpers import BEAR_ROWS, BULL_ROWS
from ict_bot.config import Config
from ict_bot.mt5_client import session as session_module
from ict_bot.runner import LiveRunner

ENTRY = 108.60          # the ask when CISD confirmed
STOP = 99.00            # the swept low
RISK = ENTRY - STOP     # 9.60
BE_TRIGGER_BID = ENTRY + 1.5 * RISK   # 123.00


def build_config(tmp: str) -> Config:
    cfg = Config()
    cfg.mt5.server_utc_offset_hours = 0
    cfg.mt5.history_bars = 100
    cfg.mt5.htf_history_bars = 10
    cfg.strategy.swing_len = 2
    cfg.strategy.max_wait = 5
    cfg.strategy.chain_max = 10
    cfg.strategy.min_rr = 0.0
    cfg.news.enabled = False
    cfg.notify.telegram_bot_token = ""
    cfg.runtime.mode = "paper"
    cfg.runtime.state_file = os.path.join(tmp, "state.json")
    cfg.runtime.signals_csv = os.path.join(tmp, "signals.csv")
    cfg.runtime.log_file = ""
    return cfg


class BreakEvenHarness(unittest.TestCase):
    ROWS = BULL_ROWS
    OPEN_BID = 108.50
    OPEN_ASK = 108.60

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.cfg = build_config(self.tmp)
        self.fake = FakeMt5(rows_from(self.ROWS[:8] + [(101, 102, 100, 101)]), h4_rows(),
                            bid=self.OPEN_BID, ask=self.OPEN_ASK)
        self._real_api = session_module.mt5_api
        session_module.mt5_api = lambda: self.fake
        self.addCleanup(self._restore)

    def _restore(self):
        session_module.mt5_api = self._real_api
        self._tmp.cleanup()

    def open_trade(self) -> LiveRunner:
        """Start the bot and let the CISD bar close, producing one position."""
        bot = LiveRunner(self.cfg)
        bot.start()
        self.fake.m15 = rows_from(self.ROWS[:9] + [(108.5, 109, 108, 108.7)])
        bot.tick_once()
        self.assertEqual(len(self.fake.positions), 1, "setup should have opened a trade")
        self.addCleanup(bot.stop)
        return bot

    @property
    def position(self):
        return self.fake.positions[0]


class TestBreakEvenTrigger(BreakEvenHarness):
    def test_entry_and_risk_are_what_we_think(self):
        self.open_trade()
        self.assertEqual(self.position.price_open, ENTRY)
        self.assertEqual(self.position.sl, STOP)

    def test_no_move_below_the_threshold(self):
        bot = self.open_trade()
        self.fake.move_price(ENTRY + 1.4 * RISK)      # +1.40R
        bot.tick_once()
        self.assertEqual(self.fake.sltp_requests(), [])
        self.assertEqual(self.position.sl, STOP)

    def test_moves_exactly_at_1_5r(self):
        bot = self.open_trade()
        self.fake.move_price(BE_TRIGGER_BID)          # +1.50R
        bot.tick_once()
        self.assertEqual(len(self.fake.sltp_requests()), 1)
        self.assertEqual(self.position.sl, ENTRY, "stop should sit exactly at entry")

    def test_take_profit_is_preserved(self):
        bot = self.open_trade()
        original_tp = self.position.tp
        self.fake.move_price(BE_TRIGGER_BID)
        bot.tick_once()
        self.assertEqual(self.position.tp, original_tp)

    def test_progress_is_measured_at_the_bid_not_the_ask(self):
        """A wide spread must not fake the trade into break-even early."""
        bot = self.open_trade()
        self.fake.bid = ENTRY + 1.4 * RISK            # 1.40R on the bid
        self.fake.ask = ENTRY + 1.8 * RISK            # 1.80R if you wrongly used ask
        bot.tick_once()
        self.assertEqual(self.fake.sltp_requests(), [],
                         "the spread is not profit and must not trigger break-even")

    def test_it_only_fires_once(self):
        bot = self.open_trade()
        self.fake.move_price(BE_TRIGGER_BID)
        bot.tick_once()
        self.fake.move_price(BE_TRIGGER_BID + 20)
        bot.tick_once()
        bot.tick_once()
        self.assertEqual(len(self.fake.sltp_requests()), 1)

    def test_offset_locks_a_few_points(self):
        self.cfg.management.break_even_offset_points = 50   # 0.50 at point 0.01
        bot = self.open_trade()
        self.fake.move_price(BE_TRIGGER_BID)
        bot.tick_once()
        self.assertEqual(self.position.sl, round(ENTRY + 0.50, 2))

    def test_disabled_does_nothing(self):
        self.cfg.management.break_even_enabled = False
        bot = self.open_trade()
        self.fake.move_price(BE_TRIGGER_BID + 50)
        bot.tick_once()
        self.assertEqual(self.fake.sltp_requests(), [])

    def test_custom_threshold(self):
        self.cfg.management.break_even_at_r = 3.0
        bot = self.open_trade()
        self.fake.move_price(ENTRY + 2.0 * RISK)
        bot.tick_once()
        self.assertEqual(self.fake.sltp_requests(), [])
        self.fake.move_price(ENTRY + 3.0 * RISK)
        bot.tick_once()
        self.assertEqual(len(self.fake.sltp_requests()), 1)

    def test_stop_is_never_moved_backwards(self):
        """If the stop is already better than entry, leave it alone."""
        bot = self.open_trade()
        self.position.sl = ENTRY + 5.0        # already locked in profit
        self.fake.move_price(BE_TRIGGER_BID)
        bot.tick_once()
        self.assertEqual(self.fake.sltp_requests(), [])
        self.assertEqual(self.position.sl, ENTRY + 5.0)


class TestBreakEvenShort(BreakEvenHarness):
    ROWS = BEAR_ROWS
    OPEN_BID = 91.50      # a short fills at the bid
    OPEN_ASK = 91.60

    def test_short_moves_to_break_even_measured_at_the_ask(self):
        bot = self.open_trade()
        entry = self.position.price_open
        risk = self.position.sl - entry
        self.assertGreater(risk, 0)

        # 1.40R on the ask -> no move
        self.fake.ask = entry - 1.4 * risk
        self.fake.bid = self.fake.ask - 0.10
        bot.tick_once()
        self.assertEqual(self.fake.sltp_requests(), [])

        # 1.50R on the ask -> break-even
        self.fake.ask = entry - 1.5 * risk
        self.fake.bid = self.fake.ask - 0.10
        bot.tick_once()
        self.assertEqual(len(self.fake.sltp_requests()), 1)
        self.assertEqual(self.position.sl, entry)


class TestBreakEvenPersistence(BreakEvenHarness):
    def test_original_risk_survives_a_restart(self):
        """After the stop moves to entry the broker no longer knows the risk;
        the state file must."""
        bot = self.open_trade()
        self.fake.move_price(BE_TRIGGER_BID)
        bot.tick_once()
        bot.stop()

        again = LiveRunner(self.cfg)
        again.start()
        record = again.state.managed()[self.position.ticket]
        self.assertEqual(record.original_stop, STOP)
        self.assertEqual(record.risk_distance, RISK)
        self.assertTrue(record.break_even_done)

        again.tick_once()
        self.assertEqual(len(self.fake.sltp_requests()), 1, "must not re-apply after restart")
        again.stop()

    def test_a_position_adopted_after_a_lost_state_file_is_still_managed(self):
        bot = self.open_trade()
        bot.stop()
        os.remove(bot.state_path)

        again = LiveRunner(self.cfg)
        again.start()
        record = again.state.managed()[self.position.ticket]
        self.assertEqual(record.entry, ENTRY)
        self.assertEqual(record.original_stop, STOP)
        self.assertFalse(record.break_even_done)
        again.stop()

    def test_records_are_dropped_when_the_position_closes(self):
        bot = self.open_trade()
        ticket = self.position.ticket
        self.assertIn(ticket, bot.state.managed())
        self.fake.positions.clear()
        bot.tick_once()
        self.assertEqual(bot.state.managed(), {})


if __name__ == "__main__":
    unittest.main()
