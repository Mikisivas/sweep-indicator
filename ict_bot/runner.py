"""Live / paper orchestration.

One pass per new CLOSED execution bar:

    new closed bar -> engine.on_bar(bar, completed HTF bars) -> events
    CONFIRMED event -> gates -> size -> order

Gates, in order, every one of which can veto the trade:
    structural sanity -> daily kill switch -> session -> NEWS BLACKOUT ->
    exposure -> spread -> sizing -> margin

Startup replays history through the engine WITHOUT trading, then only acts on
bars that close from that point on. A signal that fired while the bot was down is
deliberately skipped: this model enters at market on the confirmation close, and
that price is long gone.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from .config import Config
from .journal import SignalJournal, describe_event
from .models import Direction, EventKind, FinalTrade, GateResult, SignalEvent
from .mt5_client.execution import Executor, OpenPosition
from .mt5_client.feed import MarketFeed
from .mt5_client.session import Mt5Session, Mt5Unavailable, timeframe_seconds
from .news import NewsFilter
from .notify import Notifier
from .risk import RiskManager
from .signals.engine import IctSignalEngine
from .state import PendingLimit, load_state

log = logging.getLogger(__name__)


class LiveRunner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = Mt5Session(cfg.mt5)
        self.notifier = Notifier(cfg.notify)
        self.news = NewsFilter(cfg.news)
        self.engine = self._fresh_engine()
        self.feed: Optional[MarketFeed] = None
        self.executor: Optional[Executor] = None
        self.risk: Optional[RiskManager] = None
        self.journal: Optional[SignalJournal] = None
        self.state = None
        self.state_path = ""
        self.watermark = 0
        self._known_tickets: dict[int, OpenPosition] = {}
        self._running = False

    def _fresh_engine(self) -> IctSignalEngine:
        s = self.cfg.strategy
        return IctSignalEngine(
            swing_len=s.swing_len, max_wait=s.max_wait, chain_max=s.chain_max,
            entry_mode=s.entry_mode, tp_mode=s.tp_mode, rr_target=s.rr_target,
            require_sweep_in_htf_fvg=s.require_sweep_in_htf_fvg,
        )

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        account = self.session.connect()
        self._assert_mode_allowed(account)

        self.feed = MarketFeed(self.session)
        self.executor = Executor(self.session, self.cfg.mt5.magic, self.cfg.mt5.deviation_points)
        self.risk = RiskManager(
            risk=self.cfg.risk, strategy=self.cfg.strategy, session=self.cfg.session,
            spec=self.session.spec,
            profit_calc=self.executor.profit_calc, margin_calc=self.executor.margin_calc,
        )
        self.journal = SignalJournal(self.cfg.runtime.signals_csv, to_utc=self.session.server_to_utc)

        self.state, self.state_path = load_state(self.cfg.runtime.state_file, self.session.symbol)
        self.state.restore_book(self.risk.book)

        news_ok = self.news.refresh(self.session.utc_now(), force=True)
        if self.cfg.news.enabled and not news_ok:
            log.error("news calendar could not be loaded at startup - entries will be %s",
                      "BLOCKED (fail safe)" if self.cfg.news.on_feed_unavailable == "block"
                      else "allowed by config")

        self._warm_up()
        self._known_tickets = {p.ticket: p for p in self.executor.positions()}
        log.info("ready: %s %s, %d open position(s), watermark %s",
                 self.session.symbol, self.cfg.mt5.exec_timeframe, len(self._known_tickets),
                 self.session.server_datetime(self.watermark))
        self.notifier.send(
            f"ICT bot started [{self.cfg.runtime.mode}] {self.session.symbol} "
            f"{self.cfg.mt5.exec_timeframe} | equity {account.equity:,.2f} {account.currency}",
            kind="info",
        )

    def _assert_mode_allowed(self, account) -> None:
        """Live money needs both a config flag and a CLI acknowledgement."""
        if self.cfg.runtime.mode == "live":
            if not self.cfg.runtime.allow_live_trading:
                raise RuntimeError(
                    "mode=live but runtime.allow_live_trading is false. Refusing to trade "
                    "real money without an explicit opt-in.")
            if account.is_demo:
                log.warning("mode=live but the account reports DEMO - trading the demo account")
            else:
                log.warning("LIVE TRADING on real account %s @ %s", account.login, account.server)
        elif not account.is_demo:
            raise RuntimeError(
                f"mode={self.cfg.runtime.mode} refuses to run on a REAL account "
                f"({account.login} @ {account.server}). Use a demo account, or set "
                f"mode=live plus allow_live_trading if you really mean it.")

    def _warm_up(self) -> None:
        """Rebuild engine state from history. No orders are placed here."""
        exec_bars = self.feed.closed_bars(self.cfg.mt5.exec_timeframe, self.cfg.mt5.history_bars)
        htf_bars = self.feed.closed_bars(self.cfg.mt5.htf_timeframe, self.cfg.mt5.htf_history_bars)
        htf_seconds = timeframe_seconds(self.cfg.mt5.htf_timeframe)

        sweeps = confirms = 0
        for bar in exec_bars:
            completed_htf = [h for h in htf_bars if h.time + htf_seconds <= bar.time]
            for event in self.engine.on_bar(bar, completed_htf):
                if event.kind is EventKind.SWEEP:
                    sweeps += 1
                elif event.kind is EventKind.CONFIRMED:
                    confirms += 1
        self.watermark = max(exec_bars[-1].time, int(self.state.last_acted_bar_time or 0))
        log.info("warm-up: replayed %d bars (%d sweeps, %d confirmations, "
                 "%d live HTF FVGs) - no orders placed",
                 len(exec_bars), sweeps, confirms,
                 len(self.engine.fvg.bullish) + len(self.engine.fvg.bearish))
        if self.engine.bull or self.engine.bear:
            armed = [s.direction.value for s in (self.engine.bull, self.engine.bear) if s]
            log.info("carrying armed setup(s) into the session: %s", ", ".join(armed))

    # ------------------------------------------------------------------- loop
    def run(self) -> None:
        self._running = True
        try:
            self.start()
            while self._running:
                try:
                    self.tick_once()
                except Mt5Unavailable as exc:
                    log.error("terminal problem: %s", exc)
                    if not self.session.ensure_connected():
                        log.critical("cannot reconnect - stopping")
                        break
                except Exception:  # noqa: BLE001 - a bad bar must not kill the bot
                    log.exception("unhandled error in the trading loop")
                time.sleep(self.cfg.runtime.poll_seconds)
        except KeyboardInterrupt:
            log.info("interrupted - shutting down")
        finally:
            self.stop()

    def stop(self) -> None:
        """Safe to call even if start() failed half way through."""
        self._running = False
        if self.state is not None:
            if self.risk is not None:
                self.state.absorb_book(self.risk.book)
            try:
                self.state.save(self.state_path)
            except OSError as exc:
                log.error("could not save state: %s", exc)
        try:
            self.session.shutdown()
        except Exception as exc:  # noqa: BLE001
            log.warning("terminal shutdown was not clean: %s", exc)
        log.info("stopped")

    def tick_once(self) -> None:
        """One poll: housekeeping, then a new closed bar if there is one."""
        if not self.session.ensure_connected():
            return
        utc_now = self.session.utc_now()
        self.news.refresh(utc_now)

        self._reconcile_positions()
        self._expire_pending_limits()
        self._maybe_flatten_for_news(utc_now)

        latest = self.feed.latest_closed(self.cfg.mt5.exec_timeframe)
        if latest is None or latest.time <= self.watermark:
            return

        # More than one bar may have closed (e.g. after a network stall): replay
        # them in order so the state machine never skips a bar.
        exec_seconds = timeframe_seconds(self.cfg.mt5.exec_timeframe)
        bars_behind = int((latest.time - self.watermark) / exec_seconds) + 2
        if bars_behind > 200:
            # Too far behind to stitch onto the current state; rebuild it from
            # history instead of feeding the engine a sequence with a hole in it.
            log.warning("%d bars behind - rebuilding engine state from history", bars_behind)
            self.engine = self._fresh_engine()
            self._warm_up()
            self.state.last_acted_bar_time = self.watermark
            return

        pending = [b for b in self.feed.closed_bars(self.cfg.mt5.exec_timeframe,
                                                    max(bars_behind, 3))
                   if b.time > self.watermark]
        htf_bars = self.feed.closed_bars(self.cfg.mt5.htf_timeframe, 20)
        htf_seconds = timeframe_seconds(self.cfg.mt5.htf_timeframe)

        for bar in pending:
            completed_htf = [h for h in htf_bars if h.time + htf_seconds <= bar.time]
            events = self.engine.on_bar(bar, completed_htf)
            is_latest = bar.time == pending[-1].time
            for event in events:
                self._handle_event(event, act=is_latest)
            self.watermark = bar.time
            self.state.last_acted_bar_time = bar.time

        self.state.absorb_book(self.risk.book)
        try:
            self.state.save(self.state_path)
        except OSError as exc:
            log.error("could not save state: %s", exc)

    # ------------------------------------------------------------- event flow
    def _handle_event(self, event: SignalEvent, act: bool) -> None:
        log.info("%s", describe_event(event, self.session.spec.digits))
        if event.kind is not EventKind.CONFIRMED or event.plan is None:
            self.journal.write(event)
            return
        if not act:
            # Signal from a bar that closed before we caught up: the market-on-close
            # price is stale, so we log it and stand down.
            self.journal.write(event, action="skipped", detail="stale bar during catch-up")
            log.warning("skipping a confirmation from an older bar (catch-up)")
            return

        entry_price = self._intended_entry(event.plan)
        trade = event.plan.finalize(entry_price)
        gate = self._gates(trade)
        if not gate.allowed:
            reason = "; ".join(gate.reasons)
            log.warning("NO TRADE (%s): %s", trade.direction.value, reason)
            self.journal.write(event, action="blocked", trade=trade, detail=reason)
            self.notifier.send(f"Setup skipped ({trade.direction.value}): {reason}", kind="block")
            return

        sizing = self.risk.position_size(trade, self.session.account().equity)
        if not sizing.ok:
            log.warning("NO TRADE (%s): %s", trade.direction.value, sizing.reason)
            self.journal.write(event, action="blocked", trade=trade, detail=sizing.reason)
            self.notifier.send(f"Setup skipped: {sizing.reason}", kind="block")
            return

        margin_ok, margin_reason = self.risk.margin_ok(
            trade, sizing.volume, self.session.account().free_margin)
        if not margin_ok:
            log.warning("NO TRADE (%s): %s", trade.direction.value, margin_reason)
            self.journal.write(event, action="blocked", trade=trade, detail=margin_reason)
            return

        self._execute(event, trade, sizing)

    def _intended_entry(self, plan) -> float:
        """Price the gates and sizing are evaluated against.

        NO RETEST: in the default market_on_cisd mode that is the live ask/bid at
        the moment CISD confirmed, not the order-block level the indicator draws.
        """
        if self.cfg.strategy.entry_trigger == "limit_at_ob":
            return plan.reference_entry
        tick = self.session.tick()
        return tick.ask if plan.direction is Direction.LONG else tick.bid

    def _gates(self, trade: FinalTrade) -> GateResult:
        gate = GateResult(True)
        server_now = self.session.server_now()
        utc_now = self.session.utc_now()
        equity = self.session.account().equity

        for check in (
            self.risk.check_trade_quality(trade),
            self.risk.check_daily(server_now, equity),
            self.risk.check_session(server_now),
            self.risk.check_exposure(self.executor.positions(), trade.direction),
            self.risk.check_spread(self.session.spread_points()),
        ):
            if not check.allowed:
                gate.allowed = False
                gate.reasons.extend(check.reasons)

        blackout = self.news.status(utc_now)
        if blackout.blocked:
            gate.allowed = False
            gate.reasons.append(blackout.reason)
        return gate

    def _execute(self, event: SignalEvent, trade: FinalTrade, sizing) -> None:
        digits = self.session.spec.digits
        if self.cfg.strategy.entry_trigger == "limit_at_ob":
            self._place_limit(event, trade, sizing)
            return

        result = self.executor.open_market(trade, sizing.volume)
        if not result.ok:
            self.journal.write(event, action="order_failed", trade=trade,
                               volume=sizing.volume, detail=result.message)
            self.notifier.send(f"ORDER FAILED {trade.direction.value}: {result.message}",
                               kind="entry")
            return

        self.risk.record_fill()
        filled = trade.plan.finalize(result.price)  # true numbers at the real fill
        self.journal.write(event, action="entered", trade=filled, volume=result.volume,
                           ticket=result.ticket,
                           detail=f"sized via {sizing.method}, risk {sizing.risk_amount:,.2f}")
        slip = result.price - trade.entry
        log.info("ENTERED %s %.2f lots @ %.*f | stop %.*f | target %.*f | R:R %.2f | "
                 "risk %.2f | slippage %+.*f",
                 filled.direction.value, result.volume, digits, result.price, digits,
                 filled.stop, digits, filled.take_profit, filled.rr, sizing.risk_amount,
                 digits, slip)
        self.notifier.send(
            f"{'LONG' if filled.direction is Direction.LONG else 'SHORT'} {self.session.symbol}\n"
            f"Entry {result.price:.{digits}f}\nStop {filled.stop:.{digits}f}\n"
            f"Target {filled.take_profit:.{digits}f} ({filled.tp_source})\n"
            f"R:R {filled.rr:.2f} | {result.volume} lots | risk {sizing.risk_amount:,.2f}",
            kind="entry",
        )

    def _place_limit(self, event: SignalEvent, trade: FinalTrade, sizing) -> None:
        """Optional mode: rest an order at the order block instead of market."""
        plan = event.plan
        limit_trade = plan.finalize(plan.reference_entry)
        tick = self.session.tick()
        wrong_side = (
            (limit_trade.direction is Direction.LONG and plan.reference_entry >= tick.ask)
            or (limit_trade.direction is Direction.SHORT and plan.reference_entry <= tick.bid)
        )
        if wrong_side:
            detail = (f"order block {plan.reference_entry:.{self.session.spec.digits}f} is not "
                      f"below/above the market - no limit placed")
            log.warning("%s", detail)
            self.journal.write(event, action="blocked", trade=limit_trade, detail=detail)
            return
        if not limit_trade.is_valid:
            self.journal.write(event, action="blocked", trade=limit_trade,
                               detail="limit levels malformed")
            return

        bar_seconds = timeframe_seconds(self.cfg.mt5.exec_timeframe)
        expires = event.bar_time + self.cfg.strategy.limit_expiry_bars * bar_seconds
        result = self.executor.place_limit(limit_trade, sizing.volume)
        if not result.ok:
            self.journal.write(event, action="order_failed", trade=limit_trade,
                               detail=result.message)
            return
        self.state.add_pending(PendingLimit(
            ticket=result.ticket, direction=limit_trade.direction.value,
            placed_bar_time=event.bar_time, expires_bar_time=expires,
            entry=limit_trade.entry, stop=limit_trade.stop,
            take_profit=limit_trade.take_profit, volume=sizing.volume,
        ))
        self.journal.write(event, action="limit_placed", trade=limit_trade,
                           volume=sizing.volume, ticket=result.ticket)

    # ---------------------------------------------------------- housekeeping
    def _expire_pending_limits(self) -> None:
        if not self.state.pending_limits:
            return
        live = {o.ticket for o in self.executor.pending_orders()}
        now = int(time.time() + self.session.utc_offset_seconds)
        for pending in self.state.pendings():
            if pending.ticket not in live:
                self.state.drop_pending(pending.ticket)  # filled or already gone
                continue
            if now >= pending.expires_bar_time:
                log.info("order block limit %s expired unfilled", pending.ticket)
                if self.executor.cancel_order(pending.ticket):
                    self.state.drop_pending(pending.ticket)

    def _reconcile_positions(self) -> None:
        """Detect closes so the daily book and notifications stay honest."""
        current = {p.ticket: p for p in self.executor.positions()}
        for ticket, previous in list(self._known_tickets.items()):
            if ticket in current:
                continue
            profit = self._closed_profit(ticket)
            self.risk.record_close(profit)
            log.info("position %s closed, realised %.2f (day total %.2f)",
                     ticket, profit, self.risk.book.realised)
            self.notifier.send(
                f"Closed {previous.direction.value} {self.session.symbol}: {profit:+,.2f}",
                kind="exit")
        self._known_tickets = current

    def _closed_profit(self, ticket: int) -> float:
        """Sum the deals of a closed position (profit + swap + commission)."""
        mt5 = self.session.mt5
        try:
            deals = mt5.history_deals_get(position=ticket)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read deal history for %s: %s", ticket, exc)
            return 0.0
        if not deals:
            return 0.0
        return float(sum(d.profit + d.swap + d.commission for d in deals))

    def _maybe_flatten_for_news(self, utc_now: datetime) -> None:
        status = self.news.should_flatten(utc_now)
        if not status.blocked:
            return
        for position in self.executor.positions():
            log.warning("flattening %s ahead of news: %s", position.ticket, status.reason)
            self.executor.close_position(position.ticket, reason="news")
            self.notifier.send(f"Closed {position.ticket} ahead of news: {status.reason}",
                               kind="exit")


def run_live(cfg: Config) -> None:
    LiveRunner(cfg).run()
