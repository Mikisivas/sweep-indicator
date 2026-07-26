"""Core value types shared by the signal engine, risk layer and execution layer.

Everything here is plain stdlib so the strategy logic can be unit-tested without
MetaTrader 5, pandas or a broker connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1


class EventKind(str, Enum):
    SWEEP = "sweep"              # liquidity taken, setup armed, waiting for CISD
    CONFIRMED = "confirmed"      # CISD closed through -> tradable setup
    INVALIDATED = "invalidated"  # no CISD in time, or close back through the swept level


class ZoneKind(str, Enum):
    BISI = "BISI"  # bullish HTF fair value gap (buy-side imbalance / sell-side inefficiency)
    SIBI = "SIBI"  # bearish HTF fair value gap


@dataclass(frozen=True)
class Candle:
    """One completed bar. `time` is the bar OPEN time in broker-server epoch seconds."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def is_down(self) -> bool:
        return self.close < self.open

    @property
    def is_up(self) -> bool:
        return self.close > self.open


@dataclass
class Zone:
    """A higher-timeframe fair value gap, built only from COMPLETED HTF bars."""

    kind: ZoneKind
    top: float
    bottom: float
    created_htf_time: int
    filled: bool = False

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass
class ArmedSetup:
    """A liquidity sweep waiting for its Change In State of Delivery."""

    direction: Direction
    sweep_index: int          # engine bar index of the sweep bar
    sweep_time: int           # broker-server epoch seconds of the sweep bar
    swept_pool: float         # the pivot level whose stops were taken
    swept_extreme: float      # protected low (long) / high (short); may extend while waiting
    cisd_level: float         # open of the earliest candle in the sweep series
    chain_start_index: int    # engine bar index of that earliest candle (order-block origin)
    htf_ok: bool              # sweep happened inside a matching HTF FVG


@dataclass
class TradePlan:
    """A confirmed setup.

    `reference_entry` is the order-block price the indicator draws. In the default
    `market_on_cisd` mode we do NOT wait for price to return to it -- it is kept for
    logging and for the optional `limit_at_ob` entry mode. Final numbers always come
    from :meth:`finalize` using the price we actually got filled at.
    """

    direction: Direction
    reference_entry: float
    signal_price: float           # close of the CISD confirmation bar
    stop: float
    liquidity_target: Optional[float]  # opposing pivot, if one exists beyond entry
    rr_target: float
    tp_mode: str
    entry_mode: str
    cisd_level: float
    swept_extreme: float
    swept_pool: float
    chain_start_index: int
    sweep_index: int
    sweep_time: int
    confirm_index: int
    confirm_time: int
    htf_ok: bool

    def finalize(self, entry_price: float) -> "FinalTrade":
        """Resolve stop/target/R:R against the price we are actually entering at."""
        risk = abs(entry_price - self.stop)
        use_liq = (
            self.tp_mode == "opposing_liquidity"
            and self.liquidity_target is not None
            and (
                self.liquidity_target > entry_price
                if self.direction is Direction.LONG
                else self.liquidity_target < entry_price
            )
        )
        if use_liq:
            tp = float(self.liquidity_target)
            tp_source = "opposing_liquidity"
        else:
            tp = entry_price + self.direction.sign * self.rr_target * risk
            tp_source = "fixed_rr"
        rr = (abs(tp - entry_price) / risk) if risk > 0 else 0.0
        return FinalTrade(
            direction=self.direction,
            entry=entry_price,
            stop=self.stop,
            take_profit=tp,
            risk_distance=risk,
            rr=rr,
            tp_source=tp_source,
            plan=self,
        )


@dataclass
class FinalTrade:
    direction: Direction
    entry: float
    stop: float
    take_profit: float
    risk_distance: float
    rr: float
    tp_source: str
    plan: TradePlan

    @property
    def is_valid(self) -> bool:
        """Stop must be on the losing side and target on the winning side of entry."""
        if self.risk_distance <= 0:
            return False
        if self.direction is Direction.LONG:
            return self.stop < self.entry < self.take_profit
        return self.take_profit < self.entry < self.stop


@dataclass
class SignalEvent:
    kind: EventKind
    direction: Direction
    bar_index: int
    bar_time: int
    price: float
    setup: Optional[ArmedSetup] = None
    plan: Optional[TradePlan] = None
    note: str = ""


@dataclass
class SymbolSpec:
    """Contract specs read LIVE from the terminal -- never hardcoded.

    MT5's symbol panel rounds `tick size` / `tick value` for display (UT100 shows
    0.00 / 0), so those printed values are meaningless and we always resolve the
    real ones at runtime.
    """

    name: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    stops_level_points: int
    filling_mode_mask: int
    currency_margin: str = ""
    currency_profit: str = ""

    def round_price(self, price: float) -> float:
        return round(price, self.digits)

    def round_volume(self, volume: float) -> float:
        """Floor to the broker's volume step, then clamp into [min, max]."""
        step = self.volume_step or 0.01
        steps = int((volume + 1e-9) / step)
        vol = steps * step
        vol = max(self.volume_min, min(self.volume_max, vol))
        # Volume steps such as 0.01 are not representable in binary floating point.
        decimals = max(0, len(f"{step:.8f}".rstrip("0").split(".")[-1]))
        return round(vol, decimals)


@dataclass
class GateResult:
    """Outcome of the pre-trade checks (spread, session, news, risk caps...).

    `reasons` are human sentences for the log; `codes` are stable categories so
    reports can group "47 blocked on R:R" instead of 47 distinct messages.
    """

    allowed: bool
    reasons: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)

    def block(self, reason: str, code: str = "blocked") -> "GateResult":
        self.allowed = False
        self.reasons.append(reason)
        self.codes.append(code)
        return self

    @property
    def code(self) -> str:
        return self.codes[0] if self.codes else ""
