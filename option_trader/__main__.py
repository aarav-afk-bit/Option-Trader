"""Run the Option Trader server: ``python -m option_trader``."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Option Trader web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    args = parser.parse_args()
    uvicorn.run("option_trader.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
