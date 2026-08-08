"""Crash-safe state.

The signal engine itself is NOT persisted -- on restart it is rebuilt by replaying
history, which is deterministic and cannot drift from a stale snapshot. What we do
persist is the small set of facts that history cannot tell us:

    last_acted_bar_time   the newest bar we already made a decision on, so a
                          restart can never enter the same setup twice
    daily book            kill-switch accounting for the current broker day
    pending limits        limit_at_ob orders awaiting fill or expiry
    managed positions     each open trade's ORIGINAL stop, so break-even can
                          still measure R after the stop has been moved
    paused                an admin /pause survives a restart
    subscribers           Telegram chats that self-subscribed to alerts

Writes are atomic (temp file + os.replace) so a kill mid-write cannot corrupt it.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

STATE_VERSION = 1


@dataclass
class PendingLimit:
    ticket: int
    direction: str
    placed_bar_time: int
    expires_bar_time: int
    entry: float
    stop: float
    take_profit: float
    volume: float


@dataclass
class ManagedPosition:
    """What we must remember about an open trade once its stop starts moving.

    After break-even fires, the live SL equals the entry, so the original risk
    is gone from the broker's view. Without this record R could never be
    measured again.
    """

    ticket: int
    direction: str
    entry: float
    original_stop: float
    take_profit: float
    volume: float
    opened_at: int = 0
    break_even_done: bool = False

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.original_stop)


@dataclass
class BotState:
    version: int = STATE_VERSION
    symbol: str = ""
    last_acted_bar_time: int = 0
    day: str = ""
    day_start_equity: float = 0.0
    day_trades: int = 0
    day_realised: float = 0.0
    day_halted: bool = False
    day_halt_reason: str = ""
    pending_limits: list[dict] = field(default_factory=list)
    managed_positions: list[dict] = field(default_factory=list)
    paused: bool = False
    subscribers: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------- io
    @classmethod
    def load(cls, path: str, symbol: str) -> "BotState":
        if not os.path.exists(path):
            return cls(symbol=symbol)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw: dict[str, Any] = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("state file unreadable (%s) - starting clean but NOT trading "
                      "until the next fresh bar", exc)
            return cls(symbol=symbol)
        if raw.get("version") != STATE_VERSION:
            log.warning("state file version %s != %s - ignoring it",
                        raw.get("version"), STATE_VERSION)
            return cls(symbol=symbol)
        if raw.get("symbol") and raw["symbol"] != symbol:
            log.warning("state file belongs to %s, not %s - ignoring it",
                        raw["symbol"], symbol)
            return cls(symbol=symbol)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: str) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---------------------------------------------------------------- book
    def absorb_book(self, book) -> None:
        """Copy the live daily book into the snapshot."""
        self.day = book.day.isoformat() if book.day else ""
        self.day_start_equity = book.start_equity
        self.day_trades = book.trades
        self.day_realised = book.realised
        self.day_halted = book.halted
        self.day_halt_reason = book.halt_reason

    def restore_book(self, book) -> None:
        """Reinstate the daily book so a restart cannot reset the kill switch."""
        if not self.day:
            return
        try:
            book.day = date.fromisoformat(self.day)
        except ValueError:
            return
        book.start_equity = self.day_start_equity
        book.trades = self.day_trades
        book.realised = self.day_realised
        book.halted = self.day_halted
        book.halt_reason = self.day_halt_reason
        if book.halted:
            log.warning("restored an ACTIVE kill switch from state: %s", book.halt_reason)

    # -------------------------------------------------------------- limits
    def add_pending(self, pending: PendingLimit) -> None:
        self.pending_limits.append(asdict(pending))

    def drop_pending(self, ticket: int) -> None:
        self.pending_limits = [p for p in self.pending_limits if p.get("ticket") != ticket]

    def pendings(self) -> list[PendingLimit]:
        return [PendingLimit(**p) for p in self.pending_limits]

    # ----------------------------------------------------- managed positions
    def managed(self) -> dict[int, ManagedPosition]:
        out: dict[int, ManagedPosition] = {}
        for row in self.managed_positions:
            try:
                position = ManagedPosition(**row)
            except TypeError:
                continue  # a record from an older layout; drop it rather than crash
            out[position.ticket] = position
        return out

    def put_managed(self, position: ManagedPosition) -> None:
        self.managed_positions = [r for r in self.managed_positions
                                  if r.get("ticket") != position.ticket]
        self.managed_positions.append(asdict(position))

    def drop_managed(self, ticket: int) -> None:
        self.managed_positions = [r for r in self.managed_positions
                                  if r.get("ticket") != ticket]

    def keep_only_managed(self, tickets) -> None:
        """Forget positions the broker no longer reports as open."""
        live = {int(t) for t in tickets}
        self.managed_positions = [r for r in self.managed_positions
                                  if int(r.get("ticket", -1)) in live]


def find_state_path(configured: str, symbol: str) -> str:
    """Keep separate state per symbol so two bots never share a file."""
    if not configured:
        return os.path.join("state", f"ict_bot_{symbol}.json")
    root, ext = os.path.splitext(configured)
    return f"{root}_{symbol}{ext or '.json'}"


def load_state(path: str, symbol: str) -> tuple[BotState, str]:
    resolved = find_state_path(path, symbol)
    return BotState.load(resolved, symbol), resolved
