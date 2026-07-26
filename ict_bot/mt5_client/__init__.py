"""Everything that touches the MetaTrader 5 terminal lives here.

The `MetaTrader5` package is imported lazily by :func:`mt5_api`, so the strategy,
risk and news layers (and their tests) run anywhere -- including Linux CI, where
no terminal exists.
"""

from .session import Mt5Session, mt5_api

__all__ = ["Mt5Session", "mt5_api"]
