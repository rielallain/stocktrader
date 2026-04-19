# StockTracker Web

Bloomberg-style portfolio tracker + email alert system. Web port of the
PySide6 desktop app, designed to be installable as a PWA on iOS/Android
home screens and hosted for pennies on Render's free tier.

## Quick links

- **DEPLOY.md** — full step-by-step deployment guide (start here)
- **render.yaml** — declarative Render blueprint; two services (web + cron)
- **data/stocks_seed.db** — your existing 61 tickers, auto-migrated on first boot

## What's in here

```
stock-tracker-web/
├── backend/
│   ├── app.py              Flask REST API
│   ├── db.py               SQLite schema + seed migration
│   ├── market_data.py      yfinance wrapper, RSI + SMA calc
│   └── market_hours.py     NYSE open/close/pre/post detection
├── worker/
│   └── alerts.py           Scheduled alert evaluator + Resend email
├── frontend/
│   ├── templates/
│   │   └── index.html      Single-page app shell
│   └── static/
│       ├── app.js          Vanilla JS app (sorting, filtering, modals)
│       ├── styles.css      Dark theme (port of Qt stylesheet)
│       ├── service-worker.js
│       ├── manifest.webmanifest
│       ├── icon-192.png
│       └── icon-512.png
├── data/
│   └── stocks_seed.db      Your 61 tickers, copied on first boot
├── render.yaml             Deployment blueprint
├── requirements.txt        Python dependencies
└── DEPLOY.md               Step-by-step deploy guide
```

## Running locally (optional)

If you ever want to run this on your own machine for testing:

```bash
pip install -r requirements.txt
python -m backend.app              # http://localhost:5000
python -m worker.alerts --force    # manually run the alert check
```

Set `RESEND_API_KEY` and `ALERT_TO_EMAIL` env vars first if you want email to actually send.

## Alert types

Three supported rule types:

| Type                       | Threshold meaning                              |
|----------------------------|------------------------------------------------|
| `price_above`              | Fire when last price ≥ threshold ($)           |
| `price_below`              | Fire when last price ≤ threshold ($)           |
| `pct_from_endorsement`     | Signed %. +20 = up 20%, -15 = down 15%         |
| `rsi_above`                | Fire when RSI(14) ≥ threshold (typ. 70)        |
| `rsi_below`                | Fire when RSI(14) ≤ threshold (typ. 30)        |

RSI and % types are exposed in the UI. The cron worker evaluates all
active rules every 5 minutes during market hours + extended hours.

## Data model preserved from desktop

All columns from the Qt app's StockTable are present: ticker, company,
current price, day change (with PRE/AH indicator colors), endorsement
price & date, allocation %, target price, $ and % P/L, volume,
market cap, 52-week range bar, RSI (colored red ≥70, green ≤30), %
distance from 200-day SMA, status. Same color palette (`#0D1117` bg,
`#58A6FF` blue, `#3FB950` green, `#F85149` red, `#D29922` yellow,
`#BC8CFF` purple for after-hours).
