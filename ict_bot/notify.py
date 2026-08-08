"""Outbound alerts: Telegram broadcast plus optional Discord.

Never allowed to break the trading loop -- every failure is logged and swallowed.
Command handling lives in telegram.py; this module only pushes messages out.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from .telegram import TelegramClient

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg, telegram: Optional[TelegramClient] = None) -> None:
        self.cfg = cfg
        self.telegram = telegram if telegram is not None else TelegramClient(cfg)
        self.enabled = bool(self.telegram.enabled or cfg.discord_webhook)

    def send(self, text: str, kind: str = "info") -> None:
        if not self.enabled or not self._wanted(kind):
            return
        if self.telegram.enabled:
            self.telegram.broadcast(text)
        if self.cfg.discord_webhook:
            self._post(self.cfg.discord_webhook, {"content": text[:1900]})

    def send_admin(self, text: str) -> None:
        """Operational replies go to the controller only, not the whole audience."""
        if self.telegram.enabled:
            self.telegram.send_admin(text)

    def _wanted(self, kind: str) -> bool:
        if kind == "entry":
            return self.cfg.notify_entries
        if kind == "exit":
            return self.cfg.notify_exits
        if kind == "block":
            return self.cfg.notify_blocks
        return True

    def _post(self, url: str, payload: dict) -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json", "User-Agent": "ict-bot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10):  # noqa: S310
                pass
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("discord notification failed: %s", exc)
