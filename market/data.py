"""
Price data providers for XAU/USD.

Two providers are supported:

* ``yfinance`` (default) – free, no API key.  ``GC=F`` (COMEX gold futures)
  is the most reliable symbol; ``XAUUSD=X`` is spot but sometimes has gaps.
* ``twelvedata`` – free API key from https://twelvedata.com, true XAU/USD
  spot quotes, 800 requests/day on the free tier.

Every provider returns a ``MarketData`` object holding an hourly and a daily
OHLC DataFrame (columns: open, high, low, close, volume; UTC DatetimeIndex).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import pandas as pd

from config import settings

log = logging.getLogger(__name__)

COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass
class MarketData:
    hourly: pd.DataFrame
    daily: pd.DataFrame
    source: str
    fetched_at: datetime

    @property
    def last_price(self) -> float:
        return float(self.hourly["close"].iloc[-1])

    @property
    def last_bar_time(self) -> datetime:
        return self.hourly.index[-1].to_pydatetime()

    def change_pct(self, bars: int) -> float:
        """Percent change over the last ``bars`` hourly bars."""
        if len(self.hourly) <= bars:
            return 0.0
        prev = float(self.hourly["close"].iloc[-1 - bars])
        return (self.last_price / prev - 1.0) * 100.0 if prev else 0.0


class DataError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Lower-case columns, enforce UTC index, drop empty rows, sort."""
    if df is None or df.empty:
        raise DataError("provider returned no data")

    # yfinance may return MultiIndex columns like ("Close", "GC=F")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]) for c in df.columns]
    df = df.rename(columns={c: str(c).lower() for c in df.columns})

    missing = [c for c in ["open", "high", "low", "close"] if c not in df.columns]
    if missing:
        raise DataError(f"provider data missing columns: {missing}")
    if "volume" not in df.columns:
        df["volume"] = 0.0

    df = df[COLUMNS].astype(float)
    df = df.dropna(subset=["close"])

    idx = pd.to_datetime(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    df.index = idx
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if len(df) < 50:
        raise DataError(f"not enough bars ({len(df)}) for analysis")
    return df


# ---------------------------------------------------------------------------
# yfinance
# ---------------------------------------------------------------------------
def _fetch_yfinance(symbol: str) -> MarketData:
    import yfinance as yf  # imported lazily so tests don't need it

    log.info("Fetching %s from yfinance", symbol)
    ticker = yf.Ticker(symbol)
    hourly = ticker.history(period="60d", interval="1h", auto_adjust=False)
    daily = ticker.history(period="1y", interval="1d", auto_adjust=False)
    return MarketData(
        hourly=_normalize(hourly),
        daily=_normalize(daily),
        source=f"yfinance:{symbol}",
        fetched_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Twelve Data
# ---------------------------------------------------------------------------
def _fetch_twelvedata(symbol: str, api_key: str) -> MarketData:
    log.info("Fetching %s from Twelve Data", symbol)

    def pull(interval: str, outputsize: int) -> pd.DataFrame:
        resp = httpx.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "timezone": "UTC",
                "apikey": api_key,
                "format": "JSON",
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") == "error" or "values" not in payload:
            raise DataError(f"Twelve Data error: {payload.get('message', payload)}")
        df = pd.DataFrame(payload["values"])
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime")
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
        return df

    return MarketData(
        hourly=_normalize(pull("1h", 1000)),
        daily=_normalize(pull("1day", 400)),
        source=f"twelvedata:{symbol}",
        fetched_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def fetch_market_data() -> MarketData:
    """Fetch hourly + daily XAU/USD data using the configured provider.

    Falls back to yfinance if Twelve Data fails, so a bad key never leaves
    the bot without prices.
    """
    if settings.data_provider == "twelvedata":
        try:
            return _fetch_twelvedata(settings.twelvedata_symbol, settings.twelvedata_api_key)
        except Exception as exc:  # noqa: BLE001 - we want to fall back on anything
            log.warning("Twelve Data failed (%s); falling back to yfinance", exc)
    return _fetch_yfinance(settings.yf_symbol)
