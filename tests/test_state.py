"""Persistence: a restart must never double-enter or lose the kill switch."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date

from helpers import mk  # noqa: F401  (sys.path)
from ict_bot.risk import DailyBook
from ict_bot.state import BotState, PendingLimit, find_state_path


class TestStateRoundTrip(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            state = BotState(symbol="UT100", last_acted_bar_time=1_700_000_900)
            state.save(path)
            loaded = BotState.load(path, "UT100")
            self.assertEqual(loaded.last_acted_bar_time, 1_700_000_900)

    def test_missing_file_starts_clean(self):
        state = BotState.load("/nonexistent/state.json", "UT100")
        self.assertEqual(state.last_acted_bar_time, 0)

    def test_a_state_file_for_another_symbol_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            BotState(symbol="EURUSD", last_acted_bar_time=99).save(path)
            self.assertEqual(BotState.load(path, "UT100").last_acted_bar_time, 0)

    def test_corrupt_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(BotState.load(path, "UT100").last_acted_bar_time, 0)

    def test_version_mismatch_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"version": 999, "symbol": "UT100", "last_acted_bar_time": 5}, fh)
            self.assertEqual(BotState.load(path, "UT100").last_acted_bar_time, 0)

    def test_writes_leave_no_temp_files_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            BotState(symbol="UT100").save(path)
            BotState(symbol="UT100").save(path)
            self.assertEqual(os.listdir(tmp), ["state.json"])

    def test_paths_are_namespaced_per_symbol(self):
        self.assertEqual(find_state_path("state/bot.json", "UT100"), "state/bot_UT100.json")
        self.assertTrue(find_state_path("", "UT100").endswith("ict_bot_UT100.json"))


class TestBookPersistence(unittest.TestCase):
    def test_kill_switch_survives_a_restart(self):
        book = DailyBook(day=date(2026, 7, 30), start_equity=10_000, trades=3,
                         realised=-350.0, halted=True, halt_reason="down 3.50% today")
        state = BotState(symbol="UT100")
        state.absorb_book(book)

        restored = DailyBook()
        state.restore_book(restored)
        self.assertTrue(restored.halted)
        self.assertEqual(restored.trades, 3)
        self.assertEqual(restored.day, date(2026, 7, 30))

    def test_an_empty_snapshot_leaves_the_book_alone(self):
        book = DailyBook()
        BotState(symbol="UT100").restore_book(book)
        self.assertIsNone(book.day)


class TestPendingLimits(unittest.TestCase):
    def test_add_and_drop(self):
        state = BotState(symbol="UT100")
        state.add_pending(PendingLimit(ticket=1, direction="long", placed_bar_time=0,
                                       expires_bar_time=900, entry=1.0, stop=0.5,
                                       take_profit=2.0, volume=0.1))
        self.assertEqual(len(state.pendings()), 1)
        self.assertEqual(state.pendings()[0].ticket, 1)
        state.drop_pending(1)
        self.assertEqual(state.pendings(), [])


if __name__ == "__main__":
    unittest.main()
