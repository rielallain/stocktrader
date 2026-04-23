"""
Refresh market data from GitHub Actions and POST it back to the web app.

Why this exists: Yahoo now blocks cloud IPs (Render + GitHub runners) and
Stooq added a captcha-gated API key. So we use Twelve Data: free tier,
800 req/day, 8 req/min. We fetch daily closes, compute RSI and SMA-200,
and POST back. The server uses COALESCE so this only overwrites the two
fields we send — current_price / market_cap / extended_price etc. stay
whatever the Render-side Finnhub path set.

Usage (from .github/workflows/refresh.yml):
    APP_URL=https://... BULK_REFRESH_SECRET=... TWELVEDATA_API_KEY=... \
      python scripts/refresh_via_github.py
"""
import os
import sys
import time
from typing import Optional

import pandas as pd
import requests

APP_URL = os.environ["APP_URL"].rstrip("/")
SECRET = os.environ["BULK_REFRESH_SECRET"]
TD_KEY = os.environ["TWELVEDATA_API_KEY"]
# Twelve Data free tier is 8 req/min. 60/8 = 7.5s floor; 8s gives headroom.
DELAY_SEC = float(os.environ.get("DELAY_SEC", "8"))


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


def _td_symbol(ticker: str) -> str:
    """Map our ticker format to Twelve Data's."""
    t = ticker.strip().upper()
    # Crypto: BTC-USD -> BTC/USD
    if t.endswith("-USD"):
        return t.replace("-USD", "/USD")
    # Twelve Data accepts FOO.TO, FOO.V, AIXA.DE as-is
    return t


def fetch_one(ticker: str) -> Optional[dict]:
    sym = _td_symbol(ticker)
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": sym,
        "interval": "1day",
        "outputsize": 250,  # ~1 year of trading days
        "apikey": TD_KEY,
        "order": "ASC",
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            print(f"[{ticker}] HTTP {r.status_code}")
            return None
        data = r.json()
        if data.get("status") == "error":
            print(f"[{ticker}] td error: {data.get('message', '')[:120]}")
            return None
        values = data.get("values")
        if not values:
            print(f"[{ticker}] no values returned")
            return None
        df = pd.DataFrame(values)
        closes = pd.to_numeric(df["close"], errors="coerce").dropna()
        if closes.empty:
            return None
        payload = {
            "ticker": ticker,
            "rsi": _compute_rsi(closes),
            "sma_200_pct": _compute_sma_pct(closes),
        }
        if "high" in df.columns and "low" in df.columns and len(df) >= 200:
            highs = pd.to_numeric(df["high"], errors="coerce").dropna()
            lows = pd.to_numeric(df["low"], errors="coerce").dropna()
            if not highs.empty:
                payload["high_52w"] = float(highs.max())
            if not lows.empty:
                payload["low_52w"] = float(lows.min())
        return payload
    except Exception as e:
        print(f"[{ticker}] fetch_one failed: {e}")
        return None


def main() -> int:
    # 1. Get the list of tickers from the live app
    r = requests.get(f"{APP_URL}/api/stocks", timeout=30)
    r.raise_for_status()
    tickers = sorted({s["ticker"] for s in r.json()})
    print(f"Fetching {len(tickers)} tickers from Twelve Data...")

    # 2. Fetch each one, respecting the 8-req/min free-tier cap
    results: list[dict] = []
    failed: list[str] = []
    for i, ticker in enumerate(tickers):
        data = fetch_one(ticker)
        if data is not None and data.get("rsi") is not None:
            results.append(data)
        else:
            failed.append(ticker)
        # Sleep between requests (skip after the last one)
        if i < len(tickers) - 1:
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
