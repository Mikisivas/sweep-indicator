"""A stand-in for the MetaTrader5 module, so the live path can be tested off-Windows.

It implements only what the bot actually calls, and records every order_send
request so tests can assert on exactly what would hit the broker.
"""

from __future__ import annotations

from types import SimpleNamespace

from helpers import STEP


class FakeMt5:
    # --- constants the bot references ------------------------------------
    TIMEFRAME_M15 = 15
    TIMEFRAME_H4 = 240
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_REMOVE = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_RETCODE_DONE = 10009
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self, m15_rows, h4_rows, bid=108.50, ask=108.60,
                 equity=10_000.0, is_demo=True, retcode=None):
        self.m15 = list(m15_rows)
        self.h4 = list(h4_rows)
        self.bid = bid
        self.ask = ask
        self.equity = equity
        self.trade_mode = self.ACCOUNT_TRADE_MODE_DEMO if is_demo else self.ACCOUNT_TRADE_MODE_REAL
        self.forced_retcode = retcode
        self.requests: list[dict] = []
        self.positions: list[SimpleNamespace] = []
        self.shutdown_called = False
        self._ticket = 5000

    # --- lifecycle --------------------------------------------------------
    def initialize(self, **kwargs):
        return True

    def shutdown(self):
        self.shutdown_called = True

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return SimpleNamespace(trade_allowed=True, name="FakeTerminal")

    def account_info(self):
        return SimpleNamespace(login=12345678, server="MEXAtlantic-Demo", currency="USD",
                               balance=self.equity, equity=self.equity,
                               margin_free=self.equity * 5, leverage=100,
                               trade_allowed=True, trade_mode=self.trade_mode)

    # --- symbol -----------------------------------------------------------
    def symbol_info(self, symbol):
        return SimpleNamespace(
            name=symbol, visible=True, digits=2, point=0.01,
            trade_tick_size=0.01, trade_tick_value=0.0,   # UT100: panel reports 0
            trade_contract_size=1.0, volume_min=0.01, volume_step=0.01,
            volume_max=100.0, trade_stops_level=0, filling_mode=3,
            currency_margin="USD", currency_profit="USD", spread=10,
        )

    def symbol_select(self, symbol, enable=True):
        return True

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(time=self.m15[-1]["time"], bid=self.bid, ask=self.ask,
                               last=self.bid)

    # --- data -------------------------------------------------------------
    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        rows = self.h4 if timeframe == self.TIMEFRAME_H4 else self.m15
        return rows[-count:] if count < len(rows) else list(rows)

    # --- money ------------------------------------------------------------
    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        move = (price_close - price_open) if order_type == self.ORDER_TYPE_BUY \
            else (price_open - price_close)
        return move * volume * 1.0        # contract size 1: 1 lot = 1 USD per point

    def order_calc_margin(self, order_type, symbol, volume, price):
        return price * volume / 100.0     # 1:100 leverage

    # --- trading ----------------------------------------------------------
    def order_send(self, request):
        self.requests.append(dict(request))
        if self.forced_retcode is not None:
            return SimpleNamespace(retcode=self.forced_retcode, order=0, deal=0,
                                   price=0.0, volume=0.0, comment="rejected")

        if request["action"] == self.TRADE_ACTION_SLTP:
            for position in self.positions:
                if position.ticket == request.get("position"):
                    position.sl = request.get("sl", position.sl)
                    position.tp = request.get("tp", position.tp)
            return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=0, deal=0,
                                   price=0.0, volume=0.0, comment="done")

        if request["action"] == self.TRADE_ACTION_DEAL and "position" in request:
            self.positions = [p for p in self.positions
                              if p.ticket != request["position"]]
            return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=0, deal=0,
                                   price=request.get("price", self.bid),
                                   volume=request.get("volume", 0.0), comment="closed")

        self._ticket += 1
        price = request.get("price", self.ask)
        if request["action"] == self.TRADE_ACTION_DEAL and "position" not in request:
            self.positions.append(SimpleNamespace(
                ticket=self._ticket,
                type=(self.POSITION_TYPE_BUY if request["type"] == self.ORDER_TYPE_BUY
                      else self.POSITION_TYPE_SELL),
                volume=request["volume"], price_open=price, sl=request.get("sl", 0.0),
                tp=request.get("tp", 0.0), profit=0.0, time=self.m15[-1]["time"],
                magic=request.get("magic", 0), comment=request.get("comment", ""),
            ))
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=self._ticket,
                               deal=self._ticket, price=price,
                               volume=request.get("volume", 0.0), comment="done")

    def positions_get(self, symbol=None):
        return list(self.positions)

    def orders_get(self, symbol=None):
        return []

    def history_deals_get(self, **kwargs):
        return []

    # --- helpers for tests -------------------------------------------------
    def deals(self):
        return [r for r in self.requests
                if r["action"] == self.TRADE_ACTION_DEAL and "position" not in r]

    def sltp_requests(self):
        return [r for r in self.requests if r["action"] == self.TRADE_ACTION_SLTP]

    def closes(self):
        return [r for r in self.requests
                if r["action"] == self.TRADE_ACTION_DEAL and "position" in r]

    def move_price(self, bid, spread=0.10):
        self.bid = bid
        self.ask = round(bid + spread, 2)


def rows_from(candle_rows, start=1_700_000_000, step=STEP):
    """(o,h,l,c) tuples -> MT5-shaped rate dicts."""
    return [{"time": start + i * step, "open": o, "high": h, "low": lo, "close": c,
             "tick_volume": 100}
            for i, (o, h, lo, c) in enumerate(candle_rows)]


def h4_rows(count=5, start=1_699_000_000):
    return [{"time": start + i * 14400, "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.5, "tick_volume": 100} for i in range(count)]
