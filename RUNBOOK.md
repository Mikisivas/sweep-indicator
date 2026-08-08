# Runbook — from extracted folder to a bot running on demo

Follow this in order. Each stage has a **pass condition**; do not move on until you
see it. Everything here assumes Windows with the MT5 terminal installed.

---

## Stage 0 — Python and the virtual environment

Open the project folder in VS Code, then open a terminal (`Ctrl` + `` ` ``).

```powershell
python --version
```

You need **3.10 or newer**. If Windows opens the Microsoft Store instead, install
Python from python.org and tick *"Add python.exe to PATH"* during setup.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell refuses with *"running scripts is disabled"*:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

Your prompt should now start with `(.venv)`.

Then point VS Code at it: `Ctrl+Shift+P` → **Python: Select Interpreter** →
choose the one inside `.venv`. This makes the Testing panel and the debugger use
the right Python.

**Pass condition:** `(.venv)` in the prompt, and the VS Code status bar shows the
`.venv` interpreter.

---

## Stage 1 — Install and prove the logic (no MT5 yet)

```powershell
pip install -r requirements.txt
python -m unittest discover -s tests -t tests
```

`MetaTrader5` installs only on Windows. If you are on Mac/Linux it will fail —
everything except live/paper still works, see Stage 6.

**Pass condition:** `Ran 143 tests ... OK`.

That result means the sweep detection, CISD chain, state machine, position
sizing, news blackout and the full order path are all verified on your machine,
before a single byte reaches your broker.

You can also run these from the VS Code **Testing** panel (flask icon in the left
bar) — `.vscode/settings.json` is already configured for it.

---

## Stage 2 — Prepare the MT5 terminal

Do all of this inside MT5 itself, not in VS Code:

1. **Log in to a DEMO account.** `paper` mode refuses to start on a real account —
   deliberately. If you do not have one: *File → Open an Account* → MEXAtlantic →
   demo.
2. **Enable AutoTrading.** The *Algo Trading* button in the toolbar must be green.
   Without it every order comes back `10027`.
3. **Make UT100 visible.** *View → Market Watch* (`Ctrl+M`), find `UT100`,
   right-click → *Show*. Confirm the **exact** spelling — some brokers use
   suffixes like `UT100.r`. Whatever it says, that string goes in your config.
4. **Download history.** Open a `UT100` **M15** chart and press `Home`, then keep
   scrolling left for a few seconds. Do the same on an **H4** chart. The Python
   API can only read bars the terminal has already downloaded; skipping this is
   the single most common cause of "not enough bars" later.
5. **Leave the terminal running.** The bot talks to this window. Close it and the
   bot loses its connection.

**Pass condition:** UT100 in Market Watch, Algo Trading green, M15 and H4 charts
open with history scrolled back.

---

## Stage 3 — Configuration

```powershell
copy config.example.yaml config.yaml
```

Open `config.yaml` in VS Code. **The simplest setup: change nothing.**

Leave `login`, `password` and `server` blank. When they are blank the bot attaches
to whatever account your terminal is already logged into — which is exactly what
you want, and means no credentials anywhere on disk.

The only line you may need to touch is the symbol, if Market Watch showed
something other than `UT100`:

```yaml
mt5:
  symbol: UT100        # <- must match Market Watch exactly
```

> **Run every command from the project root** (the folder containing
> `ict_bot/` and `config.yaml`). Paths like `calendar/events.csv` are relative.
> Run from elsewhere and the news filter cannot find its calendar, and because it
> fails safe it will block every entry.

*Optional, only if you want the bot to log into a specific account itself:*
credentials are read from the environment. In PowerShell — note this is
`$env:NAME="value"`, not `set NAME=value`, which is the old cmd.exe syntax and
silently does nothing in PowerShell:

```powershell
$env:ICT_MT5__LOGIN="12345678"
$env:ICT_MT5__PASSWORD="your-password"
$env:ICT_MT5__SERVER="MEXAtlantic-Demo"
```

---

## Stage 4 — `--mode check`, the most important command

```powershell
python -m ict_bot --config config.yaml --mode check
```

Or press `F5` in VS Code and pick **"1. Check"**.

This touches nothing and places no orders. It reads your terminal and prints what
it found. Read the output carefully:

```
--- connection ---
account       : 12345678 @ MEXAtlantic-Demo (DEMO)
equity        : 10,000.00 USD
trade allowed : True

--- symbol (read live, nothing hardcoded) ---
name          : UT100
digits/point  : 2 / 0.01
tick size/val : 0.01 / 0.0   <- panel shows 0; sizing uses order_calc_profit
contract size : 1.0
volume        : min 0.01 step 0.01 max 100.0
stops level   : 0 points
filling mask  : 3 -> using 0
spread now    : 14 points (cap 60)

--- money check on a 1.00 lot, 10-point stop ---
order_calc_profit : -10.0
order_calc_margin : 200.0

--- clock ---
server offset : UTC+3.0h
server now    : 2026-07-26 12:34:56 (broker)
utc now       : 2026-07-26 09:34:56 UTC
last M15 bar  : 2026-07-26 12:30 O20140.5 H20155.0 L20138.2 C20151.0
last H4 bar   : 2026-07-26 12:00

--- news filter ---
source        : csv (calendar/events.csv)
window        : 5 min before / 5 min after
scope         : impacts=['high'] currencies=['USD']
feed usable   : True
entries now   : allowed
next event    : 2026-08-07 12:30 UTC USD [high] Non-Farm Payrolls
```

### What must be true before you continue

| Line | Requirement |
|---|---|
| `account` | ends in **(DEMO)** |
| `trade allowed` | **True** — if False, the Algo Trading button is off |
| `order_calc_profit` | a **non-zero number**. `-10.0` means a 10-point stop on 1 lot costs $10, so sizing works. `None` or `0` means the terminal cannot price this symbol and the bot will fall back to tick maths |
| `server offset` | matches reality. Compare `server now` to the clock in MT5's Market Watch. If it is wrong, set `mt5.server_utc_offset_hours` in the config — the news blackout depends on it |
| `feed usable` | **True** |

`tick size/val : 0.01 / 0.0` is expected on UT100 and is fine — that is the
display-rounding issue, and it is precisely why sizing goes through
`order_calc_profit` instead.

**Pass condition:** all five rows above check out.

---

## Stage 5 — Backtest on your own data

```powershell
python -m ict_bot --config config.yaml --mode backtest --bars 5000
```

5,000 M15 bars is roughly two months. Start there; raise it once you have
confirmed the terminal has the history (`bars processed` in the report tells you
how many it actually got — if it is far below what you asked for, go back to
Stage 2 step 4 and scroll further).

Read the summary, and in particular the **"confirmations not traded"** block:

```
sweeps / confirms   : 339 / 100 (invalidated 238)
trades taken        : 48  (long 28, short 20)
win rate            : 39.6%
profit factor       : 1.11
expectancy          : 6.45 per trade (+0.07 R)
max drawdown        : 857.49 (8.2%)
--------------------------------------------------------------
confirmations not traded:
    47  rr_below_min
     5  max_positions
```

If `rr_below_min` is eating half your setups, that is the default `min_rr: 1.0`
rejecting trades whose opposing-liquidity target sits closer than the stop. Two
honest options — test both:

```yaml
strategy:
  min_rr: 0.5              # accept them, or
  tp_mode: fixed_rr        # ignore the liquidity target, take 2R
  rr_target: 2.0
```

Per-trade detail lands in `logs/backtest_trades.csv`. Open it in VS Code — the
Rainbow CSV or Edit CSV extension makes it readable.

**Pass condition:** a report you believe, built on bars your terminal actually
holds. Not a profitable one — a *believable* one.

---

## Stage 6 — Confirm parity with your indicator (optional but recommended)

```powershell
python -m ict_bot --config config.yaml --mode signals --out logs/parity.csv --bars 2000
```

Open `logs/parity.csv` next to your TradingView chart with the indicator loaded on
the same symbol and timeframe, and spot-check ten rows by timestamp:

| CSV `event` | On the chart |
|---|---|
| `sweep` | orange candle + triangle |
| `confirmed` | green (long) / red (short) candle with "CISD ✓" |
| `invalidated` | gray candle |

Times in the CSV are **UTC**; TradingView shows your local timezone, so mind the
offset when matching them up.

If a bar differs, that is worth investigating before you trade it —
`docs/indicator_parity.md` documents every intentional difference.

---

## Stage 7 — Paper trade

```powershell
python -m ict_bot --config config.yaml --mode paper
```

**Set your expectations before you watch it:** the bot acts only on *closed* M15
bars. After startup it prints its warm-up summary and then goes quiet. Nothing
will happen for up to 15 minutes, and a tradable setup may not appear for hours.
Silence is the bot working correctly.

What you should see immediately:

```
connected: login 12345678 @ MEXAtlantic-Demo (MetaTrader 5), equity 10000.00 USD, DEMO account
symbol UT100: digits=2 point=0.01 tick_size=0.01 tick_value=0.0 ...
broker server time detected as UTC+3.0h
news calendar loaded: 6 relevant events (of 6)
warm-up: replayed 1500 bars (43 sweeps, 12 confirmations, 4 live HTF FVGs) - no orders placed
ready: UT100 M15, 0 open position(s), watermark 2026-07-26 12:30:00
```

That warm-up line is important: it replayed history to rebuild state and
**placed no orders**. Only bars that close from now on can trigger a trade.

Then, when a setup fires:

```
SWEEP long: pool 20143.50 taken at 20138.20, CISD level 20161.00, HTF FVG yes
CISD CONFIRMED long @ 20166.40 (order block 20149.60, stop 20138.20) - entering now, no retest
ENTERED long 2.10 lots @ 20166.90 | stop 20138.20 | target 20219.50 | R:R 1.84 | risk 100.00
```

Cross-check that against MT5's *Trade* tab — same volume, same SL, same TP.

Stop the bot with `Ctrl+C`. It saves state on the way out, so restarting will not
re-enter a setup it already traded.

**Leave it running for at least two weeks of demo before you even think about
real money.** You are measuring whether no-retest entries actually beat the
retest — that is the whole hypothesis, and a handful of trades cannot answer it.

---

## Stage 7b — Telegram (optional, do it during demo)

Set this up while paper trading so you can watch the alerts arrive before real
money is involved. Full walkthrough: [`docs/telegram_setup.md`](docs/telegram_setup.md).

Short version:

1. Message **@BotFather** -> `/newbot` -> copy the token.
2. Message **@userinfobot** -> copy your numeric chat id.
3. Create a group, add your users and the bot, then read the group id from
   `https://api.telegram.org/bot<TOKEN>/getUpdates` (group ids are negative).

```powershell
$env:ICT_NOTIFY__TELEGRAM_BOT_TOKEN="123456789:AAH..."
```

```yaml
notify:
  telegram_broadcast_chat_ids: ["-1001234567890"]   # the group: read-only
  telegram_admin_chat_id: "987654321"               # you: the only controller
```

Restart the bot and send `/status` from your own chat. If it replies, the control
channel works. Ask someone in the group to send `/status` too — they should get
nothing, and the log should show `ignoring /status from non-admin chat`.

**Pass condition:** you get a reply, the group does not.

---

## Stage 8 — Live (only after Stage 7 convinces you)

Two independent locks, both required:

```yaml
runtime:
  mode: live
  allow_live_trading: true
```

```powershell
python -m ict_bot --config config.yaml --mode live --i-understand-live
```

Before you do, turn the risk down — `risk_pct: 0.25` — and leave it down until
live fills match what the demo led you to expect. Live spreads and slippage on a
CFD around the open and around news are worse than any simulation.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ModuleNotFoundError: No module named 'MetaTrader5'` | venv not active, or you are not on Windows. Re-run `.venv\Scripts\Activate.ps1` |
| `mt5.initialize failed` | Terminal not running, or not logged in. Open MT5 first. If installed somewhere unusual, set `mt5.terminal_path` to your `terminal64.exe` |
| `symbol 'UT100' not found` | Wrong name. Check Market Watch for a suffix (`UT100.r`) and copy it exactly |
| `no M15 history for UT100` | Terminal has not downloaded bars. Open the M15 chart, press `Home`, scroll left |
| `refuses to run on a REAL account` | Working as designed. Log MT5 into a demo, or go to Stage 8 knowingly |
| Every entry blocked by `news calendar unavailable` | You ran from the wrong folder, or `calendar/events.csv` is missing. `cd` to the project root |
| `AutoTrading is DISABLED` / retcode `10027` | The Algo Trading button in MT5 is off |
| Orders rejected `10019` | Not enough money for that lot size. Lower `risk_pct`, or use an account with more equity |
| `risk-based size ... under the broker minimum` | Correct behaviour, not a bug — it refuses to round up and exceed your risk budget. The message states the equity that setup needed |
| Bot runs but never trades | Usually normal. Check `logs/signals.csv`: if there are `blocked` rows, the `detail` column names the gate that stopped it |
| Telegram silent | Token wrong, or the bot was never messaged/added to the group. Check the log for `telegram sendMessage failed` |
| `/status` ignored | You are not the admin chat id. The log names the chat id it saw — copy that into `telegram_admin_chat_id` |
| Bot refuses to start: "admin_chat_id is required" | Commands are enabled but no admin is set. Set it, or set `telegram_commands_enabled: false` |
| Everything blocked by "paused by admin" | Someone sent `/pause`, and it survives restarts. Send `/resume` |

---

## The files you will actually look at

| Path | What it is |
|---|---|
| `config.yaml` | every setting; the only file you edit |
| `logs/ict_bot.log` | full runtime log |
| `logs/signals.csv` | every sweep, confirmation and blocked entry, with reasons |
| `logs/backtest_trades.csv` | per-trade backtest results |
| `state/ict_bot_UT100.json` | crash-safe state; delete it only if you want a clean slate |
| `ict_bot/signals/engine.py` | the strategy, if you want to read or change the logic |
| `docs/indicator_parity.md` | how the Python maps onto your Pine indicator |
| `docs/telegram_setup.md` | alerts for the team, commands for the admin |
