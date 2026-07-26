"""Swing pivot detection -- the liquidity pools of the model.

Port of Pine's ``ta.pivotlow(low, len, len)`` / ``ta.pivothigh(high, len, len)``:
a pivot is only CONFIRMED ``len`` bars after it printed, and its value is the
low/high of that centre bar. A confirmed pivot low is sell-side liquidity resting
below the market; a confirmed pivot high is buy-side liquidity above it.

Comparison is strict on both sides (the centre must be strictly lower/higher than
every bar in the left and right windows), matching the indicator's description:
"needs this many higher-lows / lower-highs on each side". A flat double-bottom is
therefore not a pivot.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..models import Candle


def confirmed_pivot_low(bars: Sequence[Candle], strength: int) -> Optional[float]:
    """Return the pivot low confirmed by the LAST bar in `bars`, else None.

    `bars` is ordered oldest -> newest and must hold at least ``2*strength+1``
    bars for a pivot to be confirmable.
    """
    if strength < 1 or len(bars) < 2 * strength + 1:
        return None
    centre = bars[-(strength + 1)]
    window = bars[-(2 * strength + 1):]
    for offset, bar in enumerate(window):
        if offset == strength:
            continue
        if bar.low <= centre.low:
            return None
    return centre.low


def confirmed_pivot_high(bars: Sequence[Candle], strength: int) -> Optional[float]:
    """Return the pivot high confirmed by the LAST bar in `bars`, else None."""
    if strength < 1 or len(bars) < 2 * strength + 1:
        return None
    centre = bars[-(strength + 1)]
    window = bars[-(2 * strength + 1):]
    for offset, bar in enumerate(window):
        if offset == strength:
            continue
        if bar.high >= centre.high:
            return None
    return centre.high
