"""
Market data fetching — yfinance wrapper.

Mirrors the data model from the original desktop app's StockData: current
price, previous close, day change, volume, market cap, 52W high/low, RSI,
% vs 200-day SMA. Fetches in batches and caches via the stocks table's
last_price/last_fetched columns.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from backend.db import get_conn

log = logging.getLogger(__name__)


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


def fetch_one(ticker: str) -> Optional[Dict]:
    """Fetch a single ticker's full data snapshot. Returns None on failure."""
    try:
        t = yf.Ticker(ticker)

        # 1 year of daily closes — enough for 200-day SMA and 52W range
        hist = t.history(period="1y", auto_adjust=False)
        if hist.empty:
            log.warning(f"No history for {ticker}")
            return None

        closes = hist["Close"].dropna()
        if closes.empty:
            return None

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

        if company_name is None or market_cap is None:
            try:
                info = t.info
                company_name = company_name or info.get("longName") or info.get("shortName")
                market_cap = market_cap or info.get("marketCap")
            except Exception:
                pass

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
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        log.error(f"fetch_one({ticker}) failed: {e}")
        return None


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
    for ticker in tickers:
        data = fetch_one(ticker)
        if data is None:
            continue
        results[ticker] = data
        with get_conn() as conn:
            conn.execute("""
                UPDATE stocks SET
                    company_name   = COALESCE(?, company_name),
                    last_price     = ?,
                    last_fetched   = ?,
                    previous_close = ?,
                    volume         = ?,
                    market_cap     = ?,
                    high_52w       = ?,
                    low_52w        = ?,
                    rsi            = ?,
                    sma_200_pct    = ?
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
                ticker,
            ))

    return results


def validate_ticker(ticker: str) -> Optional[Dict]:
    """Quick validation — just confirm the ticker exists and return basic info."""
    return fetch_one(ticker)
