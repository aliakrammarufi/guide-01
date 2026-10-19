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
    basis: float = 1.0   # multiplier applied to put the bars on the spot basis (1.0 = untouched)
    m15: pd.DataFrame | None = None   # 15-minute bars (last few days) for entry timing; None if unavailable
    basis_known: bool = True          # False when a rescale was wanted but no spot reference was available

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
YF_FALLBACK_SYMBOLS = ["GC=F", "XAUUSD=X", "GLD"]   # tried in order if the configured symbol fails


def _fetch_yfinance_one(symbol: str) -> MarketData:
    import yfinance as yf  # imported lazily so tests don't need it

    log.info("Fetching %s from yfinance", symbol)
    ticker = yf.Ticker(symbol)
    hourly = ticker.history(period="60d", interval="1h", auto_adjust=False)
    daily = ticker.history(period="1y", interval="1d", auto_adjust=False)
    m15 = None
    try:
        m15 = _normalize(ticker.history(period="7d", interval="15m", auto_adjust=False))
    except Exception as exc:  # noqa: BLE001 – entry timing degrades gracefully without M15
        log.warning("15m bars unavailable for %s: %s", symbol, exc)
    return MarketData(
        hourly=_normalize(hourly),
        daily=_normalize(daily),
        source=f"yfinance:{symbol}",
        fetched_at=datetime.now(timezone.utc),
        m15=m15,
    )


def _fetch_yfinance(symbol: str) -> MarketData:
    """Fetch ``symbol``; if Yahoo refuses it, fall back to the alternates so a bad or
    temporarily unavailable symbol never leaves the terminal without bars."""
    last: Exception | None = None
    for sym in [symbol] + [s for s in YF_FALLBACK_SYMBOLS if s != symbol]:
        try:
            return _fetch_yfinance_one(sym)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("yfinance %s failed (%s)", sym, exc)
    raise DataError(f"all yfinance symbols failed: {last}")


# ---------------------------------------------------------------------------
# Spot basis
# ---------------------------------------------------------------------------
def spot_reference() -> float | None:
    """Live spot XAU/USD from gold-api.com (free, no key); None if unavailable (two attempts)."""
    for attempt in (1, 2):
        try:
            r = httpx.get("https://api.gold-api.com/price/XAU", timeout=6, headers={"User-Agent": "XAUSentinel/1.0"})
            r.raise_for_status()
            v = float(r.json()["price"])
            if v > 0:
                return v
        except Exception as exc:  # noqa: BLE001
            log.debug("spot reference attempt %d failed: %s", attempt, exc)
    return None


_LAST_BASIS: dict[str, float] = {}   # symbol → last good spot/bars ratio (reused if the spot feed is briefly down)
_LAST_BASIS_TS: dict[str, float] = {}


def to_spot_basis(data: MarketData, spot: float | None) -> MarketData:
    """Rescale bars from another instrument (e.g. COMEX futures GC=F, which trade at a
    premium to spot) onto the spot XAU/USD basis the trader actually sees in MT5.

    All OHLC values are multiplied by ``spot / last close``. Percentage-based
    indicators (EMA stack, RSI, MACD, Bollinger) are unchanged by a constant scale;
    levels, ATR stops and targets land on the spot price scale. GLD (an ETF at ~1/10
    the price) is also handled by the same ratio.
    """
    import time as _time
    if data.hourly.empty:
        return data
    ref = float(data.hourly["close"].iloc[-1])
    if not ref:
        return data
    key = data.source
    if not spot:
        last, ts = _LAST_BASIS.get(key), _LAST_BASIS_TS.get(key, 0.0)
        if last is not None and _time.time() - ts < 6 * 3600:
            log.warning("Spot reference unavailable – reusing the last basis (x%.5f)", last)
            ratio = last
        else:
            log.warning("Spot reference unavailable and no recent basis – bars left on the %s basis", key)
            data.basis_known = False
            return data
    else:
        ratio = spot / ref
        _LAST_BASIS[key], _LAST_BASIS_TS[key] = ratio, _time.time()
    if abs(ratio - 1.0) < 0.0015:          # same instrument – nothing to do
        return data
    if not (0.05 < ratio < 20.0):          # not a plausible instrument of gold at all
        log.warning("spot basis ratio %.4f looks wrong; leaving bars untouched", ratio)
        return data
    for df in (data.hourly, data.daily, data.m15):
        if df is None:
            continue
        for col in ("open", "high", "low", "close"):
            df[col] = df[col] * ratio
    data.basis = ratio
    data.source = f"{data.source}→spot"
    log.info("Bars rescaled to spot basis (x%.5f, spot %.2f vs bars %.2f)", ratio, spot, ref)
    return data


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

    m15 = None
    try:
        m15 = _normalize(pull("15min", 500))
    except Exception as exc:  # noqa: BLE001
        log.warning("Twelve Data 15min bars unavailable: %s", exc)
    return MarketData(
        hourly=_normalize(pull("1h", 1000)),
        daily=_normalize(pull("1day", 400)),
        m15=m15,
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
    data: MarketData | None = None
    if settings.data_provider == "twelvedata":
        try:
            data = _fetch_twelvedata(settings.twelvedata_symbol, settings.twelvedata_api_key)
        except Exception as exc:  # noqa: BLE001 - we want to fall back on anything
            log.warning("Twelve Data failed (%s); falling back to yfinance", exc)
    if data is None:
        data = _fetch_yfinance(settings.yf_symbol)
    # Put the bars on the same basis as the live tick (spot XAU/USD) when they differ
    if settings.spot_basis:
        data = to_spot_basis(data, spot_reference())
    return data
