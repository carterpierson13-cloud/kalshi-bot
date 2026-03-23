"""
main.py — Entry point for the Kalshi algorithmic trading bot.

Run with:
    python main.py

What happens each poll cycle (every POLL_INTERVAL_SECONDS):
  1. Fetch all active markets from Kalshi.
  2. For each market, fetch the order book and store a price snapshot in SQLite.
  3. Run signal checks (momentum, liquidity, mispricing) on every market.
  4. For any signal that fires, route to the execution engine.
  5. Refresh the terminal dashboard.

Press Ctrl-C to stop cleanly.
"""

import logging
import signal
import sys
import time
from datetime import datetime

import config
import database
from api import KalshiClient, KalshiAPIError
from dashboard import Dashboard
from execution import ExecutionEngine
from logger_setup import setup_logging
from signals import evaluate_market

# ── Initialisation ─────────────────────────────────────────────────────────────

setup_logging()
logger = logging.getLogger(__name__)


def run_poll_cycle(client: KalshiClient, engine: ExecutionEngine,
                   dashboard: Dashboard) -> str | None:
    """
    Execute one complete poll cycle.

    Returns an error string if something went wrong (for display in the
    dashboard), or None on success.
    """
    try:
        # ── 1. Fetch markets ───────────────────────────────────────────────
        markets = client.get_markets()
        logger.info("Poll cycle: %d markets fetched", len(markets))

        all_signals = []

        for market in markets:
            ticker = market.get("ticker", "")
            title  = market.get("title", ticker)

            # ── 2. Fetch order book ────────────────────────────────────────
            try:
                orderbook = client.get_orderbook(ticker)
            except KalshiAPIError as exc:
                logger.warning("Could not fetch orderbook for %s: %s", ticker, exc)
                orderbook = {}

            # Derive mid-price for storage (Kalshi prices are in cents).
            yes_bid_cents = (orderbook.get("yes") or [[None]])[-1][0]  # best bid
            yes_ask_cents = (orderbook.get("yes") or [[None]])[0][0]   # best ask

            yes_bid   = (yes_bid_cents / 100.0) if yes_bid_cents else None
            yes_ask   = (yes_ask_cents / 100.0) if yes_ask_cents else None
            yes_price = ((yes_bid + yes_ask) / 2.0) if (yes_bid and yes_ask) else (
                (market.get("last_price") or 50) / 100.0
            )

            # ── 3. Persist price snapshot ──────────────────────────────────
            database.insert_price_snapshot(
                ticker=ticker,
                title=title,
                yes_bid=yes_bid or 0.0,
                yes_ask=yes_ask or 0.0,
                yes_price=yes_price,
                volume=market.get("volume", 0) or 0,
            )

            # Attach derived prices back onto the market dict so the
            # dashboard can display them without a second lookup.
            market["yes_bid"] = yes_bid_cents
            market["yes_ask"] = yes_ask_cents

            # ── 4. Run signal checks ───────────────────────────────────────
            fired = evaluate_market(market, orderbook)
            all_signals.extend(fired)

            # ── 5. Execute on signals ──────────────────────────────────────
            for sig in fired:
                engine.handle_signal(sig, orderbook)
                dashboard.print_signal(sig)

        # Refresh position data from the API once per cycle.
        engine.refresh_positions()
        positions = engine.get_positions_summary()

        # ── 6. Refresh dashboard ───────────────────────────────────────────
        dashboard.update(markets, positions)

        logger.info(
            "Poll cycle complete: %d markets, %d signals fired",
            len(markets), len(all_signals)
        )
        return None  # no error

    except KalshiAPIError as exc:
        error_msg = f"API error: {exc}"
        logger.error(error_msg)
        return error_msg

    except Exception as exc:
        error_msg = f"Unexpected error: {exc}"
        logger.exception(error_msg)
        return error_msg


def main() -> None:
    logger.info("=" * 60)
    logger.info("Kalshi Bot starting up — %s", datetime.utcnow().isoformat())
    logger.info("DRY_RUN=%s, MAX_POSITION_SIZE=%d", config.DRY_RUN, config.MAX_POSITION_SIZE)
    logger.info("=" * 60)

    # ── One-time setup ─────────────────────────────────────────────────────
    database.init_db()

    client    = KalshiClient()
    engine    = ExecutionEngine(client)
    dashboard = Dashboard()

    # Handle Ctrl-C gracefully.
    shutdown_requested = False

    def _handle_shutdown(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True

    signal.signal(signal.SIGINT,  _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    # ── Print startup banner ───────────────────────────────────────────────
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    con = Console()
    mode_label = "DRY RUN (no real orders)" if config.DRY_RUN else "[bold red]LIVE TRADING[/]"
    con.print(Panel(
        f"Kalshi Algo Bot\n"
        f"Mode: {mode_label}\n"
        f"Poll interval: {config.POLL_INTERVAL_SECONDS}s\n"
        f"Watching: {config.WATCHED_CATEGORIES or 'all categories'}\n"
        f"Press Ctrl-C to stop.",
        title="[bold blue]Starting[/]",
        border_style="blue",
    ))
    time.sleep(1)

    # ── Main loop ──────────────────────────────────────────────────────────
    # We use dashboard.start_live() as a context manager so Rich can own
    # the full-screen terminal display. If you prefer plain scrolling output,
    # remove the `with` block and just call dashboard.update(...) directly.
    with dashboard.start_live():
        while not shutdown_requested:
            error = run_poll_cycle(client, engine, dashboard)

            # Wait for the next poll interval, checking for shutdown every second.
            for _ in range(config.POLL_INTERVAL_SECONDS):
                if shutdown_requested:
                    break
                time.sleep(1)

    logger.info("Kalshi Bot stopped cleanly.")
    con.print("[bold green]Bot stopped.[/]")


if __name__ == "__main__":
    main()
