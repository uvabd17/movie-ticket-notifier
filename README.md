# movie-ticket-notifier

Catches booking-open signals for big-movie ticket drops in Hyderabad (PCX / Prasads focus) and fires a loud phone alarm so you're on BookMyShow / District refreshing at the moment seats go live.

No scraping. Reads **Google News RSS** (primary) and optionally Twitter/X (if you have paid Basic-tier access).

## How it works

News sites (Sacnilk, Pinkvilla, Moneycontrol, Indian Express, Siasat, etc.) publish articles within minutes of a theater or distributor announcement. This system polls **Google News RSS** every 5 minutes for per-movie queries you configure, classifies each new article as:

- **SCHEDULED** — article mentions a future booking date/time → full reminder cascade (T−24h, T−1h, T−5min, T+0, T+2min).
- **IMMEDIATE** — article reports bookings are already live → loud alarm now (deduped: once per movie per day).
- **IGNORE** — reviews, retrospectives, "will open soon", etc.

The **loud alarm** uses ntfy.sh with max priority + "Override DND" so it rings through silent mode. Telegram is a routine backup.

You still manually book. The system's job is to get you on the page in time.

## Why Google News RSS and not Twitter directly

Twitter/X Free tier allows ~100 user-timeline reads per month across all handles — unusable for a 5-min cron across 10+ sources. Paid Basic tier is ~$100/month. Google News indexes the same signal (plus press releases + news articles) without an API key, without rate limits that matter, and without ToS issues. Twitter is retained as an optional secondary — if you set `TWITTER_BEARER_TOKEN`, the system will also poll `sources.yaml` handles.

## Setup

### Step 0 — belt and braces (do these too, zero code)

This system's weakest spot is a theater that tweets *at* or *after* the booking flip rather than the day before. Our 5-minute cron can be up to 5 min behind the tweet itself, which — for PCX FDFS — is often too slow. Mitigate with:

1. **Create a private X List** with `@PrasadsLargeIM`, `@amb_cinemas`, `@AsianCinemasHyd`, `@MarvelIndia`, `@WarnerBrosIn`, and whichever distributor is relevant for your movie. **In the X app, open the List → bell icon → "All posts"**. You'll get a native push notification within seconds of any tweet in the list. This is strictly faster than our cron — treat our system as backup, this as primary.
2. **Tap "Notify Me" on BMS and District** for the movie. They'll email when bookings open. Slow but official.
3. **Enable BMS app push notifications**. BMS itself sometimes pushes a day-before reminder.
4. Optional, loudest-but-fiddly: **IFTTT "new tweet by user" → Phone call** (via a Twilio applet). Near real-time, three free applets, so cover your three top handles.

### Step 1 — get the system running

1. **Fork this repo on GitHub.** Keep it public — public repos get unlimited Actions minutes.

2. **Create a Telegram bot** by messaging [@BotFather](https://t.me/BotFather) on Telegram:
   - `/newbot`, follow prompts, copy the token.
   - Message your new bot anything (so it has a chat with you).
   - Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy your `chat.id`.

3. **Pick an ntfy topic name.** Anything hard to guess, e.g. `hyd-pcx-7f3a9k`. Install the [ntfy Android app](https://play.google.com/store/apps/details?id=io.heckel.ntfy), subscribe to that exact topic, and in the app's per-topic settings turn on **"Override Do Not Disturb"** and pick an alarm-style sound. Confirm by running `curl -d test ntfy.sh/<topic>` from any machine — your phone should ring.

4. *(Optional, highly recommended for PCX)* **Get an xpoz.ai API key** for Instagram polling of `@prasadsmultiplx`. Prasads posts about every major release on Instagram (Ramayana, DNK, Suriya, Thaman — all in the last week) and will post when PCX opens Doomsday bookings. Google News doesn't index Instagram, so xpoz is the only way to catch this signal in the cron.
   - Sign in at **https://xpoz.ai** (free tier includes ~500 credits + some bonus).
   - Dashboard → Access Keys → create a key.
   - Save that string — it's your **`XPOZ_API_KEY`**.
   - At ~1 credit per poll and a 30-min poll interval, free credits last ~5–10 days. Plenty for a specific movie's drop window. **In parallel, also enable Instagram post-notifications in the Instagram app** (`@prasadsmultiplx` → Following → Notifications → Posts ON) — that's free and instant.

5. **In your repo → Settings → Secrets and variables → Actions**, add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `NTFY_TOPIC` (just the topic name, no URL)
   - `XPOZ_API_KEY` *(optional — only if using Instagram source)*
   - `TWITTER_BEARER_TOKEN` *(optional — only if you have Twitter Basic tier)*

6. **Edit `watchlist.yaml`** to list movies you're tracking. Avengers: Doomsday is seeded as an example, with `@prasadsmultiplx` already set as the Instagram handle to poll.

7. **Enable Actions** on your repo (first time GitHub asks). The `watch` workflow runs every 5 minutes (news) and polls Instagram every 30 minutes within that same tick.

### Test it right now

Locally, with secrets in your env:

```bash
pip install -r requirements.txt
python -m src.notify alarm "test alarm"
```

Your phone should ring through DND and Telegram should buzz. If not, fix that first — nothing else matters if the alarm doesn't reach you.

## Editing the watchlist

See `watchlist.yaml`. Fields:

- `name` — canonical name shown in alerts.
- `aliases` — alternate spellings the tweet might use.
- `hashtags` — without the `#`.
- `priority` — `high` fires the full cascade (T−24h, T−1h, T−5min, T+0, T+2min). `normal` fires just T−1h, T−5min, T+0.

## Editing the sources

See `sources.yaml`. The defaults cover Prasads, AMB, Asian (Hyd); PVR, INOX (national); Marvel, Warner, Universal, Disney; BMS, District; Sacnilk, BoxOfficeHQ. Add or remove to taste.

## Honest limitations

- **If nobody tweets at all** for a specific drop, we miss it. Only mitigation is Step 0's Notify Me email.
- **Up to 5-min lag between a tweet and our alert.** GitHub Actions' cron minimum *is* 5 min — you can't go lower on this runtime. For pre-announcement tweets (hours/days ahead) this is irrelevant. For a theater that tweets at or just after the flip, we may be 5–7 min behind the moment of opening, which is often too slow for PCX FDFS. The X List bell-notification (Step 0) covers this case.
- **If you need sub-minute latency**, see "Phase 3 (upgrade)" below — one-page port to Cloudflare Workers Cron Triggers, which do 1-minute minimum on free tier.
- **Wrong date parsed** → alarm fires at the wrong time. We always include the source-tweet URL in every reminder so you can sanity-check.

## Phase 3 (upgrade) — 1-minute cron on Cloudflare Workers

GitHub Actions cron minimum is 5 min; Cloudflare Workers Cron Triggers support 1 min on free tier. Not implemented yet. To port:

1. Rewrite `src/runner.py` logic in TypeScript (or keep Python and run it from a CF Worker that shells out — only possible via Durable Objects + WASM; easier to just port).
2. Replace `state.json` / `schedule.json` with Workers KV.
3. Replace `.github/workflows/watch.yml` with `wrangler.toml` having `[triggers] crons = ["* * * * *"]`.
4. Secrets go in `wrangler secret put`.

Keep this repo as-is for the 5-min tier; switch when you decide the latency delta is worth the port effort.

## Project layout

```
.
├── watchlist.yaml         # your movies
├── sources.yaml           # who to watch on X
├── state.json             # written by the workflow: last seen tweet id per handle
├── schedule.json          # written by the workflow: upcoming reminders
├── src/
│   ├── twitter.py         # X API client (since_id based)
│   ├── classify.py        # SCHEDULED / IMMEDIATE / IGNORE
│   ├── schedule.py        # reminder scheduling with dedupe
│   ├── notify.py          # Telegram + ntfy
│   └── runner.py          # one-tick entry point
├── .github/workflows/
│   └── watch.yml          # every 5 minutes
└── requirements.txt
```

## License

Personal use. If you fork, keep it that way.
