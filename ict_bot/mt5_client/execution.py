"""Order execution against the terminal.

UT100 on MEXAtlantic is Market Execution with FOK/IOC filling and stop level 0,
so stops and targets can be attached straight to the deal. Some servers still
refuse SL/TP on the opening deal (retcode 10016); when that happens we open flat
and immediately attach the protection with a TRADE_ACTION_SLTP modify. A position
is never knowingly left naked -- if the modify also fails, the position is closed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ..models import Direction, FinalTrade
from .session import Mt5Session

log = logging.getLogger(__name__)

# Retcodes worth another attempt with a refreshed price.
TRANSIENT = {10004, 10008, 10012, 10020, 10021, 10024, 10031}
RETCODE_TEXT = {
    10004: "requote", 10006: "request rejected", 10007: "cancelled by trader",
    10008: "order placed", 10009: "done", 10010: "done partially",
    10011: "request processing error", 10012: "request timed out",
    10013: "invalid request", 10014: "invalid volume", 10015: "invalid price",
    10016: "invalid stops", 10017: "trading disabled", 10018: "market closed",
    10019: "not enough money", 10020: "prices changed", 10021: "no quotes",
    10024: "too many requests", 10027: "AutoTrading disabled in the terminal",
    10030: "unsupported filling mode", 10031: "no connection to the trade server",
    10040: "position limit reached",
}


def describe(retcode: int) -> str:
    return RETCODE_TEXT.get(int(retcode), f"retcode {retcode}")


@dataclass
class OpenPosition:
    ticket: int
    direction: Direction
    volume: float
    price_open: float
    stop: float
    take_profit: float
    profit: float
    time: int
    comment: str = ""


@dataclass
class ExecResult:
    ok: bool
    ticket: int = 0
    price: float = 0.0
    volume: float = 0.0
    retcode: int = 0
    message: str = ""
    attempts: int = 0
    warnings: list[str] = field(default_factory=list)


class Executor:
    def __init__(self, session: Mt5Session, magic: int, deviation_points: int,
                 max_attempts: int = 3) -> None:
        self.session = session
        self.magic = magic
        self.deviation = deviation_points
        self.max_attempts = max_attempts

    # ------------------------------------------------------------------ read
    def positions(self) -> list[OpenPosition]:
        mt5 = self.session.mt5
        raw = mt5.positions_get(symbol=self.session.symbol)
        if raw is None:
            return []
        out = []
        for pos in raw:
            if pos.magic != self.magic:
                continue  # never touch trades this bot did not open
            out.append(OpenPosition(
                ticket=pos.ticket,
                direction=Direction.LONG if pos.type == mt5.POSITION_TYPE_BUY else Direction.SHORT,
                volume=pos.volume, price_open=pos.price_open, stop=pos.sl,
                take_profit=pos.tp, profit=pos.profit, time=int(pos.time),
                comment=pos.comment,
            ))
        return out

    def pending_orders(self) -> list:
        raw = self.session.mt5.orders_get(symbol=self.session.symbol)
        return [o for o in (raw or []) if o.magic == self.magic]

    # ----------------------------------------------------------------- write
    def open_market(self, trade: FinalTrade, volume: float, comment: str = "ICT CISD") -> ExecResult:
        """Market entry with SL/TP attached; retries transient rejections."""
        mt5 = self.session.mt5
        spec = self.session.spec
        result = ExecResult(False)

        for attempt in range(1, self.max_attempts + 1):
            result.attempts = attempt
            tick = self.session.tick()
            price = tick.ask if trade.direction is Direction.LONG else tick.bid
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.session.symbol,
                "volume": float(volume),
                "type": (mt5.ORDER_TYPE_BUY if trade.direction is Direction.LONG
                         else mt5.ORDER_TYPE_SELL),
                "price": float(price),
                "sl": float(spec.round_price(trade.stop)),
                "tp": float(spec.round_price(trade.take_profit)),
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": comment[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self.session.filling_type(),
            }
            sent = mt5.order_send(request)
            if sent is None:
                result.message = f"order_send returned None: {mt5.last_error()}"
                log.error("%s (attempt %d)", result.message, attempt)
                time.sleep(0.5 * attempt)
                continue

            code = int(sent.retcode)
            result.retcode = code
            if code in (mt5.TRADE_RETCODE_DONE, 10010):
                result.ok = True
                result.ticket = int(sent.order or getattr(sent, "deal", 0) or 0)
                result.price = float(sent.price or price)
                result.volume = float(sent.volume or volume)
                if code == 10010:
                    result.warnings.append(
                        f"partial fill: {result.volume} of {volume} lots")
                    log.warning("partial fill %.2f of %.2f lots", result.volume, volume)
                log.info("FILLED %s %.2f lots @ %.*f (sl %.*f tp %.*f) ticket %s",
                         trade.direction.value, result.volume, spec.digits,
                         result.price, spec.digits, trade.stop, spec.digits,
                         trade.take_profit, result.ticket)
                return result

            result.message = f"{describe(code)} ({code}) - {sent.comment}"
            log.error("order rejected: %s (attempt %d)", result.message, attempt)

            if code == 10016:  # invalid stops -> open flat, then attach protection
                return self._open_then_protect(trade, volume, comment)
            if code == 10030:  # unsupported filling -> nothing to retry, spec was wrong
                return result
            if code not in TRANSIENT:
                return result
            time.sleep(0.5 * attempt)

        return result

    def _open_then_protect(self, trade: FinalTrade, volume: float, comment: str) -> ExecResult:
        mt5 = self.session.mt5
        spec = self.session.spec
        log.warning("server refused SL/TP on the deal - opening flat then attaching them")
        tick = self.session.tick()
        price = tick.ask if trade.direction is Direction.LONG else tick.bid
        sent = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.session.symbol,
            "volume": float(volume),
            "type": (mt5.ORDER_TYPE_BUY if trade.direction is Direction.LONG
                     else mt5.ORDER_TYPE_SELL),
            "price": float(price),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self.session.filling_type(),
        })
        if sent is None or int(sent.retcode) not in (mt5.TRADE_RETCODE_DONE, 10010):
            code = int(sent.retcode) if sent else 0
            return ExecResult(False, retcode=code,
                              message=f"flat entry also rejected: {describe(code)}")

        result = ExecResult(True, ticket=int(sent.order or 0), price=float(sent.price or price),
                            volume=float(sent.volume or volume), retcode=int(sent.retcode))
        position = next((p for p in self.positions() if p.stop == 0.0), None)
        ticket = position.ticket if position else result.ticket
        if not self.modify_sltp(ticket, trade.stop, trade.take_profit):
            log.critical("could not attach SL/TP to ticket %s - closing it immediately "
                         "rather than running an unprotected position", ticket)
            self.close_position(ticket)
            return ExecResult(False, retcode=10016,
                              message="opened without protection and was closed again")
        result.warnings.append("SL/TP attached after entry")
        return result

    def modify_sltp(self, ticket: int, stop: float, take_profit: float) -> bool:
        mt5 = self.session.mt5
        spec = self.session.spec
        for attempt in range(1, self.max_attempts + 1):
            sent = mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": self.session.symbol,
                "position": int(ticket),
                "sl": float(spec.round_price(stop)),
                "tp": float(spec.round_price(take_profit)),
                "magic": self.magic,
            })
            if sent is not None and int(sent.retcode) == mt5.TRADE_RETCODE_DONE:
                return True
            code = int(sent.retcode) if sent else 0
            log.error("SLTP modify failed on %s: %s (attempt %d)", ticket, describe(code), attempt)
            if code not in TRANSIENT:
                return False
            time.sleep(0.5 * attempt)
        return False

    def place_limit(self, trade: FinalTrade, volume: float, expiry_epoch: Optional[int] = None,
                    comment: str = "ICT OB limit") -> ExecResult:
        """`limit_at_ob` mode: rest an order at the order block instead of paying market."""
        mt5 = self.session.mt5
        spec = self.session.spec
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self.session.symbol,
            "volume": float(volume),
            "type": (mt5.ORDER_TYPE_BUY_LIMIT if trade.direction is Direction.LONG
                     else mt5.ORDER_TYPE_SELL_LIMIT),
            "price": float(spec.round_price(trade.entry)),
            "sl": float(spec.round_price(trade.stop)),
            "tp": float(spec.round_price(trade.take_profit)),
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self.session.filling_type(),
        }
        sent = mt5.order_send(request)
        if sent is None:
            return ExecResult(False, message=f"order_send None: {mt5.last_error()}")
        code = int(sent.retcode)
        if code == mt5.TRADE_RETCODE_DONE:
            log.info("limit %s %.2f lots @ %.*f ticket %s", trade.direction.value, volume,
                     spec.digits, trade.entry, sent.order)
            return ExecResult(True, ticket=int(sent.order), price=float(trade.entry),
                              volume=float(volume), retcode=code)
        return ExecResult(False, retcode=code, message=f"{describe(code)} - {sent.comment}")

    def cancel_order(self, ticket: int) -> bool:
        mt5 = self.session.mt5
        sent = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": int(ticket)})
        ok = sent is not None and int(sent.retcode) == mt5.TRADE_RETCODE_DONE
        if ok:
            log.info("cancelled pending order %s", ticket)
        else:
            log.warning("could not cancel order %s: %s", ticket,
                        describe(int(sent.retcode)) if sent else "no reply")
        return ok

    def close_position(self, ticket: int, reason: str = "") -> ExecResult:
        mt5 = self.session.mt5
        position = next((p for p in self.positions() if p.ticket == ticket), None)
        if position is None:
            return ExecResult(False, message=f"position {ticket} not found")
        for attempt in range(1, self.max_attempts + 1):
            tick = self.session.tick()
            closing_long = position.direction is Direction.LONG
            sent = mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.session.symbol,
                "position": int(ticket),
                "volume": float(position.volume),
                "type": mt5.ORDER_TYPE_SELL if closing_long else mt5.ORDER_TYPE_BUY,
                "price": float(tick.bid if closing_long else tick.ask),
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": (reason or "ICT close")[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self.session.filling_type(),
            })
            if sent is not None and int(sent.retcode) in (mt5.TRADE_RETCODE_DONE, 10010):
                log.info("closed ticket %s (%s)", ticket, reason or "manual")
                return ExecResult(True, ticket=ticket, price=float(sent.price or 0.0),
                                  volume=float(sent.volume or position.volume),
                                  retcode=int(sent.retcode))
            code = int(sent.retcode) if sent else 0
            log.error("close failed on %s: %s (attempt %d)", ticket, describe(code), attempt)
            if code not in TRANSIENT:
                return ExecResult(False, retcode=code, message=describe(code))
            time.sleep(0.5 * attempt)
        return ExecResult(False, message="close retries exhausted")

    # ------------------------------------------------------------ calculators
    def profit_calc(self, direction: Direction, volume: float, entry: float,
                    exit_price: float) -> Optional[float]:
        mt5 = self.session.mt5
        order_type = (mt5.ORDER_TYPE_BUY if direction is Direction.LONG
                      else mt5.ORDER_TYPE_SELL)
        return mt5.order_calc_profit(order_type, self.session.symbol, float(volume),
                                     float(entry), float(exit_price))

    def margin_calc(self, direction: Direction, volume: float, price: float) -> Optional[float]:
        mt5 = self.session.mt5
        order_type = (mt5.ORDER_TYPE_BUY if direction is Direction.LONG
                      else mt5.ORDER_TYPE_SELL)
        return mt5.order_calc_margin(order_type, self.session.symbol, float(volume), float(price))
