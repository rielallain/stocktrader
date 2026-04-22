"""
Refresh market data from GitHub Actions and POST it back to the web app.

Why this exists: Yahoo Finance aggressively rate-limits cloud provider IPs
(Render, AWS, GCP), so yfinance running on Render returns empty histories
for US tickers. GitHub's runners have different IPs that Yahoo doesn't
throttle the same way, so this script runs there and pushes the data in.

Usage (from .github/workflows/refresh.yml):
    APP_URL=https://... BULK_REFRESH_SECRET=... python scripts/refresh_via_github.py
"""
import os
import sys
import time
from typing import Optional

import pandas as pd
import requests
import yfinance as yf
from curl_cffi import requests as curl_requests

APP_URL = os.environ["APP_URL"].rstrip("/")
SECRET = os.environ["BULK_REFRESH_SECRET"]
DELAY_SEC = float(os.environ.get("DELAY_SEC", "1.5"))

# Chrome-impersonation session — yfinance will route Yahoo calls through this,
# which bypasses the bot detection that returns "Too Many Requests" on bare
# python-requests traffic.
_SESSION = curl_requests.Session(impersonate="chrome")


def _compute_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    last = rsi.iloc[-1]
    return None if pd.isna(last) else float(last)


def _compute_sma_pct(closes: pd.Series, window: int = 200) -> Optional[float]:
    if len(closes) < window:
        return None
    sma = closes.rolling(window=window).mean().iloc[-1]
    if pd.isna(sma) or sma == 0:
        return None
    return float((closes.iloc[-1] - sma) / sma * 100)


def fetch_one(ticker: str) -> Optional[dict]:
    try:
        t = yf.Ticker(ticker, session=_SESSION)
        hist = t.history(period="1y", auto_adjust=False)
        if hist.empty:
            print(f"[{ticker}] empty history")
            return None
        closes = hist["Close"].dropna()
        if closes.empty:
            return None

        current = float(closes.iloc[-1])
        previous = float(closes.iloc[-2]) if len(closes) >= 2 else current
        volume = int(hist["Volume"].iloc[-1]) if not pd.isna(hist["Volume"].iloc[-1]) else None

        company_name = None
        market_cap = None
        extended_price = None
        extended_session = None
        try:
            info = t.info
            company_name = info.get("longName") or info.get("shortName")
            market_cap = info.get("marketCap")
            pre_price = info.get("preMarketPrice")
            post_price = info.get("postMarketPrice")
            if post_price not in (None, 0) and abs(float(post_price) - current) > 1e-6:
                extended_price = float(post_price)
                extended_session = "post"
            elif pre_price not in (None, 0) and abs(float(pre_price) - current) > 1e-6:
                extended_price = float(pre_price)
                extended_session = "pre"
        except Exception as e:
            print(f"[{ticker}] info lookup failed: {e}")

        return {
            "ticker": ticker,
            "company_name": company_name,
            "current_price": current,
            "previous_close": previous,
            "volume": volume,
            "market_cap": float(market_cap) if market_cap else None,
            "high_52w": float(hist["High"].max()),
            "low_52w": float(hist["Low"].min()),
            "rsi": _compute_rsi(closes),
            "sma_200_pct": _compute_sma_pct(closes),
            "extended_price": extended_price,
            "extended_session": extended_session,
        }
    except Exception as e:
        print(f"[{ticker}] fetch_one failed: {e}")
        return None


def main() -> int:
    # 1. Get the list of tickers from the live app
    r = requests.get(f"{APP_URL}/api/stocks", timeout=30)
    r.raise_for_status()
    tickers = sorted({s["ticker"] for s in r.json()})
    print(f"Fetching {len(tickers)} tickers from yfinance...")

    # 2. Fetch each one locally (GitHub runner IP, not rate-limited by Yahoo)
    results: list[dict] = []
    failed: list[str] = []
    for ticker in tickers:
        data = fetch_one(ticker)
        if data is not None:
            results.append(data)
        else:
            failed.append(ticker)
        time.sleep(DELAY_SEC)

    print(f"Fetched OK: {len(results)}/{len(tickers)}")
    if failed:
        print(f"Failed: {', '.join(failed)}")

    if not results:
        print("No successful fetches — aborting upload")
        return 1

    # 3. POST the bulk result back to the web app
    resp = requests.post(
        f"{APP_URL}/api/bulk-upsert",
        json=results,
        headers={"X-Refresh-Token": SECRET, "Content-Type": "application/json"},
        timeout=60,
    )
    print(f"Upsert response: HTTP {resp.status_code} {resp.text[:500]}")
    resp.raise_for_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
