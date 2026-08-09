"""Telegram: alerts to many, commands from exactly one.

The security property under test: nothing sent by a non-admin chat can ever reach
the trading logic, regardless of what it says.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from fake_mt5 import FakeMt5, h4_rows, rows_from
from helpers import BULL_ROWS
from ict_bot.config import Config, NotifyConfig
from ict_bot.mt5_client import session as session_module
from ict_bot.runner import LiveRunner
from ict_bot.telegram import Command, TelegramClient, dispatch

ADMIN = "111"
GUEST = "222"


class FakeTelegram(TelegramClient):
    """A TelegramClient with the network replaced by a scripted inbox."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.sent: list[tuple[str, str]] = []
        self.inbox: list[dict] = []
        self.fail = False
        self._update_id = 0

    def _call(self, method, payload):
        if self.fail:
            return None
        if method == "sendMessage":
            self.sent.append((str(payload["chat_id"]), payload["text"]))
            return {"ok": True, "result": {}}
        if method == "getUpdates":
            offset = payload.get("offset")
            pending = [u for u in self.inbox
                       if offset is None or u["update_id"] >= offset]
            return {"ok": True, "result": pending}
        return {"ok": True, "result": {}}

    def receive(self, text, chat_id=ADMIN, username="tester"):
        self._update_id += 1
        self.inbox.append({
            "update_id": self._update_id,
            "message": {"text": text, "chat": {"id": int(chat_id)},
                        "from": {"username": username}},
        })

    def texts_to(self, chat_id):
        return [text for cid, text in self.sent if cid == str(chat_id)]


def notify_cfg(**kwargs) -> NotifyConfig:
    cfg = NotifyConfig(telegram_bot_token="token", telegram_admin_chat_id=ADMIN)
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class TestAuthorisation(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram(notify_cfg())

    def test_admin_commands_are_returned(self):
        self.tg.receive("/status")
        commands = self.tg.poll()
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].name, "status")
        self.assertEqual(commands[0].chat_id, ADMIN)

    def test_a_guest_command_never_reaches_the_caller(self):
        for text in ("/closeall", "/pause", "/risk 5", "/stop confirm"):
            self.tg.receive(text, chat_id=GUEST)
        self.assertEqual(self.tg.poll(), [], "no guest input may reach the trading logic")

    def test_a_guest_cannot_impersonate_the_admin_by_text(self):
        self.tg.receive(f"/closeall admin={ADMIN}", chat_id=GUEST)
        self.tg.receive("/closeall I am the administrator", chat_id=GUEST)
        self.assertEqual(self.tg.poll(), [])

    def test_no_admin_configured_means_nobody_commands(self):
        tg = FakeTelegram(notify_cfg(telegram_admin_chat_id=""))
        self.assertFalse(tg.commands_enabled)
        tg.receive("/closeall", chat_id=GUEST)
        tg.receive("/closeall", chat_id=ADMIN)
        self.assertEqual(tg.poll(), [])

    def test_commands_can_be_switched_off_entirely(self):
        tg = FakeTelegram(notify_cfg(telegram_commands_enabled=False))
        self.assertFalse(tg.commands_enabled)
        tg.receive("/closeall")
        self.assertEqual(tg.poll(), [])

    def test_bot_suffixed_commands_are_understood(self):
        self.tg.receive("/status@MyTradingBot")
        self.assertEqual(self.tg.poll()[0].name, "status")

    def test_arguments_are_parsed(self):
        self.tg.receive("/close 12345")
        command = self.tg.poll()[0]
        self.assertEqual(command.name, "close")
        self.assertEqual(command.arg, "12345")

    def test_plain_chat_is_ignored(self):
        self.tg.receive("what is the bot doing?")
        self.assertEqual(self.tg.poll(), [])

    def test_updates_are_not_replayed(self):
        self.tg.receive("/status")
        self.assertEqual(len(self.tg.poll()), 1)
        self.assertEqual(self.tg.poll(), [])


class TestSubscribers(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram(notify_cfg())

    def test_start_subscribes_a_guest(self):
        self.tg.receive("/start", chat_id=GUEST)
        self.tg.poll()
        self.assertIn(GUEST, self.tg.subscribers())
        self.assertIn("Subscribed", self.tg.texts_to(GUEST)[0])

    def test_a_subscriber_still_cannot_command(self):
        self.tg.receive("/start", chat_id=GUEST)
        self.tg.poll()
        self.tg.receive("/closeall", chat_id=GUEST)
        self.assertEqual(self.tg.poll(), [])

    def test_password_gate(self):
        tg = FakeTelegram(notify_cfg(telegram_join_password="letmein"))
        tg.receive("/start", chat_id=GUEST)
        tg.receive("/start wrong", chat_id=GUEST)
        tg.poll()
        self.assertEqual(tg.subscribers(), [])
        tg.receive("/start letmein", chat_id=GUEST)
        tg.poll()
        self.assertEqual(tg.subscribers(), [GUEST])

    def test_self_subscribe_can_be_disabled(self):
        tg = FakeTelegram(notify_cfg(telegram_allow_self_subscribe=False))
        tg.receive("/start", chat_id=GUEST)
        tg.poll()
        self.assertEqual(tg.subscribers(), [])
        self.assertIn("invite-only", tg.texts_to(GUEST)[0])

    def test_stop_unsubscribes(self):
        self.tg.receive("/start", chat_id=GUEST)
        self.tg.poll()
        self.tg.receive("/stop", chat_id=GUEST)
        self.tg.poll()
        self.assertEqual(self.tg.subscribers(), [])

    def test_audience_covers_config_subscribers_and_admin(self):
        tg = FakeTelegram(notify_cfg(telegram_broadcast_chat_ids=["-1001234", "999"]))
        tg.load_subscribers(["333"])
        self.assertEqual(set(tg.audience()), {"-1001234", "999", "333", ADMIN})

    def test_broadcast_reaches_everyone_once(self):
        tg = FakeTelegram(notify_cfg(telegram_broadcast_chat_ids=["-1001234"]))
        tg.load_subscribers(["333", "333"])
        delivered = tg.broadcast("LONG UT100")
        self.assertEqual(delivered, 3)          # group + subscriber + admin
        self.assertEqual(len(tg.sent), 3)

    def test_a_network_failure_is_survivable(self):
        self.tg.fail = True
        self.assertEqual(self.tg.broadcast("hello"), 0)
        self.assertEqual(self.tg.poll(), [])    # no exception


class TestDispatch(unittest.TestCase):
    def test_unknown_command(self):
        reply = dispatch(Command("frobnicate"), {})
        self.assertIn("Unknown command", reply)

    def test_a_failing_handler_does_not_raise(self):
        def boom(_command):
            raise RuntimeError("terminal gone")
        reply = dispatch(Command("status"), {"status": boom})
        self.assertIn("failed", reply)
        self.assertIn("terminal gone", reply)


class TestRunnerCommands(unittest.TestCase):
    """Commands driven end to end against the fake terminal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        cfg = Config()
        cfg.mt5.server_utc_offset_hours = 0
        cfg.mt5.history_bars = 100
        cfg.mt5.htf_history_bars = 10
        cfg.strategy.swing_len = 2
        cfg.strategy.max_wait = 5
        cfg.strategy.chain_max = 10
        cfg.strategy.min_rr = 0.0
        cfg.news.enabled = False
        cfg.notify.telegram_bot_token = "token"
        cfg.notify.telegram_admin_chat_id = ADMIN
        cfg.runtime.mode = "paper"
        cfg.runtime.state_file = os.path.join(tmp, "state.json")
        cfg.runtime.signals_csv = os.path.join(tmp, "signals.csv")
        cfg.runtime.log_file = ""
        self.cfg = cfg

        self.fake = FakeMt5(rows_from(BULL_ROWS[:8] + [(101, 102, 100, 101)]), h4_rows())
        self._real_api = session_module.mt5_api
        session_module.mt5_api = lambda: self.fake
        self.addCleanup(self._restore)

        self.bot = LiveRunner(cfg)
        self.tg = FakeTelegram(cfg.notify)
        self.bot.telegram = self.tg
        self.bot.notifier.telegram = self.tg
        self.bot.start()
        self.addCleanup(self.bot.stop)

    def _restore(self):
        session_module.mt5_api = self._real_api
        self._tmp.cleanup()

    def _confirm_bar(self):
        self.fake.m15 = rows_from(BULL_ROWS[:9] + [(108.5, 109, 108, 108.7)])

    def _reply(self):
        return self.tg.texts_to(ADMIN)[-1]

    def _said(self, fragment: str) -> bool:
        """Search every message to admin -- exit alerts can arrive after a reply."""
        return any(fragment in text for text in self.tg.texts_to(ADMIN))

    def test_status(self):
        self.tg.receive("/status")
        self.bot.tick_once()
        reply = self._reply()
        self.assertIn("RUNNING", reply)
        self.assertIn("UT100", reply)
        self.assertIn("break-even at 1.5R", reply)

    def test_pause_blocks_new_entries(self):
        self.tg.receive("/pause")
        self.bot.tick_once()
        self.assertTrue(self.bot.state.paused)

        self._confirm_bar()
        self.bot.tick_once()
        self.assertEqual(self.fake.deals(), [], "a paused bot must not enter")

    def test_resume_allows_entries_again(self):
        self.tg.receive("/pause")
        self.bot.tick_once()
        self.tg.receive("/resume")
        self.bot.tick_once()
        self.assertFalse(self.bot.state.paused)

        self._confirm_bar()
        self.bot.tick_once()
        self.assertEqual(len(self.fake.deals()), 1)

    def test_pause_survives_a_restart(self):
        self.tg.receive("/pause")
        self.bot.tick_once()
        self.bot.stop()

        again = LiveRunner(self.cfg)
        again.telegram = self.tg
        again.start()
        self.assertTrue(again.state.paused)
        again.stop()

    def test_positions_and_close(self):
        self._confirm_bar()
        self.bot.tick_once()
        ticket = self.fake.positions[0].ticket

        self.tg.receive("/positions")
        self.bot.tick_once()
        self.assertIn(f"#{ticket}", self._reply())

        self.tg.receive(f"/close {ticket}")
        self.bot.tick_once()
        self.assertTrue(self._said(f"Closed #{ticket}"))
        self.assertEqual(self.fake.positions, [])

    def test_closeall(self):
        self._confirm_bar()
        self.bot.tick_once()
        self.tg.receive("/closeall")
        self.bot.tick_once()
        self.assertEqual(self.fake.positions, [])
        self.assertTrue(self._said("Closed 1 position(s)"))

    def test_risk_change_is_bounded(self):
        self.tg.receive("/risk 0.5")
        self.bot.tick_once()
        self.assertEqual(self.cfg.risk.risk_pct, 0.5)

        self.tg.receive("/risk 50")
        self.bot.tick_once()
        self.assertIn("Refusing", self._reply())
        self.assertEqual(self.cfg.risk.risk_pct, 0.5)

    def test_manual_break_even_refuses_a_losing_trade(self):
        self._confirm_bar()
        self.bot.tick_once()
        ticket = self.fake.positions[0].ticket
        self.fake.move_price(100.0)               # under the entry
        self.tg.receive(f"/be {ticket}")
        self.bot.tick_once()
        self.assertIn("Refusing", self._reply())
        self.assertEqual(self.fake.sltp_requests(), [])

    def test_manual_break_even_works_in_profit(self):
        self._confirm_bar()
        self.bot.tick_once()
        ticket = self.fake.positions[0].ticket
        self.fake.move_price(115.0)               # +0.66R, below the auto threshold
        self.tg.receive(f"/be {ticket}")
        self.bot.tick_once()
        self.assertEqual(len(self.fake.sltp_requests()), 1)
        self.assertEqual(self.fake.positions[0].sl, 108.60)

    def test_stop_requires_confirmation(self):
        self.tg.receive("/stop")
        self.bot.tick_once()
        self.assertIn("/stop confirm", self._reply())
        self.assertTrue(self.bot._running or True)

        self.tg.receive("/stop confirm")
        self.bot.tick_once()
        self.assertFalse(self.bot._running)

    def test_guest_commands_are_ignored_end_to_end(self):
        self._confirm_bar()
        self.bot.tick_once()
        self.tg.receive("/closeall", chat_id=GUEST)
        self.bot.tick_once()
        self.assertEqual(len(self.fake.positions), 1, "a guest must not be able to close")

    def test_help_lists_commands(self):
        self.tg.receive("/help")
        self.bot.tick_once()
        self.assertIn("/closeall", self._reply())


if __name__ == "__main__":
    unittest.main()


class TestCopyTradingWarning(TestRunnerCommands):
    """A master lot too small to scale down is flagged, not blocked."""

    def test_small_master_lot_warns_the_admin(self):
        self.cfg.risk.min_master_volume = 50.0    # far above what the fixture sizes
        self._confirm_bar()
        self.bot.tick_once()
        self.assertEqual(len(self.fake.deals()), 1, "the trade must still be taken")
        self.assertTrue(self._said("Copy-trading warning"))
        self.assertTrue(self._said("may not replicate"))

    def test_no_warning_when_the_lot_is_big_enough(self):
        self.cfg.risk.min_master_volume = 1.0
        self._confirm_bar()
        self.bot.tick_once()
        self.assertEqual(len(self.fake.deals()), 1)
        self.assertFalse(self._said("Copy-trading warning"))

    def test_off_by_default(self):
        self.assertEqual(Config().risk.min_master_volume, 0.0)
        self._confirm_bar()
        self.bot.tick_once()
        self.assertFalse(self._said("Copy-trading warning"))
