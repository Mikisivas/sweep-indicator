"""ICT liquidity-sweep / CISD trading bot for MetaTrader 5.

Layout:
    models.py       value types (candles, setups, trade plans, symbol specs)
    config.py       one YAML/env config for every knob
    signals/        pure strategy logic -- no MT5, no clock, no I/O
    news.py         economic-calendar blackout filter
    risk.py         position sizing, daily loss kill switch, spread/session gates
    state.py        crash-safe persistence (never double-enter after a restart)
    mt5_client/     everything that touches the terminal
    runner.py       live/paper orchestration loop
    backtest.py     historical simulation + performance metrics
    cli.py          entry point
"""

__version__ = "1.0.0"
