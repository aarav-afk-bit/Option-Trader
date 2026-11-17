# Option Trader

An analysis tool, valuation lab and strategy builder for **US listed options** (stocks, ETFs and
indices such as SPY, QQQ, AAPL, SPX, NDX, RUT) that works **only with real market data**, pulled
live from **Tradier**, **Cboe** or **Yahoo Finance**, priced with the **US Treasury yield curve**.

- **Live option chain**: OI (with bars), day-over-day change in OI, volume, IV, Greeks,
  bid/ask/last, spread %, ATM/ITM highlighting, contract multiplier, exercise style, data source
  and exchange timestamps. Real-time ticks with a Tradier token; Cboe's free quotes otherwise.
- **Valuation lab (key 0).** Price any live contract with 11 models side by side and see which
  land inside the live bid/ask: Black-Scholes-Merton, Black-76, Bachelier, Merton jump-diffusion,
  Heston (calibrated to the expiry's smile), CRR binomial tree, Barone-Adesi-Whaley,
  Bjerksund-Stensland 2002, Monte Carlo (antithetic + control variate, with 95% CI),
  Longstaff-Schwartz and Heston Monte Carlo. Shows early-exercise value and American IV for
  equity options, the Heston smile fit, and tree/Monte Carlo convergence charts.
- **Earnings & dividends.** Upcoming earnings (before open / after close) with the move the
  options price in for the report versus past report-day moves, IV-crush warnings, ex-dividend
  dates and early-assignment risk on short in-the-money calls.
- **Positioning.** Dealer gamma exposure by strike with the zero-gamma level and call/put walls,
  unusual options activity ranked by premium, and put-call ratios read against US norms.
- **Volatility.** Volatility surface across expiries, 25-delta risk reversal and butterfly,
  IV30, IV rank/percentile (from a daily history the app records) and IV versus realised vol.
- **Signals & ideas: what to buy/sell.** Option-chain signals plus technical analysis of the
  underlying (EMA 20/50, 200-day SMA, RSI, MACD, Bollinger, Supertrend, ADX) combine into a
  market view; matching strategies are placed on the live chain and ranked. Output is explicit,
  e.g. `BUY 1 contract (x100) SPY 2026-11-20 600 call @ 6.88`.
- **Strategy builder with payoff graph.** 29 built-in strategies (verticals, straddles,
  strangles, iron condors/butterflies, ratio spreads, jade lizard, collars, calendars...). Edit
  every leg and see P&L at expiry, today and at a target date with an IV-change slider.
- **⚡ Quick trade** (three plain questions → an optimised trade explained in plain sentences) and
  **✨ Optimise** (step-by-step search for the best blend of win rate and expected profit on live
  prices, net of fees, judged by the distribution the option chain itself implies).
- **Net of real US fees**: commission per contract, ORF, OCC clearing, FINRA TAF and the SEC fee,
  all editable in Settings.
- **Data you can trust**: data-quality score per chain (crossed books, stale trades, parity
  breaks and IV outliers flagged and excluded), cross-check against a second source, NYSE
  holiday/early-close calendar, 4:00 pm ET expiries and AM-settled index roots handled.
- **Watchlist, screener, paper portfolio, alerts, charts**, a settings page with a connection
  doctor, a futuristic HUD look that stays easy to read, dark mode, keyboard shortcuts (1-9, 0,
  /, R, O, Q, T, ?) and a Ctrl+K command palette.

## Real data only

The option chain is never simulated, sampled or filled with placeholder values:

- Each chain records the source it came from and when the exchange produced it; chains that are
  empty, unpriced or have no source are rejected (`OptionChain.validate`).
- If no source can be reached the API returns HTTP 503 with every source's reason and the UI shows
  that reason instead of stale or invented numbers.
- IV and Greeks are solved from each contract's live price on the put-call-parity forward with
  the term-matched Treasury rate; missing prices stay blank.
- Earnings and ex-dividend dates are shown only when announced; IV rank and OI change are shown
  only once the app has recorded enough real history, and say how much exists until then.

The JSON/CSV files in `tests/fixtures` mirror the format of real responses so the parsers can be
unit-tested offline. Only the test suite reads them.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m option_trader     # then open http://127.0.0.1:8000
```

Or use the one-step launchers, which create the virtualenv on first run and open your browser:

- **Windows:** double-click `run.bat`
- **macOS / Linux:** `./run.sh`
- **Docker:** `docker build -t option-trader . && docker run -p 8000:8000 -v option-trader-data:/data option-trader`

Useful flags: `python -m option_trader --open --port 8080 --log-level debug`. Run the tests with
`pytest`.

## Real-time prices

Without any setup the app uses Cboe's free delayed quotes (about 15 minutes behind) and labels
them as such. For **real-time, tick-by-tick prices**, paste a **Tradier** API token in Settings
(a Tradier brokerage account includes one; Dashboard → API Access). The app then streams quotes
and trades for every contract in the chain you're viewing over Tradier's WebSocket, recomputes
IV and Greeks from each tick, pushes them to the browser and shows **LIVE** in the header. The
token is stored only in a private file in the data directory and never shown in full again.

## Data sources

Sources are tried in the order set in Settings (default `tradier,cboe,yahoo`); the first that
returns a valid, fresh chain is used. You can also pin a source in the UI.

| Source | What it provides | Setup |
| --- | --- | --- |
| **Tradier** | Real-time chains with Greeks, quotes, history, streaming ticks | API token from a Tradier account (sandbox tokens work but are delayed) |
| **Cboe** (`cdn.cboe.com`) | Full chains with Greeks and IV30 for US stocks, ETFs and indices; daily history | None (quotes delayed about 15 minutes) |
| **Yahoo Finance** | Chains, price history, earnings and dividend calendars | None (delayed) |
| **US Treasury** | Daily par yield curve for term-matched risk-free rates | None |

Symbols: any US optionable ticker (`AAPL`, `SPY`, `BRK.B`) and indices `SPX`, `NDX`, `RUT`,
`VIX`, `XSP`, `DJX` (`^GSPC`, `$SPX` and weekly roots like `SPXW` are understood too).

## Configuration

Everything can be changed on the Settings page; `.env` (see `.env.example`) sets defaults.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPTION_TRADER_PROVIDERS` | `tradier,cboe,yahoo` | Source order |
| `OPTION_TRADER_RISK_FREE_RATE` | `0.04` | Fallback rate if the Treasury curve is unavailable |
| `OPTION_TRADER_CACHE_TTL` | `30` | Seconds a fetched chain is reused |
| `OPTION_TRADER_STALE_SECONDS` | `180` | During market hours, older prices make the app try the next source |
| `OPTION_TRADER_COMMISSION_PER_CONTRACT` | `0.65` | Broker commission (ORF, OCC, FINRA TAF and SEC fee are separate settings) |
| `OPTION_TRADER_DATA_DIR` | `~/.option_trader` | Saved strategies, portfolio, settings, OI and IV history |

## HTTP API

| Method & path | Purpose |
| --- | --- |
| `GET /api/status`, `GET /api/market` | Source health; NYSE session (open/closed, next open, early closes) |
| `GET /api/expiries?symbol=`, `GET /api/chain?symbol=&expiry=&source=` | Listed expiries; live option chain |
| `GET /api/stream?symbol=&expiry=` | Server-Sent Events with live ticks |
| `GET /api/history?symbol=`, `GET /api/indices`, `GET /api/rates`, `GET /api/verify` | Candles, index strip, Treasury curve, cross-check |
| `GET /api/valuation/contract?symbol=&strike=&kind=` | Every valuation model for one contract vs the market |
| `GET /api/valuation/smile`, `GET /api/valuation/convergence`, `GET /api/valuation/models` | Heston smile fit, tree/MC convergence, model list |
| `GET /api/analytics/chain`, `/technicals`, `/recommendations` | Signals, indicators, market view and trade ideas |
| `GET /api/analytics/events` | Earnings, implied vs past moves, dividends, assignment risk |
| `GET /api/analytics/gamma`, `/flow`, `/surface` | Dealer gamma exposure, unusual activity, vol surface & IV rank |
| `GET /api/analytics/term-structure`, `/intraday`, `/watchlist` | Term structure, intraday tracker, multi-symbol snapshot |
| `POST /api/strategy/analyze`, `/screen`, `/score`, `/optimize` | Analyse, rank, score and optimise strategies |
| `GET /api/strategy/templates`, `POST /api/strategy/templates/{id}/build`, `/api/strategy/saved...` | Built-in and saved strategies |
| `GET /api/portfolio`, `POST /api/portfolio/open`, `POST /api/portfolio/{id}/close` | Paper trading |
| `GET/POST /api/settings`, `POST /api/settings/test` | Settings and the connection doctor (changes only from this computer) |

Interactive docs are at `/docs` while the server runs.

## Project layout

```
option_trader/
  providers/   tradier.py, cboe.py, yahoo.py, manager.py (fallback, cache, freshness, live hub)
  pricing/     black_scholes.py (IV, Greeks), forward.py, valuation.py (11 models), lab.py
  analytics/   chain.py, technicals.py, recommender.py, gamma.py, flow.py, surface.py
  strategy/    engine.py (payoff/risk), templates.py, screener.py, store.py
  optimizer/   implied distribution, objective, search
  events.py, rates.py, market_hours.py, charges.py, oi_history.py, iv_history.py
  api/         FastAPI routers
  web/static/  single-page UI (Plotly bundled under vendor/)
```

## Disclaimer

This tool produces rule-based analysis and model values from market data. It is not investment
advice. Options can lose value quickly; check liquidity, assignment risk, margin requirements
and your own risk limits before trading.
