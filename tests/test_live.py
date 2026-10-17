import json
import struct
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.live import LiveHub
from option_trader.providers.angel_stream import (
    MODE_LTP,
    MODE_SNAP,
    AngelTickStream,
    parse_tick,
    subscribe_message,
)
from option_trader.providers.angelone import InstrumentMaster

from .helpers import RecordedNSEProvider, manager_with
from .test_angelone import MASTER


def snap_packet(token, ltp, bid, ask, oi=150000, volume=999, close=None, ts=1_792_000_000_000, mode=MODE_SNAP,
                exch=2):
    """Build a binary tick exactly as Angel's SmartAPI WebSocket 2.0 sends it (prices in paise)."""
    p = lambda x: int(round(x * 100))
    head = struct.pack("<BB25sqqq", mode, exch, token.encode().ljust(25, b"\x00"), 7, ts, p(ltp))
    if mode == MODE_LTP:
        return head
    close = close if close is not None else ltp
    quote = struct.pack("<qqqddqqqq", 75, p(ltp), volume, 1e5, 2e5, p(ltp), p(ltp), p(ltp), p(close))
    snap = struct.pack("<qqd", ts, oi, 1.5)
    levels = b"".join(struct.pack("<HqqH", 1, 750 - i, p(bid - i * 0.05), 3) for i in range(5))
    levels += b"".join(struct.pack("<HqqH", 0, 300 + i, p(ask + i * 0.05), 2) for i in range(5))
    tail = struct.pack("<qqqq", 0, 0, 0, 0)
    return head + quote + snap + levels + tail


def test_parse_snapquote():
    t = parse_tick(snap_packet("41001", 151.25, 151.2, 151.35, oi=123450, volume=4242, close=145))
    assert t["token"] == "41001" and t["exchange_type"] == 2
    assert t["ltp"] == pytest.approx(151.25)
    assert t["bid"] == pytest.approx(151.2) and t["ask"] == pytest.approx(151.35)
    assert t["bid_qty"] == 750 and t["ask_qty"] == 300
    assert t["oi"] == 123450 and t["volume"] == 4242 and t["close"] == pytest.approx(145)


def test_parse_ltp_only_and_short():
    t = parse_tick(snap_packet("99926000", 25100.5, 0, 0, mode=MODE_LTP, exch=1))
    assert t["ltp"] == pytest.approx(25100.5) and "bid" not in t
    with pytest.raises(ValueError):
        parse_tick(b"\x01\x02")


def test_subscribe_message():
    m = json.loads(subscribe_message({2: ["1", "2"], 1: []}))
    assert m["action"] == 1 and m["params"]["mode"] == 3
    assert m["params"]["tokenList"] == [{"exchangeType": 2, "tokens": ["1", "2"]}]


class FakeWS:
    """Stands in for websocket.WebSocketApp: opens, delivers packets, then closes."""

    def __init__(self, headers, stream, packets):
        self.headers, self.stream, self.packets, self.sent = headers, stream, packets, []

    def send(self, msg):
        self.sent.append(msg)

    def run_forever(self):
        self.stream._on_open(self)
        for p in self.packets:
            self.stream._on_message(self, p)
        self.stream._on_message(self, "pong")
        self.stream.stop()

    def close(self):
        pass


def test_stream_subscribes_on_open_and_stores_ticks():
    holder = {}

    def factory(headers, stream):
        holder["ws"] = FakeWS(headers, stream, [snap_packet("41001", 150.0, 149.9, 150.1)])
        return holder["ws"]

    creds = {"jwt": "j", "api_key": "k", "client_code": "c", "feed_token": "f"}
    s = AngelTickStream(lambda: creds, ws_factory=factory)
    s.subscribe(2, ["41001"])
    for _ in range(50):
        if s.tick("41001"):
            break
        time.sleep(0.02)
    assert s.tick("41001")["ltp"] == 150.0
    ws = holder["ws"]
    assert ws.headers["x-feed-token"] == "f" and ws.headers["Authorization"] == "Bearer j"
    assert json.loads(ws.sent[0])["params"]["tokenList"][0]["tokens"] == ["41001"]
    assert s.version == 1


class FakeAngel:
    def master(self):
        return InstrumentMaster(MASTER)


class ManualStream(AngelTickStream):
    def __init__(self):
        super().__init__(lambda: {})
        self.subs = []

    def subscribe(self, exchange_type, tokens):
        self.subs.append((exchange_type, list(tokens)))


def test_hub_overlays_ticks_on_nse_snapshot():
    stream = ManualStream()
    hub = LiveHub(FakeAngel(), stream=stream)
    chain = RecordedNSEProvider().get_option_chain("NIFTY")
    assert hub.apply(chain) == 0  # subscribed, no ticks yet
    assert (2, sorted(["41001", "41002", "41003"])) == (stream.subs[0][0], sorted(stream.subs[0][1]))
    assert stream.subs[1] == (1, ["99926000"])
    stream.on_binary(snap_packet("41001", 151.25, 151.2, 151.35, oi=123450, close=145))
    stream.on_binary(snap_packet("99926000", 25060.0, 0, 0, mode=MODE_LTP, exch=1))
    assert hub.apply(chain) == 1
    q = chain.quote(25000, "CE")
    assert q.ltp == 151.25 and q.bid == 151.2 and q.ask == 151.35 and q.oi == 123450
    assert q.change == pytest.approx(6.25) and q.tick_ts is not None
    assert q.iv is not None and q.delta is not None  # recomputed from the live price
    assert chain.spot == 25060.0 and chain.live_at is not None
    assert chain.quote(25000, "PE").ltp == 128.0  # untouched without a tick


def test_manager_applies_live_overlay_to_cached_chain():
    stream = ManualStream()
    m = manager_with(RecordedNSEProvider())
    m.live = LiveHub(FakeAngel(), stream=stream)
    m.get_chain("NIFTY")
    stream.on_binary(snap_packet("41003", 99.0, 98.9, 99.1))
    assert m.get_chain("NIFTY").quote(25100, "CE").ltp == 99.0  # cached snapshot, live price


def test_stream_endpoint_without_credentials():
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    with client.stream("GET", "/api/stream", params={"symbol": "NIFTY"}) as r:
        body = "".join(r.iter_text())
    assert "event: status" in body and '"live": false' in body
