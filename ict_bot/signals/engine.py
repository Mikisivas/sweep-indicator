"""The ICT signal engine: liquidity sweep -> CISD -> order block trade plan.

This is a faithful, bar-for-bar port of the Pine v6 indicator
"ICT Model - Sweep + CISD + HTF FVG + Order Block", with ONE deliberate change:

    *** NO RETEST ***
    The indicator draws an entry level and leaves the trader waiting for price to
    come back to it. We do not wait. The setup is tradable the instant CISD closes
    through, because these setups frequently run straight to target without ever
    retesting the order block. The drawn order-block price survives as
    `TradePlan.reference_entry` (used for logging, and for the optional
    `limit_at_ob` entry mode).

Everything here is pure: feed it closed candles, get events out. No MT5, no I/O,
no clock. That is what makes it unit-testable and what makes backtest and live
share the exact same code path.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..models import (
    ArmedSetup,
    Candle,
    Direction,
    EventKind,
    SignalEvent,
    TradePlan,
)
from .fvg import FvgStore
from .pivots import confirmed_pivot_high, confirmed_pivot_low


class IctSignalEngine:
    """Two independent state machines (bullish / bearish), one bar at a time.

    State per direction: idle -> armed (liquidity swept) -> confirmed | invalidated.
    """

    def __init__(
        self,
        swing_len: int = 5,
        max_wait: int = 20,
        chain_max: int = 30,
        entry_mode: str = "ob_mean_threshold",   # or "ob_edge"
        tp_mode: str = "opposing_liquidity",     # or "fixed_rr"
        rr_target: float = 2.0,
        require_sweep_in_htf_fvg: bool = False,
    ) -> None:
        if swing_len < 2:
            raise ValueError("swing_len must be >= 2")
        if max_wait < 1:
            raise ValueError("max_wait must be >= 1")
        if chain_max < 3:
            raise ValueError("chain_max must be >= 3")
        if entry_mode not in ("ob_mean_threshold", "ob_edge"):
            raise ValueError(f"unknown entry_mode: {entry_mode}")
        if tp_mode not in ("opposing_liquidity", "fixed_rr"):
            raise ValueError(f"unknown tp_mode: {tp_mode}")

        self.swing_len = swing_len
        self.max_wait = max_wait
        self.chain_max = chain_max
        self.entry_mode = entry_mode
        self.tp_mode = tp_mode
        self.rr_target = rr_target
        self.require_sweep_in_htf_fvg = require_sweep_in_htf_fvg

        # rolling window: enough for the CISD chain scan and pivot confirmation
        self._keep = max(chain_max, 2 * swing_len + 1) + 8
        self.bars: list[Candle] = []
        self.bar_index = -1

        # liquidity pools: most recent pivot that has not been consumed yet
        self.last_pivot_low: Optional[float] = None
        self.last_pivot_high: Optional[float] = None

        self.bull: Optional[ArmedSetup] = None
        self.bear: Optional[ArmedSetup] = None

        self.fvg = FvgStore()

    # ------------------------------------------------------------------ helpers
    def _at(self, i: int) -> Candle:
        """Pine-style history access: `_at(0)` is the current bar."""
        return self.bars[-1 - i]

    @property
    def current(self) -> Candle:
        return self.bars[-1]

    def _chain_bull(self) -> tuple[Optional[float], int]:
        """CISD level for a bullish setup.

        Walk back from the current bar: skip candles that are not down-closes, then
        follow the contiguous run of down-closes. The level is the OPEN of the
        EARLIEST candle in that run -- the start of the sell programme that has to
        be reversed for delivery to flip bullish. Returns (level, offset_of_oldest).
        """
        limit = min(self.chain_max, len(self.bars))
        i = 0
        while i < limit and self._at(i).close >= self._at(i).open:
            i += 1
        level: Optional[float] = None
        j = i
        while j < limit and self._at(j).close < self._at(j).open:
            level = self._at(j).open
            j += 1
        return level, j - 1

    def _chain_bear(self) -> tuple[Optional[float], int]:
        """Mirror of :meth:`_chain_bull` for a bearish setup (run of up-closes)."""
        limit = min(self.chain_max, len(self.bars))
        i = 0
        while i < limit and self._at(i).close <= self._at(i).open:
            i += 1
        level: Optional[float] = None
        j = i
        while j < limit and self._at(j).close > self._at(j).open:
            level = self._at(j).open
            j += 1
        return level, j - 1

    # --------------------------------------------------------------------- feed
    def on_bar(self, bar: Candle, htf_bars: Optional[Sequence[Candle]] = None) -> list[SignalEvent]:
        """Process one CLOSED execution-timeframe bar.

        `htf_bars` are COMPLETED higher-timeframe bars (oldest -> newest). Passing
        them on every call is fine; already-seen HTF bars are ignored.
        """
        self.bars.append(bar)
        if len(self.bars) > self._keep:
            del self.bars[0: len(self.bars) - self._keep]
        self.bar_index += 1

        events: list[SignalEvent] = []

        # 1) HTF context first, then retire gaps price has closed through.
        if htf_bars:
            self.fvg.ingest_htf(htf_bars)
        self.fvg.prune(bar.close)

        # 2) Pivots confirm `swing_len` bars late; a pivot confirmed on this bar can
        #    be swept by this same bar (matches the indicator).
        pl = confirmed_pivot_low(self.bars, self.swing_len)
        if pl is not None:
            self.last_pivot_low = pl
        ph = confirmed_pivot_high(self.bars, self.swing_len)
        if ph is not None:
            self.last_pivot_high = ph

        # 3) CISD chains, evaluated every bar.
        bull_level, bull_offset = self._chain_bull()
        bear_level, bear_offset = self._chain_bear()

        events.extend(self._step_bull(bar, bull_level, bull_offset))
        events.extend(self._step_bear(bar, bear_level, bear_offset))

        # 4) Trade plans are built only AFTER BOTH state machines have run, because
        #    the indicator reads the opposing liquidity pool at that point too. On a
        #    bar where one side confirms while the other side sweeps, the pool has
        #    already been consumed and the target falls back to a fixed R multiple.
        #    Building the plan any earlier would silently break bar-for-bar parity.
        for event in events:
            if event.kind is EventKind.CONFIRMED and event.setup is not None:
                event.plan = self._build_plan(event.setup, bar)
        return events

    # ------------------------------------------------------------ bullish branch
    def _step_bull(self, bar: Candle, level: Optional[float], offset: int) -> list[SignalEvent]:
        events: list[SignalEvent] = []

        if self.bull is None:
            # Sweep of SELL-side liquidity: price trades below the resting pivot low.
            if self.last_pivot_low is not None and bar.low < self.last_pivot_low:
                if level is not None and level > bar.low:
                    self.bull = ArmedSetup(
                        direction=Direction.LONG,
                        sweep_index=self.bar_index,
                        sweep_time=bar.time,
                        swept_pool=self.last_pivot_low,
                        swept_extreme=bar.low,
                        cisd_level=level,
                        chain_start_index=self.bar_index - offset,
                        htf_ok=self.fvg.price_in_bullish(bar.low),
                    )
                    events.append(SignalEvent(
                        kind=EventKind.SWEEP, direction=Direction.LONG,
                        bar_index=self.bar_index, bar_time=bar.time, price=bar.low,
                        setup=self.bull,
                        note=f"sell-side liquidity taken at {self.last_pivot_low}",
                    ))
                # The pool is consumed either way -- those stops are gone.
                self.last_pivot_low = None
            return events

        setup = self.bull
        # Confirmation: a CLOSE above the CISD level flips the state of delivery.
        if bar.close > setup.cisd_level and (not self.require_sweep_in_htf_fvg or setup.htf_ok):
            self.bull = None
            events.append(SignalEvent(
                kind=EventKind.CONFIRMED, direction=Direction.LONG,
                bar_index=self.bar_index, bar_time=bar.time, price=bar.close,
                setup=setup, note="CISD confirmed - enter now, no retest",
            ))
        elif bar.close < setup.swept_extreme:
            self.bull = None
            events.append(SignalEvent(
                kind=EventKind.INVALIDATED, direction=Direction.LONG,
                bar_index=self.bar_index, bar_time=bar.time, price=bar.close,
                setup=setup, note="closed back below the swept low",
            ))
        elif self.bar_index - setup.sweep_index > self.max_wait:
            self.bull = None
            events.append(SignalEvent(
                kind=EventKind.INVALIDATED, direction=Direction.LONG,
                bar_index=self.bar_index, bar_time=bar.time, price=bar.close,
                setup=setup, note=f"no CISD within {self.max_wait} bars",
            ))
        else:
            # Still waiting: the sweep is allowed to run deeper.
            setup.swept_extreme = min(setup.swept_extreme, bar.low)
        return events

    # ------------------------------------------------------------ bearish branch
    def _step_bear(self, bar: Candle, level: Optional[float], offset: int) -> list[SignalEvent]:
        events: list[SignalEvent] = []

        if self.bear is None:
            # Sweep of BUY-side liquidity: price trades above the resting pivot high.
            if self.last_pivot_high is not None and bar.high > self.last_pivot_high:
                if level is not None and level < bar.high:
                    self.bear = ArmedSetup(
                        direction=Direction.SHORT,
                        sweep_index=self.bar_index,
                        sweep_time=bar.time,
                        swept_pool=self.last_pivot_high,
                        swept_extreme=bar.high,
                        cisd_level=level,
                        chain_start_index=self.bar_index - offset,
                        htf_ok=self.fvg.price_in_bearish(bar.high),
                    )
                    events.append(SignalEvent(
                        kind=EventKind.SWEEP, direction=Direction.SHORT,
                        bar_index=self.bar_index, bar_time=bar.time, price=bar.high,
                        setup=self.bear,
                        note=f"buy-side liquidity taken at {self.last_pivot_high}",
                    ))
                self.last_pivot_high = None
            return events

        setup = self.bear
        if bar.close < setup.cisd_level and (not self.require_sweep_in_htf_fvg or setup.htf_ok):
            self.bear = None
            events.append(SignalEvent(
                kind=EventKind.CONFIRMED, direction=Direction.SHORT,
                bar_index=self.bar_index, bar_time=bar.time, price=bar.close,
                setup=setup, note="CISD confirmed - enter now, no retest",
            ))
        elif bar.close > setup.swept_extreme:
            self.bear = None
            events.append(SignalEvent(
                kind=EventKind.INVALIDATED, direction=Direction.SHORT,
                bar_index=self.bar_index, bar_time=bar.time, price=bar.close,
                setup=setup, note="closed back above the swept high",
            ))
        elif self.bar_index - setup.sweep_index > self.max_wait:
            self.bear = None
            events.append(SignalEvent(
                kind=EventKind.INVALIDATED, direction=Direction.SHORT,
                bar_index=self.bar_index, bar_time=bar.time, price=bar.close,
                setup=setup, note=f"no CISD within {self.max_wait} bars",
            ))
        else:
            setup.swept_extreme = max(setup.swept_extreme, bar.high)
        return events

    # ----------------------------------------------------------------- planning
    def _build_plan(self, setup: ArmedSetup, bar: Candle) -> TradePlan:
        """Order block -> entry / stop / target.

        Order block = the sweep series, spanning the CISD level and the swept
        extreme. Stop sits at the protected extreme. Target is the opposing
        liquidity pool if one is still resting beyond us, else a fixed R multiple.
        """
        if self.entry_mode == "ob_edge":
            reference_entry = setup.cisd_level
        else:  # 50% mean threshold of the order block
            reference_entry = (setup.cisd_level + setup.swept_extreme) / 2.0

        if setup.direction is Direction.LONG:
            liquidity_target = self.last_pivot_high
        else:
            liquidity_target = self.last_pivot_low

        return TradePlan(
            direction=setup.direction,
            reference_entry=reference_entry,
            signal_price=bar.close,
            stop=setup.swept_extreme,
            liquidity_target=liquidity_target,
            rr_target=self.rr_target,
            tp_mode=self.tp_mode,
            entry_mode=self.entry_mode,
            cisd_level=setup.cisd_level,
            swept_extreme=setup.swept_extreme,
            swept_pool=setup.swept_pool,
            chain_start_index=setup.chain_start_index,
            sweep_index=setup.sweep_index,
            sweep_time=setup.sweep_time,
            confirm_index=self.bar_index,
            confirm_time=bar.time,
            htf_ok=setup.htf_ok,
        )
