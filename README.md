# Option Trader

An options-market analysis tool and strategy builder for NSE (and US) options that works
**only with real market data**, pulled live from **NSE**, **Angel One SmartAPI** or **Yahoo Finance**.

- **Live option chain**: OI (with bars), change in OI, volume, IV, delta, bid/ask/LTP, ATM/ITM
  highlighting, lot size, data source and fetch/exchange timestamps. Auto-refreshes every 30 s.
- **Signals & ideas: what to buy/sell.** Combines option-chain signals (PCR, max pain, OI
  support/resistance, OI build-up, IV skew) with technical analysis of the underlying (EMA 20/50,
  200-day SMA, RSI, MACD, Bollinger, Supertrend, ADX, historical vs implied volatility) into a
  market view, then picks matching strategies, places them on the live chain and ranks them by
  probability of profit, reward:risk and fit. Output is explicit, e.g.
  `BUY 1 lot (75 qty) NIFTY 2026-10-27 25000 CE @ 145.00`.
- **Strategy builder with payoff graph.** 29 built-in strategies (spreads, straddles,
  strangles, iron condor/butterfly, butterflies, ratio spreads, backspreads, jade lizard,
  synthetics, collars, calendars...). Load any of them onto the current chain, change lots,
  width or ATM shift, edit every leg (side, type, expiry, strike, lots, entry price), and see
  P&L at expiry, today and at a target date (with an IV-change slider). Breakevens, max
  profit/loss, probability of profit and position Greeks update as you edit.
- **Save your own strategies.** Saved strategies are stored strike-relative (offsets from
  ATM), so you can immediately put them on any symbol, expiry or a later chain.

## Real data only

The option chain is never simulated, sampled or filled with placeholder values:

- Each chain records the source it came from and when it was fetched; chains that are empty,
  unpriced or have no source are rejected (`OptionChain.validate`).
- If no source can be reached (or the machine is offline) the API returns HTTP 503 with the
  reason from every source, and the UI clears the table and shows that reason instead of
  stale or invented numbers.
- IV/Greeks that a source doesn't publish are *derived* from that source's live prices with
  Black-Scholes and labelled as such; missing prices stay blank.
- Strategy legs are priced from the live chain; a leg with no live quote is refused.

The JSON/CSV files in `tests/fixtures` mirror the format of real responses so the parsers can be
unit-tested offline. Only the test suite reads them.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # optional: Angel One credentials, provider order, risk-free rate
python -m option_trader     # then open http://127.0.0.1:8000
```

Run the tests with `pytest`.

## Data sources

Sources are tried in the order set by `OPTION_TRADER_PROVIDERS` (default `nse,angelone,yahoo`).
The first that returns a valid chain is used. You can also pin a source in the UI.

| Source | What it provides | Setup |
| --- | --- | --- |
| **NSE** (`nseindia.com`) | Option chains for NSE indices and F&O stocks, lot sizes | None. NSE blocks some cloud/VPN IPs and rate-limits; run from a normal home/office connection. |
| **Angel One SmartAPI** | Live quotes with market depth, OI and Angel's option Greeks | Create an app at <https://smartapi.angelbroking.com/>, enable TOTP, then set `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN` and `ANGEL_TOTP_SECRET` in `.env`. |
| **Yahoo Finance** | Daily price history for technicals (NSE and US); option chains for US symbols (e.g. `US:AAPL`, `SPY`) | None. Yahoo does not publish NSE option chains, and its quotes can be delayed. |

Symbols: `NIFTY`, `BANKNIFTY`, `FINNIFTY`, `MIDCPNIFTY`, or any F&O stock such as `RELIANCE`.
For US options use `US:<TICKER>`.

## Configuration (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPTION_TRADER_PROVIDERS` | `nse,angelone,yahoo` | Source order |
| `OPTION_TRADER_RISK_FREE_RATE` | `0.065` | Annual rate for Black-Scholes |
| `OPTION_TRADER_CACHE_TTL` | `30` | Seconds a fetched chain is reused |
| `OPTION_TRADER_DATA_DIR` | `~/.option_trader` | Saved strategies and Angel instrument cache |

## HTTP API

| Method & path | Purpose |
| --- | --- |
| `GET /api/status` | Online state and per-source health |
| `GET /api/expiries?symbol=` | Listed expiries |
| `GET /api/chain?symbol=&expiry=&source=&refresh=` | Live option chain |
| `GET /api/history?symbol=` | Daily candles |
| `GET /api/analytics/chain?symbol=` | PCR, max pain, OI levels, build-up, expected move, skew, signals |
| `GET /api/analytics/technicals?symbol=` | Indicator values, signals, IV vs HV |
| `GET /api/analytics/recommendations?symbol=&lots=` | Market view and ranked trade ideas |
| `GET /api/strategy/templates` | Built-in and saved strategies |
| `POST /api/strategy/templates/{id}/build` | Place a strategy on the live chain and analyse it |
| `POST /api/strategy/analyze` | Analyse custom legs (blank premium = live price) |
| `GET/POST /api/strategy/saved`, `POST /api/strategy/saved/from-legs`, `DELETE /api/strategy/saved/{id}` | Manage saved strategies |

Interactive docs are at `/docs` while the server runs.

## Project layout

```
option_trader/
  providers/   nse.py, angelone.py, yahoo.py, manager.py (fallback, cache, online check)
  pricing/     Black-Scholes, Greeks, implied volatility
  strategy/    engine.py (payoff/risk), templates.py (built-ins), store.py (saved strategies)
  analytics/   chain.py, technicals.py, recommender.py
  api/         FastAPI routers
  web/static/  single-page UI (Plotly bundled under vendor/)
```

## Disclaimer

This tool produces rule-based analysis from market data. It is not investment advice. Options
can lose value quickly; check liquidity, margin requirements and your own risk limits before
trading.
