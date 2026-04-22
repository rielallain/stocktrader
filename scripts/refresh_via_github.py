"""
Refresh market data from GitHub Actions and POST it back to the web app.

Why this exists: Yahoo Finance now blocks both Render's IPs AND GitHub runner
IPs (returns 429 / empty history). So we switched to Stooq for historical
closes — it's free, no auth, no rate limits, and serves daily OHLC as CSV.
We compute RSI and SMA-200 from that, and let the web app keep whatever
current_price / market_cap / extended_price it already has (via COALESCE
on the server side).

Usage (from .github/workflows/refresh.yml):
    APP_URL=https://... BULK_REFRESH_SECRET=... python scripts/refresh_via_github.py
"""
import io
import os
import sys
import time
from typing import Optional

import pandas as pd
import requests

APP_URL = os.environ["APP_URL"].rstrip("/")
SECRET = os.environ["BULK_REFRESH_SECRET"]
DELAY_SEC = float(os.environ.get("DELAY_SEC", "0.4"))


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


def _stooq_symbol(ticker: str) -> Optional[str]:
    """Map our ticker format to Stooq's. Returns None for tickers we can't map."""
    t = ticker.strip().upper()
    # Crypto: BTC-USD -> btcusd, ETH-USD -> ethusd
    if t.endswith("-USD"):
        return t.replace("-", "").lower()
    # Toronto: FOO.TO -> foo.ca (Stooq uses .ca for TSX)
    if t.endswith(".TO"):
        return t[:-3].lower() + ".ca"
    # TSX Venture: FOO.V -> foo.v (Stooq uses .v)
    if t.endswith(".V"):
        return t[:-2].lower() + ".v"
    # German: AIXA.DE -> aixa.de (same suffix)
    if t.endswith(".DE"):
        return t.lower()
    # US: plain ticker -> ticker.us
    if "." not in t and "-" not in t:
        return t.lower() + ".us"
    return None


def fetch_one(ticker: str) -> Optional[dict]:
    sym = _stooq_symbol(ticker)
    if sym is None:
        print(f"[{ticker}] no Stooq mapping")
        return None
    url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            print(f"[{ticker}] stooq HTTP {r.status_code}")
            return None
        body = r.text.strip()
        if not body or body.lower().startswith("no data") or "," not in body:
            print(f"[{ticker}] stooq no data ({sym})")
            return None
        df = pd.read_csv(io.StringIO(body))
        if "Close" not in df.columns or df.empty:
            print(f"[{ticker}] stooq unexpected format")
            return None
        closes = df["Close"].dropna()
        if closes.empty:
            return None
        # Stooq returns data sorted ascending by date
        payload = {
            "ticker": ticker,
            "rsi": _compute_rsi(closes),
            "sma_200_pct": _compute_sma_pct(closes),
        }
        # Only include 52w high/low if we have enough history
        if len(df) >= 200:
            payload["high_52w"] = float(df["High"].tail(252).max())
            payload["low_52w"] = float(df["Low"].tail(252).min())
        return payload
    except Exception as e:
        print(f"[{ticker}] fetch_one failed: {e}")
        return None


def main() -> int:
    # 1. Get the list of tickers from the live app
    r = requests.get(f"{APP_URL}/api/stocks", timeout=30)
    r.raise_for_status()
    tickers = sorted({s["ticker"] for s in r.json()})
    print(f"Fetching {len(tickers)} tickers from Stooq...")

    # 2. Fetch each one (Stooq has no meaningful rate limit for this volume)
    results: list[dict] = []
    failed: list[str] = []
    for ticker in tickers:
        data = fetch_one(ticker)
        if data is not None and data.get("rsi") is not None:
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
