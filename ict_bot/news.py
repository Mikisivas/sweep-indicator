"""Economic-calendar blackout filter.

Rule: no NEW entries inside the window around a scheduled event -- by default
5 minutes before and 5 minutes after (both configurable). Spreads gap and fills
turn hostile around releases, and a CISD confirmation printed into an NFP spike
is noise, not delivery.

The `MetaTrader5` Python package exposes no economic calendar, so events come from
a configurable source:

    csv        datetime_utc,currency,impact,title      (recommended: simple, auditable)
    ics        an .ics calendar export (DTSTART / SUMMARY)
    http_json  a JSON endpoint plus a field map
    none       filter effectively disabled

FAIL SAFE: if the filter is enabled and the feed is unavailable or stale, the
default (`on_feed_unavailable: block`) refuses new entries and logs loudly. A
missing calendar must never silently turn the protection off.

All event times are normalised to UTC. The runner converts broker server time to
UTC before asking this module anything -- see mt5_client.session.server_to_utc.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Protocol, Sequence

log = logging.getLogger(__name__)

_IMPACT_ALIASES = {
    "high": "high", "3": "high", "red": "high", "hoch": "high", "h": "high",
    "medium": "medium", "2": "medium", "orange": "medium", "moderate": "medium", "m": "medium",
    "low": "low", "1": "low", "yellow": "low", "l": "low",
    "holiday": "holiday", "none": "low", "": "low",
}


def normalise_impact(value: object) -> str:
    return _IMPACT_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())


def parse_utc(value: str) -> datetime:
    """Parse common calendar timestamp formats into an aware UTC datetime."""
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.isdigit() and len(text) >= 10:  # epoch seconds
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    cleaned = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
                    "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M", "%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"unrecognised calendar timestamp: {value!r}")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class CalendarEvent:
    time_utc: datetime
    currency: str
    impact: str
    title: str

    def __str__(self) -> str:
        return (f"{self.time_utc:%Y-%m-%d %H:%M UTC} {self.currency} "
                f"[{self.impact}] {self.title}")


class CalendarSource(Protocol):
    def fetch(self) -> Sequence[CalendarEvent]:
        """Return all known events, or raise if the feed cannot be read."""


class NullCalendarSource:
    def fetch(self) -> Sequence[CalendarEvent]:
        return []


class CsvCalendarSource:
    """CSV with headers: datetime_utc, currency, impact, title.

    Column aliases are accepted (date/time/datetime, country/currency,
    impact/importance, title/event/name). Blank lines and lines starting with '#'
    are ignored, so the file can be annotated by hand.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def fetch(self) -> Sequence[CalendarEvent]:
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"calendar file not found: {self.path}")
        events: list[CalendarEvent] = []
        with open(self.path, "r", encoding="utf-8-sig", newline="") as fh:
            lines = [line for line in fh
                     if line.strip() and not line.lstrip().startswith("#")]
            for row in csv.DictReader(lines):
                keys = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
                raw_time = _first(keys, ("datetime_utc", "datetime", "date_utc", "date", "time"))
                if not raw_time:
                    continue
                if "date" in keys and "time" in keys and keys["date"] and keys["time"]:
                    raw_time = f"{keys['date'].strip()} {keys['time'].strip()}"
                try:
                    when = parse_utc(raw_time)
                except ValueError:
                    log.warning("skipping calendar row with bad timestamp: %r", raw_time)
                    continue
                events.append(CalendarEvent(
                    time_utc=when,
                    currency=_first(keys, ("currency", "country", "ccy")).strip().upper(),
                    impact=normalise_impact(_first(keys, ("impact", "importance", "level"))),
                    title=_first(keys, ("title", "event", "name", "summary")).strip(),
                ))
        return events


class IcsCalendarSource:
    """Minimal .ics reader (DTSTART + SUMMARY).

    Currency/impact are inferred from the summary text, so tag your entries like
    "USD High CPI m/m" if you rely on filtering by them.
    """

    _DT = re.compile(r"^DTSTART[^:]*:(?P<value>.+)$", re.IGNORECASE)
    _SUM = re.compile(r"^SUMMARY:(?P<value>.+)$", re.IGNORECASE)

    def __init__(self, path: str) -> None:
        self.path = path

    def fetch(self) -> Sequence[CalendarEvent]:
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"calendar file not found: {self.path}")
        events: list[CalendarEvent] = []
        when: Optional[datetime] = None
        summary = ""
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.upper() == "BEGIN:VEVENT":
                    when, summary = None, ""
                elif match := self._DT.match(line):
                    try:
                        when = parse_utc(match.group("value").strip())
                    except ValueError:
                        when = None
                elif match := self._SUM.match(line):
                    summary = match.group("value").strip()
                elif line.upper() == "END:VEVENT" and when is not None:
                    upper = summary.upper()
                    currency = next((c for c in ("USD", "EUR", "GBP", "JPY", "CHF",
                                                 "CAD", "AUD", "NZD") if c in upper), "USD")
                    impact = ("high" if "HIGH" in upper else
                              "medium" if "MEDIUM" in upper else "low")
                    events.append(CalendarEvent(when, currency, impact, summary))
                    when, summary = None, ""
        return events


class HttpJsonCalendarSource:
    """JSON endpoint + field map. Uses urllib so it honours proxy env vars."""

    def __init__(self, url: str, field_map: dict, timeout: float = 15.0) -> None:
        self.url = url
        self.field_map = field_map or {}
        self.timeout = timeout

    def fetch(self) -> Sequence[CalendarEvent]:
        import urllib.request

        req = urllib.request.Request(self.url, headers={"User-Agent": "ict-bot/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        rows = payload if isinstance(payload, list) else (
            payload.get("events") or payload.get("data") or payload.get("result") or []
        )
        fm = {
            "time": self.field_map.get("time", "date"),
            "currency": self.field_map.get("currency", "country"),
            "impact": self.field_map.get("impact", "impact"),
            "title": self.field_map.get("title", "title"),
        }
        events: list[CalendarEvent] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get(fm["time"]):
                continue
            try:
                when = parse_utc(row[fm["time"]])
            except ValueError:
                continue
            events.append(CalendarEvent(
                time_utc=when,
                currency=str(row.get(fm["currency"], "")).strip().upper(),
                impact=normalise_impact(row.get(fm["impact"], "")),
                title=str(row.get(fm["title"], "")).strip(),
            ))
        return events


def build_source(cfg) -> CalendarSource:
    if not cfg.enabled or cfg.source == "none":
        return NullCalendarSource()
    if cfg.source == "csv":
        return CsvCalendarSource(cfg.path)
    if cfg.source == "ics":
        return IcsCalendarSource(cfg.path)
    if cfg.source == "http_json":
        if not cfg.url:
            raise ValueError("news.source=http_json requires news.url")
        return HttpJsonCalendarSource(cfg.url, cfg.http_field_map)
    raise ValueError(f"unknown news.source: {cfg.source}")


@dataclass
class BlackoutStatus:
    blocked: bool
    reason: str = ""
    event: Optional[CalendarEvent] = None


class NewsFilter:
    """Answers one question: may we open a NEW position right now?

    Events are cached and refreshed every `refresh_minutes`; a cache older than
    `max_stale_minutes` counts as no feed at all.
    """

    def __init__(self, cfg, source: Optional[CalendarSource] = None) -> None:
        self.cfg = cfg
        self.source = source if source is not None else build_source(cfg)
        self._events: list[CalendarEvent] = []
        self._fetched_at: Optional[datetime] = None
        self._last_error: str = ""

    # ------------------------------------------------------------------- feed
    def refresh(self, now_utc: Optional[datetime] = None, force: bool = False) -> bool:
        """Reload the calendar if due. Returns True when the cache is usable."""
        now = now_utc or datetime.now(timezone.utc)
        if not self.cfg.enabled:
            return True
        due = (
            force
            or self._fetched_at is None
            or (now - self._fetched_at) >= timedelta(minutes=self.cfg.refresh_minutes)
        )
        if not due:
            return True
        try:
            events = list(self.source.fetch())
        except Exception as exc:  # feed problems must never crash the trading loop
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.error("news calendar fetch failed: %s", self._last_error)
            return self.feed_is_fresh(now)
        self._events = sorted(self._filter_relevant(events), key=lambda e: e.time_utc)
        self._fetched_at = now
        self._last_error = ""
        log.info("news calendar loaded: %d relevant events (of %d)", len(self._events), len(events))
        return True

    def _filter_relevant(self, events: Iterable[CalendarEvent]) -> list[CalendarEvent]:
        impacts = {normalise_impact(i) for i in self.cfg.impacts}
        currencies = {c.strip().upper() for c in self.cfg.currencies}
        out = []
        for event in events:
            if impacts and event.impact not in impacts:
                continue
            if currencies and event.currency and event.currency not in currencies:
                continue
            out.append(event)
        return out

    def feed_is_fresh(self, now_utc: datetime) -> bool:
        if self._fetched_at is None:
            return False
        return (now_utc - self._fetched_at) <= timedelta(minutes=self.cfg.max_stale_minutes)

    # -------------------------------------------------------------- blackout
    def status(self, now_utc: datetime) -> BlackoutStatus:
        """Blackout state at `now_utc` (must be timezone-aware UTC)."""
        if not self.cfg.enabled:
            return BlackoutStatus(False)
        if now_utc.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware")

        if isinstance(self.source, NullCalendarSource):
            return BlackoutStatus(False, "news filter enabled with source=none")

        if not self.feed_is_fresh(now_utc):
            if self.cfg.on_feed_unavailable == "block":
                return BlackoutStatus(
                    True,
                    "news calendar unavailable/stale - blocking entries (fail safe)"
                    + (f": {self._last_error}" if self._last_error else ""),
                )
            return BlackoutStatus(False, "news calendar unavailable - configured to allow")

        before = timedelta(minutes=self.cfg.minutes_before)
        after = timedelta(minutes=self.cfg.minutes_after)
        for event in self._events:
            if event.time_utc - before <= now_utc <= event.time_utc + after:
                delta_min = (event.time_utc - now_utc).total_seconds() / 60.0
                when = (f"in {delta_min:.1f} min" if delta_min >= 0
                        else f"{-delta_min:.1f} min ago")
                return BlackoutStatus(
                    True, f"news blackout ({when}): {event}", event
                )
        return BlackoutStatus(False)

    def should_flatten(self, now_utc: datetime) -> BlackoutStatus:
        """Optional: close open positions shortly before an event."""
        if not (self.cfg.enabled and self.cfg.flatten_before_event):
            return BlackoutStatus(False)
        if not self.feed_is_fresh(now_utc):
            return BlackoutStatus(False)
        lead = timedelta(minutes=self.cfg.flatten_minutes_before)
        for event in self._events:
            if event.time_utc - lead <= now_utc <= event.time_utc:
                return BlackoutStatus(True, f"flatten before news: {event}", event)
        return BlackoutStatus(False)

    def next_event(self, now_utc: datetime) -> Optional[CalendarEvent]:
        return next((e for e in self._events if e.time_utc >= now_utc), None)


def _first(mapping: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if value:
            return str(value)
    return ""
