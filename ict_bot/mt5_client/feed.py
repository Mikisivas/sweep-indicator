"""Market data.

THE non-repainting rule of this bot: `copy_rates_from_pos(..., 0, n)` returns the
still-forming bar last, and we always drop it. Every decision -- execution
timeframe and 4H alike -- is taken on CLOSED bars only, exactly like the
indicator's `barstate.isconfirmed` / completed-HTF-bar logic.
"""

from __future__ import annotations

import csv
import logging
from typing import Optional

from ..models import Candle
from .session import Mt5Session, Mt5Unavailable, timeframe_const

log = logging.getLogger(__name__)


def _to_candle(row) -> Candle:
    """Accept a numpy void row (what MT5 returns), a mapping, or a plain tuple."""
    try:
        try:
            volume = float(row["tick_volume"])
        except (AttributeError, TypeError, KeyError, IndexError, ValueError):
            volume = 0.0
        return Candle(
            time=int(row["time"]), open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]), volume=volume,
        )
    except (TypeError, KeyError, IndexError, ValueError):
        return Candle(time=int(row[0]), open=float(row[1]), high=float(row[2]),
                      low=float(row[3]), close=float(row[4]),
                      volume=float(row[5]) if len(row) > 5 else 0.0)


class MarketFeed:
    def __init__(self, session: Mt5Session) -> None:
        self.session = session

    def closed_bars(self, timeframe: str, count: int) -> list[Candle]:
        """The last `count` CLOSED bars, oldest -> newest."""
        mt5 = self.session.mt5
        rates = mt5.copy_rates_from_pos(self.session.symbol, timeframe_const(timeframe),
                                        0, count + 1)
        if rates is None or len(rates) == 0:
            raise Mt5Unavailable(
                f"no {timeframe} history for {self.session.symbol}: {mt5.last_error()}")
        bars = [_to_candle(row) for row in rates]
        closed = bars[:-1]  # the newest row is the bar still forming - never trade it
        if not closed:
            raise Mt5Unavailable(f"only a forming {timeframe} bar available")
        return closed

    def latest_closed(self, timeframe: str) -> Optional[Candle]:
        bars = self.closed_bars(timeframe, 2)
        return bars[-1] if bars else None


def load_csv_candles(path: str) -> list[Candle]:
    """Offline OHLC for backtests: time,open,high,low,close[,volume].

    `time` may be epoch seconds or an ISO timestamp; it is treated as broker
    server time, exactly like MT5 rates.
    """
    from ..news import parse_utc

    candles: list[Candle] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            keys = {(k or "").strip().lower(): v for k, v in row.items()}
            raw_time = keys.get("time") or keys.get("date") or keys.get("datetime")
            if not raw_time:
                continue
            if str(raw_time).strip().isdigit():
                stamp = int(raw_time)
            else:
                if keys.get("date") and keys.get("time") and "datetime" not in keys:
                    raw_time = f"{keys['date']} {keys['time']}"
                stamp = int(parse_utc(str(raw_time)).timestamp())
            candles.append(Candle(
                time=stamp,
                open=float(keys["open"]), high=float(keys["high"]),
                low=float(keys["low"]), close=float(keys["close"]),
                volume=float(keys.get("volume") or 0.0),
            ))
    candles.sort(key=lambda c: c.time)
    log.info("loaded %d candles from %s", len(candles), path)
    return candles
