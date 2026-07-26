"""Telegram / Discord push. Never allowed to break the trading loop."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.enabled = bool(
            (cfg.telegram_bot_token and cfg.telegram_chat_id) or cfg.discord_webhook
        )

    def send(self, text: str, kind: str = "info") -> None:
        if not self.enabled:
            return
        if kind == "entry" and not self.cfg.notify_entries:
            return
        if kind == "exit" and not self.cfg.notify_exits:
            return
        if kind == "block" and not self.cfg.notify_blocks:
            return
        if self.cfg.telegram_bot_token and self.cfg.telegram_chat_id:
            self._post(
                f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage",
                {"chat_id": self.cfg.telegram_chat_id, "text": text,
                 "disable_web_page_preview": True},
            )
        if self.cfg.discord_webhook:
            self._post(self.cfg.discord_webhook, {"content": text[:1900]})

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
            log.warning("notification failed (%s): %s", url.split("/")[2], exc)
