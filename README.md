# sweep-indicator

An early-hours liquidity sweep indicator (Pine v6), and a Python trading bot that
automates it on MetaTrader 5.

---

# ICT Sweep + CISD bot — UT100 on MetaTrader 5

Automates the four-pillar ICT model on **UT100 (US Tech 100 / NASDAQ-100 CFD,
MEXAtlantic)**:

1. **Liquidity sweep** — price wicks through a prior swing low/high and takes the stops.
2. **CISD** — a close back through the opens of the candle series that did the sweeping.
3. **Order block** — the sweep series becomes the reference zone; the protected extreme is the stop.
4. **HTF fair value gaps** — 4H BISI/SIBI as optional context.

> **The one change from the indicator: no retest.**
> The indicator draws an entry line and expects you to wait for price to come back
> to it. This bot **enters at market the moment CISD confirms**, because those
> setups frequently run straight to target without ever retesting the block. The
> drawn order-block price is kept for logging only. If you want the old behaviour,
> set `strategy.entry_trigger: limit_at_ob`.

---

## Requirements

| | |
|---|---|
| Platform | **Windows** (or Windows under Wine) with the MT5 terminal **running and logged in** |
| Python | 3.10+ |
| Packages | `MetaTrader5`, `numpy`, `PyYAML` |
| Terminal | *Algo Trading* enabled, and the symbol visible in Market Watch |

The `MetaTrader5` package talks to a local terminal over IPC — there is no
server-side API, and it does not exist on Linux/macOS. Only the live/paper path
needs it: the strategy, risk, news and backtest layers are pure stdlib and run
(and are tested) anywhere.

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Leave `mt5.login` / `password` / `server` blank and the bot attaches to whichever
account your terminal is already logged into — no credentials on disk at all. That
is the recommended setup.

If you do want the bot to log in itself, pass them through the environment
(PowerShell syntax — `set NAME=value` is cmd.exe and does nothing here):

```powershell
$env:ICT_MT5__LOGIN="12345678"
$env:ICT_MT5__PASSWORD="your-password"
$env:ICT_MT5__SERVER="MEXAtlantic-Demo"
```

Any setting can be overridden the same way: `ICT_<SECTION>__<FIELD>`, e.g.
`ICT_RISK__RISK_PCT=0.5`.

---

## Run it

```bash
# 1. Prove the plumbing: connection, LIVE contract specs, clock, calendar
python -m ict_bot --config config.yaml --mode check

# 2. Simulate on history
python -m ict_bot --config config.yaml --mode backtest --bars 20000

# 3. Compare signals against the TradingView indicator, bar for bar
python -m ict_bot --config config.yaml --mode signals --out logs/parity.csv

# 4. Paper trade on a DEMO account
python -m ict_bot --config config.yaml --mode paper

# 5. Live, on real money - needs BOTH the flag and runtime.allow_live_trading
python -m ict_bot --config config.yaml --mode live --i-understand-live
```

`paper` mode refuses to start on a real account. `live` mode requires the config
flag *and* the command-line acknowledgement. That is deliberate.

---

## Why nothing is hardcoded for UT100

MT5's symbol panel shows **Tick size 0.00 / Tick value 0** for UT100. That is
display rounding, not reality. The bot therefore:

* reads `point`, `trade_tick_size`, `trade_tick_value`, `trade_contract_size`,
  `volume_min/step/max`, `digits`, `filling_mode` and `trade_stops_level` from
  `symbol_info()` at startup, after `symbol_select()`;
* prices the stop in money with **`order_calc_profit()`** (authoritative, and
  immune to the rounded panel values), falling back to tick maths, then to
  contract size, and **refusing to trade** if none of them can value it;
* pre-checks margin with `order_calc_margin()`;
* picks FOK or IOC from the symbol's own filling mask.

`--mode check` prints all of it so you can see what your broker actually reports.

---

## Risk controls

| Control | Default | Config |
|---|---|---|
| Risk per trade | 1% of equity, sized from the stop distance | `risk.risk_pct` |
| Concurrent positions | 1, and one per direction | `risk.max_positions`, `risk.one_per_direction` |
| Daily kill switch | halt for the day at −3% | `risk.daily_max_loss_pct` |
| Daily trade cap | off | `risk.daily_max_trades` |
| Spread filter | skip above 60 points | `risk.max_spread_points` |
| Margin buffer | required margin ≤ 50% of free margin | `risk.margin_buffer` |
| Session hours | off (broker server time when on) | `session.*` |
| Minimum R:R | 1.0 | `strategy.min_rr` |
| Stop distance bounds | off | `strategy.max_stop_points`, `min_stop_points` |
| Break-even stop | move to entry at +1.5R | `management.break_even_at_r` |

If the risk-based size comes out below the broker minimum lot, the trade is
**skipped, not rounded up** — rounding up would silently blow the risk budget.
The log tells you what equity that setup would have needed.

The kill switch survives a restart (it is persisted), so a crash-and-restart
cannot reset your daily loss limit.

---

## News blackout

No new entries within **5 minutes before and 5 minutes after** a scheduled event
(both configurable). The `MetaTrader5` package has no economic calendar, so events
come from a source you control:

```yaml
news:
  enabled: true
  minutes_before: 5
  minutes_after: 5
  impacts: ["high"]
  currencies: ["USD"]
  source: csv                 # csv | ics | http_json | none
  path: calendar/events.csv
  on_feed_unavailable: block  # fail safe
```

`calendar/events.csv` ships as a starting point:

```csv
datetime_utc,currency,impact,title
2026-08-07T12:30:00Z,USD,high,Non-Farm Payrolls
2026-08-19T18:00:00Z,USD,high,FOMC Statement and Rate Decision
```

Times are **UTC**; `#` comments and blank lines are ignored. The bot converts the
broker's server clock to UTC before every check (auto-detected, or pin it with
`mt5.server_utc_offset_hours`) — get that wrong and a UTC+3 server would look for
NFP three hours late.

**Fail safe:** if the filter is on and the feed is missing, unreadable or older
than `max_stale_minutes`, entries are **blocked** and the reason is logged loudly.
Set `on_feed_unavailable: allow` to invert that, at your own risk. Keep the file
current — a stale calendar is the one way this protection quietly stops working.

`flatten_before_event: true` additionally closes open positions ahead of a release
(off by default; it will cut winners short).

---

## Break-even stop

Once a trade reaches **+1.5R**, its stop moves to the entry price and it can no
longer lose:

```yaml
management:
  break_even_enabled: true
  break_even_at_r: 1.5
  break_even_offset_points: 0   # >0 locks a few points instead of exact entry
```

Three details that matter:

* **Progress is measured at the exit price** — bid for a long, ask for a short —
  so a wide spread is never counted as profit you do not actually have.
* **Checked on every poll** (every 5s), not on bar close. +1.5R can be reached
  mid-bar, and waiting 15 minutes to protect the trade would defeat the point.
* **The original risk is persisted.** After the stop moves to entry the broker no
  longer knows what the trade risked, so R could never be measured again — the
  state file remembers it, and a restart does not re-apply or lose it.

With a floating spread, a stop exactly at entry can still exit a few points
negative. Set `break_even_offset_points` to roughly your typical spread to cover
that.

---

## Telegram: alerts for a team, control for one person

```yaml
notify:
  telegram_bot_token: ""                    # keep in the environment
  telegram_broadcast_chat_ids: ["-1001234567890"]   # a group of 50 -> one id
  telegram_admin_chat_id: "987654321"       # the ONLY chat that may command
```

Everyone in the broadcast list receives entry, exit and break-even alerts and is
strictly **read-only**. Exactly one chat id may send commands — `/status`,
`/positions`, `/pause`, `/resume`, `/close`, `/closeall`, `/be`, `/risk`,
`/stop confirm`. Everything from any other chat is logged and ignored, and a
blank admin id means *nobody*, never *anybody*.

Full walkthrough, including how to get the ids: [`docs/telegram_setup.md`](docs/telegram_setup.md).

---

## What the logs tell you

Every setup is traced from sweep to exit, and every skipped entry says why:

```
SWEEP long: pool 20143.50 taken at 20138.20, CISD level 20161.00, HTF FVG yes
CISD CONFIRMED long @ 20166.40 (order block 20149.60, stop 20138.20) - entering now, no retest
ENTERED long 2.10 lots @ 20166.90 | stop 20138.20 | target 20219.50 | R:R 1.84 | risk 100.00 | slippage +0.50
NO TRADE (short): news blackout (in 3.2 min): 2026-08-12 12:30 UTC USD [high] CPI m/m
```

`logs/signals.csv` is the machine-readable version of the same thing, and the file
you diff against the indicator.

---

## Layout

```
ict_bot/
  models.py            candles, setups, trade plans, symbol specs
  config.py            YAML + env config, validated on load
  signals/
    pivots.py          swing detection  (= ta.pivotlow / ta.pivothigh)
    fvg.py             4H BISI/SIBI zones, built from completed bars only
    engine.py          sweep -> CISD -> order block state machine   <- the strategy
  news.py              economic-calendar blackout
  telegram.py          alerts out to many, commands in from one
  risk.py              sizing, kill switch, gates
  state.py             crash-safe persistence
  mt5_client/
    session.py         connect, resolve symbol specs, server clock
    feed.py            closed-bar market data
    execution.py       orders, retcodes, retries
  runner.py            live/paper loop
  backtest.py          simulation + metrics
  cli.py               entry point
tests/                 188 tests, no MT5 required
docs/indicator_parity.md
```

Run the tests:

```bash
python -m unittest discover -s tests -t tests
```

---

## Non-repainting

`copy_rates_from_pos(..., 0, n)` returns the forming bar last; the bot **always
drops it**, on the execution timeframe and on 4H. Every decision is taken on
closed bars, mirroring `barstate.isconfirmed` and the indicator's completed-HTF-bar
logic. On startup, history is replayed through the engine to rebuild state, but
**no orders are placed from replayed bars** — a confirmation that fired while the
bot was down is skipped, because its market-on-close entry price is long gone.

Step-by-step setup instructions live in [`RUNBOOK.md`](RUNBOOK.md).

See [`docs/indicator_parity.md`](docs/indicator_parity.md) for the full mapping,
including one subtle ordering detail that parity depends on.

---

## Safety notes

* Backtest, then run **weeks** on demo before considering real money. The
  no-retest entry gives more fills and worse average entry prices than the
  indicator's drawn level — that trade-off is the whole hypothesis, so measure it
  on your own data.
* Backtest fills are modelled pessimistically (full spread + slippage charged at
  entry, stop assumed first when a bar covers both levels), but they are still a
  model. CFD fills around news and the open are worse than any simulation.
* The bot only ever touches positions carrying its own `magic` number. Manual
  trades on the same symbol are left alone — and are *not* counted in its exposure
  limits.
* Requires the terminal to stay running and logged in. If the link drops it
  reconnects with backoff; if it cannot, it stops rather than trading blind.
* Past performance in a backtest says nothing about tomorrow. Trade money you can
  afford to lose.
