# StockTracker Web — Deploy Guide

Everything you need to get this running on a URL you can open from your phone
and desktop, with email alerts firing in the background. No terminal, no GitHub
knowledge required.

Total time: ~30 minutes. Total cost: **$0.25/month** (just Render's persistent disk).

---

## Overview

You'll do three things, in this order:

1. **Sign up for Resend** and get an API key for sending alert emails (~5 min)
2. **Sign up for Render** and deploy this project (~15 min)
3. **Install the app** on your phone's home screen (~2 min)

When you're done, you'll have:

- A web app at `https://stocktracker-web-XXXX.onrender.com` that loads on
  your phone and desktop
- All 61 of your existing tickers migrated over automatically
- A background worker that checks your alerts every 5 minutes and emails
  you when one fires

### Why email instead of SMS?

The original plan was Twilio SMS, but Canadian mobile carriers have tightened
their delivery rules and Twilio's trial-account verification doesn't reliably
work for Canadian numbers anymore. Email is simpler and more reliable:

- Free tier is genuinely free (no credit card required at signup)
- Works on any phone, any carrier, any country
- Push notifications for email land on your phone within seconds
- Searchable history of every alert that ever fired

If you ever want to move to SMS later, we can swap the email sender back out
for Twilio after you upgrade your Twilio account to a paid plan.

---

## Part 1 — Resend setup (~5 minutes)

Resend is an email-sending service. Free tier: 3,000 emails/month, more than
enough for personal alerts. No credit card required.

### 1.1 Create the account

1. Go to **https://resend.com/signup**
2. Sign up with **email + password** (or GitHub/Google if you prefer)
3. Verify your email address (they'll send a link)

That's it. No phone verification, no payment info needed for the free tier.

### 1.2 Create an API key

1. In the left sidebar, click **API Keys**
2. Click **Create API Key**
3. Name: `stocktracker` (or whatever)
4. Permission: **Full access** (or "Sending access" — either works)
5. Click **Add**
6. Copy the API key it shows you — it starts with `re_` and you won't be
   able to see it again. Paste it into a text file for now.

### 1.3 Decide on your "from" address

Resend will let you send emails from their shared domain `resend.dev` without
any extra setup — the from address is `onboarding@resend.dev`. For personal
use this is **completely fine**. Your alert emails will come from
`StockTracker <onboarding@resend.dev>` and land in your inbox normally.

If you'd rather use your own domain (e.g., alerts coming from `alerts@riecampbell.com`),
you'd need to verify the domain in Resend's dashboard with DNS records. For
now, **just use `onboarding@resend.dev`** — you can always upgrade later.

You now have three values:

| What              | Example                                  |
|-------------------|------------------------------------------|
| Resend API key    | `re_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`    |
| From email        | `onboarding@resend.dev` (recommended)    |
| Your email        | `yourname@example.com` (where alerts go) |

Keep these handy. You'll paste them into Render next.

---

## Part 2 — Render deployment (~15 minutes)

Render is the hosting service. It will run both the web app and the
scheduled alert worker.

### 2.1 Create a Render account

1. Go to **https://render.com/**
2. Click **Sign Up**
3. Sign up with **email** (you can use GitHub/Google if you prefer, but
   email works fine — no GitHub needed)
4. Verify your email

### 2.2 Deploy from the uploaded zip

Render's cleanest path for non-GitHub users is the **"Deploy from Git repo"
with Render's Git server**. We'll use a simpler approach instead: I'll give
you the project files, you upload them to a free GitHub account (one click
to create), and Render pulls from there. It's the simplest workflow that
doesn't require a terminal.

**But you said no GitHub.** Right. Here's the simplest zero-GitHub option:

#### Option A — Using Render's Blueprint with a public ZIP (simplest)

1. Take the `stock-tracker-web.zip` file and upload it somewhere public:
   - **Dropbox**: upload the file → right-click → "Copy link" → make sure
     the link ends in `?dl=1` (change `?dl=0` to `?dl=1`)
   - **Google Drive**: upload, right-click → Share → "Anyone with the link"
   - **iCloud**: upload to a folder, "Share folder" → "Anyone with the link"

2. In the Render dashboard, click **New → Web Service**
3. Choose **Public Git Repository** and paste the URL
   — *however, Render prefers a Git URL here, not a zip URL*

#### Option B — One-time GitHub (most reliable, ~5 extra minutes)

Honestly, the cleanest no-terminal path is a free GitHub account. You
don't need to learn git — just drag-and-drop the files into the web UI.

1. Go to **https://github.com/signup** and make an account (free, ~2 min)
2. Click the **+** in the top right → **New repository**
3. Name it `stocktracker-web` (or anything), set it to **Private**, click
   **Create repository**
4. On the empty repo page, click **"uploading an existing file"**
5. Unzip the `stock-tracker-web.zip` on your computer
6. Drag the *contents* of the unzipped folder (not the folder itself) into
   the GitHub upload page
   - Make sure you see files like `render.yaml`, `requirements.txt`,
     folders `backend/`, `frontend/`, `worker/`, `data/`
7. Scroll down, add a commit message like "initial", click **Commit changes**

You now have a GitHub repo. Moving on.

### 2.3 Deploy the blueprint on Render

1. In Render, click **New → Blueprint**
2. Click **Connect a repository** → authorize GitHub → select your
   `stocktracker-web` repo
3. Render detects `render.yaml` and previews two services:
   - `stocktracker-web` — the web service
   - `stocktracker-alerts` — the cron worker
4. Click **Apply**

Render will start building both services. The web service takes ~3 minutes
on the first build. The cron won't run until scheduled.

### 2.4 Add the Resend secrets

While the build runs, set the email environment variables so alerts
actually send:

1. Click **stocktracker-alerts** (the cron job, not the web service)
2. Left sidebar → **Environment**
3. Click **Add Environment Variable** for each of the three below:

   | Key                | Value                                        |
   |--------------------|----------------------------------------------|
   | `RESEND_API_KEY`   | your API key from Part 1.2 (`re_...`)        |
   | `ALERT_FROM_EMAIL` | `onboarding@resend.dev` (from Part 1.3)      |
   | `ALERT_TO_EMAIL`   | your personal email address                  |

4. Click **Save Changes**

### 2.4b Add the Finnhub fallback API key (important — read this)

Yahoo Finance aggressively rate-limits requests from cloud-hosted IP addresses
like Render's. Without a fallback, validating new tickers and refreshing prices
will fail. The app uses **Finnhub** as a fallback whenever Yahoo blocks a
request. Free tier: 60 calls/min, no credit card, email signup only.

1. Go to **https://finnhub.io/register**
2. Sign up with email + password (no phone needed). Verify your email.
3. Once logged in, you'll land on a dashboard with a section showing your
   **API key** (top of the page, next to "API key:")
4. Copy it (looks like a long string of letters and numbers, no prefix)

Now add it to **both** the web service AND the cron worker on Render:

5. In Render, click **stocktracker-web** → **Environment** → add:

   | Key                | Value                          |
   |--------------------|--------------------------------|
   | `FINNHUB_API_KEY`  | your Finnhub key from step 4   |

6. Save. Then click **stocktracker-alerts** → **Environment** → add the
   same `FINNHUB_API_KEY` there too. Save.

Both services need it because both fetch market data (web service: when
you validate or add a new ticker; cron: when checking alerts).

**Note on RSI / 200-day SMA:** Finnhub's free tier doesn't include long
historical price series, so when a ticker falls back to Finnhub, those
two columns will show `—` for that ticker. Price, day change, market cap,
and 52W range still work. If you want RSI back for those tickers, just
trigger a refresh later when Yahoo's rate limit has cleared (usually
hourly windows).

### 2.5 Open the app

When the web service build finishes (green "Live" status), click its URL
at the top. You'll land on something like
`https://stocktracker-web-XXXX.onrender.com`.

You should see your full Portfolio + Watchlist populated with all 61 tickers.

### 2.6 Test an alert end-to-end

Before trusting this, fire a test alert to confirm email delivery:

1. In the web app, click **Alerts** tab → **＋ New Alert**
2. Pick any ticker — e.g., `SANM`
3. Rule type: **Price rises above…**
4. Threshold: set to **something below the current price** (for SANM
   at ~$174, set threshold to `100`)
5. Create the alert

Now trigger a manual run of the worker:

1. Back in Render, click **stocktracker-alerts**
2. Top right → **Trigger Run**
3. Wait ~30 seconds
4. Check your email — you should get a message with subject `StockTracker alert: SANM`
5. Check the **Alerts** tab in the web app — you'll see it in "Recent Fires"
6. Delete the test alert

If you don't get an email, check the worker logs:
**stocktracker-alerts → Logs** — it will tell you what went wrong (invalid
API key, missing env vars, etc.). Also check your spam folder on the first
run — `onboarding@resend.dev` is a shared domain and Gmail/Outlook sometimes
filters it initially. Mark the first email as "not spam" and subsequent ones
will land in your inbox.

### 2.7 Cold-start note (free tier)

Render's free web service **sleeps after 15 minutes of inactivity** and
takes about 30 seconds to wake up when you open the URL. The cron job
(the alert worker) is unaffected — it runs on schedule whether the web
app is asleep or not. So alerts will still fire.

If the cold start bothers you:
- Upgrade the web service to **Starter ($7/mo)** — always-on, instant load
- Or leave it — your alerts still work, you just wait 30s when you check

---

## Part 3 — Install on your phone (~2 minutes)

The app is a Progressive Web App, which means you can install it from the
browser to get an app-icon on your home screen and full-screen (no browser
chrome) experience.

### iPhone (Safari)

1. Open the app URL in **Safari** (not Chrome — Safari is required for PWAs on iOS)
2. Tap the **Share** button (box with arrow)
3. Scroll down, tap **Add to Home Screen**
4. Tap **Add**

An ascending-bars icon appears on your home screen. Tapping it launches
the app in full-screen, like a native app.

### Android (Chrome)

1. Open the app URL in Chrome
2. Tap the **⋮** menu → **Install app** (or "Add to Home Screen")
3. Confirm

---

## Using the app

### Portfolio / Watchlist tabs
- Click any row to open the detail panel (full Bloomberg-style info)
- Click column headers to sort
- Use the filter box to search across tickers + company names

### Adding a ticker
- Click **＋ Add Ticker**
- Pick the exchange (US / TSX / Crypto / LSE / etc. — same list as your
  desktop app)
- Enter the symbol, optionally click Validate to see company name + price
- Set endorsement price (your buy-in) and optional target price
- Add to Portfolio, Watchlist, or both

### Alerts
- Click the **Alerts** tab → **＋ New Alert**
- Three alert types:
  - **Price above / below** — simple threshold crossing
  - **% move from endorsement** — signed; `+20` = fires when up 20%,
    `-15` = fires when down 15% from your endorsement price
  - **RSI above / below** — overbought (≥70) or oversold (≤30)
- **One-shot** (default): fires once, then turns itself off
- **Repeating**: uncheck "fire once"; will fire every 5 min while the
  condition holds (careful — this can spam you)
- You can pause/resume any alert without deleting it

### Manual refresh
- The **⟳ Refresh** button in the header pulls fresh prices from Yahoo
  Finance and updates everything
- The cron worker also pulls fresh prices every 5 minutes when checking
  alerts, so data stays reasonably current without you clicking anything

---

## Migrating new tickers or holdings later

Your data lives in a SQLite file on Render's persistent disk at
`/var/data/stocks.db`. To change holdings, just use the app's UI (Add /
Edit / Move / Remove). No need to re-deploy.

If you ever want to sync changes from the desktop app to the web app,
you'd export your desktop `stocks.db` and manually update the web
version's database — but honestly, at that point the web app *is*
your source of truth, so there's no reason to keep the desktop one
updated.

---

## Troubleshooting

**"Failed to load stocks" on first load**
Wait 30 seconds and refresh — the free tier is probably waking up from sleep.

**Alert didn't fire when I expected**
- Check the worker logs: **stocktracker-alerts → Logs**
- The worker skips runs outside market hours (weekends, overnight, holidays) —
  this is intentional, otherwise you'd get weekend spam for stale prices
- Make sure the alert is marked **active** (not paused)
- If one-shot, confirm it hasn't already fired once and deactivated itself

**Email not arriving**
- Check your spam/junk folder — the first email from `onboarding@resend.dev`
  is often flagged by Gmail/Outlook. Mark as "not spam" and it'll stop
- Check the worker logs for Resend API errors (wrong key, rate limited, etc.)
- Verify the env vars `RESEND_API_KEY` and `ALERT_TO_EMAIL` are set on the
  **cron job** (stocktracker-alerts), not just the web service
- Log into Resend → **Logs** to see delivery history. If the email left
  Resend but didn't reach your inbox, the issue is on your email provider's
  side, not Resend's

**Want to wipe the database and start over**
SSH into the Render disk isn't available on free tier. Easiest path: in the
Render dashboard for `stocktracker-web`, delete and recreate the persistent
disk. On next boot it will re-seed from `data/stocks_seed.db` (which has your
original 61 tickers from when we zipped this).

---

## Costs (monthly)

| Item                          | Cost           |
|-------------------------------|----------------|
| Render web service (free)     | $0             |
| Render cron job (free)        | $0             |
| Render persistent disk (1 GB) | $0.25          |
| Resend email (3000/mo free)   | $0             |
| **Total**                     | **$0.25/mo**   |

Optional upgrade: Render Starter web service to avoid cold starts = +$7/mo.

---

## What's not included (yet)

The original desktop app has a few features the web port skips for now.
If you end up wanting them, we can add them in follow-up iterations:

- **Sector heatmap** (XLK/XLF/XLE treemap) — straightforward SVG port
- **Detail panel "Recent News"** — requires a news API (yfinance's is flaky)
- **Earnings dates & analyst targets** — same, needs reliable data source
- **Multiple watchlists** — the schema supports it, the UI currently shows
  just one
- **Drag-to-reorder rows** — QTableView feature not in the web version

Everything else from the desktop app is preserved.
