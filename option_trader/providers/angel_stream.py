"""Real-time exchange ticks from Angel One's SmartAPI WebSocket 2.0 feed.

The REST snapshot (NSE or Angel) is refreshed every few seconds at best. This module keeps a
WebSocket open to ``wss://smartapisocket.angelone.in/smart-stream`` and receives every trade
and best-bid/ask change for the subscribed option contracts and the underlying index, so
premiums in the app move with the market tick by tick.

Binary packet layout (little-endian), per Angel's SmartAPI WebSocket 2.0 specification:

====== ======= =======================================================
bytes  type    field
====== ======= =======================================================
0      uint8   subscription mode (1 LTP, 2 Quote, 3 SnapQuote)
1      uint8   exchange type (1 NSE cash, 2 NSE F&O, ...)
2-27   char25  token (NUL padded)
27-35  int64   sequence number
35-43  int64   exchange timestamp (epoch ms)
43-51  int64   last traded price (paise)
51-59  int64   last traded quantity                 (Quote+)
59-67  int64   average traded price                 (Quote+)
67-75  int64   volume traded today                  (Quote+)
75-83  double  total buy quantity                   (Quote+)
83-91  double  total sell quantity                  (Quote+)
91-123 int64x4 open, high, low, close               (Quote+)
123-131 int64  last traded timestamp                (SnapQuote)
131-139 int64  open interest                        (SnapQuote)
139-147 double open interest change %               (SnapQuote)
147-347 10x20B best five buy then sell levels:      (SnapQuote)
               flag uint16, qty int64, price int64, orders uint16
====== ======= =======================================================
"""

from __future__ import annotations

import json
import logging
import struct
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

log = logging.getLogger(__name__)

WS_URL = "wss://smartapisocket.angelone.in/smart-stream"
MODE_LTP, MODE_QUOTE, MODE_SNAP = 1, 2, 3
EXCHANGE_NSE_CM, EXCHANGE_NSE_FO = 1, 2
MAX_TOKENS = 1000
HEARTBEAT_SECONDS = 10


def _q(data: bytes, start: int) -> int:
    return struct.unpack_from("<q", data, start)[0]


def _d(data: bytes, start: int) -> float:
    return struct.unpack_from("<d", data, start)[0]


def parse_tick(data: bytes, price_divisor: float = 100.0) -> dict:
    """Decode one binary tick. Prices for NSE segments arrive in paise."""
    if len(data) < 51:
        raise ValueError(f"Tick too short: {len(data)} bytes")
    mode, exch = data[0], data[1]
    token = data[2:27].split(b"\x00", 1)[0].decode("ascii", "ignore")
    tick = {
        "mode": mode,
        "exchange_type": exch,
        "token": token,
        "sequence": _q(data, 27),
        "exchange_ts": _q(data, 35),
        "ltp": _q(data, 43) / price_divisor,
    }
    if mode in (MODE_QUOTE, MODE_SNAP) and len(data) >= 123:
        tick.update(
            last_qty=_q(data, 51),
            avg_price=_q(data, 59) / price_divisor,
            volume=_q(data, 67),
            total_buy_qty=_d(data, 75),
            total_sell_qty=_d(data, 83),
            open=_q(data, 91) / price_divisor,
            high=_q(data, 99) / price_divisor,
            low=_q(data, 107) / price_divisor,
            close=_q(data, 115) / price_divisor,
        )
    if mode == MODE_SNAP and len(data) >= 347:
        tick.update(oi=_q(data, 131), oi_change_pct=_d(data, 139))
        buys, sells = [], []
        for i in range(10):
            off = 147 + i * 20
            flag, qty, price, orders = struct.unpack_from("<HqqH", data, off)
            level = {"qty": qty, "price": price / price_divisor, "orders": orders}
            (buys if flag == 1 else sells).append(level)
        tick["bid"] = buys[0]["price"] if buys and buys[0]["price"] > 0 else None
        tick["bid_qty"] = buys[0]["qty"] if buys else None
        tick["ask"] = sells[0]["price"] if sells and sells[0]["price"] > 0 else None
        tick["ask_qty"] = sells[0]["qty"] if sells else None
    return tick


def subscribe_message(tokens_by_exchange: dict[int, list[str]], mode: int = MODE_SNAP,
                      action: int = 1, correlation_id: str = "optiontrader") -> str:
    return json.dumps({
        "correlationID": correlation_id,
        "action": action,  # 1 subscribe, 0 unsubscribe
        "params": {
            "mode": mode,
            "tokenList": [{"exchangeType": ex, "tokens": toks} for ex, toks in tokens_by_exchange.items() if toks],
        },
    })


class AngelTickStream:
    """Background WebSocket connection that keeps the latest tick per token."""

    def __init__(self, credentials_fn: Callable[[], dict], ws_factory: Optional[Callable] = None):
        self._credentials_fn = credentials_fn
        self._ws_factory = ws_factory
        self._ticks: dict[str, dict] = {}
        self._subscribed: dict[int, set[str]] = {}
        self._lock = threading.Lock()
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.connected = False
        self.last_error: Optional[str] = None
        self.last_tick_at: Optional[float] = None
        self.version = 0  # increments on every tick; lets consumers detect changes cheaply

    # -- public ------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="angel-ticks", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def subscribe(self, exchange_type: int, tokens: list[str]) -> None:
        with self._lock:
            have = self._subscribed.setdefault(exchange_type, set())
            new = [t for t in tokens if t not in have]
            total = sum(len(v) for v in self._subscribed.values())
            if total + len(new) > MAX_TOKENS:
                new = new[: max(0, MAX_TOKENS - total)]
            have.update(new)
        if new and self.connected:
            self._send(subscribe_message({exchange_type: new}))
        self.start()

    def tick(self, token: str) -> Optional[dict]:
        with self._lock:
            return self._ticks.get(token)

    def status(self) -> dict:
        with self._lock:
            n = sum(len(v) for v in self._subscribed.values())
        return {
            "connected": self.connected,
            "subscribed_tokens": n,
            "last_tick_at": datetime.fromtimestamp(self.last_tick_at, timezone.utc).isoformat()
            if self.last_tick_at else None,
            "last_error": self.last_error,
        }

    # -- internals ---------------------------------------------------------------------------
    def _send(self, msg: str) -> None:
        try:
            if self._ws is not None:
                self._ws.send(msg)
        except Exception as exc:
            self.last_error = f"send failed: {exc}"

    def on_binary(self, data: bytes) -> None:
        try:
            tick = parse_tick(data)
        except (ValueError, struct.error) as exc:
            log.debug("Bad tick: %s", exc)
            return
        tick["received_at"] = time.time()
        with self._lock:
            prev = self._ticks.get(tick["token"], {})
            prev.update({k: v for k, v in tick.items() if v is not None})
            self._ticks[tick["token"]] = prev
            self.version += 1
        self.last_tick_at = tick["received_at"]

    def _on_open(self, ws) -> None:
        self.connected = True
        self.last_error = None
        with self._lock:
            subs = {ex: sorted(toks) for ex, toks in self._subscribed.items() if toks}
        if subs:
            self._send(subscribe_message(subs))

    def _on_message(self, ws, message) -> None:
        if isinstance(message, (bytes, bytearray)):
            self.on_binary(bytes(message))
        # text frames are "pong" heartbeats or JSON errors
        elif message and message != "pong":
            log.info("Angel stream message: %s", message)
            if "error" in str(message).lower():
                self.last_error = str(message)[:300]

    def _on_error(self, ws, error) -> None:
        self.last_error = str(error)[:300]

    def _on_close(self, ws, *args) -> None:
        self.connected = False

    def _heartbeat(self) -> None:
        while not self._stop.is_set():
            if self.connected:
                self._send("ping")
            self._stop.wait(HEARTBEAT_SECONDS)

    def _run(self) -> None:
        threading.Thread(target=self._heartbeat, name="angel-ticks-hb", daemon=True).start()
        backoff = 2.0
        while not self._stop.is_set():
            try:
                creds = self._credentials_fn()
                headers = {
                    "Authorization": f"Bearer {creds['jwt']}",
                    "x-api-key": creds["api_key"],
                    "x-client-code": creds["client_code"],
                    "x-feed-token": creds["feed_token"],
                }
                if self._ws_factory is not None:
                    ws = self._ws_factory(headers, self)
                else:
                    import websocket  # websocket-client

                    ws = websocket.WebSocketApp(
                        WS_URL, header=headers, on_open=self._on_open, on_message=self._on_message,
                        on_error=self._on_error, on_close=self._on_close,
                    )
                self._ws = ws
                ws.run_forever()
                backoff = 2.0 if self.last_tick_at else backoff
            except Exception as exc:  # credentials or network failure: retry with backoff
                self.last_error = str(exc)[:300]
                log.warning("Angel tick stream error: %s", exc)
            self.connected = False
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 60.0)
