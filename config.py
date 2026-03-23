"""
config.py — Central configuration for the Kalshi trading bot.
Edit the values here to tune bot behavior without touching any other file.
"""

# ── Trading controls ──────────────────────────────────────────────────────────

# When True, the bot logs what it *would* do but never sends real orders.
# Set to False only when you're ready to trade live.
DRY_RUN = True

# Maximum number of contracts per order.
MAX_POSITION_SIZE = 10

# Maximum dollar amount at risk across all open positions.
MAX_PORTFOLIO_EXPOSURE = 500.0

# ── Market filter ─────────────────────────────────────────────────────────────

# Which Kalshi market categories (series tags) to watch.
# Leave empty to watch all active markets.
# Examples: ["POLITICS", "ECONOMICS", "SPORTS", "CRYPTO"]
WATCHED_CATEGORIES = []

# Skip markets where the last trade was older than this many seconds.
STALE_MARKET_CUTOFF_SECONDS = 3600  # 1 hour

# ── Data collection ───────────────────────────────────────────────────────────

# How often (seconds) to poll the API for fresh market data.
POLL_INTERVAL_SECONDS = 60

# SQLite database file path (created automatically if absent).
DB_PATH = "./kalshi_data.db"

# Log file path.
LOG_PATH = "./kalshi_bot.log"

# ── Signal thresholds ─────────────────────────────────────────────────────────

# Momentum signal: flag a market if YES price moved more than this fraction
# (e.g. 0.15 = 15%) within the look-back window below.
MOMENTUM_THRESHOLD = 0.15
MOMENTUM_LOOKBACK_SECONDS = 600  # 10 minutes

# Liquidity imbalance signal: flag a market if the ratio of total size on the
# thinner side to the thicker side falls below this value.
# 0.2 means the thin side has less than 20% of the depth of the thick side.
LIQUIDITY_IMBALANCE_THRESHOLD = 0.2

# Mispricing signal: flag a market when |current_price - fair_value| > this.
# Prices are expressed as fractions (0.0–1.0), so 0.20 = 20 cents.
MISPRICING_THRESHOLD = 0.20

# Default fair value used until you plug in a real model.
DEFAULT_FAIR_VALUE = 0.50

# ── Execution ─────────────────────────────────────────────────────────────────

# Kalshi REST API base URL.
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# HTTP request timeout in seconds.
REQUEST_TIMEOUT = 10
