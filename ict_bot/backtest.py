"""Historical simulation over the SAME signal engine the live bot uses.

Nothing about the strategy is re-implemented here -- only fills, costs and
book-keeping are modelled. If the backtest and the live bot ever disagree about a
signal, that is a bug in this file, not in the strategy.

Fill model (deliberately pessimistic):
  * entry at the CISD confirmation bar's CLOSE -- NO RETEST -- plus the full
    modelled spread and slippage, charged against us in both directions;
  * exits checked from the NEXT bar onward against raw bar prices;
  * if one bar's range covers both the stop and the target, the STOP is assumed
    first;
  * commission is charged per lot, round turn.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from .config import Config
from .models import Candle, Direction, EventKind, FinalTrade, SymbolSpec
from .news import NewsFilter
from .risk import RiskManager
from .signals.engine import IctSignalEngine

log = logging.getLogger(__name__)


@dataclass
class ClosedTrade:
    direction: Direction
    entry_time: int
    exit_time: int
    entry: float
    stop: float
    take_profit: float
    exit_price: float
    volume: float
    profit: float
    r_multiple: float
    bars_held: int
    outcome: str          # tp | sl | eod
    tp_source: str
    htf_ok: bool
    swept_pool: float
    cisd_level: float


@dataclass
class BacktestReport:
    trades: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    start_equity: float = 0.0
    end_equity: float = 0.0
    sweeps: int = 0
    confirmations: int = 0
    invalidations: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    bars: int = 0

    # ------------------------------------------------------------- metrics
    @property
    def wins(self) -> list[ClosedTrade]:
        return [t for t in self.trades if t.profit > 0]

    @property
    def losses(self) -> list[ClosedTrade]:
        return [t for t in self.trades if t.profit <= 0]

    @property
    def win_rate(self) -> float:
        return len(self.wins) / len(self.trades) * 100 if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        gross_win = sum(t.profit for t in self.wins)
        gross_loss = abs(sum(t.profit for t in self.losses))
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 0.0
        return gross_win / gross_loss

    @property
    def expectancy(self) -> float:
        return sum(t.profit for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def expectancy_r(self) -> float:
        return sum(t.r_multiple for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def net_profit(self) -> float:
        return self.end_equity - self.start_equity

    @property
    def max_drawdown(self) -> tuple[float, float]:
        """(absolute, percent) peak-to-trough on the closed-trade equity curve."""
        peak = self.start_equity
        worst_abs = worst_pct = 0.0
        for _, equity in self.equity_curve:
            peak = max(peak, equity)
            drop = peak - equity
            if drop > worst_abs:
                worst_abs = drop
                worst_pct = drop / peak * 100 if peak else 0.0
        return worst_abs, worst_pct

    def summary(self, digits: int = 2) -> str:
        dd_abs, dd_pct = self.max_drawdown
        pf = self.profit_factor
        longs = [t for t in self.trades if t.direction is Direction.LONG]
        shorts = [t for t in self.trades if t.direction is Direction.SHORT]
        lines = [
            "=" * 62,
            "BACKTEST SUMMARY",
            "=" * 62,
            f"bars processed      : {self.bars:,}",
            f"sweeps / confirms   : {self.sweeps} / {self.confirmations} "
            f"(invalidated {self.invalidations})",
            f"trades taken        : {len(self.trades)}  "
            f"(long {len(longs)}, short {len(shorts)})",
            f"win rate            : {self.win_rate:.1f}%  "
            f"({len(self.wins)}W / {len(self.losses)}L)",
            f"profit factor       : {'inf' if pf == float('inf') else f'{pf:.2f}'}",
            f"expectancy          : {self.expectancy:,.2f} per trade "
            f"({self.expectancy_r:+.2f} R)",
            f"net profit          : {self.net_profit:,.2f} "
            f"({self.net_profit / self.start_equity * 100:+.1f}%)",
            f"equity              : {self.start_equity:,.2f} -> {self.end_equity:,.2f}",
            f"max drawdown        : {dd_abs:,.2f} ({dd_pct:.1f}%)",
        ]
        if self.trades:
            avg_bars = sum(t.bars_held for t in self.trades) / len(self.trades)
            tp_hits = sum(1 for t in self.trades if t.outcome == "tp")
            lines.append(f"target hit / stopped: {tp_hits} / "
                         f"{sum(1 for t in self.trades if t.outcome == 'sl')}")
            lines.append(f"avg bars in trade   : {avg_bars:.1f}")
        if self.skipped:
            lines.append("-" * 62)
            lines.append("confirmations not traded:")
            for reason, count in sorted(self.skipped.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {count:4d}  {reason}")
        lines.append("=" * 62)
        return "\n".join(lines)


@dataclass
class _OpenTrade:
    trade: FinalTrade
    volume: float
    entry_time: int
    entry_index: int
    per_lot_risk: float


class Backtester:
    def __init__(self, cfg: Config, spec: Optional[SymbolSpec] = None,
                 news: Optional[NewsFilter] = None, utc_offset_seconds: int = 0) -> None:
        self.cfg = cfg
        self.spec = spec or offline_spec(cfg)
        self.utc_offset_seconds = utc_offset_seconds
        self.news = news
        if news is None and cfg.backtest.apply_news_filter and cfg.news.enabled:
            self.news = NewsFilter(cfg.news)
            self.news.refresh(datetime.now(timezone.utc), force=True)

    # ------------------------------------------------------------------ run
    def run(self, exec_bars: Sequence[Candle], htf_bars: Sequence[Candle],
            htf_seconds: int) -> BacktestReport:
        cfg = self.cfg
        engine = IctSignalEngine(
            swing_len=cfg.strategy.swing_len, max_wait=cfg.strategy.max_wait,
            chain_max=cfg.strategy.chain_max, entry_mode=cfg.strategy.entry_mode,
            tp_mode=cfg.strategy.tp_mode, rr_target=cfg.strategy.rr_target,
            require_sweep_in_htf_fvg=cfg.strategy.require_sweep_in_htf_fvg,
        )
        risk = RiskManager(risk=cfg.risk, strategy=cfg.strategy, session=cfg.session,
                           spec=self.spec)
        report = BacktestReport(start_equity=cfg.backtest.start_equity,
                                end_equity=cfg.backtest.start_equity,
                                bars=len(exec_bars))
        equity = cfg.backtest.start_equity
        open_trades: list[_OpenTrade] = []
        cost = (cfg.backtest.spread_points + cfg.backtest.slippage_points) * self.spec.point

        for index, bar in enumerate(exec_bars):
            # 1) manage what is already open, on this bar's range
            still_open = []
            for position in open_trades:
                if position.entry_index == index:
                    still_open.append(position)  # entered on this bar's close
                    continue
                outcome = self._check_exit(position, bar)
                if outcome is None:
                    still_open.append(position)
                    continue
                exit_price, label = outcome
                closed = self._close(position, bar, exit_price, label, index)
                equity += closed.profit
                report.trades.append(closed)
                report.equity_curve.append((bar.time, equity))
                risk.record_close(closed.profit)
            open_trades = still_open

            # 2) new signals from this closed bar
            completed_htf = [h for h in htf_bars if h.time + htf_seconds <= bar.time]
            for event in engine.on_bar(bar, completed_htf):
                if event.kind is EventKind.SWEEP:
                    report.sweeps += 1
                    continue
                if event.kind is EventKind.INVALIDATED:
                    report.invalidations += 1
                    continue
                report.confirmations += 1

                entry_price = bar.close + (cost if event.direction is Direction.LONG else -cost)
                trade = event.plan.finalize(entry_price)
                reason = self._blocked_reason(risk, trade, bar, equity, open_trades)
                if reason:
                    report.skipped[reason] = report.skipped.get(reason, 0) + 1
                    continue
                sizing = risk.position_size(trade, equity)
                if not sizing.ok:
                    key = sizing.code or "sizing"
                    report.skipped[key] = report.skipped.get(key, 0) + 1
                    continue
                open_trades.append(_OpenTrade(
                    trade=trade, volume=sizing.volume, entry_time=bar.time,
                    entry_index=index, per_lot_risk=sizing.loss_per_lot,
                ))
                risk.record_fill()

        # 3) mark any survivor out at the last close
        if open_trades and exec_bars:
            last = exec_bars[-1]
            for position in open_trades:
                closed = self._close(position, last, last.close, "eod", len(exec_bars) - 1)
                equity += closed.profit
                report.trades.append(closed)
                report.equity_curve.append((last.time, equity))

        report.end_equity = equity
        return report

    # -------------------------------------------------------------- helpers
    def _blocked_reason(self, risk: RiskManager, trade: FinalTrade, bar: Candle,
                        equity: float, open_trades: list[_OpenTrade]) -> str:
        """Returns a stable reason CODE (not a formatted sentence) so the report
        can say "47 blocked on R:R" rather than 47 one-off lines."""
        server_now = datetime.fromtimestamp(bar.time, tz=timezone.utc).replace(tzinfo=None)

        quality = risk.check_trade_quality(trade)
        if not quality.allowed:
            return quality.code

        daily = risk.check_daily(server_now, equity)
        if not daily.allowed:
            return daily.code

        if self.cfg.backtest.apply_session_filter:
            session = risk.check_session(server_now)
            if not session.allowed:
                return session.code

        exposure = risk.check_exposure(
            [type("P", (), {"direction": p.trade.direction})() for p in open_trades],
            trade.direction)
        if not exposure.allowed:
            return exposure.code

        if self.news is not None and self.cfg.backtest.apply_news_filter:
            utc_now = datetime.fromtimestamp(bar.time - self.utc_offset_seconds,
                                             tz=timezone.utc)
            status = self.news.status(utc_now)
            if status.blocked:
                return "news_blackout"
        return ""

    def _check_exit(self, position: _OpenTrade, bar: Candle) -> Optional[tuple[float, str]]:
        """Stop first when a single bar covers both levels -- never flatter than reality."""
        trade = position.trade
        if trade.direction is Direction.LONG:
            if bar.low <= trade.stop:
                return trade.stop, "sl"
            if bar.high >= trade.take_profit:
                return trade.take_profit, "tp"
            return None
        if bar.high >= trade.stop:
            return trade.stop, "sl"
        if bar.low <= trade.take_profit:
            return trade.take_profit, "tp"
        return None

    def _close(self, position: _OpenTrade, bar: Candle, exit_price: float,
               label: str, index: int) -> ClosedTrade:
        trade = position.trade
        move = (exit_price - trade.entry) * trade.direction.sign
        per_lot = ((move / self.spec.tick_size) * self.spec.tick_value
                   if self.spec.tick_size else 0.0)
        gross = per_lot * position.volume
        commission = self.cfg.backtest.commission_per_lot * position.volume
        profit = gross - commission
        risk_money = position.per_lot_risk * position.volume
        return ClosedTrade(
            direction=trade.direction, entry_time=position.entry_time, exit_time=bar.time,
            entry=trade.entry, stop=trade.stop, take_profit=trade.take_profit,
            exit_price=exit_price, volume=position.volume, profit=profit,
            r_multiple=(profit / risk_money) if risk_money else 0.0,
            bars_held=index - position.entry_index, outcome=label,
            tp_source=trade.tp_source, htf_ok=trade.plan.htf_ok,
            swept_pool=trade.plan.swept_pool, cisd_level=trade.plan.cisd_level,
        )


def offline_spec(cfg: Config) -> SymbolSpec:
    b = cfg.backtest
    return SymbolSpec(
        name=cfg.mt5.symbol, digits=b.spec_digits, point=b.spec_point,
        tick_size=b.spec_tick_size, tick_value=b.spec_tick_value,
        contract_size=b.spec_contract_size, volume_min=b.spec_volume_min,
        volume_step=b.spec_volume_step, volume_max=b.spec_volume_max,
        stops_level_points=0, filling_mode_mask=3,
        currency_margin="USD", currency_profit="USD",
    )


def write_trades_csv(report: BacktestReport, path: str, utc_offset_seconds: int = 0) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    columns = ["entry_time_utc", "exit_time_utc", "direction", "entry", "stop", "take_profit",
               "exit_price", "outcome", "volume", "profit", "r_multiple", "bars_held",
               "tp_source", "htf_ok", "swept_pool", "cisd_level"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for t in report.trades:
            writer.writerow([
                datetime.fromtimestamp(t.entry_time - utc_offset_seconds,
                                       tz=timezone.utc).isoformat(timespec="minutes"),
                datetime.fromtimestamp(t.exit_time - utc_offset_seconds,
                                       tz=timezone.utc).isoformat(timespec="minutes"),
                t.direction.value, t.entry, t.stop, t.take_profit, t.exit_price, t.outcome,
                t.volume, round(t.profit, 2), round(t.r_multiple, 3), t.bars_held,
                t.tp_source, t.htf_ok, t.swept_pool, t.cisd_level,
            ])
    log.info("wrote %d trades to %s", len(report.trades), path)


def write_signals_csv(exec_bars: Sequence[Candle], htf_bars: Sequence[Candle],
                      htf_seconds: int, cfg: Config, path: str,
                      utc_offset_seconds: int = 0) -> int:
    """Dump every engine event -- the file to diff against the indicator."""
    engine = IctSignalEngine(
        swing_len=cfg.strategy.swing_len, max_wait=cfg.strategy.max_wait,
        chain_max=cfg.strategy.chain_max, entry_mode=cfg.strategy.entry_mode,
        tp_mode=cfg.strategy.tp_mode, rr_target=cfg.strategy.rr_target,
        require_sweep_in_htf_fvg=cfg.strategy.require_sweep_in_htf_fvg,
    )
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["bar_time_utc", "bar_time_server_epoch", "event", "direction",
                         "price", "swept_pool", "swept_extreme", "cisd_level",
                         "reference_entry", "stop", "htf_ok", "note"])
        for bar in exec_bars:
            completed = [h for h in htf_bars if h.time + htf_seconds <= bar.time]
            for event in engine.on_bar(bar, completed):
                setup, plan = event.setup, event.plan
                writer.writerow([
                    datetime.fromtimestamp(bar.time - utc_offset_seconds,
                                           tz=timezone.utc).isoformat(timespec="minutes"),
                    bar.time, event.kind.value, event.direction.value, event.price,
                    setup.swept_pool if setup else "",
                    setup.swept_extreme if setup else "",
                    setup.cisd_level if setup else "",
                    plan.reference_entry if plan else "",
                    plan.stop if plan else "",
                    setup.htf_ok if setup else "",
                    event.note,
                ])
                count += 1
    log.info("wrote %d signal rows to %s", count, path)
    return count
