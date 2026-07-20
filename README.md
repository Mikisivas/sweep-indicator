# sweep-indicator

A TradingView (Pine Script v6) indicator implementing the four-pillar ICT model:
**liquidity sweep → CISD → order block**, framed by **4-hour fair value gaps**.

- Indicator source: [`ict_sweep_cisd_fvg_ob.pine`](ict_sweep_cisd_fvg_ob.pine)
- Visual explainer (valid vs. invalid setup, colored candles): [`docs/valid-vs-invalid-setups.html`](docs/valid-vs-invalid-setups.html)

## The model

The framework aligns a higher timeframe (4H) with a lower timeframe (15m):

1. **HTF context** — price trades into a 4-hour fair value gap (BISI for longs,
   SIBI for shorts) or a 4H order block/CISD level.
2. **LTF sweep of liquidity** — on the 15m chart, price wicks through a previous
   session or internal low (sell-side liquidity) or high (buy-side liquidity),
   taking out the stops resting there.
3. **CISD — Change in State of Delivery** — the validation step. Take the series
   of consecutive down-close candles that performed the sweep; the CISD level is
   the **open of the first candle** of that series. A candle **closing above** that
   level confirms delivery has flipped from sell-side to buy-side (mirrored for
   shorts). A wick through the level is *not* a CISD — only a close counts.
4. **Order block** — once the CISD close prints, the sweep series becomes the
   order block. Entry is on the retest of that zone, stop below the **protected
   low** created by the sweep, target the **opposing liquidity** (e.g. London /
   Asia session highs).

A sweep **without** a subsequent CISD close is an invalid setup — no order block
exists and no trade is taken. The indicator marks both outcomes on the chart.

## What the indicator draws

| Element | Rendering |
|---|---|
| Liquidity sweep candle | **Orange** candle + triangle marker |
| Valid bullish setup (CISD close above) | **Green** candle + `CISD ✓` label |
| Valid bearish setup (CISD close below) | **Red** candle + `CISD ✓` label |
| Invalid setup (timeout or close through the swept level) | **Gray** candle + `✗ no CISD` label |
| 4H fair value gaps | Teal boxes (BISI) / maroon boxes (SIBI), pruned once rebalanced |
| **Exact entry** | Solid **blue** line at one price (order-block 50% or near edge) + `ENTRY <price>` label |
| **Stop** | Solid **red** line just beyond the protected low/high + `STOP <price>` label |
| **Take profit** | Solid **green** line at the opposing liquidity (or a fixed R:R) + `TAKE PROFIT <price>` label |
| Trade card | Plain-language label: direction, entry, stop, target, and reward:risk |
| Order block (optional) | Shaded box over the sweep series — off by default to keep the chart clean |

### Beginner mode (exact levels)

For handing to someone new, the indicator prints **one precise price per role** — no
zones to interpret — plus a trade card summarising the whole trade:

- **Exact entry price** — choose the order-block *50% (mean threshold)* for a better
  price, or the *edge* for a more reliable fill.
- **Take profit** — the opposing liquidity (previous high for longs / low for shorts),
  or a fixed reward:risk multiple if no opposing pool is available.
- **Stop** — just beyond the protected low/high created by the sweep.

Alerts fire with the actual entry / stop / take-profit numbers filled in.

## Inputs

- **Swing pivot strength** — bars each side needed to define the swept swing (default 5).
- **Max bars to confirm CISD** — sweep expires as invalid if no CISD close within this window (default 20).
- **HTF for FVGs** — default `240` (4-hour), works on any chart timeframe (use 15m per the model).
- **Require HTF FVG confluence** — optional filter: only validate setups whose sweep occurred inside a 4H FVG.
- **Exact entry price** — order-block 50% (mean threshold) or the order-block edge.
- **Take profit target** — opposing liquidity or a fixed reward:risk.
- **Reward:risk** — the multiple used for TP when fixed, or as a fallback (default 2.0).
- Colors, trade-line drawing, the trade card, and the optional order-block zone are all configurable.

Alerts are included for sweeps, bullish CISD confirmations, and bearish CISD confirmations.

## Usage

1. Open TradingView → Pine Editor → paste the contents of `ict_sweep_cisd_fvg_ob.pine` → **Add to chart**.
2. Set the chart to 15m; leave the HTF input at 240 for the 4H/15m alignment from the model.
3. Trade only green/red (valid) confirmations: entry on the order-block retest,
   stop beyond the protected low/high, target opposing liquidity.

> Educational tool — signals describe structure, not guaranteed outcomes. Not financial advice.
