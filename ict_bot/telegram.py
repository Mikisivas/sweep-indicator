"""Telegram: alerts out to many, commands in from exactly one.

Two separate channels, deliberately asymmetric:

  BROADCAST  every subscriber and every configured chat id receives alerts.
             Read-only. Nothing they type can reach the trading logic.

  ADMIN      one single chat id, set in config, may send commands. Every other
             chat is ignored no matter what it sends. There is no command that
             promotes another chat to admin, and a blank admin id means
             "nobody", never "anybody" -- config validation refuses to start
             with commands enabled and no admin set.

Polling is non-blocking (`getUpdates` with timeout=0) and is called from the same
single-threaded loop as everything else, so a Telegram outage can never stall or
crash trading -- it just means no commands arrive until it recovers.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


@dataclass
class Command:
    name: str                       # "status", "close", ... (no leading slash)
    args: list[str] = field(default_factory=list)
    chat_id: str = ""
    sender: str = ""
    raw: str = ""

    @property
    def arg(self) -> str:
        return self.args[0] if self.args else ""


class TelegramClient:
    def __init__(self, cfg, timeout: float = 10.0) -> None:
        self.cfg = cfg
        self.timeout = timeout
        self.token = cfg.telegram_bot_token
        self.admin = str(cfg.telegram_admin_chat_id or "").strip()
        self._offset: Optional[int] = None
        self._subscribers: set[str] = set()
        self._failures = 0

    # ------------------------------------------------------------------ state
    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @property
    def commands_enabled(self) -> bool:
        return bool(self.token and self.cfg.telegram_commands_enabled and self.admin)

    def load_subscribers(self, chat_ids) -> None:
        self._subscribers = {str(c) for c in (chat_ids or []) if str(c).strip()}

    def subscribers(self) -> list[str]:
        return sorted(self._subscribers)

    def audience(self) -> list[str]:
        """Everyone who gets alerts: configured chats, the legacy single chat,
        self-subscribed users, and the admin."""
        out: set[str] = set(self._subscribers)
        out.update(str(c).strip() for c in (self.cfg.telegram_broadcast_chat_ids or []))
        if self.cfg.telegram_chat_id:
            out.add(str(self.cfg.telegram_chat_id).strip())
        if self.admin:
            out.add(self.admin)
        return sorted(c for c in out if c)

    def is_admin(self, chat_id) -> bool:
        return bool(self.admin) and str(chat_id) == self.admin

    # -------------------------------------------------------------- transport
    def _call(self, method: str, payload: dict) -> Optional[dict]:
        if not self.token:
            return None
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                API.format(token=self.token, method=method), data=data,
                headers={"Content-Type": "application/json", "User-Agent": "ict-bot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                body = json.loads(resp.read().decode("utf-8"))
            self._failures = 0
            return body if body.get("ok") else None
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            self._failures += 1
            if self._failures <= 3 or self._failures % 20 == 0:
                log.warning("telegram %s failed (%d in a row): %s", method, self._failures, exc)
            return None

    def send(self, chat_id: str, text: str) -> bool:
        result = self._call("sendMessage", {
            "chat_id": str(chat_id), "text": text[:4000],
            "disable_web_page_preview": True,
        })
        return result is not None

    def broadcast(self, text: str) -> int:
        """Send to everyone. Returns how many chats accepted it."""
        delivered = 0
        for chat_id in self.audience():
            if self.send(chat_id, text):
                delivered += 1
        return delivered

    def send_admin(self, text: str) -> bool:
        return self.send(self.admin, text) if self.admin else False

    # ---------------------------------------------------------------- polling
    def poll(self) -> list[Command]:
        """Fetch new messages. Returns ADMIN commands only.

        Non-admin traffic is handled here (a /start subscribes the sender when
        that is enabled) and never reaches the caller, so the trading loop cannot
        act on a stranger's message even by accident.
        """
        if not self.enabled:
            return []
        payload = {"timeout": 0, "allowed_updates": ["message"]}
        if self._offset is not None:
            payload["offset"] = self._offset
        body = self._call("getUpdates", payload)
        if not body:
            return []

        commands: list[Command] = []
        for update in body.get("result", []):
            self._offset = int(update["update_id"]) + 1
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            if not text or not chat_id:
                continue
            sender = (message.get("from") or {}).get("username") \
                or (message.get("from") or {}).get("first_name") or "?"

            if not text.startswith("/"):
                continue
            parts = text.split()
            name = parts[0].lstrip("/").split("@")[0].lower()   # /status@MyBot -> status
            args = parts[1:]

            if self.is_admin(chat_id):
                if not self.cfg.telegram_commands_enabled:
                    continue
                commands.append(Command(name, args, chat_id, sender, text))
                continue

            # Everyone else is read-only.
            self._handle_guest(name, args, chat_id, sender)
        return commands

    def _handle_guest(self, name: str, args: list[str], chat_id: str, sender: str) -> None:
        if name not in ("start", "stop", "help"):
            log.info("ignoring /%s from non-admin chat %s (%s)", name, chat_id, sender)
            return
        if name == "stop":
            if chat_id in self._subscribers:
                self._subscribers.discard(chat_id)
                self.send(chat_id, "Unsubscribed. You will no longer receive alerts.")
            return
        if name == "help":
            self.send(chat_id, "You receive trade alerts from this bot. It is read-only "
                               "for you - only the administrator can control it.\n"
                               "/start to subscribe, /stop to unsubscribe.")
            return
        # /start
        if not self.cfg.telegram_allow_self_subscribe:
            self.send(chat_id, "This bot is invite-only. Ask the administrator to add you.")
            return
        if self.cfg.telegram_join_password:
            supplied = args[0] if args else ""
            if supplied != self.cfg.telegram_join_password:
                self.send(chat_id, "Wrong or missing password. Use: /start <password>")
                log.warning("rejected /start from chat %s (%s): bad password", chat_id, sender)
                return
        if chat_id in self._subscribers:
            self.send(chat_id, "You are already subscribed to alerts.")
            return
        self._subscribers.add(chat_id)
        self.send(chat_id, "Subscribed. You will receive trade alerts here.\n"
                           "This is read-only - only the administrator can control the bot.")
        log.info("new alert subscriber: chat %s (%s)", chat_id, sender)

    def add_subscriber(self, chat_id: str) -> None:
        self._subscribers.add(str(chat_id))

    def remove_subscriber(self, chat_id: str) -> bool:
        if str(chat_id) in self._subscribers:
            self._subscribers.discard(str(chat_id))
            return True
        return False


HELP_TEXT = """ICT bot commands (admin only)

/status          equity, open positions, armed setups, filters
/positions       open positions with live P&L
/pause           stop taking NEW entries (open trades keep SL/TP)
/resume          allow new entries again
/close <ticket>  close one position
/closeall        close every position this bot opened
/be <ticket>     move a stop to break-even now
/risk <pct>      change risk per trade, e.g. /risk 0.5
/subscribers     list who receives alerts
/kick <chat_id>  remove an alert subscriber
/stop confirm    shut the bot down (positions keep their SL/TP)
/help            this message"""


def dispatch(command: Command, handlers: dict[str, Callable[[Command], str]]) -> str:
    """Run a handler and turn any failure into a readable reply."""
    handler = handlers.get(command.name)
    if handler is None:
        return f"Unknown command /{command.name}. Send /help for the list."
    try:
        return handler(command)
    except Exception as exc:  # noqa: BLE001 - a bad command must never stop trading
        log.exception("telegram command /%s failed", command.name)
        return f"/{command.name} failed: {type(exc).__name__}: {exc}"
