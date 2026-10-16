# Option Trader

An options-market analysis and strategy-building tool for Indian (NSE) and other markets
that works **only with real market data** pulled live from NSE, Angel One SmartAPI or
Yahoo Finance. If no source can be reached, the app says so — it never shows made-up prices.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional: fill in Angel One credentials, provider order, etc.
python -m option_trader     # then open http://127.0.0.1:8000
```

Run the tests with `pytest`.
