"""
Market data fetching — yfinance primary, Finnhub fallback.

Yahoo rate-limits cloud-hosted IPs aggressively, so when yfinance returns
a 429 / Too Many Requests, we automatically fall back to Finnhub. Set
FINNHUB_API_KEY in env to enable the fallback (free tier: 60 calls/min).

Mirrors the data model from the original desktop app's StockData: current
price, previous close, day change, volume, market cap, 52W high/low, RSI,
% vs 200-day SMA. Fetches in batches and caches via the stocks table's
last_price/last_fetched columns.
"""
import json
import logging
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from backend.db import get_conn

log = logging.getLogger(__name__)

# Seconds to sleep between successive yfinance calls in a batch. Yahoo
# aggressively rate-limits cloud IPs (Render egress), which causes fetches
# to silently downgrade to Finnhub and lose RSI / 200-day SMA. Spacing the
# batch out past Yahoo's burst threshold keeps us on the yfinance path for
# US tickers. Override via env var if we need to tune.
YFINANCE_BATCH_DELAY_SEC = float(os.environ.get("YFINANCE_BATCH_DELAY_SEC", "2.0"))


def _compute_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    """Wilder's RSI from a series of closing prices. Returns latest value."""
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
    if pd.isna(last):
        return None
    return float(last)


def _compute_sma_pct(closes: pd.Series, window: int = 200) -> Optional[float]:
    """% distance of latest close from the N-day SMA. Positive = above SMA."""
    if len(closes) < window:
        return None
    sma = closes.rolling(window=window).mean().iloc[-1]
    if pd.isna(sma) or sma == 0:
        return None
    latest = closes.iloc[-1]
    return float((latest - sma) / sma * 100)


# -------------------------------------------------------------------
# Finnhub fallback
# -------------------------------------------------------------------

def _fetch_one_finnhub(ticker: str) -> Optional[Dict]:
    """
    Fall back to Finnhub when yfinance rate-limits us.
    Free tier: 60 calls/min. Returns same shape as fetch_one().

    Note: Finnhub's free tier doesn't include long historical price series,
    so we can't compute RSI or 200-day SMA from it. Those fields will be
    None for tickers fetched via this path. The /quote endpoint does give
    us 52W high/low directly though.
    """
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        return None

    # Don't fall back to Finnhub for non-US exchanges. Finnhub's free tier
    # doesn't cover international exchanges, and stripping the suffix to try
    # as a US ticker is DANGEROUS — AMT.V (AmeriTrust on TSXV) becomes AMT
    # (American Tower on NYSE), which is a completely different company with
    # totally different price. Return None so the caller shows "no data" instead
    # of returning wrong data that silently gets stored.
    non_us_suffixes = (
        ".TO", ".V", ".CN", ".NE",      # Canada (TSX, TSXV, CSE, Cboe Canada)
        ".L", ".AS", ".PA", ".MI", ".BR", ".LS", ".MC", ".BE", ".IR",  # Europe
        ".DE", ".F", ".MU", ".SG", ".DU", ".HM", ".HA", ".VI",          # Germany/Austria
        ".ST", ".HE", ".CO", ".OL",     # Nordics
        ".SW", ".IL",                    # Switzerland/Israel
        ".TA",                           # Tel Aviv
        ".HK", ".SS", ".SZ", ".T", ".KS", ".KQ", ".TW", ".SI",          # Asia
        ".AX", ".NZ",                    # Australia/NZ
        ".SA", ".MX", ".BA",             # LatAm
        ".JO",                           # Johannesburg
    )
    for suffix in non_us_suffixes:
        if ticker.endswith(suffix):
            log.warning(
                f"Finnhub: skipping {ticker} — free tier doesn't cover {suffix} "
                f"reliably and stripping the suffix risks returning a different "
                f"US-listed company with the same base ticker"
            )
            return None

    # Safe to pass the base ticker to Finnhub now
    fh_symbol = ticker.upper()

    try:
        # /quote endpoint: current price, previous close, 52W high/low (sometimes)
        quote_url = f"https://finnhub.io/api/v1/quote?symbol={fh_symbol}&token={api_key}"
        with urllib.request.urlopen(quote_url, timeout=10) as resp:
            quote = json.loads(resp.read().decode("utf-8"))

        # Finnhub returns 0s instead of an error when the ticker isn't found
        if not quote or quote.get("c") in (None, 0):
            log.warning(f"Finnhub: no data for {fh_symbol}")
            return None


        current = float(quote["c"])  # current price
        previous = float(quote.get("pc") or current)  # previous close
        high_52w = float(quote["h"]) if quote.get("h") else None  # day high actually
        low_52w = float(quote["l"]) if quote.get("l") else None   # day low actually

        # /stock/profile2 for company name + market cap
        company_name = None
        market_cap = None
        try:
            prof_url = f"https://finnhub.io/api/v1/stock/profile2?symbol={fh_symbol}&token={api_key}"
            with urllib.request.urlopen(prof_url, timeout=10) as resp:
                prof = json.loads(resp.read().decode("utf-8"))
            company_name = prof.get("name")
            mc = prof.get("marketCapitalization")
            if mc:
                market_cap = float(mc) * 1_000_000  # Finnhub returns in millions
        except Exception as e:
            log.warning(f"Finnhub profile lookup failed for {fh_symbol}: {e}")

        # /stock/metric for true 52w range (basic financials endpoint)
        try:
            met_url = f"https://finnhub.io/api/v1/stock/metric?symbol={fh_symbol}&metric=price&token={api_key}"
            with urllib.request.urlopen(met_url, timeout=10) as resp:
                met = json.loads(resp.read().decode("utf-8"))
            metrics = met.get("metric", {}) or {}
            if metrics.get("52WeekHigh"):
                high_52w = float(metrics["52WeekHigh"])
            if metrics.get("52WeekLow"):
                low_52w = float(metrics["52WeekLow"])
        except Exception as e:
            log.warning(f"Finnhub metric lookup failed for {fh_symbol}: {e}")

        log.info(f"Finnhub: fetched {ticker} -> ${current}")

        return {
            "ticker": ticker,
            "company_name": company_name or ticker,
            "current_price": current,
            "previous_close": previous,
            "volume": None,           # not in /quote; would need extra call
            "market_cap": market_cap,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "rsi": None,              # not available on free tier
            "sma_200_pct": None,      # not available on free tier
            "extended_price": None,    # Finnhub free tier doesn't surface this
            "extended_session": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except urllib.error.HTTPError as e:
        log.error(f"Finnhub HTTP {e.code} for {fh_symbol}: {e.read().decode('utf-8', errors='replace')[:200]}")
        return None
    except Exception as e:
        log.error(f"Finnhub fetch failed for {fh_symbol}: {e}")
        return None


def fetch_one(ticker: str) -> Optional[Dict]:
    """Fetch a single ticker. Tries yfinance first, falls back to Finnhub on rate-limit."""
    # Sanitize: strip any whitespace and uppercase
    ticker = ticker.strip().upper()

    yfinance_failed_with_rate_limit = False

    try:
        t = yf.Ticker(ticker)

        # 1 year of daily closes — enough for 200-day SMA and 52W range
        hist = t.history(period="1y", auto_adjust=False)
        if hist.empty:
            log.warning(f"yfinance: no history for {ticker}")
            # Try Finnhub before giving up
            return _fetch_one_finnhub(ticker)

        closes = hist["Close"].dropna()
        if closes.empty:
            return _fetch_one_finnhub(ticker)

        current = float(closes.iloc[-1])
        previous = float(closes.iloc[-2]) if len(closes) >= 2 else current
        high_52w = float(hist["High"].max())
        low_52w = float(hist["Low"].min())
        volume = int(hist["Volume"].iloc[-1]) if not pd.isna(hist["Volume"].iloc[-1]) else None
        rsi = _compute_rsi(closes)
        sma_pct = _compute_sma_pct(closes, window=200)

        # Try fast_info first (cheap); fall back to info (expensive, sometimes fails)
        company_name = None
        market_cap = None
        try:
            fi = t.fast_info
            company_name = getattr(fi, "longName", None) or getattr(fi, "shortName", None)
            market_cap = getattr(fi, "market_cap", None)
        except Exception:
            pass

        # Extended-hours pricing: yfinance exposes pre/post market prices via .info
        # for most US tickers. Non-US tickers (TSX, .DE, etc.) won't have these —
        # we silently leave them None, no harm done.
        extended_price = None
        extended_session = None
        info = None

        if company_name is None or market_cap is None:
            try:
                info = t.info
                company_name = company_name or info.get("longName") or info.get("shortName")
                market_cap = market_cap or info.get("marketCap")
            except Exception:
                pass

        if info is None:
            try:
                info = t.info
            except Exception:
                info = {}

        if info:
            post_price = info.get("postMarketPrice")
            pre_price = info.get("preMarketPrice")
            # Prefer post-market if both are set (post comes later in the day).
            # Only surface the extended price if it actually differs from the
            # regular-session close — otherwise it's redundant noise.
            if post_price not in (None, 0) and abs(float(post_price) - current) > 1e-6:
                extended_price = float(post_price)
                extended_session = "post"
            elif pre_price not in (None, 0) and abs(float(pre_price) - current) > 1e-6:
                extended_price = float(pre_price)
                extended_session = "pre"

        return {
            "ticker": ticker,
            "company_name": company_name,
            "current_price": current,
            "previous_close": previous,
            "volume": volume,
            "market_cap": float(market_cap) if market_cap else None,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "rsi": rsi,
            "sma_200_pct": sma_pct,
            "extended_price": extended_price,
            "extended_session": extended_session,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        err_msg = str(e).lower()
        if "rate limit" in err_msg or "too many requests" in err_msg or "429" in err_msg:
            yfinance_failed_with_rate_limit = True
            log.warning(f"yfinance rate-limited for {ticker}, trying Finnhub fallback")
        else:
            log.error(f"yfinance fetch_one({ticker}) failed: {e}")

    # Fallback to Finnhub
    if yfinance_failed_with_rate_limit:
        return _fetch_one_finnhub(ticker)
    return _fetch_one_finnhub(ticker)  # try anyway for non-rate-limit failures too


def fetch_and_store_all(tickers: Optional[List[str]] = None) -> Dict[str, Dict]:
    """
    Fetch all given tickers (or all in DB if None), write to stocks table,
    and return the results keyed by ticker.
    """
    if tickers is None:
        with get_conn() as conn:
            rows = conn.execute("SELECT ticker FROM stocks").fetchall()
        tickers = [r["ticker"] for r in rows]

    results: Dict[str, Dict] = {}
    for i, ticker in enumerate(tickers):
        # Pace the batch so Yahoo's burst limiter doesn't force us onto the
        # Finnhub fallback path (which can't supply RSI / 200-day SMA).
        # Single-ticker refreshes (len(tickers)==1) get no delay.
        if i > 0 and YFINANCE_BATCH_DELAY_SEC > 0:
            time.sleep(YFINANCE_BATCH_DELAY_SEC)
        data = fetch_one(ticker)
        if data is None:
            continue
        results[ticker] = data
        with get_conn() as conn:
            conn.execute("""
                UPDATE stocks SET
                    company_name     = COALESCE(?, company_name),
                    last_price       = ?,
                    last_fetched     = ?,
                    previous_close   = ?,
                    volume           = ?,
                    market_cap       = ?,
                    high_52w         = ?,
                    low_52w          = ?,
                    rsi              = ?,
                    sma_200_pct      = ?,
                    extended_price   = ?,
                    extended_session = ?
                WHERE ticker = ?
            """, (
                data["company_name"],
                data["current_price"],
                data["fetched_at"],
                data["previous_close"],
                data["volume"],
                data["market_cap"],
                data["high_52w"],
                data["low_52w"],
                data["rsi"],
                data["sma_200_pct"],
                data.get("extended_price"),
                data.get("extended_session"),
                ticker,
            ))

    return results


def validate_ticker(ticker: str) -> Optional[Dict]:
    """Quick validation — just confirm the ticker exists and return basic info."""
    return fetch_one(ticker)
