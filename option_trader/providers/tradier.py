"""Tradier market data: real-time US option chains, quotes and a streaming feed.

Needs an API access token from a Tradier brokerage account (free to open; market data is
real-time for account holders) or a developer sandbox token (delayed). Set TRADIER_TOKEN and,
for a sandbox token, TRADIER_SANDBOX=1.

REST (https://api.tradier.com/v1 or https://sandbox.tradier.com/v1):
* /markets/options/expirations?symbol=   listed expiries
* /markets/options/chains?symbol=&expiration=&greeks=true   chain with Greeks and bid/mid/ask IV
* /markets/quotes?symbols=   quotes, /markets/history   daily bars, /markets/clock   session

Streaming (production only): POST /markets/events/session for a session id, then a WebSocket
to wss://ws.tradier.com/v1/markets/events subscribed to OCC option symbols and the underlying
receives every quote (bid/ask/sizes) and trade (price, cumulative volume) as it happens.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

import requests

from ..errors import DataUnavailableError, ProviderNotConfiguredError
from ..models import CONTRACT_MULTIPLIER, ET, Candle, OptionChain, OptionQuote, StrikeRow, utcnow
from .base import DataProvider, normalize_symbol

log = logging.getLogger(__name__)

PROD = "https://api.tradier.com/v1"
SANDBOX = "https://sandbox.tradier.com/v1"
WS_URL = "wss://ws.tradier.com/v1/markets/events"


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _list(x: Any) -> list:
    """Tradier returns a single object instead of a one-element list."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def parse_chain(payload: dict, symbol: str, expiry: date) -> list[StrikeRow]:
    rows: dict[float, StrikeRow] = {}
    for o in _list((payload.get("options") or {}).get("option")):
        strike = _f(o.get("strike"))
        typ = {"call": "CE", "put": "PE"}.get(str(o.get("option_type")))
        if strike is None or typ is None:
            continue
        if int(_f(o.get("contract_size")) or CONTRACT_MULTIPLIER) != CONTRACT_MULTIPLIER:
            continue  # adjusted contract with a non-standard deliverable
        g = o.get("greeks") or {}
        iv = _f(g.get("mid_iv")) or _f(g.get("smv_vol"))
        trade_ms = _f(o.get("trade_date"))
        q = OptionQuote(
            strike=strike, option_type=typ, expiry=expiry,  # type: ignore[arg-type]
            ltp=_f(o.get("last")) or None, bid=_f(o.get("bid")) or None, ask=_f(o.get("ask")) or None,
            bid_qty=int(_f(o.get("bidsize")) or 0) or None, ask_qty=int(_f(o.get("asksize")) or 0) or None,
            volume=int(_f(o.get("volume")) or 0), oi=_f(o.get("open_interest")), change=_f(o.get("change")),
            iv=iv if iv and 0.005 < iv < 5 else None,
            delta=_f(g.get("delta")), gamma=_f(g.get("gamma")), theta=_f(g.get("theta")), vega=_f(g.get("vega")),
            symbol=o.get("symbol"),
            tick_ts=datetime.fromtimestamp(trade_ms / 1000, timezone.utc).isoformat() if trade_ms else None,
        )
        row = rows.setdefault(strike, StrikeRow(strike))
        if typ == "CE":
            row.call = q
        else:
            row.put = q
    return list(rows.values())


class TradierProvider(DataProvider):
    name = "tradier"
    min_interval = 0.2  # Tradier allows ~120 market-data requests per minute

    def __init__(self, token: str = "", sandbox: bool = False, session: Optional[requests.Session] = None,
                 timeout: float = 15.0):
        self.token = token
        self.sandbox = sandbox
        self.base = SANDBOX if sandbox else PROD
        self._http = session or requests.Session()
        self._timeout = timeout
        self._sleep = time.sleep

    @classmethod
    def from_settings(cls, settings) -> "TradierProvider":
        return cls(settings.tradier_token, settings.tradier_sandbox)

    def is_configured(self) -> bool:
        return bool(self.token)

    def _get(self, path: str, params: Optional[dict] = None, attempts: int = 3) -> Any:
        if not self.token:
            raise ProviderNotConfiguredError("Tradier token missing: set TRADIER_TOKEN (Settings page)")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        last: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                resp = self._http.get(f"{self.base}{path}", params=params, headers=headers, timeout=self._timeout)
            except requests.RequestException as exc:
                last = DataUnavailableError(f"Cannot reach Tradier: {exc}")
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        last = DataUnavailableError("Tradier returned a non-JSON response")
                elif resp.status_code == 401:
                    raise DataUnavailableError("Tradier rejected the token (HTTP 401): check TRADIER_TOKEN / sandbox setting")
                elif resp.status_code == 429 or resp.status_code >= 500:
                    last = DataUnavailableError(f"Tradier HTTP {resp.status_code}")
                else:
                    raise DataUnavailableError(f"Tradier request {path} failed: HTTP {resp.status_code}")
            if attempt < attempts - 1:
                self._sleep(min(4.0, 0.5 * 2 ** attempt) + random.uniform(0, 0.2))
        raise DataUnavailableError(f"Tradier request {path} failed: {last}")

    def get_expiries(self, symbol: str) -> list[date]:
        sym = normalize_symbol(symbol)
        d = self._get("/markets/options/expirations", {"symbol": sym, "includeAllRoots": "true"})
        raw = _list((d.get("expirations") or {}).get("date"))
        today = datetime.now(ET).date()
        exps = sorted({date.fromisoformat(x) for x in raw if date.fromisoformat(x) >= today})
        if not exps:
            raise DataUnavailableError(f"Tradier lists no option expiries for {sym}")
        return exps

    def get_quote(self, symbol: str) -> dict:
        q = _list((self._get("/markets/quotes", {"symbols": normalize_symbol(symbol)}).get("quotes") or {}).get("quote"))
        if not q or not _f(q[0].get("last")):
            raise DataUnavailableError(f"Tradier returned no price for {symbol}")
        q = q[0]
        return {"last": _f(q.get("last")), "prev_close": _f(q.get("prevclose")), "high": _f(q.get("high")),
                "low": _f(q.get("low")), "change": _f(q.get("change")), "pct": _f(q.get("change_percentage"))}

    def get_spot(self, symbol: str) -> float:
        return self.get_quote(symbol)["last"]

    def get_option_chain(self, symbol: str, expiry: Optional[date] = None) -> OptionChain:
        sym = normalize_symbol(symbol)
        exps = self.get_expiries(sym)
        if expiry is None:
            expiry = exps[0]
        elif expiry not in exps:
            raise DataUnavailableError(f"{expiry} is not a listed {sym} expiry on Tradier")
        payload = self._get("/markets/options/chains", {"symbol": sym, "expiration": expiry.isoformat(), "greeks": "true"})
        chain = OptionChain(sym, expiry, self.get_spot(sym), parse_chain(payload, sym, expiry), "tradier", utcnow(),
                            lot_size=CONTRACT_MULTIPLIER, expiries=exps, currency="USD",
                            exchange_timestamp=datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"))
        chain.notes.append("Tradier " + ("sandbox (delayed)" if self.sandbox else "real-time") + " quotes")
        return chain.validate()

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        days = {"1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731}.get(period, 366)
        start = date.fromordinal(datetime.now(ET).date().toordinal() - days)
        d = self._get("/markets/history", {"symbol": normalize_symbol(symbol), "interval": "daily", "start": start.isoformat()})
        out = []
        for r in _list((d.get("history") or {}).get("day")):
            try:
                out.append(Candle(datetime.fromisoformat(r["date"]).replace(tzinfo=timezone.utc), float(r["open"]),
                                  float(r["high"]), float(r["low"]), float(r["close"]), _f(r.get("volume")) or 0.0))
            except (KeyError, TypeError, ValueError):
                continue
        if not out:
            raise DataUnavailableError(f"Tradier returned no history for {symbol}")
        return out

    def clock(self) -> dict:
        return (self._get("/markets/clock") or {}).get("clock") or {}

    def create_stream_session(self) -> str:
        if self.sandbox:
            raise DataUnavailableError("Tradier streaming needs a production (brokerage) token; sandbox tokens are delayed only")
        resp = self._http.post(f"{self.base}/markets/events/session",
                               headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                               timeout=self._timeout)
        if resp.status_code != 200:
            raise DataUnavailableError(f"Tradier stream session failed: HTTP {resp.status_code}")
        sid = ((resp.json() or {}).get("stream") or {}).get("sessionid")
        if not sid:
            raise DataUnavailableError("Tradier returned no stream session id")
        return sid


def parse_event(msg: dict) -> Optional[dict]:
    """Normalise a Tradier stream event into a tick dict (prices in dollars, times in ms)."""
    typ, sym = msg.get("type"), msg.get("symbol")
    if not sym:
        return None
    if typ == "quote":
        return {"symbol": sym, "bid": _f(msg.get("bid")), "ask": _f(msg.get("ask")),
                "bid_qty": int(_f(msg.get("bidsz")) or 0) or None, "ask_qty": int(_f(msg.get("asksz")) or 0) or None,
                "exchange_ts": int(max(_f(msg.get("biddate")) or 0, _f(msg.get("askdate")) or 0)) or None}
    if typ in ("trade", "timesale"):
        return {"symbol": sym, "ltp": _f(msg.get("price") or msg.get("last")),
                "volume": int(_f(msg.get("cvol")) or 0) or None, "exchange_ts": int(_f(msg.get("date")) or 0) or None}
    if typ == "summary":
        return {"symbol": sym, "close": _f(msg.get("prevClose")), "oi": None}
    return None


class TradierStream:
    """WebSocket feed keeping the latest tick per symbol (same interface as the Angel feed)."""

    def __init__(self, session_fn: Callable[[], str], ws_factory: Optional[Callable] = None):
        self._session_fn = session_fn
        self._ws_factory = ws_factory
        self._ticks: dict[str, dict] = {}
        self._symbols: set[str] = set()
        self._lock = threading.Lock()
        self._ws = None
        self._sid: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.connected = False
        self.last_error: Optional[str] = None
        self.last_tick_at: Optional[float] = None
        self.version = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tradier-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def subscribe(self, symbols: list[str]) -> None:
        with self._lock:
            new = [s for s in symbols if s and s not in self._symbols]
            self._symbols.update(new)
        if new and self.connected:
            self._send_subscription()
        self.start()

    def tick(self, symbol: str) -> Optional[dict]:
        with self._lock:
            return self._ticks.get(symbol)

    def status(self) -> dict:
        with self._lock:
            n = len(self._symbols)
        return {"connected": self.connected, "subscribed_tokens": n, "feed": "tradier",
                "last_tick_at": datetime.fromtimestamp(self.last_tick_at, timezone.utc).isoformat() if self.last_tick_at else None,
                "last_error": self.last_error}

    def _send_subscription(self) -> None:
        with self._lock:
            syms = sorted(self._symbols)
        try:
            # Tradier replaces the subscription with each payload, so always send the full list.
            self._ws.send(json.dumps({"symbols": syms, "sessionid": self._sid, "filter": ["quote", "trade", "summary"],
                                      "linebreak": True, "validOnly": True}))
        except Exception as exc:
            self.last_error = f"subscribe failed: {exc}"

    def on_message(self, text: str) -> None:
        for line in str(text).splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            t = parse_event(msg)
            if not t:
                continue
            with self._lock:
                cur = self._ticks.setdefault(t["symbol"], {})
                cur.update({k: v for k, v in t.items() if v is not None})
                self.version += 1
            self.last_tick_at = time.time()

    def _on_open(self, ws) -> None:
        self.connected = True
        self.last_error = None
        self._send_subscription()

    def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                self._sid = self._session_fn()
                if self._ws_factory is not None:
                    ws = self._ws_factory(self)
                else:
                    import websocket

                    ws = websocket.WebSocketApp(
                        WS_URL, on_open=self._on_open, on_message=lambda w, m: self.on_message(m),
                        on_error=lambda w, e: setattr(self, "last_error", str(e)[:300]),
                        on_close=lambda w, *a: setattr(self, "connected", False))
                self._ws = ws
                ws.run_forever(ping_interval=20, ping_timeout=10) if self._ws_factory is None else ws.run_forever()
                backoff = 2.0 if self.last_tick_at else backoff
            except Exception as exc:
                self.last_error = str(exc)[:300]
                log.warning("Tradier stream error: %s", exc)
            self.connected = False
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 60.0)
