# kitefinder — kitesurf gear finder for Israel

A free agent that runs on your Android phone (Termux). It recommends the kites, board, bar and
harness that fit **you** — your weight, hip/waist, where you ride (or a wind range) and the gear
you already own — then searches Israeli shops, Yad2 and Facebook (groups + Marketplace) for
them, new and second-hand, and sends each match to your Telegram with photos, price, location,
description and a condition check from the photos.

- **Quiver options**: *comfortable* (more overlap), *minimum* (fewest kites), *one kite*
  (cheapest) — each with an estimated price; or the best set **under a budget**.
- **Cheapest real set**: assembles a set from actual listings across sources, mixed brands or
  one brand, respecting your minimum model year.
- **Single items or whole sets**: `search kite 12m`, `watch harness M`.
- **Nothing silently missed**: every fetched post is accounted for after each run (`/report`).
- **Favorites / dismiss** from the alert buttons; dismissed items never come back. All history
  lives in a local SQLite database on the phone.
- **Free**: Gemini free tier for reading posts and photos, Telegram, Cloudflare quick tunnel.

## 1. Install (Termux)

1. Install **Termux** from F-Droid (the Play Store version is outdated). Optionally also
   **Termux:API** (notifications) and **Termux:Boot** (start after a reboot).
2. In Termux:
   ```sh
   pkg install -y git
   git clone https://github.com/EyalPinczowski/Kitesurfing-equipment-finder
   cd Kitesurfing-equipment-finder
   bash install_termux.sh
   ```
   It installs Python, Pillow and cloudflared from Termux's packages (nothing to compile),
   the Python packages, a background service, and share-to-Termux.
3. In Android settings, set Termux's battery use to **Unrestricted**, or Android will stop it.

## 2. Keys and cookies (never share these; they stay on the phone)

Edit `.env` (`nano .env`):

| Setting | Where to get it |
|---|---|
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> → *Create API key* (free). Without it, posts are read by offline rules (less accurate) and photos aren't checked. |
| `TELEGRAM_BOT_TOKEN` | In Telegram, talk to **@BotFather** → `/newbot` → copy the token. |
| `TELEGRAM_CHAT_ID` | Your numeric id — ask **@userinfobot**. Recommended. If empty, the first chat that sends `/start` to your bot owns it. |
| `FB_COOKIES_PATH` | Default `secrets/fb_cookies.json`, see below. |

**Facebook cookies** (for groups and Marketplace): on a computer, log in to Facebook, install
the *Cookie-Editor* browser extension, open facebook.com, *Export → JSON*, and save the file on
the phone as `secrets/fb_cookies.json` (a Netscape `cookies.txt` works too). When they expire
the bot tells you — export them again. Tip: a secondary Facebook account lowers the risk to
your main one; reading Facebook this way is against its terms.

Check: `kitefinder llm status`.

## 3. Start

```sh
sv-enable kitefinder                               # runs now and keeps running
tail -f $PREFIX/var/log/sv/kitefinder/current      # what it's doing
```
(`kitefinder daemon` runs it in the foreground instead; `kitefinder daemon --once` does one
round and exits.)

Then open your bot in Telegram and send **/setup** — it asks, one at a time: weight, hip/waist,
level, style, where you ride (region buttons, spot names in Hebrew or English, or a wind range
like `12-25`), season, the gear you own, budget, new/used, oldest model year, pick-up distance,
and **the websites to search**. It ends with your quiver options.

The six shops from `config/sources.yaml` are searched from the start; add more with
`/sites add <link>`. Facebook groups and Marketplace searches are in `config/sources.yaml` too.

## 4. Daily use

| In Telegram | What it does |
|---|---|
| `/recommend` `[minimum\|comfortable\|one_kite]` | quiver options with sizes and prices |
| `/under 9000` | the best set within ₪9,000 |
| `/assemble` `[mixed\|same\|kites_bar]` | cheapest set from real listings |
| `/search kite 12m` · `/watch harness M` · `/unwatch …` | one item now · alerts for it |
| `/favorites` · `/history` · `/profile` · `/gear` · `/sites` | your data |
| `/run` · `/report` | search now · what the last run found, and whether anything was missed |
| ☰ menu → **Open app** | the Mini App: listings, options, profile form |

Alerts arrive on their own: websites every 3 h, Yad2 every hour, Facebook every 2 h (change in
`config/sources.yaml` → `schedule`). Each alert: title, price vs. the market, location,
description, condition from photos, source, why it fits, and ✅ Favorite / ❌ Dismiss / 🔗 Open.
The same item posted in two places comes as one alert.

**Share to Termux**: in any app, share a post or product link to Termux — it's read and
alerted like the rest.

Everything also works from the terminal: `kitefinder --help` (e.g. `kitefinder recommend
--under 9000`, `kitefinder search 'twin tip 138'`, `kitefinder fav 12`, `kitefinder backup
/sdcard/Download`).

## 5. Backups

A daily copy of the database is kept in `data/backups/daily/` (the last 7). For a copy outside
Termux: `termux-setup-storage` once, then `kitefinder backup /sdcard/Download`. Restore with
`kitefinder restore <file>` (a safety copy of the current database is made first).

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| "Facebook cookies expired" | export the cookies again (section 2) |
| "page format may have changed" / "blocked by bot protection" | the site changed or blocks automated reads; run `kitefinder run --source sites --save-pages pages/` and send me the saved pages |
| "The Mini App is off: cloudflared …" | `pkg install cloudflared`, then `sv restart kitefinder` |
| No alerts at night / with the screen off | battery optimisation: set Termux to *Unrestricted*; the service holds a wake lock |
| Gemini "quota" | the free daily limit; posts are read by the offline rules and re-read by Gemini later |
| The bot answers "This bot is private." to you | set `TELEGRAM_CHAT_ID` in `.env`, then `sv restart kitefinder` |

## For developers

```sh
pip install -e '.[dev]'   # or: pip install -e . pytest pytest-cov ruff
./scripts/check.sh        # lint, format, offline tests, coverage ≥ 85%
pytest -m live            # real Gemini + real sites (needs keys and network)
```
Design notes and open questions: `docs/STEP_REPORTS.md`, `docs/DECISIONS_FOR_APPROVAL.md`.
