"""
api.py — Kalshi REST API client.

Handles:
  - RSA-signed authentication (Kalshi's required auth method)
  - Market listing and order book fetching
  - Position and order management
  - Rate-limit-aware request helpers

Kalshi auth flow (API v2):
  Every request needs three headers:
    KALSHI-ACCESS-KEY        — your API Key ID from Account Settings
    KALSHI-ACCESS-TIMESTAMP  — milliseconds since Unix epoch (string)
    KALSHI-ACCESS-SIGNATURE  — base64( RSA-SHA256-sign(private_key,
                                         timestamp_ms_str + METHOD + /path) )
"""

import base64
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

import config

load_dotenv()
logger = logging.getLogger(__name__)


class KalshiAPIError(Exception):
    """Raised when the Kalshi API returns an unexpected response."""


class KalshiClient:
    """
    Thin wrapper around the Kalshi Trade API v2.

    Usage:
        client = KalshiClient()
        markets = client.get_markets()
    """

    def __init__(self) -> None:
        self.base_url = config.KALSHI_API_BASE
        self.api_key_id = os.getenv("KALSHI_API_KEY_ID")
        private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private_key.pem")

        if not self.api_key_id:
            raise EnvironmentError(
                "KALSHI_API_KEY_ID not set. Check your .env file."
            )

        # Load RSA private key used to sign every request.
        key_path = Path(private_key_path)
        if not key_path.exists():
            raise FileNotFoundError(
                f"Private key not found at {key_path}. "
                "Generate one with: openssl genrsa -out kalshi_private_key.pem 2048 "
                "and upload the public key to Kalshi's API settings."
            )
        with open(key_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(f.read(), password=None)

        self.session = requests.Session()
        logger.info("KalshiClient initialised (key_id=%s)", self.api_key_id)

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        """
        Produce the KALSHI-ACCESS-SIGNATURE header value.

        Kalshi expects: base64( RSA-SHA256-sign(timestamp_ms + METHOD + /path) )
        The path must start with '/' and must NOT include the base URL or query string.
        """
        message = (timestamp_ms + method.upper() + path).encode()
        signature = self.private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(signature).decode()

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Return the three auth headers required by every Kalshi request."""
        timestamp_ms = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": self._sign(timestamp_ms, method, path),
            "Content-Type": "application/json",
        }

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> Any:
        """Authenticated GET; returns parsed JSON."""
        url = self.base_url + path
        headers = self._auth_headers("GET", path)
        try:
            resp = self.session.get(
                url, headers=headers, params=params,
                timeout=config.REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            logger.error("GET %s failed: %s — %s", path, exc, exc.response.text)
            raise KalshiAPIError(str(exc)) from exc
        except requests.RequestException as exc:
            logger.error("GET %s network error: %s", path, exc)
            raise KalshiAPIError(str(exc)) from exc

    def _post(self, path: str, body: dict) -> Any:
        """Authenticated POST; returns parsed JSON."""
        url = self.base_url + path
        headers = self._auth_headers("POST", path)
        try:
            resp = self.session.post(
                url, headers=headers, json=body,
                timeout=config.REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            logger.error("POST %s failed: %s — %s", path, exc, exc.response.text)
            raise KalshiAPIError(str(exc)) from exc
        except requests.RequestException as exc:
            logger.error("POST %s network error: %s", path, exc)
            raise KalshiAPIError(str(exc)) from exc

    # ── Market endpoints ──────────────────────────────────────────────────────

    def get_markets(self, status: str = "open",
                    series_ticker: str | None = None,
                    limit: int = 200) -> list[dict]:
        """
        Fetch active markets.

        Returns a flat list of market dicts. Handles Kalshi's cursor-based
        pagination so callers always get the full result set.
        """
        markets: list[dict] = []
        cursor: str | None = None

        while True:
            params: dict = {"status": status, "limit": limit}
            if series_ticker:
                params["series_ticker"] = series_ticker
            if cursor:
                params["cursor"] = cursor

            data = self._get("/markets", params=params)
            batch = data.get("markets", [])
            markets.extend(batch)

            # Kalshi uses a cursor field in the response for pagination.
            cursor = data.get("cursor")
            if not cursor or len(batch) < limit:
                break

        # Optional: filter to watched categories.
        if config.WATCHED_CATEGORIES:
            markets = [
                m for m in markets
                if m.get("category") in config.WATCHED_CATEGORIES
                or m.get("series_ticker", "").split("-")[0] in config.WATCHED_CATEGORIES
            ]

        logger.debug("Fetched %d markets", len(markets))
        return markets

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
        """
        Fetch the order book for a single market.

        Returns a dict with 'yes' and 'no' keys, each a list of
        [price_cents, size] pairs sorted best-first.
        """
        data = self._get(f"/markets/{ticker}/orderbook", params={"depth": depth})
        return data.get("orderbook", {})

    def get_market(self, ticker: str) -> dict:
        """Fetch a single market's details."""
        data = self._get(f"/markets/{ticker}")
        return data.get("market", {})

    # ── Portfolio endpoints ───────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """Return all current open positions."""
        data = self._get("/portfolio/positions")
        return data.get("market_positions", [])

    def get_balance(self) -> float:
        """Return available balance in dollars."""
        data = self._get("/portfolio/balance")
        # Kalshi returns balance in cents
        return data.get("balance", 0) / 100.0

    def get_orders(self, status: str = "resting") -> list[dict]:
        """Return open orders (status: resting | filled | canceled)."""
        data = self._get("/portfolio/orders", params={"status": status})
        return data.get("orders", [])

    # ── Order placement ───────────────────────────────────────────────────────

    def place_order(self, ticker: str, side: str, action: str,
                    contracts: int, limit_price_cents: int) -> dict:
        """
        Place a limit order.

        Args:
            ticker:             Market ticker (e.g. "PRES-2024-DEM")
            side:               "yes" or "no"
            action:             "buy" or "sell"
            contracts:          Number of contracts
            limit_price_cents:  Limit price in cents (1–99)

        Returns:
            The order object from Kalshi.
        """
        body = {
            "ticker": ticker,
            "client_order_id": f"bot_{int(time.time() * 1000)}",
            "type": "limit",
            "action": action,
            "side": side,
            "count": contracts,
            "yes_price": limit_price_cents if side == "yes" else (100 - limit_price_cents),
        }
        data = self._post("/portfolio/orders", body)
        return data.get("order", {})

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order by its Kalshi order ID."""
        path = f"/portfolio/orders/{order_id}"
        url = self.base_url + path
        headers = self._auth_headers("DELETE", path)
        try:
            resp = self.session.delete(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("DELETE %s failed: %s", path, exc)
            raise KalshiAPIError(str(exc)) from exc
