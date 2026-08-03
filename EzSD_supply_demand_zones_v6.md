# EzSD — Supply & Demand Zones [MTF]

Pine Script **v6** indicator. Multi-timeframe supply/demand zones drawn as thin,
dotted-edge bands that span the chart, each carrying a right-edge price tag in the
`45M | 94.86` format.

File: [`EzSD_supply_demand_zones_v6.pine`](EzSD_supply_demand_zones_v6.pine)

---

## What it draws

| Element | Look |
|---|---|
| Supply zone | Pink/red translucent band, dotted border, extends both directions |
| Demand zone | Teal translucent band, dotted border, extends both directions |
| Price tag | Rounded label pinned to the right edge — `TF | price`, pointer aimed at the level. Supply tags sit above their band, demand tags below |
| Flipped zone | Tag prefixed with `⇄` |

Up to three timeframe groups run at once (default: chart TF, 1H, 4H-off). Every zone
tag names the timeframe it came from, so a 4H zone on a 45m chart reads `4H | 94.05`.

## How supply and demand are identified

A pivot on its own is just a level. This script promotes a pivot to a zone only when
the leg that *leaves* it looks like an impulse — the same volume-first idea as the
ChartPrime script this grew out of, but measured on the departure leg instead of the
single pivot bar:

1. **Pivot** — `ta.pivothigh(high, len, len)` / `ta.pivotlow(low, len, len)`. The pivot
   bar sits `len` bars back, so bars `[0 … len-1]` are the departure leg.
2. **Delta volume** — each bar of that leg is signed: `close > open` → `+volume`,
   `close < open` → `−volume`.
3. **Imbalance** — `|Σ delta| ÷ Σ volume` must clear *Minimum Delta Imbalance*
   (default 0.25), and the sign must point away from the pivot: negative below a pivot
   high (supply), positive above a pivot low (demand).
4. **Participation** — the leg's gross volume must exceed
   `SMA(volume, baseline) × len × multiplier`, measured as of the pivot bar.

Symbols with no volume data skip steps 2–4 automatically rather than drawing nothing.

**Zone height** — three modes: `ATR` (uniform bands, the screenshot look),
`Candle Wick` (high → body top for supply, low → body bottom for demand, floored at
10% ATR so a wickless candle can't produce a hairline), or `Percent` of price.

## Zone lifecycle

- **Test** — the first bar whose range touches the band fires a test event (optional `◆` marker).
- **Break** — a close beyond the far edge (`close > top` for supply, `close < bot` for demand). Then, per *When Price Breaks a Zone*:
  - **Flip** (default) — polarity inverts, broken supply repaints as demand and keeps working; a second break retires it. This is the `res_is_sup` / `sup_is_res` behaviour of the original, carried onto the zone object itself.
  - **Delete** — removed.
  - **Fade** — greyed, dashed, tag dropped, no longer tracked.
- **Dedupe** — a fresh level replaces any same-side zone it overlaps (on by default).
- **Cap** — each group keeps its newest N zones; older boxes and labels are deleted, not just hidden.

## Inputs

**Detection Engine** — Zone Height (ATR / Candle Wick / Percent) · Height Multiplier ·
ATR Length · Volume Confirmation · Volume Baseline · Impulse Volume × Average ·
Minimum Delta Imbalance · When Price Breaks a Zone · Replace Overlapping Zones

**Timeframe 1 / 2 / 3** — each group carries its own: TF · Show · Pivot ·
Supply colour · Demand colour · Fill transparency · Line width · Line style
(Solid/Dotted/Dashed) · Extend Left · Tags · Tag Offset · Tag Size · Max zones

A group asking for a timeframe below the chart's is silently clamped to the chart
timeframe, so a 4H group on a Daily chart won't produce garbage.

**Extras** — Monospaced Tags · Break Labels · Retest Markers · Dynamic `alert()` Messages

## Alerts

Two routes:

- **`alert()` messages** (Extras → on by default). Create an alert on *Any alert() function call*; the message names the timeframe, side, and price, e.g. `🟥 1H supply @ 94.86`.
- **`alertcondition` streams** for simple setups: `New Zone`, `Zone Test`, `Zone Break`.

## Repainting

Higher-timeframe requests use `lookahead = barmerge.lookahead_off`, so an HTF zone
only appears once that HTF bar has closed — history shows what was actually knowable
at the time. The live HTF bar can still shift a pivot until it closes; that is inherent
to any MTF pivot indicator, not a defect of this one.

## Install

1. TradingView → **Pine Editor** → paste `EzSD_supply_demand_zones_v6.pine`
2. **Save**, then **Add to chart**

## v6 features used

`//@version=6` with `dynamic_requests`, user-defined types (`Cfg`, `Zone`) and `method`s,
`array<Zone>` collections, tuple-returning `request.security()` evaluated in the target
timeframe's context, and `text_font_family` on labels.

## Credit

Detection concept — volume-filtered pivot levels — comes from ChartPrime's
*Support and Resistance (High Volume Boxes)*, MPL-2.0. This is a v6 rewrite:
multi-timeframe engine, departure-leg delta confirmation, object-based zone
lifecycle, and the tag/band styling from the reference chart. Same licence.
