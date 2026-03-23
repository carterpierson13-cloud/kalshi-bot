'use strict';
const Database = require('better-sqlite3');
const config = require('./config');
const logger = require('./logger');

let db;

function getDb() {
  if (!db) {
    db = new Database(config.DB_PATH);
    db.exec('PRAGMA journal_mode = WAL;');
  }
  return db;
}

function initDb() {
  const d = getDb();

  d.exec(`
    CREATE TABLE IF NOT EXISTS price_history (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      ticker      TEXT NOT NULL,
      title       TEXT,
      yes_bid     REAL,
      yes_ask     REAL,
      yes_price   REAL,
      volume      INTEGER,
      recorded_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_price_history_ticker_time
      ON price_history (ticker, recorded_at);

    CREATE TABLE IF NOT EXISTS signals (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      ticker        TEXT NOT NULL,
      title         TEXT,
      signal_type   TEXT NOT NULL,
      current_price REAL,
      fair_value    REAL,
      recommended   TEXT,
      detail        TEXT,
      triggered_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS orders (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      ticker          TEXT NOT NULL,
      side            TEXT,
      action          TEXT,
      contracts       INTEGER,
      limit_price     REAL,
      is_dry_run      INTEGER DEFAULT 1,
      status          TEXT,
      kalshi_order_id TEXT,
      placed_at       TEXT NOT NULL
    );
  `);

  logger.info('Database initialised', { path: config.DB_PATH });
}

// ── Price history ─────────────────────────────────────────────────────────────

function insertPriceSnapshot({ ticker, title, yes_bid, yes_ask, yes_price, volume }) {
  getDb()
    .prepare(`
      INSERT INTO price_history (ticker, title, yes_bid, yes_ask, yes_price, volume, recorded_at)
      VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    `)
    .run(ticker, title ?? null, yes_bid ?? null, yes_ask ?? null, yes_price ?? null, volume ?? 0);
}

function getPriceHistory(ticker, sinceIso) {
  return getDb()
    .prepare(`
      SELECT * FROM price_history
      WHERE ticker = ? AND recorded_at >= ?
      ORDER BY recorded_at ASC
    `)
    .all(ticker, sinceIso);
}

// ── Signals ───────────────────────────────────────────────────────────────────

function insertSignal({ ticker, title, signal_type, current_price, fair_value, recommended, detail }) {
  getDb()
    .prepare(`
      INSERT INTO signals (ticker, title, signal_type, current_price, fair_value, recommended, detail, triggered_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
    `)
    .run(ticker, title ?? null, signal_type, current_price ?? null, fair_value ?? null, recommended, detail ?? null);
}

function getSignalsToday() {
  return getDb()
    .prepare(`
      SELECT * FROM signals
      WHERE date(triggered_at) = date('now')
      ORDER BY triggered_at DESC
      LIMIT 50
    `)
    .all();
}

// ── Orders ────────────────────────────────────────────────────────────────────

function insertOrder({ ticker, side, action, contracts, limit_price, is_dry_run, status, kalshi_order_id }) {
  getDb()
    .prepare(`
      INSERT INTO orders (ticker, side, action, contracts, limit_price, is_dry_run, status, kalshi_order_id, placed_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    `)
    .run(ticker, side, action, contracts, limit_price, is_dry_run ? 1 : 0, status, kalshi_order_id ?? null);
}

function getOpenOrders() {
  return getDb()
    .prepare(`SELECT * FROM orders WHERE status IN ('resting','pending') ORDER BY placed_at DESC`)
    .all();
}

function getAllOrders() {
  return getDb()
    .prepare(`SELECT * FROM orders ORDER BY placed_at DESC LIMIT 100`)
    .all();
}

module.exports = {
  initDb,
  insertPriceSnapshot,
  getPriceHistory,
  insertSignal,
  getSignalsToday,
  insertOrder,
  getOpenOrders,
  getAllOrders,
};
