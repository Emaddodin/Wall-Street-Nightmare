"""
quant/hft/run_hft.py
=====================
Entry-point script. Run with:
    python -m quant.hft.run_hft --symbols BTC ETH --balance 500 --dry-run

Set HYPERLIQUID_API_KEY in .env for live trading.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from rich.logging import RichHandler

from .engine import EngineConfig, HFTEngine


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


async def _run(args: argparse.Namespace) -> None:
    cfg = EngineConfig(
        symbols=[s.upper() for s in args.symbols],
        balance_usdt=args.balance,
        kelly_fraction=args.kelly_fraction,
        max_leverage=args.max_leverage,
        dry_run=not args.live,
        model_path=args.model_path,
    )
    engine = HFTEngine(cfg)

    loop = asyncio.get_event_loop()

    def _shutdown(sig, _frame):
        logging.getLogger(__name__).info("Signal %s — shutting down.", sig.name)
        asyncio.ensure_future(engine.stop())

    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, _shutdown, s, None)

    try:
        await engine.run()
    finally:
        stats = engine.stats()
        print("\n=== SESSION STATS ===")
        for k, v in stats.items():
            print(f"  {k}: {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="HFT Quant Engine for Hyperliquid")
    parser.add_argument("--symbols", nargs="+", default=["BTC"], help="Trading symbols")
    parser.add_argument("--balance", type=float, default=100.0, help="Starting balance USDT")
    parser.add_argument("--kelly-fraction", type=float, default=0.25)
    parser.add_argument("--max-leverage", type=int, default=20)
    parser.add_argument("--live", action="store_true", help="Enable live trading (default: dry-run)")
    parser.add_argument("--model-path", type=str, default=None, help="Path to pre-trained CatBoost model")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
