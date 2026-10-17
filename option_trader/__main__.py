"""Run the Option Trader server: ``python -m option_trader``."""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Option Trader web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    parser.add_argument("--open", action="store_true", help="Open the app in your browser")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.open:
        url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run("option_trader.app:app", host=args.host, port=args.port, reload=args.reload,
                log_level=args.log_level)


if __name__ == "__main__":
    main()
