"""The news blackout: 5 minutes before, 5 minutes after, and fail-safe behaviour."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from helpers import mk  # noqa: F401  (keeps sys.path set up)
from ict_bot.config import NewsConfig
from ict_bot.news import (
    CalendarEvent,
    CsvCalendarSource,
    NewsFilter,
    normalise_impact,
    parse_utc,
)

EVENT_TIME = datetime(2026, 7, 30, 12, 30, tzinfo=timezone.utc)  # a classic 08:30 ET print


class StaticSource:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("feed down")
        return self.events


def cfg(**kwargs) -> NewsConfig:
    base = NewsConfig(enabled=True, minutes_before=5, minutes_after=5,
                      impacts=["high"], currencies=["USD"], source="csv")
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


def nfp(impact="high", currency="USD") -> CalendarEvent:
    return CalendarEvent(EVENT_TIME, currency, impact, "Non-Farm Payrolls")


class TestBlackoutWindow(unittest.TestCase):
    def setUp(self):
        self.filter = NewsFilter(cfg(), source=StaticSource([nfp()]))
        self.filter.refresh(EVENT_TIME - timedelta(hours=1), force=True)

    def test_clear_well_before_the_event(self):
        self.assertFalse(self.filter.status(EVENT_TIME - timedelta(minutes=6)).blocked)

    def test_blocked_exactly_five_minutes_before(self):
        self.assertTrue(self.filter.status(EVENT_TIME - timedelta(minutes=5)).blocked)

    def test_blocked_at_the_event(self):
        status = self.filter.status(EVENT_TIME)
        self.assertTrue(status.blocked)
        self.assertIn("Non-Farm Payrolls", status.reason)

    def test_blocked_exactly_five_minutes_after(self):
        self.assertTrue(self.filter.status(EVENT_TIME + timedelta(minutes=5)).blocked)

    def test_clear_once_the_window_has_passed(self):
        self.assertFalse(self.filter.status(EVENT_TIME + timedelta(minutes=6)).blocked)

    def test_asymmetric_windows(self):
        filt = NewsFilter(cfg(minutes_before=30, minutes_after=1), source=StaticSource([nfp()]))
        filt.refresh(EVENT_TIME - timedelta(hours=2), force=True)
        self.assertTrue(filt.status(EVENT_TIME - timedelta(minutes=29)).blocked)
        self.assertFalse(filt.status(EVENT_TIME + timedelta(minutes=2)).blocked)

    def test_zero_windows_block_only_the_instant(self):
        filt = NewsFilter(cfg(minutes_before=0, minutes_after=0), source=StaticSource([nfp()]))
        filt.refresh(EVENT_TIME - timedelta(hours=2), force=True)
        self.assertTrue(filt.status(EVENT_TIME).blocked)
        self.assertFalse(filt.status(EVENT_TIME + timedelta(seconds=1)).blocked)

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            self.filter.status(EVENT_TIME.replace(tzinfo=None))


class TestScope(unittest.TestCase):
    def test_low_impact_events_are_ignored(self):
        filt = NewsFilter(cfg(), source=StaticSource([nfp(impact="low")]))
        filt.refresh(EVENT_TIME - timedelta(hours=1), force=True)
        self.assertFalse(filt.status(EVENT_TIME).blocked)

    def test_other_currencies_are_ignored(self):
        filt = NewsFilter(cfg(), source=StaticSource([nfp(currency="EUR")]))
        filt.refresh(EVENT_TIME - timedelta(hours=1), force=True)
        self.assertFalse(filt.status(EVENT_TIME).blocked)

    def test_medium_can_be_opted_into(self):
        filt = NewsFilter(cfg(impacts=["high", "medium"]),
                          source=StaticSource([nfp(impact="medium")]))
        filt.refresh(EVENT_TIME - timedelta(hours=1), force=True)
        self.assertTrue(filt.status(EVENT_TIME).blocked)

    def test_impact_aliases(self):
        for raw in ("High", "HIGH", "3", "red"):
            self.assertEqual(normalise_impact(raw), "high")


class TestFailSafe(unittest.TestCase):
    def test_unavailable_feed_blocks_by_default(self):
        filt = NewsFilter(cfg(), source=StaticSource([], fail=True))
        filt.refresh(EVENT_TIME, force=True)
        status = filt.status(EVENT_TIME)
        self.assertTrue(status.blocked)
        self.assertIn("fail safe", status.reason)

    def test_unavailable_feed_can_be_configured_to_allow(self):
        filt = NewsFilter(cfg(on_feed_unavailable="allow"), source=StaticSource([], fail=True))
        filt.refresh(EVENT_TIME, force=True)
        self.assertFalse(filt.status(EVENT_TIME).blocked)

    def test_stale_cache_counts_as_unavailable(self):
        filt = NewsFilter(cfg(max_stale_minutes=60), source=StaticSource([nfp()]))
        filt.refresh(EVENT_TIME - timedelta(hours=5), force=True)
        self.assertTrue(filt.status(EVENT_TIME).blocked)     # cache is 5h old
        self.assertIn("stale", filt.status(EVENT_TIME).reason)

    def test_disabled_filter_never_blocks(self):
        filt = NewsFilter(cfg(enabled=False), source=StaticSource([nfp()]))
        self.assertFalse(filt.status(EVENT_TIME).blocked)

    def test_refresh_honours_the_interval(self):
        source = StaticSource([nfp()])
        filt = NewsFilter(cfg(refresh_minutes=60), source=source)
        base = EVENT_TIME - timedelta(hours=3)
        filt.refresh(base, force=True)
        filt.refresh(base + timedelta(minutes=30))
        self.assertEqual(source.calls, 1)
        filt.refresh(base + timedelta(minutes=61))
        self.assertEqual(source.calls, 2)

    def test_a_failed_refresh_keeps_a_fresh_cache_usable(self):
        source = StaticSource([nfp()])
        filt = NewsFilter(cfg(), source=source)
        filt.refresh(EVENT_TIME - timedelta(minutes=30), force=True)
        source.fail = True
        filt.refresh(EVENT_TIME - timedelta(minutes=10), force=True)
        self.assertTrue(filt.status(EVENT_TIME).blocked)      # still knows about NFP
        self.assertIn("Non-Farm", filt.status(EVENT_TIME).reason)


class TestFlatten(unittest.TestCase):
    def test_off_by_default(self):
        filt = NewsFilter(cfg(), source=StaticSource([nfp()]))
        filt.refresh(EVENT_TIME - timedelta(hours=1), force=True)
        self.assertFalse(filt.should_flatten(EVENT_TIME - timedelta(minutes=1)).blocked)

    def test_flattens_inside_the_lead_window(self):
        filt = NewsFilter(cfg(flatten_before_event=True, flatten_minutes_before=2),
                          source=StaticSource([nfp()]))
        filt.refresh(EVENT_TIME - timedelta(hours=1), force=True)
        self.assertTrue(filt.should_flatten(EVENT_TIME - timedelta(minutes=1)).blocked)
        self.assertFalse(filt.should_flatten(EVENT_TIME - timedelta(minutes=3)).blocked)
        self.assertFalse(filt.should_flatten(EVENT_TIME + timedelta(minutes=1)).blocked)


class TestCsvSource(unittest.TestCase):
    def test_reads_and_normalises_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("datetime_utc,currency,impact,title\n")
                fh.write("2026-07-30T12:30:00Z,USD,High,Non-Farm Payrolls\n")
                fh.write("2026-07-30 18:00,USD,3,FOMC Rate Decision\n")
                fh.write("garbage,USD,high,broken row\n")
            events = list(CsvCalendarSource(path).fetch())
        self.assertEqual(len(events), 2)          # the broken row is skipped, not fatal
        self.assertEqual(events[0].time_utc, EVENT_TIME)
        self.assertEqual(events[0].impact, "high")
        self.assertEqual(events[1].impact, "high")

    def test_missing_file_raises_so_the_fail_safe_can_trigger(self):
        with self.assertRaises(FileNotFoundError):
            CsvCalendarSource("/nonexistent/events.csv").fetch()

    def test_timestamp_formats(self):
        self.assertEqual(parse_utc("2026-07-30T12:30:00Z"), EVENT_TIME)
        self.assertEqual(parse_utc("2026-07-30 12:30:00"), EVENT_TIME)
        self.assertEqual(parse_utc(str(int(EVENT_TIME.timestamp()))), EVENT_TIME)


class TestBrokerClockAlignment(unittest.TestCase):
    def test_a_server_bar_time_maps_onto_the_utc_window(self):
        """UT100 bar stamps are broker time; the filter only ever sees real UTC."""
        offset = 3 * 3600                      # broker runs UTC+3
        server_epoch = EVENT_TIME.timestamp() + offset
        utc_now = datetime.fromtimestamp(server_epoch - offset, tz=timezone.utc)
        filt = NewsFilter(cfg(), source=StaticSource([nfp()]))
        filt.refresh(EVENT_TIME - timedelta(hours=1), force=True)
        self.assertTrue(filt.status(utc_now).blocked)
        # forgetting the conversion would look like 15:30 and miss the blackout
        wrong = datetime.fromtimestamp(server_epoch, tz=timezone.utc)
        self.assertFalse(filt.status(wrong).blocked)


if __name__ == "__main__":
    unittest.main()
