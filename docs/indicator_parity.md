# Pine indicator → Python bot: line-by-line mapping

The bot is a port of **"ICT Model — Sweep + CISD + HTF FVG + Order Block"** (Pine v6),
with exactly one intentional behavioural change (the retest, see §7).

| Indicator (Pine v6) | Bot (Python) | Notes |
|---|---|---|
| `ta.pivotlow(low, swingLen, swingLen)` | `signals/pivots.py::confirmed_pivot_low` | Confirmed `swingLen` bars late, value = centre bar's low. |
| `ta.pivothigh(high, swingLen, swingLen)` | `confirmed_pivot_high` | Same, mirrored. |
| `lastPivLow` / `lastPivHigh` | `IctSignalEngine.last_pivot_low` / `.last_pivot_high` | The resting, not-yet-consumed pool. |
| `chainBull()` | `IctSignalEngine._chain_bull` | Skip non-down closes, then walk the contiguous down-close run; level = open of the earliest candle; returns `(level, offset)`. |
| `chainBear()` | `_chain_bear` | Mirrored on up-closes. |
| `bullState == 0 and low < lastPivLow` | `_step_bull`, `self.bull is None` branch | Arms only if `bullLvl > low`; the pool is consumed either way (`last_pivot_low = None`). |
| `bearState == 0 and high > lastPivHigh` | `_step_bear` | Mirrored. |
| `close > bullCisd` → `bullValidNow` | `_step_bull` → `EventKind.CONFIRMED` | The CISD close. |
| `close < bullSweptLow or bars > maxWait` | `EventKind.INVALIDATED` | Split into two branches for clearer logging; same outcome. |
| `bullSweptLow := math.min(bullSweptLow, low)` | `setup.swept_extreme = min(...)` | The sweep may extend deeper while waiting; the stop follows it. |
| `math.avg(bullCisd, bullSweptLow)` | `entry_mode: ob_mean_threshold` | Order-block 50% mean threshold (default). |
| `entry = bullCisd` | `entry_mode: ob_edge` | Order-block edge. |
| `stop = bullSweptLow` | `TradePlan.stop` | The protected low/high. |
| `hasLiq ? lastPivHigh : entry + rr*risk` | `TradePlan.finalize()` | Opposing liquidity, else fixed R:R. |
| `request.security(..., [high[3], low[3], high[1], low[1]], lookahead_on)` | `signals/fvg.py::FvgStore.ingest_htf` | Same 3-bar gap on **completed** HTF bars; the bot reads 4H bars directly and drops the forming one, which is equivalent and needs no lookahead trickery. |
| `prune()` (box closed through) | `FvgStore.prune(close)` | A gap is retired once price closes through its far side. |
| `insideGaps(bisiBoxes, low)` | `FvgStore.price_in_bullish(low)` | Feeds `require_sweep_in_htf_fvg`. |
| `barcolor(...)`, boxes, labels, table | `journal.py`, logs, notifications | Drawing has no equivalent; the same information is logged and pushed. |
| `alert(...)` / `alertcondition(...)` | `notify.py` + `SignalJournal` | Telegram/Discord instead of TradingView alerts. |

## Deliberate differences

**1. No retest (the point of the exercise).**
The indicator draws an entry line and leaves you waiting for price to return to it.
The bot enters **at market on the CISD confirmation bar's close**. The drawn level
survives as `TradePlan.reference_entry` for logging, and as the price used by the
optional `entry_trigger: limit_at_ob` mode.

**2. Pivot strictness.** `ta.pivotlow` requires the centre bar to be strictly lower
than every bar in both wings; `confirmed_pivot_low` does the same. A flat double
bottom is not a pivot, in either implementation.

**3. Take-profit timing.** The indicator computes the TP inside `if bullValidNow`,
which runs *after* both state machines. So on a bar where a long confirms **and**
the high sweeps the buy-side pool, `lastPivHigh` has already been set to `na` and
the target falls back to fixed R:R. The bot reproduces this exactly by building
`TradePlan` objects only after both `_step_bull` and `_step_bear` have run
(`engine.py`, step 4). Building them any earlier silently breaks parity — there is
a regression test for it: `TestParityDetail::test_target_pool_consumed_by_an_opposite_sweep_on_the_same_bar`.

**4. `chainMax` with short history.** Pine reads `na` past the start of the chart;
the bot bounds the scan at `min(chain_max, len(bars))`.

## Verifying parity yourself

```bash
python -m ict_bot --config config.yaml --mode signals --out logs/parity.csv
```

Load the indicator on the same symbol and timeframe and compare bar times:

| CSV `event` | Indicator |
|---|---|
| `sweep` | orange candle + sweep triangle |
| `confirmed` | green (long) / red (short) candle, "CISD ✓" label |
| `invalidated` | gray candle |

`swept_pool`, `swept_extreme`, `cisd_level` and `reference_entry` should match the
indicator's stop/entry lines to the tick.
