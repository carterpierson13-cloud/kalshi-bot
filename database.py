"""
database.py — SQLite persistence layer.

Three tables:
  price_history — one row per market snapshot (polled every 60 s)
  signals       — every signal the bot fires
  orders        — every order the bot places (real or simulated)
"""

import sqlite3
import logging
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """Return a connection with row_factory set for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't already exist."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS price_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker      TEXT    NOT NULL,
                title       TEXT,
                yes_bid     REAL,
                yes_ask     REAL,
                yes_price   REAL,      -- last trade price
                volume      INTEGER,
                recorded_at TEXT    NOT NULL   -- ISO-8601 UTC
            );

            CREATE INDEX IF NOT EXISTS idx_ph_ticker_time
                ON price_history (ticker, recorded_at);

            CREATE TABLE IF NOT EXISTS signals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker        TEXT    NOT NULL,
                title         TEXT,
                signal_type   TEXT    NOT NULL,  -- MOMENTUM | LIQUIDITY | MISPRICING
                current_price REAL,
                fair_value    REAL,
                recommended   TEXT,              -- BUY_YES | BUY_NO | NONE
                detail        TEXT,
                triggered_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sig_ticker_time
                ON signals (ticker, triggered_at);

            CREATE TABLE IF NOT EXISTS orders (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker        TEXT    NOT NULL,
                side          TEXT    NOT NULL,  -- YES | NO
                action        TEXT    NOT NULL,  -- buy | sell
                contracts     INTEGER NOT NULL,
                limit_price   REAL    NOT NULL,
                is_dry_run    INTEGER NOT NULL,  -- 1 = simulated
                status        TEXT,              -- filled | rejected | pending
                kalshi_order_id TEXT,
                placed_at     TEXT    NOT NULL
            );
        """)
    logger.info("Database initialised at %s", DB_PATH)


# ── Writes ────────────────────────────────────────────────────────────────────

def insert_price_snapshot(ticker: str, title: str, yes_bid: float,
                           yes_ask: float, yes_price: float, volume: int) -> None:
    """Store a single market price snapshot."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO price_history
               (ticker, title, yes_bid, yes_ask, yes_price, volume, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker, title, yes_bid, yes_ask, yes_price, volume,
             datetime.utcnow().isoformat())
        )


def insert_signal(ticker: str, title: str, signal_type: str,
                  current_price: float, fair_value: float,
                  recommended: str, detail: str) -> None:
    """Persist a triggered signal."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO signals
               (ticker, title, signal_type, current_price, fair_value,
                recommended, detail, triggered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, title, signal_type, current_price, fair_value,
             recommended, detail, datetime.utcnow().isoformat())
        )


def insert_order(ticker: str, side: str, action: str, contracts: int,
                 limit_price: float, is_dry_run: bool,
                 status: str, kalshi_order_id: str | None) -> None:
    """Record a placed (or simulated) order."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO orders
               (ticker, side, action, contracts, limit_price,
                is_dry_run, status, kalshi_order_id, placed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, side, action, contracts, limit_price,
             int(is_dry_run), status, kalshi_order_id,
             datetime.utcnow().isoformat())
        )


# ── Reads ─────────────────────────────────────────────────────────────────────

def get_price_history(ticker: str, since_iso: str) -> list[dict]:
    """Return price rows for a ticker after a given ISO timestamp."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT yes_price, yes_bid, yes_ask, recorded_at
               FROM price_history
               WHERE ticker = ? AND recorded_at >= ?
               ORDER BY recorded_at ASC""",
            (ticker, since_iso)
        ).fetchall()
    return [dict(r) for r in rows]


def get_signals_today() -> list[dict]:
    """Return all signals triggered since midnight UTC today."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT ticker, title, signal_type, current_price,
                      recommended, detail, triggered_at
               FROM signals
               WHERE triggered_at >= ?
               ORDER BY triggered_at DESC""",
            (today,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_open_orders() -> list[dict]:
    """Return orders that are still pending (not yet filled or rejected)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM orders
               WHERE status = 'pending'
               ORDER BY placed_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_orders() -> list[dict]:
    """Return every order ever recorded."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY placed_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
