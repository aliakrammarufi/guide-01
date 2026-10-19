"""
Multi-year XAU/USD history for the deep self-check.

Source: Dukascopy's public candle API (the same feed its charts use; no key):

    https://jetta.dukascopy.com/v1/candles/minute/XAU-USD/BID/{year}/{month}/{day}   one UTC day of 1-minute candles
    https://jetta.dukascopy.com/v1/candles/day/XAU-USD/BID/{year}                    one year of daily candles

Responses are delta-encoded JSON: a base candle plus per-candle integer deltas in
units of ``multiplier``; ``times`` are deltas in units of ``shift`` milliseconds.
Prices are spot bid – the same basis as MT5 – so no futures premium to rescale.

The minute candles are aggregated to 15-minute bars (and those to hourly bars);
the daily file is used as-is, with a one-year warm-up before the first replayed
bar so the daily EMA200 is settled. The 15-minute bars are cached, compressed, in
the key-value store and on disk; only the days after the cached end are fetched
on the next run.
"""
from __future__ import annotations

import gzip
import io
import logging
import pickle
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

log = logging.getLogger(__name__)

API = "https://jetta.dukascopy.com/v1"
CODE = "XAU-USD"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json", "Origin": "https://www.dukascopy.com", "Referer": "https://www.dukascopy.com/"}
AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
KV_KEY = "xau-sentinel:history:m15"      # cached 15-minute bars (gzip pickle) – small enough for a free store


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------
def decode_candles(payload: dict) -> pd.DataFrame:
    """Delta-encoded Dukascopy candles → DataFrame(open, high, low, close, volume) with a UTC index.
    Gaps (no trading) are left as gaps – no flat candles are invented."""
    times = payload.get("times") or []
    if not times:
        return pd.DataFrame(columns=list(AGG), dtype=float)
    mult = float(payload["multiplier"])
    shift = int(payload["shift"])
    base_ts = int(payload["timestamp"])
    o = round(float(payload["open"]) / mult)
    h = round(float(payload["high"]) / mult)
    l = round(float(payload["low"]) / mult)
    c = round(float(payload["close"]) / mult)
    opens, highs, lows, closes, vols = payload["opens"], payload["highs"], payload["lows"], payload["closes"], payload.get("volumes") or [0] * len(times)
    rows = []
    ts = base_ts
    for i in range(len(times)):
        ts += int(times[i]) * shift
        o += int(opens[i]); h += int(highs[i]); l += int(lows[i]); c += int(closes[i])
        rows.append((ts, o * mult, h * mult, l * mult, c * mult, float(vols[i])))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    return df


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------
def _get(client: httpx.Client, url: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = client.get(url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                log.warning("history: %s failed (%s)", url, exc)
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def fetch_minute_day(client: httpx.Client, day: date, code: str = CODE) -> pd.DataFrame:
    """One UTC day of 1-minute candles, aggregated to 15-minute bars."""
    payload = _get(client, f"{API}/candles/minute/{code}/BID/{day.year}/{day.month}/{day.day}")
    m1 = decode_candles(payload) if payload else pd.DataFrame(columns=list(AGG), dtype=float)
    if not len(m1):
        return m1
    return m1.resample("15min", label="left", closed="left").agg(AGG).dropna(subset=["close"])


def fetch_daily_year(client: httpx.Client, year: int, code: str = CODE) -> pd.DataFrame:
    payload = _get(client, f"{API}/candles/day/{code}/BID/{year}")
    return decode_candles(payload) if payload else pd.DataFrame(columns=list(AGG), dtype=float)


def fetch_minutes(start: date, end: date, workers: int = 4, code: str = CODE, progress=None) -> pd.DataFrame:
    """15-minute bars (built from the 1-minute candles) from ``start`` to ``end`` inclusive; closed days are skipped."""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() != 5]            # Saturday is always closed
    frames = []
    with httpx.Client(timeout=30, headers=UA, http2=False) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for n, df in enumerate(pool.map(lambda d: fetch_minute_day(client, d, code), days)):
                if len(df):
                    frames.append(df)
                if progress and n % 100 == 0:
                    progress(n, len(days))
    if not frames:
        return pd.DataFrame(columns=list(AGG), dtype=float)
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def fetch_daily(start_year: int, end_year: int, code: str = CODE) -> pd.DataFrame:
    frames = []
    with httpx.Client(timeout=30, headers=UA) as client:
        for y in range(start_year, end_year + 1):
            df = fetch_daily_year(client, y, code)
            if len(df):
                frames.append(df)
    if not frames:
        return pd.DataFrame(columns=list(AGG), dtype=float)
    out = pd.concat(frames)
    return out[~out.index.duplicated(keep="last")].sort_index()


# ---------------------------------------------------------------------------
# cache (key-value store + disk) and the public entry point
# ---------------------------------------------------------------------------
def _pack(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as g:
        pickle.dump(df, g, protocol=4)
    return buf.getvalue()


def _unpack(blob: bytes) -> pd.DataFrame:
    with gzip.GzipFile(fileobj=io.BytesIO(blob), mode="rb") as g:
        return pickle.load(g)  # noqa: S301 – our own cached frame


def _load_cache(path: Path | None, redis_url: str) -> pd.DataFrame | None:
    if path and path.exists():
        try:
            return _unpack(path.read_bytes())
        except Exception as exc:  # noqa: BLE001
            log.warning("history cache on disk unreadable: %s", exc)
    if redis_url:
        try:
            import redis

            blob = redis.Redis.from_url(redis_url, socket_timeout=20).get(KV_KEY)
            if blob:
                return _unpack(blob)
        except Exception as exc:  # noqa: BLE001
            log.warning("history cache in the store unreadable: %s", exc)
    return None


def _save_cache(df: pd.DataFrame, path: Path | None, redis_url: str) -> None:
    blob = _pack(df)
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        except Exception as exc:  # noqa: BLE001
            log.warning("history cache write failed: %s", exc)
    if redis_url:
        try:
            import redis

            redis.Redis.from_url(redis_url, socket_timeout=30).set(KV_KEY, blob)
        except Exception as exc:  # noqa: BLE001
            log.warning("history cache store write failed: %s", exc)
    log.info("history: cached %d 15-minute bars (%d KB)", len(df), len(blob) // 1024)


def load_history(years: float = 3.0, cache_path: Path | str | None = None, redis_url: str = "",
                 progress=None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(h1, d1, m15) covering the last ``years`` years, from the cache plus whatever is new.

    Spot-basis bars with UTC indexes: 15-minute bars built from Dukascopy's 1-minute candles, hourly
    bars aggregated from them, daily bars from Dukascopy's daily file starting a year earlier (warm-up).
    """
    path = Path(cache_path) if cache_path else None
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=int(365.25 * years) + 14)
    m15 = _load_cache(path, redis_url)
    fetch_from = start
    if m15 is not None and len(m15):
        last = m15.index[-1].to_pydatetime().date()
        fetch_from = max(start, last)                  # re-fetch the last cached day (it may have been partial)
        m15 = m15[m15.index >= pd.Timestamp(start, tz="UTC")]
        log.info("history: %d cached 15-minute bars up to %s", len(m15), last)
    if fetch_from <= today:
        log.info("history: fetching candles %s → %s from Dukascopy…", fetch_from, today)
        fresh = fetch_minutes(fetch_from, today, progress=progress)
        if len(fresh):
            m15 = fresh if m15 is None or not len(m15) else pd.concat([m15[m15.index < fresh.index[0]], fresh])
            _save_cache(m15, path, redis_url)
    if m15 is None or not len(m15):
        raise RuntimeError("no history available")
    m15 = m15[~m15.index.duplicated(keep="last")].sort_index()
    h1 = m15.resample("1h", label="left", closed="left").agg(AGG).dropna(subset=["close"])
    d1 = fetch_daily(start.year - 1, today.year)
    if not len(d1):
        d1 = m15.resample("1D", label="left", closed="left").agg(AGG).dropna(subset=["close"])
    return h1, d1, m15
