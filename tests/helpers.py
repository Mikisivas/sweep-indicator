"""Synthetic candle builders shared by the tests."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ict_bot.models import Candle  # noqa: E402

START = 1_700_000_000  # arbitrary fixed epoch so tests are deterministic
STEP = 900             # M15


def mk(rows, start: int = START, step: int = STEP) -> list[Candle]:
    """rows: iterable of (open, high, low, close)."""
    return [
        Candle(time=start + i * step, open=o, high=h, low=l, close=c)
        for i, (o, h, l, c) in enumerate(rows)
    ]


# A complete bullish scenario, hand-built so every level is known exactly:
#   bar 2  prints the swing low at 100 (confirmed on bar 4, strength 2)
#   bar 4  prints the swing high at 109 (confirmed on bar 6)
#   bars 5-7 are three consecutive down-closes; bar 7 wicks to 99, sweeping the
#          sell-side liquidity under 100. CISD level = open of bar 5 = 108.
#   bar 8  closes at 109, above 108 -> CISD confirmed, trade is live immediately.
BULL_ROWS = [
    (110, 111, 108, 109),   # 0
    (109, 110, 106, 107),   # 1
    (107, 108, 100, 104),   # 2  <- swing low 100
    (104, 107, 103, 106),   # 3
    (106, 109, 105, 108),   # 4  <- swing high 109
    (108, 108, 106, 107),   # 5  down close, start of the sweep series
    (107, 108, 104, 105),   # 6  down close
    (105, 106,  99, 101),   # 7  down close, sweeps 100 -> low 99
    (101, 108.5, 100, 108.5),  # 8  closes above 108 -> CISD confirmed
]

BULL_CISD_LEVEL = 108.0
BULL_SWEPT_POOL = 100.0
BULL_SWEPT_LOW = 99.0
BULL_PIVOT_HIGH = 109.0

# Mirror image for the bearish path.
BEAR_ROWS = [
    (90, 92, 89, 91),       # 0
    (91, 94, 90, 93),       # 1
    (93, 100, 92, 96),      # 2  <- swing high 100
    (96, 97, 93, 94),       # 3
    (94, 95, 91, 92),       # 4  <- swing low 91
    (92, 94, 92, 93),       # 5  up close, start of the sweep series
    (93, 96, 92, 95),       # 6  up close
    (95, 101, 94, 99),      # 7  up close, sweeps 100 -> high 101
    (99, 100, 91.5, 91.6),  # 8  closes below 92 -> CISD confirmed
]

BEAR_CISD_LEVEL = 92.0
BEAR_SWEPT_POOL = 100.0
BEAR_SWEPT_HIGH = 101.0
BEAR_PIVOT_LOW = 91.0


def bull_engine(**kwargs):
    from ict_bot.signals.engine import IctSignalEngine

    params = dict(swing_len=2, max_wait=5, chain_max=10)
    params.update(kwargs)
    return IctSignalEngine(**params)
