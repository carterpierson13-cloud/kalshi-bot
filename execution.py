"""
execution.py — Order execution layer.

Responsibilities:
  - Decide whether a signal warrants placing an order (size/exposure checks)
  - In DRY RUN mode: log what it would do, record to DB as simulated
  - In LIVE mode: call the Kalshi API to place a real limit order
  - Record every action (real or simulated) to the database

The execution layer deliberately knows nothing about signal logic — it just
receives a Signal and decides whether and how to act on it.
"""

import logging
from typing import Optional

import config
import database
from api import KalshiClient, KalshiAPIError
from signals import Signal

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    Wraps order placement logic.

    Usage:
        engine = ExecutionEngine(client)
        engine.handle_signal(signal, orderbook)
    """

    def __init__(self, client: KalshiClient) -> None:
        self.client = client
        self._open_positions: dict[str, int] = {}  # ticker → net contracts held

    # ── Public interface ──────────────────────────────────────────────────────

    def handle_signal(self, signal: Signal, orderbook: dict) -> None:
        """
        Process a single signal.

        Determines the best limit price from the order book, checks position
        limits, and either simulates or places the order.
        """
        ticker     = signal.ticker
        recommended = signal.recommended  # BUY_YES | BUY_NO | NONE

        if recommended == "NONE":
            logger.info("[EXEC] %s — no action recommended", ticker)
            return

        side   = "yes" if recommended == "BUY_YES" else "no"
        action = "buy"  # we only enter positions for now; selling is a future extension

        # Pick a limit price: we use the best ask (willing to pay up to that).
        limit_price_cents = self._best_ask_cents(orderbook, side)
        if limit_price_cents is None:
            logger.warning("[EXEC] %s — no ask available on %s side, skipping", ticker, side)
            return

        # Cap at configured max size.
        size = self._determine_size(ticker, limit_price_cents)
        if size <= 0:
            logger.info("[EXEC] %s — position limit reached, skipping", ticker)
            return

        self._execute(ticker, side, action, size, limit_price_cents, signal)

    def refresh_positions(self) -> None:
        """
        Sync open positions from the Kalshi API.
        Called once per poll cycle so the engine has fresh exposure data.
        """
        try:
            positions = self.client.get_positions()
            self._open_positions = {
                p["ticker"]: p.get("position", 0)
                for p in positions
            }
            logger.debug("Refreshed %d positions", len(self._open_positions))
        except KalshiAPIError as exc:
            logger.warning("Could not refresh positions: %s", exc)

    def get_positions_summary(self) -> list[dict]:
        """Return open positions with estimated P&L (best-effort)."""
        try:
            raw = self.client.get_positions()
        except KalshiAPIError:
            return []

        summary = []
        for pos in raw:
            ticker    = pos.get("ticker", "")
            contracts = pos.get("position", 0)
            avg_price = pos.get("market_exposure", 0) / max(abs(contracts), 1) / 100.0
            # realised_pnl from Kalshi is in cents
            realised  = pos.get("realized_pnl", 0) / 100.0
            summary.append({
                "ticker":    ticker,
                "contracts": contracts,
                "avg_price": avg_price,
                "realised_pnl": realised,
            })
        return summary

    # ── Private helpers ───────────────────────────────────────────────────────

    def _execute(self, ticker: str, side: str, action: str,
                 contracts: int, limit_price_cents: int, signal: Signal) -> None:
        """Route to dry-run or live execution."""
        limit_price = limit_price_cents / 100.0

        if config.DRY_RUN:
            self._dry_run(ticker, side, action, contracts, limit_price, signal)
        else:
            self._live_order(ticker, side, action, contracts, limit_price_cents,
                             limit_price, signal)

    def _dry_run(self, ticker: str, side: str, action: str,
                 contracts: int, limit_price: float, signal: Signal) -> None:
        """Log and record a simulated order — no real API call."""
        logger.info(
            "[DRY RUN] Would %s %d %s contracts on %s at %.2f | signal=%s | %s",
            action, contracts, side.upper(), ticker,
            limit_price, signal.signal_type, signal.detail
        )
        database.insert_order(
            ticker=ticker,
            side=side,
            action=action,
            contracts=contracts,
            limit_price=limit_price,
            is_dry_run=True,
            status="simulated",
            kalshi_order_id=None,
        )

    def _live_order(self, ticker: str, side: str, action: str,
                    contracts: int, limit_price_cents: int,
                    limit_price: float, signal: Signal) -> None:
        """Place a real limit order via the Kalshi API."""
        logger.info(
            "[LIVE] Placing %s %d %s contracts on %s at %.2f | signal=%s",
            action, contracts, side.upper(), ticker,
            limit_price, signal.signal_type
        )
        try:
            order = self.client.place_order(
                ticker=ticker,
                side=side,
                action=action,
                contracts=contracts,
                limit_price_cents=limit_price_cents,
            )
            order_id = order.get("order_id") or order.get("id")
            status   = order.get("status", "pending")
            logger.info("[LIVE] Order placed: id=%s status=%s", order_id, status)

            database.insert_order(
                ticker=ticker,
                side=side,
                action=action,
                contracts=contracts,
                limit_price=limit_price,
                is_dry_run=False,
                status=status,
                kalshi_order_id=order_id,
            )
            # Track locally too
            self._open_positions[ticker] = (
                self._open_positions.get(ticker, 0) + contracts
            )
        except KalshiAPIError as exc:
            logger.error("[LIVE] Order failed for %s: %s", ticker, exc)
            database.insert_order(
                ticker=ticker,
                side=side,
                action=action,
                contracts=contracts,
                limit_price=limit_price,
                is_dry_run=False,
                status="rejected",
                kalshi_order_id=None,
            )

    def _determine_size(self, ticker: str, limit_price_cents: int) -> int:
        """
        Return how many contracts to buy, respecting position limits.

        Simple approach: use MAX_POSITION_SIZE unless we already hold
        close to the limit on this ticker.
        """
        already_held = abs(self._open_positions.get(ticker, 0))
        remaining    = max(0, config.MAX_POSITION_SIZE - already_held)
        return min(remaining, config.MAX_POSITION_SIZE)

    @staticmethod
    def _best_ask_cents(orderbook: dict, side: str) -> Optional[int]:
        """
        Find the best (lowest) ask price in cents for the given side.

        Kalshi levels are [price_cents, size]; the YES ask is the lowest-priced
        level someone is willing to sell YES contracts.
        """
        levels = orderbook.get(side, [])
        if not levels:
            return None
        try:
            return int(levels[0][0])  # first level = best ask
        except (IndexError, TypeError, ValueError):
            return None
