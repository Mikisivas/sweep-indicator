# TradingView alert → webhook: step-by-step

How to run **`ICT_Model_Strategy.pine`** as an automated strategy and fire its
signals to a webhook (your bot, exchange bridge, Discord/Telegram relay, etc.).

The strategy already builds a ready-to-send **JSON payload** for every order, so
you do not have to type the message by hand.

---

## What the strategy sends

Every entry carries a JSON `alert_message` like this:

```json
{"action":"buy","symbol":"BTCUSDT","tf":"15","order":"limit","entry":42150.5,"sl":41980.0,"tp":42610.0,"rr":"2.7"}
```

Every exit (stop-loss or take-profit hit) sends:

```json
{"action":"close","side":"long","symbol":"BTCUSDT"}
```

`action` is `buy` / `sell` for entries and `close` for exits — map those to your
bot's endpoints.

---

## Step 1 — Add the strategy to a chart

1. Open TradingView → **Pine Editor** (bottom panel).
2. Paste the contents of `ICT_Model_Strategy.pine`.
3. Click **Add to chart**. The **Strategy Tester** tab appears with a backtest.

## Step 2 — Set the entry behaviour

Open the strategy's **Settings (⚙) → Inputs → "Entry — retest REMOVED"**:

- **How to enter once CISD confirms**
  - `Limit at entry line` — drops a resting limit at **E** the instant CISD
    prints; fills on a pullback, auto-cancels if unfilled within the CISD window.
  - `Market at CISD close` — enters immediately, to catch moves that run to
    target without ever retracing.
- **Where the entry price (E) sits** — `Order block edge` sits near price (fills
  more often / catches runners), `Order block 50%` waits for a deeper retrace.

Tune it in the Strategy Tester until the results match how you trade, then move on.

## Step 3 — Create the alert

1. Click the **⏰ Alert** button (or press `Alt+A`).
2. **Condition** → select the strategy **`ICT Model — Sweep + CISD Strategy`**.
3. In the dropdown right below, pick one:
   - **Order fills only** *(recommended — full automation)*: fires on entry
     fills **and** on SL/TP exits, so your bot both opens and closes trades.
   - **alert() function calls only**: fires once the moment CISD confirms
     (entry signal only — no exit messages).
4. **Message** box → delete the default text and type exactly:

   ```
   {{strategy.order.alert_message}}
   ```

   That placeholder is replaced with the JSON payload the script built.
5. **Notifications** tab → tick **Webhook URL** and paste your endpoint, e.g.
   `https://your-server.com/tradingview`.
6. **Expiration** → set to *Open-ended* so it keeps running.
7. **Create**.

## Step 4 — Confirm it works

- Force a test by lowering `Swing pivot strength` on a fast chart, or wait for a
  live signal.
- Check your server logs / relay for the JSON.
- On TradingView, the **Alerts log** (right panel) shows every fire with the
  exact body that was sent.

---

## Notes & gotchas

- **Webhook URL must be public HTTPS** on port 80/443. Localhost won't work —
  use a tunnel (ngrok, Cloudflare Tunnel) or a hosted endpoint.
- **Whitelist TradingView's IPs** on your server:
  `52.89.214.238`, `34.212.75.30`, `54.218.53.128`, `52.32.178.7`.
- **Signals confirm on bar close.** A forming bar can change until it closes, so
  the strategy commits on the closed candle — expect the fill on the next bar.
- **One alert per direction is enough** — the same alert handles longs and
  shorts because the payload's `action` field tells them apart.
- **Paper-trade first.** Point the webhook at a test/sandbox account and verify
  fills, sizing, and SL/TP mapping before going live.
- **Position size** is set in the strategy header
  (`default_qty_type`/`default_qty_value`, currently 10% of equity). Your bot
  should apply its own risk sizing regardless.

---

## Payload field reference

| field    | meaning                                         |
|----------|-------------------------------------------------|
| `action` | `buy` / `sell` (entry) · `close` (exit)         |
| `side`   | `long` / `short` (on close messages)            |
| `symbol` | ticker, e.g. `BTCUSDT`                           |
| `tf`     | chart timeframe, e.g. `15`                       |
| `order`  | `limit` or `market` (matches your entry choice) |
| `entry`  | exact entry price (E)                            |
| `sl`     | stop-loss price                                 |
| `tp`     | take-profit price                               |
| `rr`     | reward-to-risk of the setup                     |
