'use strict';
const config = require('./config');
const db = require('./database');
const logger = require('./logger');

class ExecutionEngine {
  constructor(client) {
    this.client = client;
    this.positions = {}; // ticker → { contracts, avgPrice }
    this.totalExposure = 0;
  }

  // ── Position management ───────────────────────────────────────────────────

  async refreshPositions() {
    try {
      const raw = await this.client.getPositions();
      this.positions = {};
      this.totalExposure = 0;

      for (const pos of raw) {
        const ticker = pos.ticker;
        const contracts = (pos.position || 0);
        const avgPrice = (pos.market_exposure || 0) / Math.max(contracts, 1) / 100;
        this.positions[ticker] = { contracts, avgPrice };
        this.totalExposure += (pos.market_exposure || 0) / 100;
      }
      logger.debug('Positions refreshed', { count: Object.keys(this.positions).length });
    } catch (err) {
      logger.warn('Could not refresh positions', { err: err.message });
    }
  }

  getPositionsSummary() {
    return Object.entries(this.positions).map(([ticker, pos]) => ({
      ticker,
      contracts: pos.contracts,
      avgPrice: pos.avgPrice,
    }));
  }

  // ── Signal handling ───────────────────────────────────────────────────────

  async handleSignal(signal, orderbook) {
    if (signal.recommended === 'NONE') return;

    const side = signal.recommended === 'BUY_YES' ? 'yes' : 'no';
    const limitPriceCents = this._bestAskCents(orderbook, side);
    if (limitPriceCents == null) {
      logger.warn('No ask found for signal', { ticker: signal.ticker, side });
      return;
    }

    const size = this._determineSize(signal.ticker);
    if (size <= 0) {
      logger.info('Position limit reached, skipping', { ticker: signal.ticker });
      return;
    }

    const exposure = (limitPriceCents / 100) * size;
    if (this.totalExposure + exposure > config.MAX_PORTFOLIO_EXPOSURE) {
      logger.info('Portfolio exposure limit reached, skipping', { ticker: signal.ticker });
      return;
    }

    if (config.DRY_RUN) {
      await this._dryRun({ signal, side, size, limitPriceCents });
    } else {
      await this._liveOrder({ signal, side, size, limitPriceCents });
    }
  }

  _determineSize(ticker) {
    const held = this.positions[ticker]?.contracts ?? 0;
    return Math.max(0, config.MAX_POSITION_SIZE - Math.abs(held));
  }

  _bestAskCents(orderbook, side) {
    // For a buy order we want the lowest ask on the given side
    const levels = side === 'yes' ? (orderbook.yes || []) : (orderbook.no || []);
    if (levels.length === 0) return null;
    return Math.min(...levels.map(l => l[0]));
  }

  async _dryRun({ signal, side, size, limitPriceCents }) {
    const msg = `[DRY RUN] Would BUY ${size} ${side.toUpperCase()} contracts of ${signal.ticker} @ ${limitPriceCents}¢ (${signal.signalType})`;
    logger.info(msg);

    db.insertOrder({
      ticker: signal.ticker,
      side,
      action: 'buy',
      contracts: size,
      limit_price: limitPriceCents / 100,
      is_dry_run: true,
      status: 'simulated',
      kalshi_order_id: null,
    });

    return msg;
  }

  async _liveOrder({ signal, side, size, limitPriceCents }) {
    try {
      const order = await this.client.placeOrder({
        ticker: signal.ticker,
        side,
        action: 'buy',
        contracts: size,
        limitPriceCents,
      });

      const orderId = order?.order_id;
      logger.info('Order placed', { ticker: signal.ticker, side, size, price: limitPriceCents, orderId });

      db.insertOrder({
        ticker: signal.ticker,
        side,
        action: 'buy',
        contracts: size,
        limit_price: limitPriceCents / 100,
        is_dry_run: false,
        status: 'resting',
        kalshi_order_id: orderId,
      });

      // Update local position tracking
      const pos = this.positions[signal.ticker] || { contracts: 0, avgPrice: 0 };
      pos.contracts += size;
      this.positions[signal.ticker] = pos;
      this.totalExposure += (limitPriceCents / 100) * size;
    } catch (err) {
      logger.error('Order failed', { ticker: signal.ticker, err: err.message });
      db.insertOrder({
        ticker: signal.ticker,
        side,
        action: 'buy',
        contracts: size,
        limit_price: limitPriceCents / 100,
        is_dry_run: false,
        status: 'rejected',
        kalshi_order_id: null,
      });
    }
  }
}

module.exports = ExecutionEngine;
