"""Command line entry point.

    python -m ict_bot --config config.yaml --mode check      # connectivity + specs + calendar
    python -m ict_bot --config config.yaml --mode backtest   # simulate, then report
    python -m ict_bot --config config.yaml --mode paper      # demo account, real fills
    python -m ict_bot --config config.yaml --mode live --i-understand-live
    python -m ict_bot --config config.yaml --mode signals    # parity dump vs the indicator
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

from .config import Config
from .news import NewsFilter


def setup_logging(cfg: Config) -> None:
    level = getattr(logging, cfg.runtime.log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if cfg.runtime.log_file:
        os.makedirs(os.path.dirname(os.path.abspath(cfg.runtime.log_file)) or ".", exist_ok=True)
        handlers.append(logging.FileHandler(cfg.runtime.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level, handlers=handlers, force=True,
        format="%(asctime)s %(levelname)-8s %(name)-22s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ict_bot",
        description="ICT liquidity sweep + CISD bot for MetaTrader 5 (no-retest entries)",
    )
    parser.add_argument("--config", default="config.yaml", help="path to the YAML config")
    parser.add_argument("--mode", choices=["check", "backtest", "paper", "live", "signals"],
                        help="overrides runtime.mode from the config")
    parser.add_argument("--symbol", help="override mt5.symbol")
    parser.add_argument("--timeframe", help="override mt5.exec_timeframe (e.g. M15)")
    parser.add_argument("--bars", type=int, help="override backtest.bars")
    parser.add_argument("--out", help="output CSV path for --mode signals")
    parser.add_argument("--i-understand-live", action="store_true",
                        help="required acknowledgement to place orders on a real account")
    return parser


def load_config(args) -> Config:
    path = args.config if args.config and os.path.exists(args.config) else None
    if args.config and path is None:
        print(f"config file {args.config!r} not found - using defaults plus env overrides",
              file=sys.stderr)
    cfg = Config.load(path)
    if args.mode:
        cfg.runtime.mode = "paper" if args.mode in ("check", "signals") else args.mode
    if args.symbol:
        cfg.mt5.symbol = args.symbol
    if args.timeframe:
        cfg.mt5.exec_timeframe = args.timeframe
    if args.bars:
        cfg.backtest.bars = args.bars
    cfg.validate()
    return cfg


# ------------------------------------------------------------------- commands
def cmd_check(cfg: Config) -> int:
    """Prove the plumbing before risking anything: terminal, specs, clock, calendar."""
    from .mt5_client.execution import Executor
    from .mt5_client.feed import MarketFeed
    from .mt5_client.session import Mt5Session

    log = logging.getLogger("check")
    session = Mt5Session(cfg.mt5)
    account = session.connect()
    try:
        feed = MarketFeed(session)
        exec_bars = feed.closed_bars(cfg.mt5.exec_timeframe, 10)
        htf_bars = feed.closed_bars(cfg.mt5.htf_timeframe, 5)
        tick = session.tick()
        spec = session.spec

        print("\n--- connection ---")
        print(f"account       : {account.login} @ {account.server} "
              f"({'DEMO' if account.is_demo else 'REAL'})")
        print(f"equity        : {account.equity:,.2f} {account.currency}")
        print(f"trade allowed : {account.trade_allowed}")

        print("\n--- symbol (read live, nothing hardcoded) ---")
        print(f"name          : {spec.name}")
        print(f"digits/point  : {spec.digits} / {spec.point}")
        note = "   <- panel shows 0; sizing uses order_calc_profit" if spec.tick_value <= 0 else ""
        print(f"tick size/val : {spec.tick_size} / {spec.tick_value}{note}")
        print(f"contract size : {spec.contract_size}")
        print(f"volume        : min {spec.volume_min} step {spec.volume_step} "
              f"max {spec.volume_max}")
        print(f"stops level   : {spec.stops_level_points} points")
        print(f"filling mask  : {spec.filling_mode_mask} -> using {session.filling_type()}")
        print(f"spread now    : {session.spread_points():.0f} points "
              f"(cap {cfg.risk.max_spread_points:.0f})")

        print("\n--- money check on a 1.00 lot, 10-point stop ---")
        executor = Executor(session, cfg.mt5.magic, cfg.mt5.deviation_points)
        from .models import Direction
        loss = executor.profit_calc(Direction.LONG, 1.0, tick.ask, tick.ask - 10 * spec.point)
        margin = executor.margin_calc(Direction.LONG, 1.0, tick.ask)
        print(f"order_calc_profit : {loss}")
        print(f"order_calc_margin : {margin}")
        if loss is None or loss == 0:
            print("  WARNING: the terminal cannot value a stop for this symbol; "
                  "sizing will fall back to tick maths.")

        print("\n--- clock ---")
        print(f"server offset : UTC{session.utc_offset_seconds / 3600:+.1f}h")
        print(f"server now    : {session.server_now():%Y-%m-%d %H:%M:%S} (broker)")
        print(f"utc now       : {session.utc_now():%Y-%m-%d %H:%M:%S} UTC")
        print(f"last {cfg.mt5.exec_timeframe} bar  : "
              f"{session.server_datetime(exec_bars[-1].time):%Y-%m-%d %H:%M} "
              f"O{exec_bars[-1].open} H{exec_bars[-1].high} "
              f"L{exec_bars[-1].low} C{exec_bars[-1].close}")
        print(f"last {cfg.mt5.htf_timeframe} bar  : "
              f"{session.server_datetime(htf_bars[-1].time):%Y-%m-%d %H:%M}")

        print("\n--- news filter ---")
        _print_news(cfg, session.utc_now())
        print("\nAll checks completed.")
        return 0
    finally:
        session.shutdown()


def _print_news(cfg: Config, utc_now: datetime) -> None:
    if not cfg.news.enabled:
        print("disabled")
        return
    news = NewsFilter(cfg.news)
    ok = news.refresh(utc_now, force=True)
    status = news.status(utc_now)
    print(f"source        : {cfg.news.source} ({cfg.news.path or cfg.news.url or '-'})")
    print(f"window        : {cfg.news.minutes_before:g} min before / "
          f"{cfg.news.minutes_after:g} min after")
    print(f"scope         : impacts={cfg.news.impacts} currencies={cfg.news.currencies}")
    print(f"feed usable   : {ok and news.feed_is_fresh(utc_now)}")
    print(f"entries now   : {'BLOCKED - ' + status.reason if status.blocked else 'allowed'}")
    upcoming = news.next_event(utc_now)
    print(f"next event    : {upcoming if upcoming else 'none in the feed'}")


def _load_backtest_data(cfg: Config):
    """CSV if configured, otherwise straight from the terminal."""
    from .mt5_client.feed import load_csv_candles
    from .mt5_client.session import timeframe_seconds

    htf_seconds = timeframe_seconds(cfg.mt5.htf_timeframe)
    if cfg.backtest.csv_path:
        exec_bars = load_csv_candles(cfg.backtest.csv_path)
        htf_bars = (load_csv_candles(cfg.backtest.htf_csv_path)
                    if cfg.backtest.htf_csv_path else [])
        if not htf_bars and cfg.strategy.require_sweep_in_htf_fvg:
            raise SystemExit("require_sweep_in_htf_fvg needs backtest.htf_csv_path")
        return exec_bars, htf_bars, htf_seconds, 0

    from .mt5_client.feed import MarketFeed
    from .mt5_client.session import Mt5Session

    session = Mt5Session(cfg.mt5)
    session.connect()
    try:
        feed = MarketFeed(session)
        exec_bars = feed.closed_bars(cfg.mt5.exec_timeframe, cfg.backtest.bars)
        htf_needed = max(50, int(cfg.backtest.bars *
                                 timeframe_seconds(cfg.mt5.exec_timeframe) / htf_seconds) + 10)
        htf_bars = feed.closed_bars(cfg.mt5.htf_timeframe, htf_needed)
        return exec_bars, htf_bars, htf_seconds, session.utc_offset_seconds
    finally:
        session.shutdown()


def _apply_date_filter(cfg: Config, bars):
    if not (cfg.backtest.from_date or cfg.backtest.to_date):
        return bars
    start = (datetime.fromisoformat(cfg.backtest.from_date).replace(tzinfo=timezone.utc).timestamp()
             if cfg.backtest.from_date else 0)
    end = (datetime.fromisoformat(cfg.backtest.to_date).replace(tzinfo=timezone.utc).timestamp()
           if cfg.backtest.to_date else 4102444800)
    return [b for b in bars if start <= b.time <= end]


def cmd_backtest(cfg: Config) -> int:
    from .backtest import Backtester, offline_spec, write_trades_csv

    exec_bars, htf_bars, htf_seconds, offset = _load_backtest_data(cfg)
    exec_bars = _apply_date_filter(cfg, exec_bars)
    if len(exec_bars) < 2 * cfg.strategy.swing_len + 5:
        raise SystemExit(f"not enough bars to backtest ({len(exec_bars)})")

    print(f"backtesting {cfg.mt5.symbol} {cfg.mt5.exec_timeframe} on {len(exec_bars):,} bars "
          f"({datetime.fromtimestamp(exec_bars[0].time - offset, tz=timezone.utc):%Y-%m-%d} "
          f"to {datetime.fromtimestamp(exec_bars[-1].time - offset, tz=timezone.utc):%Y-%m-%d}), "
          f"{len(htf_bars):,} {cfg.mt5.htf_timeframe} bars")
    tester = Backtester(cfg, spec=offline_spec(cfg), utc_offset_seconds=offset)
    report = tester.run(exec_bars, htf_bars, htf_seconds)
    print()
    print(report.summary())
    write_trades_csv(report, cfg.backtest.report_csv, offset)
    print(f"\nentry model: market at the CISD confirmation close, NO RETEST, "
          f"costed at {cfg.backtest.spread_points:g} pts spread + "
          f"{cfg.backtest.slippage_points:g} pts slippage")
    return 0


def cmd_signals(cfg: Config, out: Optional[str]) -> int:
    from .backtest import write_signals_csv

    exec_bars, htf_bars, htf_seconds, offset = _load_backtest_data(cfg)
    exec_bars = _apply_date_filter(cfg, exec_bars)
    path = out or "logs/parity_signals.csv"
    count = write_signals_csv(exec_bars, htf_bars, htf_seconds, cfg, path, offset)
    print(f"wrote {count} signal rows to {path} over {len(exec_bars):,} bars.\n"
          f"Load the indicator on the same symbol/timeframe and compare bar times: "
          f"orange sweep candles = 'sweep' rows, green/red CISD candles = 'confirmed' rows, "
          f"gray candles = 'invalidated' rows.")
    return 0


def cmd_run(cfg: Config, acknowledged: bool) -> int:
    from .runner import run_live

    if cfg.runtime.mode == "live" and not acknowledged:
        raise SystemExit(
            "Refusing to trade live without --i-understand-live.\n"
            "Run --mode paper on a demo account first, and read the risk notes in the README.")
    if cfg.runtime.mode == "live":
        cfg.runtime.allow_live_trading = True
    run_live(cfg)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args)
    setup_logging(cfg)
    mode = args.mode or cfg.runtime.mode
    if mode == "check":
        return cmd_check(cfg)
    if mode == "backtest":
        return cmd_backtest(cfg)
    if mode == "signals":
        return cmd_signals(cfg, args.out)
    return cmd_run(cfg, args.i_understand_live)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
