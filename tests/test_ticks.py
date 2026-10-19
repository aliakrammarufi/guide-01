import asyncio
import json
import time

from market.ticks import TickEngine, tv_frame, tv_parse


def test_tv_frame_roundtrip():
    msg = {"m": "quote_add_symbols", "p": ["qs_x", "OANDA:XAUUSD"]}
    raw = tv_frame(msg)
    assert raw.startswith("~m~") and tv_parse(raw) == [msg]


def test_tv_parse_mixed_heartbeat_and_messages():
    body = json.dumps({"m": "qsd", "p": ["qs", {"n": "OANDA:XAUUSD", "s": "ok", "v": {"lp": 4170.5}}]}, separators=(",", ":"))
    raw = f"~m~4~m~~h~7~m~{len(body)}~m~{body}"
    out = tv_parse(raw)
    assert out[0] == "~h~7" and out[1]["m"] == "qsd" and out[1]["p"][1]["v"]["lp"] == 4170.5


def test_engine_priority_and_fanout():
    async def run():
        eng = TickEngine()
        q = eng.subscribe()
        eng.publish("goldapi", 4170.0)
        eng.publish("tradingview:OANDA:XAUUSD", 4171.2, bid=4171.0, ask=4171.4)
        eng.publish("goldapi", 4169.0)                     # lower priority – ignored while TV is live
        eng.publish("tradingview:OANDA:XAUUSD", 4171.2, bid=4171.0, ask=4171.4)   # unchanged – no event
        eng.publish("tradingview:OANDA:XAUUSD", 4171.5)   # bid/ask carried over
        got = []
        while not q.empty():
            got.append(q.get_nowait())
        assert [t.price for t in got] == [4170.0, 4171.2, 4171.5]
        assert got[-1].bid == 4171.0 and got[-1].ask == 4171.4
        assert eng.latest().source.startswith("tradingview") and eng.updates == 3
        # when the websocket source goes quiet, the poller takes over
        eng.slots["tradingview"].seen -= 60
        eng.publish("goldapi", 4168.0)
        assert eng.latest().price == 4168.0 and eng.active_source() == "goldapi"
    asyncio.run(run())


def test_engine_ignores_bad_prices():
    eng = TickEngine()
    eng.publish("goldapi", 0)
    eng.publish("goldapi", -1)
    assert eng.latest() is None
