# sweep-indicator

An early-hours liquidity-sweep trading model for TradingView, built on the ICT
4-pillar idea: **sweep of liquidity → HTF fair value gap → CISD → order block**.

## Files

| File | Type | Use it for |
|------|------|-----------|
| [`ICT_Model_Strategy.pine`](ICT_Model_Strategy.pine) | **Strategy** | Backtesting + automation. Auto-enters the moment CISD confirms — **no manual retest** — and can fire signals to a webhook. |
| [`ICT_Model_Indicator.pine`](ICT_Model_Indicator.pine) | Indicator | The original visual tool — draws setups and levels, you place the trade. |
| [`WEBHOOK_SETUP.md`](WEBHOOK_SETUP.md) | Guide | Step-by-step: TradingView alert → webhook, with the JSON payload reference. |

## Strategy at a glance

The strategy removes the manual "wait for the retest" step. As soon as CISD
confirms it acts, using one of two entry modes (Settings → Inputs):

- **Limit at entry line** — drops a resting limit at the entry (E) the instant
  CISD prints; fills on a pullback, auto-cancels if unfilled within the CISD
  window.
- **Market at CISD close** — enters immediately, to catch moves that run to
  target without ever retracing to the order block.

Stop-loss (SL) and take-profit (TP) are attached automatically as a bracket, and
chart labels are abbreviated (**E / SL / TP / CISD**) to keep the chart clean.

Automation setup lives in [`WEBHOOK_SETUP.md`](WEBHOOK_SETUP.md).
