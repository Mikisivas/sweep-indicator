"""Terminal session: connect, resolve the symbol, keep the clock straight.

Two things here are non-negotiable for UT100 on MEXAtlantic:

1. CONTRACT SPECS ARE READ LIVE. The Market Watch panel displays "Tick size 0.00 /
   Tick value 0" purely because it rounds for display. Nothing in this bot ever
   hardcodes a spec; if a value cannot be resolved we refuse to trade.

2. THE SERVER CLOCK IS NOT UTC. MT5 rate/tick timestamps are the broker's server
   time packed into an epoch integer. The news filter works in real UTC, so the
   offset is detected from a live tick (or pinned in config) and applied.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..models import SymbolSpec

log = logging.getLogger(__name__)

_TIMEFRAMES = {
    "M1": "TIMEFRAME_M1", "M2": "TIMEFRAME_M2", "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4", "M5": "TIMEFRAME_M5", "M6": "TIMEFRAME_M6",
    "M10": "TIMEFRAME_M10", "M12": "TIMEFRAME_M12", "M15": "TIMEFRAME_M15",
    "M20": "TIMEFRAME_M20", "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1", "H2": "TIMEFRAME_H2", "H3": "TIMEFRAME_H3",
    "H4": "TIMEFRAME_H4", "H6": "TIMEFRAME_H6", "H8": "TIMEFRAME_H8",
    "H12": "TIMEFRAME_H12", "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}

TIMEFRAME_SECONDS = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360, "M10": 600,
    "M12": 720, "M15": 900, "M20": 1200, "M30": 1800, "H1": 3600, "H2": 7200,
    "H3": 10800, "H4": 14400, "H6": 21600, "H8": 28800, "H12": 43200,
    "D1": 86400, "W1": 604800,
}


class Mt5Unavailable(RuntimeError):
    pass


def mt5_api():
    """Import MetaTrader5 on demand with an actionable error message."""
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise Mt5Unavailable(
            "The MetaTrader5 package could not be imported. It is Windows-only and "
            "needs a running MT5 terminal (native Windows, or Windows under Wine). "
            "Install it with: pip install MetaTrader5"
        ) from exc
    return mt5


def timeframe_const(name: str):
    key = name.strip().upper()
    if key not in _TIMEFRAMES:
        raise ValueError(f"unsupported timeframe {name!r}; use one of {sorted(_TIMEFRAMES)}")
    mt5 = mt5_api()
    return getattr(mt5, _TIMEFRAMES[key])


def timeframe_seconds(name: str) -> int:
    key = name.strip().upper()
    if key not in TIMEFRAME_SECONDS:
        raise ValueError(f"unsupported timeframe {name!r}")
    return TIMEFRAME_SECONDS[key]


@dataclass
class AccountSnapshot:
    login: int
    server: str
    currency: str
    balance: float
    equity: float
    free_margin: float
    leverage: int
    trade_allowed: bool
    is_demo: bool


class Mt5Session:
    """Owns the terminal connection, the resolved symbol spec and the clock."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.mt5 = None
        self.symbol = cfg.symbol
        self.spec: Optional[SymbolSpec] = None
        self._utc_offset_seconds: Optional[int] = None
        self._connected = False

    # ------------------------------------------------------------- lifecycle
    def connect(self) -> AccountSnapshot:
        self.mt5 = mt5_api()
        kwargs: dict[str, Any] = {}
        if self.cfg.terminal_path:
            kwargs["path"] = self.cfg.terminal_path
        if self.cfg.login:
            kwargs.update(login=int(self.cfg.login), password=self.cfg.password,
                          server=self.cfg.server)
        if not self.mt5.initialize(**kwargs):
            raise Mt5Unavailable(f"mt5.initialize failed: {self.mt5.last_error()}")
        self._connected = True

        terminal = self.mt5.terminal_info()
        if terminal is not None and not terminal.trade_allowed:
            log.warning("AutoTrading is DISABLED in the terminal - orders will be rejected. "
                        "Enable the 'Algo Trading' button in MT5.")
        account = self.account()
        log.info("connected: login %s @ %s (%s), equity %.2f %s, %s account",
                 account.login, account.server, terminal.name if terminal else "?",
                 account.equity, account.currency, "DEMO" if account.is_demo else "LIVE")
        self.spec = self.resolve_symbol()
        self._detect_utc_offset()
        return account

    def shutdown(self) -> None:
        if self._connected and self.mt5 is not None:
            self.mt5.shutdown()
            self._connected = False

    def ensure_connected(self) -> bool:
        """Reconnect after a dropped terminal link. True if usable."""
        try:
            if self.mt5 is not None and self.mt5.terminal_info() is not None:
                return True
        except Exception:  # noqa: BLE001 - the API raises assorted errors when dead
            pass
        log.warning("terminal link lost - reconnecting")
        try:
            self.shutdown()
        except Exception:  # noqa: BLE001
            pass
        for attempt, delay in enumerate((2, 4, 8, 16), start=1):
            try:
                self.connect()
                log.info("reconnected on attempt %d", attempt)
                return True
            except Exception as exc:  # noqa: BLE001
                log.error("reconnect attempt %d failed: %s", attempt, exc)
                time.sleep(delay)
        return False

    # ---------------------------------------------------------------- account
    def account(self) -> AccountSnapshot:
        info = self.mt5.account_info()
        if info is None:
            raise Mt5Unavailable(f"account_info failed: {self.mt5.last_error()}")
        return AccountSnapshot(
            login=info.login, server=info.server, currency=info.currency,
            balance=info.balance, equity=info.equity, free_margin=info.margin_free,
            leverage=info.leverage, trade_allowed=bool(info.trade_allowed),
            is_demo=(getattr(info, "trade_mode", 0)
                     == getattr(self.mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)),
        )

    # ----------------------------------------------------------------- symbol
    def resolve_symbol(self) -> SymbolSpec:
        """Read the live contract specs. Refuse to run on placeholder values."""
        info = self.mt5.symbol_info(self.symbol)
        if info is None:
            raise Mt5Unavailable(
                f"symbol {self.symbol!r} not found. Check the exact name in Market Watch "
                f"(brokers suffix them, e.g. UT100.r / UT100-ECN)."
            )
        if not info.visible and not self.mt5.symbol_select(self.symbol, True):
            raise Mt5Unavailable(f"could not select {self.symbol} in Market Watch")
        info = self.mt5.symbol_info(self.symbol)  # re-read after selecting

        tick_size = float(info.trade_tick_size or 0.0)
        tick_value = float(info.trade_tick_value or 0.0)
        point = float(info.point or 0.0)
        if tick_size <= 0 and point > 0:
            log.warning("%s reports tick_size %s - using point %s instead",
                        self.symbol, info.trade_tick_size, point)
            tick_size = point
        if tick_size <= 0:
            raise Mt5Unavailable(
                f"{self.symbol}: neither tick size nor point is usable - refusing to trade")
        if tick_value <= 0:
            # Expected on UT100: the panel shows 0. Sizing falls back to
            # order_calc_profit, which is authoritative anyway.
            log.warning("%s reports tick_value 0 (display rounding) - position sizing will "
                        "use order_calc_profit", self.symbol)

        spec = SymbolSpec(
            name=self.symbol,
            digits=int(info.digits),
            point=point,
            tick_size=tick_size,
            tick_value=tick_value,
            contract_size=float(info.trade_contract_size or 0.0),
            volume_min=float(info.volume_min),
            volume_step=float(info.volume_step),
            volume_max=float(info.volume_max),
            stops_level_points=int(getattr(info, "trade_stops_level", 0) or 0),
            filling_mode_mask=int(getattr(info, "filling_mode", 0) or 0),
            currency_margin=getattr(info, "currency_margin", ""),
            currency_profit=getattr(info, "currency_profit", ""),
        )
        if spec.volume_min <= 0 or spec.volume_step <= 0:
            raise Mt5Unavailable(f"{self.symbol}: broken volume limits {spec}")
        log.info("symbol %s: digits=%d point=%s tick_size=%s tick_value=%s contract=%s "
                 "vol[%s..%s step %s] stops_level=%d filling_mask=%d %s/%s",
                 spec.name, spec.digits, spec.point, spec.tick_size, spec.tick_value,
                 spec.contract_size, spec.volume_min, spec.volume_max, spec.volume_step,
                 spec.stops_level_points, spec.filling_mode_mask,
                 spec.currency_margin, spec.currency_profit)
        return spec

    def filling_type(self):
        """Pick a filling mode the symbol actually supports (UT100: FOK or IOC)."""
        mt5 = self.mt5
        mask = self.spec.filling_mode_mask if self.spec else 0
        if mask & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
            return mt5.ORDER_FILLING_FOK
        if mask & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
            return mt5.ORDER_FILLING_IOC
        log.warning("symbol reports filling mask %d - falling back to IOC", mask)
        return mt5.ORDER_FILLING_IOC

    # ------------------------------------------------------------------ quotes
    def tick(self):
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            raise Mt5Unavailable(f"no tick for {self.symbol}: {self.mt5.last_error()}")
        return tick

    def spread_points(self) -> float:
        tick = self.tick()
        point = self.spec.point if self.spec and self.spec.point else 0.0
        if point <= 0:
            return 0.0
        return (tick.ask - tick.bid) / point

    # ------------------------------------------------------------------- clock
    def _detect_utc_offset(self) -> None:
        """Offset between broker server time and real UTC, in seconds."""
        if self.cfg.server_utc_offset_hours is not None:
            self._utc_offset_seconds = int(round(self.cfg.server_utc_offset_hours * 3600))
            log.info("broker server time pinned to UTC%+.1fh by config",
                     self._utc_offset_seconds / 3600)
            return
        try:
            tick = self.tick()
            server_epoch = int(tick.time)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read a tick to detect the server clock (%s) - assuming UTC", exc)
            self._utc_offset_seconds = 0
            return
        now = time.time()
        # Round to the nearest half hour; quotes may be minutes stale over a weekend.
        raw = server_epoch - now
        self._utc_offset_seconds = int(round(raw / 1800.0) * 1800)
        if abs(raw) > 86400:
            log.warning("server clock looks %0.1f h from UTC - last tick may be stale; "
                        "set mt5.server_utc_offset_hours in config to be sure", raw / 3600)
        log.info("broker server time detected as UTC%+.1fh", self._utc_offset_seconds / 3600)

    @property
    def utc_offset_seconds(self) -> int:
        return self._utc_offset_seconds or 0

    def server_to_utc(self, server_epoch: int | float) -> datetime:
        """MT5 timestamp (server time as epoch) -> real aware UTC datetime."""
        return datetime.fromtimestamp(float(server_epoch) - self.utc_offset_seconds,
                                      tz=timezone.utc)

    def server_datetime(self, server_epoch: int | float) -> datetime:
        """MT5 timestamp -> naive datetime in broker server time (for session windows)."""
        return datetime.fromtimestamp(float(server_epoch), tz=timezone.utc).replace(tzinfo=None)

    def server_now(self) -> datetime:
        return datetime.utcnow() + timedelta(seconds=self.utc_offset_seconds)

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)
