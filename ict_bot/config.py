"""Configuration: one YAML file (plus env overrides) driving every knob.

PyYAML is imported lazily so the strategy layer and its tests never need it.
Env overrides use the ``ICT_`` prefix with ``__`` for nesting, e.g.
``ICT_RISK__RISK_PCT=0.5`` or ``ICT_MT5__PASSWORD=...`` (keep secrets in env).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Optional, get_type_hints


@dataclass
class Mt5Config:
    terminal_path: Optional[str] = None   # e.g. C:/Program Files/MEXAtlantic MT5/terminal64.exe
    login: Optional[int] = None
    password: Optional[str] = None
    server: Optional[str] = None
    symbol: str = "UT100"
    exec_timeframe: str = "M15"
    htf_timeframe: str = "H4"
    history_bars: int = 1500              # warm-up bars replayed on startup
    htf_history_bars: int = 400
    magic: int = 990411
    deviation_points: int = 20            # slippage allowance on market orders
    server_utc_offset_hours: Optional[float] = None  # None -> auto-detect from a live tick


@dataclass
class StrategyConfig:
    swing_len: int = 5                    # pivot strength, bars each side
    max_wait: int = 20                    # bars a sweep may wait for its CISD
    chain_max: int = 30                   # candles scanned for the CISD chain
    entry_mode: str = "ob_mean_threshold"  # ob_mean_threshold | ob_edge
    entry_trigger: str = "market_on_cisd"  # market_on_cisd (no retest) | limit_at_ob
    limit_expiry_bars: int = 6            # limit_at_ob only: cancel if unfilled
    tp_mode: str = "opposing_liquidity"   # opposing_liquidity | fixed_rr
    rr_target: float = 2.0
    require_sweep_in_htf_fvg: bool = False
    min_rr: float = 1.0                   # skip confirmations whose R:R is worse than this
    max_stop_points: float = 0.0          # 0 = no cap; else skip setups with a wider stop
    min_stop_points: float = 0.0          # 0 = no floor; guards against absurd tiny stops


@dataclass
class RiskConfig:
    risk_pct: float = 1.0                 # % of equity risked per trade
    max_positions: int = 1                # total concurrent bot positions
    one_per_direction: bool = True        # mirrors the single-setup state machine
    daily_max_loss_pct: float = 3.0       # kill switch, % of the day's starting equity
    daily_max_trades: int = 0             # 0 = unlimited
    max_spread_points: float = 60.0       # skip entries when the floating spread blows out
    margin_buffer: float = 0.5            # required margin must fit in this fraction of free margin
    close_on_opposite_signal: bool = False


@dataclass
class SessionConfig:
    """Optional trading-hours filter, expressed in BROKER SERVER time."""

    enabled: bool = False
    windows: list[str] = field(default_factory=lambda: ["09:30-16:00"])
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon..Fri


@dataclass
class NewsConfig:
    """Economic-calendar blackout: no new entries around scheduled events."""

    enabled: bool = True
    minutes_before: float = 5.0
    minutes_after: float = 5.0
    impacts: list[str] = field(default_factory=lambda: ["high"])
    currencies: list[str] = field(default_factory=lambda: ["USD"])
    source: str = "csv"                   # csv | ics | http_json | none
    path: str = "calendar/events.csv"     # csv/ics source
    url: str = ""                         # http_json source
    http_field_map: dict = field(default_factory=lambda: {
        "time": "date", "currency": "country", "impact": "impact", "title": "title",
    })
    refresh_minutes: int = 60
    max_stale_minutes: int = 720          # feed older than this counts as unavailable
    on_feed_unavailable: str = "block"    # block (fail safe) | allow
    flatten_before_event: bool = False    # close open positions ahead of an event
    flatten_minutes_before: float = 2.0


@dataclass
class NotifyConfig:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook: str = ""
    notify_entries: bool = True
    notify_exits: bool = True
    notify_blocks: bool = False


@dataclass
class RuntimeConfig:
    mode: str = "paper"                   # backtest | paper | live
    allow_live_trading: bool = False      # live mode ALSO needs --i-understand-live
    poll_seconds: float = 5.0
    state_file: str = "state/ict_bot_state.json"
    log_file: str = "logs/ict_bot.log"
    log_level: str = "INFO"
    signals_csv: str = "logs/signals.csv"  # per-setup audit trail / indicator parity


@dataclass
class BacktestConfig:
    bars: int = 20000
    from_date: str = ""                   # YYYY-MM-DD (optional)
    to_date: str = ""
    spread_points: float = 20.0           # modelled round-trip cost
    commission_per_lot: float = 0.0
    slippage_points: float = 5.0
    start_equity: float = 10000.0
    csv_path: str = ""                    # offline OHLC instead of the terminal
    htf_csv_path: str = ""
    report_csv: str = "logs/backtest_trades.csv"
    apply_session_filter: bool = True
    apply_news_filter: bool = True
    # Contract specs for OFFLINE runs only. With a terminal attached the real ones
    # are read from it and these are ignored. Defaults describe a UT100-style index
    # CFD: contract size 1, so one lot is 1 USD per index point.
    spec_digits: int = 2
    spec_point: float = 0.01
    spec_tick_size: float = 0.01
    spec_tick_value: float = 0.01
    spec_contract_size: float = 1.0
    spec_volume_min: float = 0.01
    spec_volume_step: float = 0.01
    spec_volume_max: float = 100.0


@dataclass
class Config:
    mt5: Mt5Config = field(default_factory=Mt5Config)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        data: dict[str, Any] = {}
        if path:
            try:
                import yaml  # imported lazily: strategy tests do not need PyYAML
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise RuntimeError(
                    "PyYAML is required to read a config file: pip install pyyaml"
                ) from exc
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        cfg = _build(cls, data)
        _apply_env(cfg)
        cfg.validate()
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)

    def redacted(self) -> dict:
        """Config safe to log -- credentials and webhooks masked."""
        data = self.to_dict()
        for section, key in (
            ("mt5", "password"), ("mt5", "login"),
            ("notify", "telegram_bot_token"), ("notify", "discord_webhook"),
        ):
            if data.get(section, {}).get(key):
                data[section][key] = "***"
        return data

    # --------------------------------------------------------------- validation
    def validate(self) -> None:
        s = self.strategy
        if s.entry_mode not in ("ob_mean_threshold", "ob_edge"):
            raise ValueError(f"strategy.entry_mode invalid: {s.entry_mode}")
        if s.entry_trigger not in ("market_on_cisd", "limit_at_ob"):
            raise ValueError(f"strategy.entry_trigger invalid: {s.entry_trigger}")
        if s.tp_mode not in ("opposing_liquidity", "fixed_rr"):
            raise ValueError(f"strategy.tp_mode invalid: {s.tp_mode}")
        if s.swing_len < 2 or s.max_wait < 1 or s.chain_max < 3:
            raise ValueError("strategy: swing_len>=2, max_wait>=1, chain_max>=3 required")
        if not 0 < self.risk.risk_pct <= 100:
            raise ValueError("risk.risk_pct must be in (0, 100]")
        if self.risk.max_positions < 1:
            raise ValueError("risk.max_positions must be >= 1")
        if self.news.enabled and self.news.source not in ("csv", "ics", "http_json", "none"):
            raise ValueError(f"news.source invalid: {self.news.source}")
        if self.news.on_feed_unavailable not in ("block", "allow"):
            raise ValueError("news.on_feed_unavailable must be 'block' or 'allow'")
        if self.news.minutes_before < 0 or self.news.minutes_after < 0:
            raise ValueError("news blackout minutes must be >= 0")
        if self.runtime.mode not in ("backtest", "paper", "live"):
            raise ValueError(f"runtime.mode invalid: {self.runtime.mode}")
        for window in self.session.windows:
            _parse_window(window)  # raises on malformed entries


def _build(cls, data: dict):
    """Recursively build nested dataclasses, rejecting unknown keys.

    ``from __future__ import annotations`` turns field types into strings, so the
    real classes are resolved with ``get_type_hints``.
    """
    if not is_dataclass(cls):
        return data
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    known = {f.name for f in fields(cls)}
    for key, value in (data or {}).items():
        if key not in known:
            raise ValueError(f"unknown config key: {cls.__name__}.{key}")
        ftype = hints.get(key)
        if is_dataclass(ftype) and isinstance(value, dict):
            kwargs[key] = _build(ftype, value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def _coerce(current: Any, raw: str) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


def _apply_env(cfg: Config) -> None:
    """ICT_SECTION__FIELD=value overrides -- the way to keep secrets out of YAML."""
    for section_field in fields(cfg):
        section = getattr(cfg, section_field.name)
        if not is_dataclass(section):
            continue
        for f in fields(section):
            env_key = f"ICT_{section_field.name.upper()}__{f.name.upper()}"
            raw = os.environ.get(env_key)
            if raw is None:
                continue
            current = getattr(section, f.name)
            setattr(section, f.name, _coerce(current, raw))


def _parse_window(window: str) -> tuple[int, int]:
    """'09:30-16:00' -> (570, 960) minutes from midnight, broker server time."""
    try:
        start_s, end_s = window.split("-")
        sh, sm = (int(x) for x in start_s.strip().split(":"))
        eh, em = (int(x) for x in end_s.strip().split(":"))
    except Exception as exc:
        raise ValueError(f"session window must look like '09:30-16:00', got {window!r}") from exc
    start, end = sh * 60 + sm, eh * 60 + em
    if not (0 <= start <= 1440 and 0 <= end <= 1440):
        raise ValueError(f"session window out of range: {window!r}")
    return start, end
