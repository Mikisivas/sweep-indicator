"""Higher-timeframe Fair Value Gaps (BISI / SIBI) -- 4H context by default.

Built ONLY from completed HTF bars, which is what makes the zones non-repainting:
a 4H gap becomes visible the moment the 4H candle closes and never moves again.

Given three consecutive completed HTF bars (n-2, n-1, n):
  BISI (bullish gap / support)     low[n]  > high[n-2]  ->  zone [high[n-2] .. low[n]]
  SIBI (bearish gap / resistance)  high[n] < low[n-2]   ->  zone [high[n]   .. low[n-2]]

A zone stops being tradable context once price CLOSES through its far side
(fully rebalanced), mirroring the indicator's `prune()`.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ..models import Candle, Zone, ZoneKind


class FvgStore:
    """Maintains the live set of HTF fair value gaps."""

    def __init__(self, max_zones: int = 15) -> None:
        self.max_zones = max_zones
        self.bullish: list[Zone] = []
        self.bearish: list[Zone] = []
        self._last_htf_time: int | None = None

    # ------------------------------------------------------------------ build
    def ingest_htf(self, htf_bars: Sequence[Candle]) -> list[Zone]:
        """Add gaps for any newly completed HTF bars.

        `htf_bars` must contain COMPLETED bars only, oldest -> newest. Bars already
        processed are skipped, so this is safe to call on every execution bar.
        """
        created: list[Zone] = []
        if len(htf_bars) < 3:
            return created
        for i in range(2, len(htf_bars)):
            bar = htf_bars[i]
            if self._last_htf_time is not None and bar.time <= self._last_htf_time:
                continue
            two_back = htf_bars[i - 2]
            if bar.low > two_back.high:  # BISI
                zone = Zone(ZoneKind.BISI, top=bar.low, bottom=two_back.high,
                            created_htf_time=bar.time)
                self.bullish.append(zone)
                created.append(zone)
            if bar.high < two_back.low:  # SIBI
                zone = Zone(ZoneKind.SIBI, top=two_back.low, bottom=bar.high,
                            created_htf_time=bar.time)
                self.bearish.append(zone)
                created.append(zone)
        self._last_htf_time = htf_bars[-1].time
        self._trim()
        return created

    # ------------------------------------------------------------------ prune
    def prune(self, close: float) -> None:
        """Retire gaps that price has closed completely through."""
        self.bullish = [z for z in self.bullish if close >= z.bottom]
        self.bearish = [z for z in self.bearish if close <= z.top]
        self._trim()

    def _trim(self) -> None:
        if len(self.bullish) > self.max_zones:
            self.bullish = self.bullish[-self.max_zones:]
        if len(self.bearish) > self.max_zones:
            self.bearish = self.bearish[-self.max_zones:]

    # ------------------------------------------------------------------ query
    def price_in_bullish(self, price: float) -> bool:
        return any(z.contains(price) for z in self.bullish)

    def price_in_bearish(self, price: float) -> bool:
        return any(z.contains(price) for z in self.bearish)

    def all_zones(self) -> Iterable[Zone]:
        yield from self.bullish
        yield from self.bearish
