"""
Real-time XAU/USD tick engine.

Sources (all optional, best available wins):

* ``finnhub``      – Finnhub WebSocket, ``OANDA:XAU_USD`` trades. Needs a free key
                     (FINNHUB_API_KEY). True tick-by-tick.
* ``tradingview``  – TradingView's public quote stream (the feed its own charts use),
                     ``OANDA:XAUUSD`` by default. No key. Tick-by-tick.
* ``goldapi``      – gold-api.com polled every QUOTE_INTERVAL_SEC. No key. Updates
                     every few seconds; also the anchor / fallback.

Every source publishes into its own slot; the engine picks the highest-priority
slot that delivered a tick recently and fans those ticks out to subscribers
(the SSE endpoint, the trade-signal tracker).  Nothing is ever invented: if no
source ticks, the price simply does not move.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import string
import time
from dataclasses import dataclass, field

import httpx

from config import settings

log = logging.getLogger(__name__)

FRESH_SEC = 20.0      # a source counts as live if it ticked within this window
PRIORITY = {"finnhub": 0, "tradingview": 1, "goldapi": 2}


@dataclass
class Tick:
    price: float
    source: str
    ts: float                      # exchange/feed time (epoch seconds) when known
    bid: float | None = None
    ask: float | None = None
    seen: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        return {"price": round(self.price, 3), "bid": None if self.bid is None else round(self.bid, 3),
                "ask": None if self.ask is None else round(self.ask, 3), "ts": self.ts, "source": self.source}


class TickEngine:
    def __init__(self) -> None:
        self.slots: dict[str, Tick] = {}
        self.last: Tick | None = None
        self.updates = 0
        self._subs: set[asyncio.Queue] = set()
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self.started_at = time.time()
        self.stats: dict[str, int] = {}

    # ---- pub/sub ------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _best(self) -> Tick | None:
        now = time.monotonic()
        live = [t for t in self.slots.values() if now - t.seen <= FRESH_SEC]
        if not live:
            return None
        return min(live, key=lambda t: PRIORITY.get(t.source.split(":")[0], 9))

    def publish(self, source: str, price: float, bid: float | None = None, ask: float | None = None, ts: float | None = None) -> None:
        if not price or price <= 0:
            return
        key = source.split(":")[0]
        prev = self.slots.get(key)
        if bid is None and prev is not None and prev.source == source:
            bid = prev.bid
        if ask is None and prev is not None and prev.source == source:
            ask = prev.ask
        tick = Tick(price=float(price), source=source, ts=float(ts or time.time()), bid=bid, ask=ask)
        self.slots[key] = tick
        self.stats[key] = self.stats.get(key, 0) + 1
        best = self._best()
        if best is not tick:
            return
        changed = self.last is None or tick.price != self.last.price or tick.bid != self.last.bid or tick.ask != self.last.ask
        self.last = tick
        if not changed:
            return
        self.updates += 1
        for q in list(self._subs):
            try:
                q.put_nowait(tick)
            except asyncio.QueueFull:
                pass

    def latest(self) -> Tick | None:
        best = self._best()
        return best or self.last

    def active_source(self) -> str:
        t = self._best()
        return t.source if t else ""

    # ---- lifecycle ----------------------------------------------------------
    async def start(self, sources: list[str] | None = None) -> None:
        sources = sources or settings.tick_sources
        if "finnhub" in sources and settings.finnhub_api_key:
            self._tasks.append(asyncio.create_task(self._finnhub(), name="ticks-finnhub"))
        if "tradingview" in sources:
            self._tasks.append(asyncio.create_task(self._tradingview(), name="ticks-tradingview"))
        if "goldapi" in sources:
            self._tasks.append(asyncio.create_task(self._goldapi(), name="ticks-goldapi"))
        self._tasks.append(asyncio.create_task(self._report(), name="ticks-report"))
        log.info("Tick engine: sources %s", ", ".join(t.get_name().replace("ticks-", "") for t in self._tasks if t.get_name() != "ticks-report"))

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()

    async def _report(self) -> None:
        last = dict(self.stats)
        while not self._stop.is_set():
            await asyncio.sleep(60)
            delta = {k: self.stats.get(k, 0) - last.get(k, 0) for k in self.stats}
            last = dict(self.stats)
            log.info("Ticks/min %s · active %s · pushed %d", delta, self.active_source() or "none", self.updates)

    # ---- sources ------------------------------------------------------------
    async def _goldapi(self) -> None:
        interval = max(0.5, settings.quote_interval_sec)
        last_updated = None
        async with httpx.AsyncClient(timeout=6, headers={"User-Agent": "XAUSentinel/1.0"}) as client:
            while not self._stop.is_set():
                try:
                    r = await client.get("https://api.gold-api.com/price/XAU")
                    r.raise_for_status()
                    d = r.json()
                    if d.get("updatedAt") != last_updated or last_updated is None:
                        last_updated = d.get("updatedAt")
                        self.publish("goldapi", float(d["price"]))
                except Exception as exc:  # noqa: BLE001
                    log.debug("gold-api poll failed: %s", exc)
                    await asyncio.sleep(3)
                await asyncio.sleep(interval)

    async def _finnhub(self) -> None:
        import websockets

        uri = f"wss://ws.finnhub.io?token={settings.finnhub_api_key}"
        symbol = settings.finnhub_symbol
        backoff = 2
        while not self._stop.is_set():
            try:
                async with websockets.connect(uri, ping_interval=20, max_size=2**20) as ws:
                    await ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                    log.info("Finnhub stream connected (%s)", symbol)
                    backoff = 2
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "trade":
                            for t in msg.get("data") or []:
                                self.publish("finnhub", float(t["p"]), ts=float(t.get("t", 0)) / 1000 or None)
                        elif msg.get("type") == "error":
                            log.warning("Finnhub: %s", msg.get("msg"))
            except Exception as exc:  # noqa: BLE001
                log.warning("Finnhub stream error: %s (reconnect in %ds)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    async def _tradingview(self) -> None:
        import websockets

        symbols = settings.tv_symbols
        backoff = 2
        while not self._stop.is_set():
            try:
                kwargs = dict(origin=TV_ORIGIN, ping_interval=None, max_size=2**22)
                try:
                    ws_ctx = websockets.connect(TV_WS, additional_headers=TV_HEADERS, **kwargs)
                except TypeError:  # websockets < 13
                    ws_ctx = websockets.connect(TV_WS, extra_headers=TV_HEADERS, **kwargs)
                async with ws_ctx as ws:
                    qs = "qs_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
                    await ws.send(tv_frame({"m": "set_auth_token", "p": ["unauthorized_user_token"]}))
                    await ws.send(tv_frame({"m": "quote_create_session", "p": [qs]}))
                    await ws.send(tv_frame({"m": "quote_set_fields", "p": [qs, "lp", "lp_time", "bid", "ask", "ch", "chp", "update_mode", "rtc"]}))
                    await ws.send(tv_frame({"m": "quote_add_symbols", "p": [qs, *symbols]}))
                    log.info("TradingView stream connected (%s)", ", ".join(symbols))
                    backoff = 2
                    state: dict[str, dict] = {}
                    while not self._stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=45)
                        for msg in tv_parse(raw if isinstance(raw, str) else raw.decode()):
                            if isinstance(msg, str):            # heartbeat ~h~N – echo it back
                                await ws.send(f"~m~{len(msg)}~m~{msg}")
                                continue
                            if msg.get("m") != "qsd":
                                if msg.get("m") == "critical_error":
                                    raise RuntimeError(f"TradingView critical_error: {msg.get('p')}")
                                continue
                            p = msg.get("p") or []
                            if len(p) < 2 or not isinstance(p[1], dict):
                                continue
                            sym, status, v = p[1].get("n"), p[1].get("s"), p[1].get("v") or {}
                            if status != "ok":
                                log.warning("TradingView symbol %s: %s", sym, status)
                                continue
                            st = state.setdefault(sym, {})
                            st.update({k: v[k] for k in ("lp", "bid", "ask", "lp_time") if k in v})
                            if "lp" not in st:
                                continue
                            # several symbols may be subscribed as fallbacks – the first one in the list wins
                            rank = symbols.index(sym) if sym in symbols else 99
                            best_live = min((symbols.index(s) for s, d in state.items() if s in symbols and "lp" in d), default=rank)
                            if rank != best_live:
                                continue
                            self.publish(f"tradingview:{sym}", float(st["lp"]),
                                         bid=_f(st.get("bid")), ask=_f(st.get("ask")), ts=_f(st.get("lp_time")))
            except Exception as exc:  # noqa: BLE001
                log.warning("TradingView stream error: %s (reconnect in %ds)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)


# ---- TradingView wire format ------------------------------------------------
TV_WS = "wss://data.tradingview.com/socket.io/websocket?from=chart%2F&type=chart"
TV_ORIGIN = "https://www.tradingview.com"
TV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
_FRAME = re.compile(r"~m~(\d+)~m~")


def tv_frame(msg: dict) -> str:
    body = json.dumps(msg, separators=(",", ":"))
    return f"~m~{len(body)}~m~{body}"


def tv_parse(raw: str) -> list:
    """Split a socket.io-style payload into heartbeat strings and JSON messages."""
    out: list = []
    i = 0
    while True:
        m = _FRAME.match(raw, i)
        if not m:
            break
        n = int(m.group(1))
        body = raw[m.end(): m.end() + n]
        i = m.end() + n
        if body.startswith("~h~"):
            out.append(body)
            continue
        try:
            out.append(json.loads(body))
        except ValueError:
            continue
    return out


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
