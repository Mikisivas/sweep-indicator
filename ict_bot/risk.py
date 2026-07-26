"""Risk: position sizing, the daily kill switch, and the pre-trade gates.

Sizing never assumes contract specs. The money lost at the stop is asked of the
terminal itself via ``order_calc_profit`` (injected as `profit_calc`), which is
the only reliable way on a CFD like UT100 whose panel prints "tick value 0".
Tick maths is only a fallback, and if neither is trustworthy we refuse the trade
rather than guess a lot size.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

from .config import RiskConfig, SessionConfig, StrategyConfig, _parse_window
from .models import Direction, FinalTrade, GateResult, SymbolSpec

log = logging.getLogger(__name__)

# (direction, volume, entry_price, exit_price) -> money in account currency
ProfitCalc = Callable[[Direction, float, float, float], Optional[float]]
MarginCalc = Callable[[Direction, float, float], Optional[float]]


@dataclass
class SizingResult:
    volume: float
    risk_amount: float
    loss_per_lot: float
    method: str
    ok: bool = True
    reason: str = ""
    code: str = ""


@dataclass
class DailyBook:
    """Per-broker-day accounting for the kill switch."""

    day: Optional[date] = None
    start_equity: float = 0.0
    trades: int = 0
    realised: float = 0.0
    halted: bool = False
    halt_reason: str = ""

    def roll(self, server_now: datetime, equity: float) -> None:
        if self.day != server_now.date():
            self.day = server_now.date()
            self.start_equity = equity
            self.trades = 0
            self.realised = 0.0
            self.halted = False
            self.halt_reason = ""
            log.info("new trading day %s: start equity %.2f", self.day, equity)


@dataclass
class RiskManager:
    risk: RiskConfig
    strategy: StrategyConfig
    session: SessionConfig
    spec: SymbolSpec
    profit_calc: Optional[ProfitCalc] = None
    margin_calc: Optional[MarginCalc] = None
    book: DailyBook = field(default_factory=DailyBook)

    # --------------------------------------------------------------- sizing
    def loss_per_lot(self, trade: FinalTrade) -> tuple[float, str]:
        """Money lost on 1.00 lot if the stop is hit."""
        if self.profit_calc is not None:
            value = self.profit_calc(trade.direction, 1.0, trade.entry, trade.stop)
            if value is not None and abs(value) > 0:
                return abs(value), "order_calc_profit"
            log.warning("order_calc_profit unusable (%r) - falling back to tick maths", value)

        if self.spec.tick_size > 0 and self.spec.tick_value > 0:
            ticks = trade.risk_distance / self.spec.tick_size
            return ticks * self.spec.tick_value, "tick_value"

        if self.spec.contract_size > 0:
            # Last resort for a USD-profit index CFD: 1 lot moves contract_size per point.
            return trade.risk_distance * self.spec.contract_size, "contract_size"

        return 0.0, "unavailable"

    def position_size(self, trade: FinalTrade, equity: float) -> SizingResult:
        risk_amount = equity * (self.risk.risk_pct / 100.0)
        per_lot, method = self.loss_per_lot(trade)
        if per_lot <= 0 or method == "unavailable":
            return SizingResult(
                0.0, risk_amount, per_lot, method, False,
                "cannot value the stop distance - refusing to guess a lot size",
                "stop_not_priceable")
        raw = risk_amount / per_lot
        volume = self.spec.round_volume(raw)
        if raw < self.spec.volume_min - 1e-9:
            needed = per_lot * self.spec.volume_min / (self.risk.risk_pct / 100.0)
            return SizingResult(
                0.0, risk_amount, per_lot, method, False,
                f"risk-based size {raw:.4f} is under the broker minimum "
                f"{self.spec.volume_min}; {self.risk.risk_pct}% risk needs about "
                f"{needed:,.0f} equity for this {trade.risk_distance:.2f} point stop",
                "below_min_lot",
            )
        if volume <= 0:
            return SizingResult(0.0, risk_amount, per_lot, method, False,
                                "rounded volume is zero", "below_min_lot")
        return SizingResult(volume, risk_amount, per_lot, method)

    def margin_ok(self, trade: FinalTrade, volume: float, free_margin: float) -> tuple[bool, str]:
        if self.margin_calc is None:
            return True, ""
        required = self.margin_calc(trade.direction, volume, trade.entry)
        if required is None:
            return False, "margin could not be computed - refusing the trade"
        budget = free_margin * self.risk.margin_buffer
        if required > budget:
            return False, (f"margin {required:,.2f} exceeds {self.risk.margin_buffer:.0%} "
                           f"of free margin ({budget:,.2f})")
        return True, ""

    # ---------------------------------------------------------------- gates
    def check_daily(self, server_now: datetime, equity: float) -> GateResult:
        gate = GateResult(True)
        self.book.roll(server_now, equity)
        if self.book.halted:
            return gate.block(f"daily kill switch active: {self.book.halt_reason}",
                              "daily_kill_switch")
        if self.book.start_equity > 0:
            drawdown_pct = (self.book.start_equity - equity) / self.book.start_equity * 100.0
            if drawdown_pct >= self.risk.daily_max_loss_pct:
                self.book.halted = True
                self.book.halt_reason = (f"down {drawdown_pct:.2f}% today "
                                         f"(limit {self.risk.daily_max_loss_pct}%)")
                log.error("KILL SWITCH: %s - no new entries until tomorrow",
                          self.book.halt_reason)
                return gate.block(f"daily kill switch: {self.book.halt_reason}",
                                  "daily_kill_switch")
        if self.risk.daily_max_trades and self.book.trades >= self.risk.daily_max_trades:
            return gate.block(f"daily trade cap reached ({self.risk.daily_max_trades})",
                              "daily_trade_cap")
        return gate

    def check_spread(self, spread_points: float) -> GateResult:
        gate = GateResult(True)
        if self.risk.max_spread_points > 0 and spread_points > self.risk.max_spread_points:
            return gate.block(f"spread {spread_points:.0f} pts > cap "
                              f"{self.risk.max_spread_points:.0f}", "spread_too_wide")
        return gate

    def check_session(self, server_now: datetime) -> GateResult:
        gate = GateResult(True)
        if not self.session.enabled:
            return gate
        if self.session.weekdays and server_now.weekday() not in self.session.weekdays:
            return gate.block(f"outside trading weekdays ({server_now:%a})", "outside_session")
        minutes = server_now.hour * 60 + server_now.minute
        for window in self.session.windows:
            start, end = _parse_window(window)
            wraps_midnight = start > end
            inside = ((minutes >= start or minutes < end) if wraps_midnight
                      else (start <= minutes < end))
            if inside:
                return gate
        return gate.block(f"outside trading session ({server_now:%H:%M} server time)",
                          "outside_session")

    def check_exposure(self, open_positions: list, direction: Direction) -> GateResult:
        gate = GateResult(True)
        if len(open_positions) >= self.risk.max_positions:
            return gate.block(f"already at max_positions ({self.risk.max_positions})",
                              "max_positions")
        if self.risk.one_per_direction and any(
            getattr(p, "direction", None) == direction for p in open_positions
        ):
            return gate.block(f"a {direction.value} position is already open",
                              "already_in_direction")
        return gate

    def check_trade_quality(self, trade: FinalTrade) -> GateResult:
        """Structural sanity on the setup itself, before any broker interaction."""
        gate = GateResult(True)
        if not trade.is_valid:
            return gate.block(
                f"malformed levels: entry {trade.entry} stop {trade.stop} tp {trade.take_profit}",
                "malformed_levels")
        if trade.rr < self.strategy.min_rr:
            gate.block(f"R:R {trade.rr:.2f} below min_rr {self.strategy.min_rr}", "rr_below_min")
        max_stop = self.strategy.max_stop_points
        if max_stop > 0 and trade.risk_distance > max_stop:
            gate.block(f"stop {trade.risk_distance:.2f} pts wider than cap "
                       f"{self.strategy.max_stop_points}", "stop_too_wide")
        min_stop = self.strategy.min_stop_points
        if min_stop > 0 and trade.risk_distance < min_stop:
            gate.block(f"stop {trade.risk_distance:.2f} pts tighter than floor "
                       f"{self.strategy.min_stop_points}", "stop_too_tight")
        stops_level = self.spec.stops_level_points * self.spec.point
        if stops_level > 0 and trade.risk_distance < stops_level:
            gate.block(f"stop closer than the broker's stops level ({stops_level:.2f})",
                       "inside_stops_level")
        return gate

    def record_fill(self) -> None:
        self.book.trades += 1

    def record_close(self, profit: float) -> None:
        self.book.realised += profit
