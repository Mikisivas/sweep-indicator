"""Position sizing, the kill switch and the pre-trade gates."""

from __future__ import annotations

import unittest
from datetime import datetime

from helpers import mk  # noqa: F401  (sys.path)
from ict_bot.config import RiskConfig, SessionConfig, StrategyConfig
from ict_bot.models import Direction, FinalTrade, SymbolSpec, TradePlan
from ict_bot.risk import RiskManager


def ut100_spec(tick_value: float = 0.01) -> SymbolSpec:
    """UT100 as the terminal reports it: 2 digits, contract size 1, USD."""
    return SymbolSpec(
        name="UT100", digits=2, point=0.01, tick_size=0.01, tick_value=tick_value,
        contract_size=1.0, volume_min=0.01, volume_step=0.01, volume_max=100.0,
        stops_level_points=0, filling_mode_mask=3,
        currency_margin="USD", currency_profit="USD",
    )


def trade(direction=Direction.LONG, entry=20000.0, stop=19950.0, tp=20100.0) -> FinalTrade:
    plan = TradePlan(
        direction=direction, reference_entry=entry, signal_price=entry, stop=stop,
        liquidity_target=tp, rr_target=2.0, tp_mode="opposing_liquidity",
        entry_mode="ob_mean_threshold", cisd_level=entry, swept_extreme=stop,
        swept_pool=stop, chain_start_index=0, sweep_index=0, sweep_time=0,
        confirm_index=1, confirm_time=0, htf_ok=False,
    )
    return plan.finalize(entry)


def manager(spec=None, risk=None, session=None, strategy=None, **kwargs) -> RiskManager:
    return RiskManager(
        risk=risk or RiskConfig(), strategy=strategy or StrategyConfig(),
        session=session or SessionConfig(), spec=spec or ut100_spec(), **kwargs,
    )


class TestSizing(unittest.TestCase):
    def test_uses_order_calc_profit_when_the_terminal_can_price_the_stop(self):
        # 50-point stop on UT100: 1 lot loses 50 USD
        rm = manager(profit_calc=lambda d, v, a, b: -50.0 * v)
        result = rm.position_size(trade(), equity=10_000)
        self.assertTrue(result.ok)
        self.assertEqual(result.method, "order_calc_profit")
        self.assertAlmostEqual(result.risk_amount, 100.0)     # 1% of 10k
        self.assertAlmostEqual(result.volume, 2.0)            # 100 / 50

    def test_falls_back_to_tick_maths_when_the_terminal_returns_zero(self):
        rm = manager(profit_calc=lambda *a: 0.0)
        result = rm.position_size(trade(), equity=10_000)
        self.assertTrue(result.ok)
        self.assertEqual(result.method, "tick_value")
        self.assertAlmostEqual(result.loss_per_lot, 50.0)     # 5000 ticks * 0.01

    def test_falls_back_to_contract_size_when_tick_value_is_also_zero(self):
        rm = manager(spec=ut100_spec(tick_value=0.0), profit_calc=lambda *a: None)
        result = rm.position_size(trade(), equity=10_000)
        self.assertTrue(result.ok)
        self.assertEqual(result.method, "contract_size")
        self.assertAlmostEqual(result.loss_per_lot, 50.0)

    def test_refuses_to_guess_when_nothing_can_value_the_stop(self):
        spec = ut100_spec(tick_value=0.0)
        spec.tick_size = 0.0
        spec.contract_size = 0.0
        rm = manager(spec=spec, profit_calc=lambda *a: None)
        result = rm.position_size(trade(), equity=10_000)
        self.assertFalse(result.ok)
        self.assertIn("refusing to guess", result.reason)

    def test_volume_is_floored_to_the_broker_step(self):
        rm = manager(profit_calc=lambda d, v, a, b: -37.0 * v)
        result = rm.position_size(trade(), equity=10_000)
        self.assertAlmostEqual(result.volume, 2.70)           # 2.7027 -> 2.70, never up
        self.assertLessEqual(result.volume * 37.0, result.risk_amount)

    def test_account_too_small_is_refused_not_rounded_up(self):
        """Taking volume_min anyway would silently exceed the risk budget."""
        rm = manager(profit_calc=lambda d, v, a, b: -500.0 * v)
        result = rm.position_size(trade(), equity=300)        # 1% = 3 USD, min lot risks 5
        self.assertFalse(result.ok)
        self.assertIn("under the broker minimum", result.reason)

    def test_volume_is_clamped_to_the_broker_maximum(self):
        rm = manager(profit_calc=lambda d, v, a, b: -0.01 * v)
        result = rm.position_size(trade(), equity=10_000_000)
        self.assertEqual(result.volume, 100.0)

    def test_risk_pct_scales_linearly(self):
        rm = manager(risk=RiskConfig(risk_pct=2.0), profit_calc=lambda d, v, a, b: -50.0 * v)
        self.assertAlmostEqual(rm.position_size(trade(), 10_000).volume, 4.0)


class TestMargin(unittest.TestCase):
    def test_rejects_a_trade_that_will_not_fit(self):
        rm = manager(margin_calc=lambda d, v, p: 6_000.0)
        ok, reason = rm.margin_ok(trade(), 1.0, free_margin=10_000)
        self.assertFalse(ok)                                   # buffer is 50%
        self.assertIn("exceeds", reason)

    def test_accepts_within_the_buffer(self):
        rm = manager(margin_calc=lambda d, v, p: 4_000.0)
        ok, _ = rm.margin_ok(trade(), 1.0, free_margin=10_000)
        self.assertTrue(ok)

    def test_unavailable_margin_is_refused(self):
        rm = manager(margin_calc=lambda d, v, p: None)
        ok, reason = rm.margin_ok(trade(), 1.0, free_margin=10_000)
        self.assertFalse(ok)
        self.assertIn("refusing", reason)


class TestDailyKillSwitch(unittest.TestCase):
    def test_trips_at_the_configured_drawdown(self):
        rm = manager(risk=RiskConfig(daily_max_loss_pct=3.0))
        now = datetime(2026, 7, 30, 10, 0)
        self.assertTrue(rm.check_daily(now, 10_000).allowed)
        self.assertTrue(rm.check_daily(now, 9_800).allowed)     # -2%
        blocked = rm.check_daily(now, 9_700)                    # -3%
        self.assertFalse(blocked.allowed)
        self.assertIn("kill switch", blocked.reasons[0])

    def test_stays_tripped_for_the_rest_of_the_day(self):
        rm = manager(risk=RiskConfig(daily_max_loss_pct=3.0))
        now = datetime(2026, 7, 30, 10, 0)
        rm.check_daily(now, 10_000)
        rm.check_daily(now, 9_600)
        self.assertFalse(rm.check_daily(datetime(2026, 7, 30, 15, 0), 9_950).allowed)

    def test_resets_on_the_next_broker_day(self):
        rm = manager(risk=RiskConfig(daily_max_loss_pct=3.0))
        rm.check_daily(datetime(2026, 7, 30, 10, 0), 10_000)
        rm.check_daily(datetime(2026, 7, 30, 11, 0), 9_600)
        self.assertTrue(rm.check_daily(datetime(2026, 7, 31, 9, 0), 9_600).allowed)

    def test_daily_trade_cap(self):
        rm = manager(risk=RiskConfig(daily_max_trades=2))
        now = datetime(2026, 7, 30, 10, 0)
        rm.check_daily(now, 10_000)
        rm.record_fill()
        rm.record_fill()
        self.assertFalse(rm.check_daily(now, 10_000).allowed)


class TestGates(unittest.TestCase):
    def test_spread_cap(self):
        rm = manager(risk=RiskConfig(max_spread_points=60))
        self.assertTrue(rm.check_spread(45).allowed)
        self.assertFalse(rm.check_spread(75).allowed)

    def test_session_window(self):
        session = SessionConfig(enabled=True, windows=["09:30-16:00"], weekdays=[0, 1, 2, 3, 4])
        rm = manager(session=session)
        self.assertTrue(rm.check_session(datetime(2026, 7, 30, 10, 0)).allowed)    # Thu
        self.assertFalse(rm.check_session(datetime(2026, 7, 30, 8, 0)).allowed)
        self.assertFalse(rm.check_session(datetime(2026, 8, 1, 10, 0)).allowed)    # Sat

    def test_overnight_session_window(self):
        session = SessionConfig(enabled=True, windows=["22:00-02:00"], weekdays=list(range(7)))
        rm = manager(session=session)
        self.assertTrue(rm.check_session(datetime(2026, 7, 30, 23, 0)).allowed)
        self.assertTrue(rm.check_session(datetime(2026, 7, 30, 1, 0)).allowed)
        self.assertFalse(rm.check_session(datetime(2026, 7, 30, 12, 0)).allowed)

    def test_session_disabled_allows_everything(self):
        self.assertTrue(manager().check_session(datetime(2026, 8, 1, 3, 0)).allowed)

    def test_exposure_caps(self):
        rm = manager(risk=RiskConfig(max_positions=1, one_per_direction=True))
        open_long = [type("P", (), {"direction": Direction.LONG})()]
        self.assertFalse(rm.check_exposure(open_long, Direction.SHORT).allowed)  # max_positions
        rm2 = manager(risk=RiskConfig(max_positions=3, one_per_direction=True))
        self.assertFalse(rm2.check_exposure(open_long, Direction.LONG).allowed)
        self.assertTrue(rm2.check_exposure(open_long, Direction.SHORT).allowed)

    def test_trade_quality_rejects_inverted_levels(self):
        bad = trade(entry=20000, stop=20050, tp=20100)      # stop above a long entry
        self.assertFalse(manager().check_trade_quality(bad).allowed)

    def test_trade_quality_min_rr(self):
        rm = manager(strategy=StrategyConfig(min_rr=2.0))
        weak = trade(entry=20000, stop=19950, tp=20050)     # 1R
        self.assertFalse(rm.check_trade_quality(weak).allowed)

    def test_trade_quality_stop_bounds(self):
        rm = manager(strategy=StrategyConfig(max_stop_points=30, min_rr=0))
        self.assertFalse(rm.check_trade_quality(trade()).allowed)   # 50-point stop
        rm2 = manager(strategy=StrategyConfig(min_stop_points=100, min_rr=0))
        self.assertFalse(rm2.check_trade_quality(trade()).allowed)

    def test_trade_quality_respects_the_brokers_stops_level(self):
        spec = ut100_spec()
        spec.stops_level_points = 10_000        # 100.00 price units
        rm = manager(spec=spec, strategy=StrategyConfig(min_rr=0))
        self.assertFalse(rm.check_trade_quality(trade()).allowed)


class TestVolumeRounding(unittest.TestCase):
    def test_step_rounding_is_exact(self):
        spec = ut100_spec()
        self.assertEqual(spec.round_volume(2.7027), 2.70)
        self.assertEqual(spec.round_volume(0.005), 0.01)     # clamped up to the minimum
        self.assertEqual(spec.round_volume(999), 100.0)

    def test_price_rounding_uses_digits(self):
        self.assertEqual(ut100_spec().round_price(20000.12345), 20000.12)


if __name__ == "__main__":
    unittest.main()
