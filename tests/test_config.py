"""Config loading, env overrides and validation."""

from __future__ import annotations

import os
import unittest

from helpers import mk  # noqa: F401  (sys.path)
from ict_bot.config import Config, _apply_env, _build, _parse_window


class TestDefaults(unittest.TestCase):
    def test_defaults_match_the_model(self):
        cfg = Config()
        self.assertEqual(cfg.mt5.symbol, "UT100")
        self.assertEqual(cfg.mt5.exec_timeframe, "M15")
        self.assertEqual(cfg.mt5.htf_timeframe, "H4")
        self.assertEqual(cfg.strategy.swing_len, 5)
        self.assertEqual(cfg.strategy.max_wait, 20)
        self.assertEqual(cfg.strategy.chain_max, 30)
        self.assertEqual(cfg.strategy.entry_trigger, "market_on_cisd")   # no retest
        self.assertEqual(cfg.risk.risk_pct, 1.0)
        self.assertEqual(cfg.runtime.mode, "paper")                      # demo by default
        self.assertFalse(cfg.runtime.allow_live_trading)

    def test_news_defaults_are_five_minutes_each_side(self):
        news = Config().news
        self.assertTrue(news.enabled)
        self.assertEqual(news.minutes_before, 5.0)
        self.assertEqual(news.minutes_after, 5.0)
        self.assertEqual(news.impacts, ["high"])
        self.assertEqual(news.currencies, ["USD"])
        self.assertEqual(news.on_feed_unavailable, "block")              # fail safe
        self.assertFalse(news.flatten_before_event)


class TestBuild(unittest.TestCase):
    def test_nested_sections_are_built(self):
        cfg = _build(Config, {"strategy": {"swing_len": 7}, "risk": {"risk_pct": 0.5}})
        self.assertEqual(cfg.strategy.swing_len, 7)
        self.assertEqual(cfg.risk.risk_pct, 0.5)
        self.assertEqual(cfg.mt5.symbol, "UT100")

    def test_a_typo_is_a_hard_error(self):
        with self.assertRaises(ValueError) as ctx:
            _build(Config, {"strategy": {"swing_lenght": 7}})
        self.assertIn("swing_lenght", str(ctx.exception))

    def test_unknown_section(self):
        with self.assertRaises(ValueError):
            _build(Config, {"stratergy": {}})


class TestEnvOverrides(unittest.TestCase):
    def tearDown(self):
        for key in list(os.environ):
            if key.startswith("ICT_"):
                del os.environ[key]

    def test_scalar_types_are_coerced(self):
        os.environ["ICT_RISK__RISK_PCT"] = "0.25"
        os.environ["ICT_STRATEGY__SWING_LEN"] = "8"
        os.environ["ICT_NEWS__ENABLED"] = "false"
        cfg = Config()
        _apply_env(cfg)
        self.assertEqual(cfg.risk.risk_pct, 0.25)
        self.assertEqual(cfg.strategy.swing_len, 8)
        self.assertFalse(cfg.news.enabled)

    def test_lists_are_comma_separated(self):
        os.environ["ICT_NEWS__CURRENCIES"] = "USD,EUR"
        cfg = Config()
        _apply_env(cfg)
        self.assertEqual(cfg.news.currencies, ["USD", "EUR"])

    def test_secrets_stay_out_of_yaml(self):
        os.environ["ICT_MT5__PASSWORD"] = "hunter2"
        cfg = Config()
        _apply_env(cfg)
        self.assertEqual(cfg.mt5.password, "hunter2")
        self.assertEqual(cfg.redacted()["mt5"]["password"], "***")


class TestValidation(unittest.TestCase):
    def _bad(self, section, field, value):
        cfg = Config()
        setattr(getattr(cfg, section), field, value)
        with self.assertRaises(ValueError):
            cfg.validate()

    def test_rejects_bad_enums(self):
        self._bad("strategy", "entry_mode", "middle-ish")
        self._bad("strategy", "entry_trigger", "hope")
        self._bad("strategy", "tp_mode", "vibes")
        self._bad("runtime", "mode", "yolo")
        self._bad("news", "on_feed_unavailable", "maybe")
        self._bad("news", "source", "carrier_pigeon")

    def test_rejects_bad_numbers(self):
        self._bad("strategy", "swing_len", 1)
        self._bad("strategy", "chain_max", 2)
        self._bad("risk", "risk_pct", 0)
        self._bad("risk", "risk_pct", 101)
        self._bad("risk", "max_positions", 0)
        self._bad("news", "minutes_before", -1)

    def test_session_windows_are_parsed(self):
        self.assertEqual(_parse_window("09:30-16:00"), (570, 960))
        with self.assertRaises(ValueError):
            _parse_window("9.30 to 4pm")
        self._bad("session", "windows", ["25:00-26:00"])


if __name__ == "__main__":
    unittest.main()
