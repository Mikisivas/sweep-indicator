# Running this as a MEX Atlantic copy-trading strategy

## The architecture

```
┌──────────────────────────────────────┐
│  YOUR VPS / PC (Windows)             │
│                                      │
│   ict_bot  ──►  MT5 terminal         │
│                 (MASTER account)     │
└─────────────────────┬────────────────┘
                      │
                      ▼
            MEX Atlantic servers
            copy-trading service
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   follower 1    follower 2  ...  follower 50
                      │
                      ▼
              Telegram group (alerts only)
```

**The bot only ever touches the master account.** It has no idea followers exist,
and it needs no code for them — replication is entirely the broker's job. The
Telegram broadcast is informational: your 50 users see what the strategy did, and
their accounts follow automatically.

This means everything the bot enforces is enforced **once, at the master**, and
inherited by everyone:

* the news blackout — no follower enters into an NFP spike
* the daily kill switch — a bad day stops the whole book
* the spread filter, session filter and R:R minimum
* `/pause` — stops new entries for all 50 people at once

---

## Why `market_on_cisd` is the right entry for copy trading

Keep the default. Market orders replicate reliably; a pending limit resting at the
order block does not — the master may fill while followers do not, or followers
fill at prices that make the copy service's accounting messy. Entering at market
the moment CISD confirms gives one clean, immediately-replicated event.

This is a genuine second reason for the no-retest design, beyond the winners you
were already seeing.

---

## The five things to verify on demo

Copy services differ in what they replicate. Set up **one demo master + one demo
follower** and confirm each of these before going live. This is the most important
work you will do.

### 1. Do SL/TP modifications replicate?

**This is the critical one.** Break-even works by *modifying* an open position's
stop. If the copy service replicates entries and exits but not modifications, your
followers never get break-even protection — they keep the original stop while you
sit protected. That is the worst possible asymmetry.

Test: let a demo trade reach +1.5R, watch the master's stop move to entry, then
check the follower's stop. If it did not move, either turn break-even off
(`break_even_enabled: false`) so everyone is treated the same, or ask MEX Atlantic
whether modification replication can be enabled.

### 2. How are follower lots scaled?

Most services scale by equity ratio: `follower lot = master lot × (follower equity
/ master equity)`. If so, your `risk_pct: 1.0` on the master gives each follower
roughly 1% too, which is what you want. Confirm it — some services copy fixed
lots or fixed multipliers instead, which would risk wildly different amounts per
follower.

### 3. What happens to followers below the minimum lot?

If the master trades 0.05 lots and a follower's scaled size is 0.004, the broker
either skips the trade or rounds up to 0.01 — and 0.01 may be several times the
risk that follower intended. Find out which.

The bot can flag this for you:

```yaml
risk:
  min_master_volume: 0.10   # warn when the master lot is too small to scale down
```

It still takes the trade — this is information, not a veto — but logs a warning
and messages the admin so you can see it happening.

### 4. Do closes and partial closes replicate?

Test `/close <ticket>` and `/closeall` from Telegram and confirm the follower
closes too. If you ever need `/closeall` in a hurry, you need to already know it
works.

### 5. What is the replication latency?

Followers fill after the master, typically within a second or two on the same
broker. On a 15-minute strategy that is negligible, but measure it so you can tell
users what to expect, and so you can explain the small P&L differences they will
see.

---

## Operational requirements

**Run on a VPS, not your laptop.** With 50 people following, the master must be up
whenever the market is. If your machine sleeps or loses its connection:

* no new entries fire for anyone;
* worse, **open positions stop being managed** — break-even never triggers,
  because that runs in the bot, not on the broker.

The stops and targets themselves are held server-side by MT5 and still protect
everyone if the bot dies. But the *management* of them stops. A cheap Windows VPS
near your broker's server removes both problems and cuts latency.

**Keep the calendar current.** `calendar/events.csv` is what stops 50 accounts
entering into a CPI print. A stale file is the one failure mode where the
protection quietly stops working — though the fail-safe default (`block`) errs
towards no trading rather than unprotected trading.

**One symbol, one bot.** If you later want UT100 *and* another instrument, run a
second instance with a different `magic` and a different `state_file`. Never point
two bots at the same symbol on the same account.

**Watch the master's equity.** Position size is a percentage of the *master's*
equity, so as the master grows the lots grow, and every follower scales with it.

---

## What your 50 users see

Entries, exits and break-even moves land in the Telegram group:

```
LONG UT100
Entry 20166.90
Stop 20138.20
Target 20219.50 (opposing_liquidity)
R:R 1.84 | 2.1 lots | risk 100.00
```

The lot size and risk shown are the **master's**, not theirs — worth saying once
in the group so nobody is confused when their own terminal shows something
different.

They cannot control the bot. Only your admin chat id can, and nothing they type
reaches the trading logic. See [`telegram_setup.md`](telegram_setup.md).

---

## Before you take on 50 people's money

Two things worth settling early, because they are harder to fix later:

**Position sizing is a promise you are making.** Followers inherit your risk
percentage. Someone with a 500 USD account and someone with 50,000 USD both get
1% per trade if scaling is proportional — but the small account hits the minimum
lot problem first (see item 3 above), and may end up risking far more than 1%.
Know where that threshold sits for your master lot sizes.

**Managing other people's money is regulated in most jurisdictions**, and running
a copy-trading strategy others subscribe to can fall inside that, even when the
broker provides the mechanism and you never touch their funds. Whether it applies
depends on where you and they are, how you are compensated, and how you market
it. Worth a conversation with someone who knows your local rules before you go
live with 50 subscribers — not because the software cares, but because the
consequences of getting it wrong land on you personally.
