# Telegram setup — alerts for 50 people, control for one

The bot has two separate Telegram channels, and they are deliberately asymmetric:

| | Who | What they can do |
|---|---|---|
| **Broadcast** | your 50 users | receive alerts. **Read-only** — nothing they type reaches the trading logic |
| **Admin** | exactly one chat id | send commands |

There is no command that promotes someone to admin, and a blank
`telegram_admin_chat_id` means *nobody*, never *anybody* — the bot refuses to
start with commands enabled and no admin configured.

---

## 1. Create the bot

In Telegram, message **@BotFather**:

```
/newbot
```

Give it a name and a username. BotFather replies with a token like
`123456789:AAH...`. That token is the bot's password — anyone holding it controls
it. Keep it out of the config file:

```powershell
$env:ICT_NOTIFY__TELEGRAM_BOT_TOKEN="123456789:AAH..."
```

## 2. Find your own chat id (you are the admin)

Message **@userinfobot**. It replies with your numeric id, e.g. `987654321`.

```yaml
notify:
  telegram_admin_chat_id: "987654321"
  telegram_commands_enabled: true
```

## 3. Reach the other 49 people

Two ways. **The group is much easier.**

### Option A — one Telegram group (recommended)

1. Create a group, add your 50 users, add your bot as a member.
2. In group settings, disable "Restrict saving content" if you like; nothing else
   is needed. The bot does not need admin rights in the group, only to post.
3. Get the group id: send any message in the group, then open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read
   `"chat":{"id":-1001234567890`. Group ids are **negative** — include the minus.

```yaml
notify:
  telegram_broadcast_chat_ids: ["-1001234567890"]
```

One message per alert, one place to look, and adding user 51 is just adding them
to the group.

### Option B — individual subscriptions

Leave `telegram_allow_self_subscribe: true` and share the bot's username. Each
person sends `/start` and is added to the alert list, which is saved in the state
file and survives restarts.

Gate it with a password so strangers cannot subscribe:

```yaml
notify:
  telegram_allow_self_subscribe: true
  telegram_join_password: "some-shared-phrase"
```

They then send `/start some-shared-phrase`.

You can review the list with `/subscribers` and remove someone with
`/kick <chat_id>`. Note this sends 50 separate messages per alert — fine at this
volume, but the group is tidier.

---

## 4. Commands (admin only)

```
/status          equity, open positions, armed setups, filters, kill switch
/positions       open positions with live P&L and progress in R
/pause           stop taking NEW entries (open trades keep SL/TP and break-even)
/resume          allow new entries again
/close <ticket>  close one position
/closeall        close every position this bot opened
/be <ticket>     move a stop to break-even now, ahead of the 1.5R trigger
/risk <pct>      change risk per trade, e.g. /risk 0.5   (capped at 5%)
/subscribers     who receives alerts
/kick <chat_id>  remove a self-subscribed user
/stop confirm    shut the bot down (positions keep their SL/TP)
/help            the list above
```

Notes on the safety-relevant ones:

* **`/pause` blocks new entries only.** Open trades keep their stop, target and
  break-even management — abandoning them would be worse than leaving them.
  The paused state is persisted, so a restart stays paused until `/resume`.
* **`/risk`** applies to the next entry and is *not* written to `config.yaml`, so
  a restart returns to the configured value. It refuses anything above 5%.
* **`/be`** refuses if the trade is not in profit — moving a stop to entry while
  price sits below it would put the stop in front of the market.
* **`/stop`** requires the literal word `confirm`, so a fat-fingered `/stop` does
  nothing.

Anything sent by any other chat is logged and ignored:

```
ignoring /closeall from non-admin chat 222 (someuser)
```

---

## 5. What alerts look like

Entries, exits and break-even moves go to everyone:

```
LONG UT100
Entry 20166.90
Stop 20138.20
Target 20219.50 (opposing_liquidity)
R:R 1.84 | 2.1 lots | risk 100.00
```

```
Stop moved to break-even
LONG UT100 ticket 50231
at +1.52R | stop 20138.20 -> 20166.90
This trade can no longer lose.
```

Command replies go only to the admin, so the group is not filled with
housekeeping.

Turn individual categories off in config: `notify_entries`, `notify_exits`,
`notify_blocks` (the last is off by default — it is chatty).

---

## Security notes

* The bot token is the only credential. Anyone with it can read your alerts and
  impersonate the bot. Keep it in the environment, never in git.
* Admin authorisation is by **chat id**, checked before a message is parsed.
  Display names and message text are never trusted — a user calling themselves
  "admin" changes nothing.
* Telegram is a convenience channel, not a safety mechanism. If it goes down the
  bot keeps trading normally with its stops and targets in place; you just stop
  receiving alerts until it recovers. Command polling failures are logged and
  retried, and can never stall or crash the trading loop.
* `/closeall` is instant and irreversible. There is no confirmation on it,
  because the situation where you need it is usually one where you need it now.
