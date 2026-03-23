'use strict';
require('dotenv').config();

module.exports = {
  // ── API ──────────────────────────────────────────────────────────────────
  KALSHI_API_BASE: 'https://api.elections.kalshi.com/trade-api/v2',
  KALSHI_API_KEY_ID: process.env.KALSHI_API_KEY_ID || '',
  KALSHI_PRIVATE_KEY_PATH: process.env.KALSHI_PRIVATE_KEY_PATH || './kalshi_private_key.pem',
  REQUEST_TIMEOUT: 10_000, // ms

  // ── Mode ─────────────────────────────────────────────────────────────────
  // Set DRY_RUN=false in .env to place real orders
  DRY_RUN: false,

  // ── Polling ───────────────────────────────────────────────────────────────
  POLL_INTERVAL_SECONDS: 60,

  // ── Market filter ─────────────────────────────────────────────────────────
  // Empty array = watch all categories
  WATCHED_CATEGORIES: [],
  STALE_MARKET_CUTOFF_SECONDS: 3600,

  // ── Position limits ───────────────────────────────────────────────────────
  MAX_POSITION_SIZE: 10,       // max contracts per order
  MAX_PORTFOLIO_EXPOSURE: 500, // max dollars at risk across all positions

  // ── Signal thresholds ────────────────────────────────────────────────────
  MOMENTUM_THRESHOLD: 0.15,         // 15% price move
  MOMENTUM_LOOKBACK_SECONDS: 600,   // 10 minutes
  LIQUIDITY_IMBALANCE_THRESHOLD: 0.2, // thin side < 20% of thick side
  MISPRICING_THRESHOLD: 0.20,       // 20 cents from fair value
  DEFAULT_FAIR_VALUE: 0.50,         // default fair value (50 cents)

  // ── Storage ───────────────────────────────────────────────────────────────
  DB_PATH: './kalshi_data.db',
  LOG_PATH: './kalshi_bot.log',
};
