'use strict';
require('dotenv').config();

const config = require('./src/config');
const logger = require('./src/logger');
const db = require('./src/database');
const KalshiClient = require('./src/api');
const { evaluateMarket } = require('./src/signals');
const ExecutionEngine = require('./src/execution');
const Dashboard = require('./src/dashboard');

// ── Startup ───────────────────────────────────────────────────────────────────

function validateConfig() {
  if (!config.KALSHI_API_KEY_ID) {
    console.error('ERROR: KALSHI_API_KEY_ID is not set in your .env file.');
    process.exit(1);
  }
  if (!config.KALSHI_PRIVATE_KEY) {
    console.error('ERROR: KALSHI_PRIVATE_KEY is not set in your environment.');
    process.exit(1);
  }
}

// ── Poll cycle ────────────────────────────────────────────────────────────────

async function runPollCycle(client, engine, dashboard) {
  let cycleError = null;
  let markets = [];

  try {
    // 1. Fetch active markets
    const raw = await client.getMarkets({ status: 'open', limit: 200 });

    // 2. For each market: fetch orderbook, store snapshot, attach prices
    const processed = [];
    for (const market of raw.slice(0, 100)) { // cap at 100 to avoid rate limits
      try {
        const ob = await client.getOrderbook(market.ticker, 5);
        if (!ob) continue;

        // Derive mid-price components
        const yesLevels = ob.yes || [];
        const noLevels = ob.no || [];
        const yesBids = yesLevels.map(l => l[0]);
        const yesAsks = noLevels.map(l => 100 - l[0]); // NO ask ↔ YES ask complement

        const yesBid = yesBids.length ? Math.max(...yesBids) / 100 : null;
        const yesAsk = yesAsks.length ? Math.min(...yesAsks) / 100 : null;
        const yesPrice = yesBid != null && yesAsk != null ? (yesBid + yesAsk) / 2 : (yesBid ?? yesAsk);

        db.insertPriceSnapshot({
          ticker: market.ticker,
          title: market.title,
          yes_bid: yesBid,
          yes_ask: yesAsk,
          yes_price: yesPrice,
          volume: market.volume ?? 0,
        });

        processed.push({ ...market, yes_bid: yesBid, yes_ask: yesAsk, yes_price: yesPrice, _orderbook: ob });
      } catch (err) {
        logger.warn('Failed to process market', { ticker: market.ticker, err: err.message });
      }
    }

    markets = processed;

    // 3. Evaluate signals for each market
    for (const market of markets) {
      try {
        const signals = evaluateMarket(market, market._orderbook);
        for (const signal of signals) {
          await engine.handleSignal(signal, market._orderbook);
        }
      } catch (err) {
        logger.warn('Signal evaluation error', { ticker: market.ticker, err: err.message });
      }
    }

    // 4. Refresh positions
    await engine.refreshPositions();

  } catch (err) {
    cycleError = err.message;
    logger.error('Poll cycle error', { err: err.message });
  }

  // 5. Fetch today's signals from DB and refresh dashboard
  const signals = db.getSignalsToday();
  const positions = engine.getPositionsSummary();
  dashboard.update({ markets, signals, positions, error: cycleError });
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  validateConfig();

  logger.info('Kalshi bot starting', {
    mode: config.DRY_RUN ? 'DRY_RUN' : 'LIVE',
    interval: config.POLL_INTERVAL_SECONDS,
  });

  db.initDb();

  const client = new KalshiClient();
  const engine = new ExecutionEngine(client);
  const dashboard = new Dashboard();

  // Graceful shutdown
  let shuttingDown = false;
  const shutdown = () => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info('Shutting down...');
    dashboard.destroy();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  // Run immediately, then on interval
  await runPollCycle(client, engine, dashboard);

  const intervalMs = config.POLL_INTERVAL_SECONDS * 1000;
  setInterval(async () => {
    if (!shuttingDown) {
      await runPollCycle(client, engine, dashboard);
    }
  }, intervalMs);
}

main().catch(err => {
  console.error('Fatal error:', err.message);
  process.exit(1);
});
