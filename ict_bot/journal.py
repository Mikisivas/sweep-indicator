"""CSV audit trail of every signal and every decision.

Two jobs:
  * a record of why the bot did or did not trade (spread, session, news, risk);
  * the file you diff against the TradingView indicator to prove parity -- every
    sweep / confirmation / invalidation with its bar time and levels.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from .models import EventKind, FinalTrade, SignalEvent

log = logging.getLogger(__name__)

COLUMNS = [
    "logged_utc", "bar_time_server", "bar_time_utc", "event", "direction",
    "price", "swept_pool", "swept_extreme", "cisd_level", "reference_entry",
    "entry", "stop", "take_profit", "rr", "tp_source", "htf_ok",
    "action", "volume", "ticket", "detail",
]


class SignalJournal:
    def __init__(self, path: str, to_utc=None) -> None:
        self.path = path
        self.to_utc = to_utc or (lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc))
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    csv.writer(fh).writerow(COLUMNS)

    def write(self, event: SignalEvent, action: str = "", trade: Optional[FinalTrade] = None,
              volume: float = 0.0, ticket: int = 0, detail: str = "") -> None:
        if not self.path:
            return
        setup = event.setup
        plan = event.plan
        row = {
            "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bar_time_server": event.bar_time,
            "bar_time_utc": self.to_utc(event.bar_time).isoformat(timespec="seconds"),
            "event": event.kind.value,
            "direction": event.direction.value,
            "price": event.price,
            "swept_pool": setup.swept_pool if setup else "",
            "swept_extreme": setup.swept_extreme if setup else "",
            "cisd_level": setup.cisd_level if setup else "",
            "reference_entry": plan.reference_entry if plan else "",
            "entry": trade.entry if trade else "",
            "stop": trade.stop if trade else (plan.stop if plan else ""),
            "take_profit": trade.take_profit if trade else "",
            "rr": round(trade.rr, 2) if trade else "",
            "tp_source": trade.tp_source if trade else "",
            "htf_ok": setup.htf_ok if setup else "",
            "action": action,
            "volume": volume or "",
            "ticket": ticket or "",
            "detail": detail or event.note,
        }
        try:
            with open(self.path, "a", encoding="utf-8", newline="") as fh:
                csv.DictWriter(fh, fieldnames=COLUMNS).writerow(row)
        except OSError as exc:
            log.warning("could not write the signal journal: %s", exc)


def describe_event(event: SignalEvent, digits: int = 2) -> str:
    setup = event.setup
    if event.kind is EventKind.SWEEP and setup:
        return (f"SWEEP {event.direction.value}: pool {setup.swept_pool:.{digits}f} taken at "
                f"{event.price:.{digits}f}, CISD level {setup.cisd_level:.{digits}f}, "
                f"HTF FVG {'yes' if setup.htf_ok else 'no'}")
    if event.kind is EventKind.CONFIRMED and event.plan:
        plan = event.plan
        return (f"CISD CONFIRMED {event.direction.value} @ {plan.signal_price:.{digits}f} "
                f"(order block {plan.reference_entry:.{digits}f}, stop "
                f"{plan.stop:.{digits}f}) - entering now, no retest")
    return f"{event.kind.value.upper()} {event.direction.value}: {event.note}"
