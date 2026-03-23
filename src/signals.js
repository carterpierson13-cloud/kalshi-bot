'use strict';
const config = require('./config');
const db = require('./database');
const logger = require('./logger');

/**
 * Signal object shape:
 * {
 *   ticker, title, signalType, currentPrice, fairValue,
 *   recommended, detail
 * }
 */

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Extract best price (as fraction 0-1) from order-book levels.
 * levels: [[price_cents, size], ...]
 * side: 'yes' (want lowest ask) | 'no' (want lowest ask on no side)
 */
function bestPrice(levels, side) {
  if (!Array.isArray(levels) || levels.length === 0) return null;
  const prices = levels.map(l => l[0]);
  // 'yes' bids: highest price = best; 'yes' asks: lowest = best
  // The caller decides how to interpret; just return best available
  const best = side === 'bid' ? Math.max(...prices) : Math.min(...prices);
  return best / 100; // convert cents → fraction
}

function midPrice(orderbook) {
  const yesBid = bestPrice(orderbook.yes || [], 'bid');
  const yesAsk = bestPrice(orderbook.yes || [], 'ask');
  if (yesBid !== null && yesAsk !== null) return (yesBid + yesAsk) / 2;
  if (yesBid !== null) return yesBid;
  if (yesAsk !== null) return yesAsk;
  return null;
}

// ── Signal checks ─────────────────────────────────────────────────────────────

function checkMomentum(market, orderbook) {
  const ticker = market.ticker;
  const lookbackMs = config.MOMENTUM_LOOKBACK_SECONDS * 1000;
  const sinceDate = new Date(Date.now() - lookbackMs);
  const sinceIso = sinceDate.toISOString().replace('T', ' ').slice(0, 19);

  const history = db.getPriceHistory(ticker, sinceIso);
  if (history.length < 2) return null;

  const oldest = history[0].yes_price;
  const current = midPrice(orderbook);
  if (oldest == null || current == null) return null;

  const change = current - oldest;
  const pctChange = Math.abs(change) / oldest;

  if (pctChange < config.MOMENTUM_THRESHOLD) return null;

  const recommended = change > 0 ? 'BUY_YES' : 'BUY_NO';
  const dir = change > 0 ? 'up' : 'down';
  return {
    ticker,
    title: market.title,
    signalType: 'MOMENTUM',
    currentPrice: current,
    fairValue: config.DEFAULT_FAIR_VALUE,
    recommended,
    detail: `Price moved ${dir} ${(pctChange * 100).toFixed(1)}% in ${config.MOMENTUM_LOOKBACK_SECONDS / 60}min`,
  };
}

function checkLiquidity(market, orderbook) {
  const ticker = market.ticker;
  const yesLevels = orderbook.yes || [];
  const noLevels = orderbook.no || [];

  const yesTotal = yesLevels.reduce((s, l) => s + (l[1] || 0), 0);
  const noTotal = noLevels.reduce((s, l) => s + (l[1] || 0), 0);

  if (yesTotal === 0 && noTotal === 0) return null;

  const thick = Math.max(yesTotal, noTotal);
  const thin = Math.min(yesTotal, noTotal);
  if (thick === 0) return null;

  const ratio = thin / thick;
  if (ratio >= config.LIQUIDITY_IMBALANCE_THRESHOLD) return null;

  const thinSide = yesTotal < noTotal ? 'YES' : 'NO';
  const recommended = thinSide === 'YES' ? 'BUY_YES' : 'BUY_NO';
  const current = midPrice(orderbook) ?? config.DEFAULT_FAIR_VALUE;

  return {
    ticker,
    title: market.title,
    signalType: 'LIQUIDITY',
    currentPrice: current,
    fairValue: config.DEFAULT_FAIR_VALUE,
    recommended,
    detail: `${thinSide} side thin (ratio ${ratio.toFixed(2)}); ${yesTotal}/${noTotal} contracts`,
  };
}

function checkMispricing(market, orderbook, fairValue = null) {
  const ticker = market.ticker;
  const fv = fairValue ?? config.DEFAULT_FAIR_VALUE;
  const current = midPrice(orderbook);
  if (current == null) return null;

  const diff = Math.abs(current - fv);
  if (diff < config.MISPRICING_THRESHOLD) return null;

  const recommended = current > fv ? 'BUY_NO' : 'BUY_YES';
  const dir = current > fv ? 'overpriced' : 'underpriced';

  return {
    ticker,
    title: market.title,
    signalType: 'MISPRICING',
    currentPrice: current,
    fairValue: fv,
    recommended,
    detail: `Market ${dir}: price ${current.toFixed(2)} vs fair ${fv.toFixed(2)}`,
  };
}

// ── Main evaluation ───────────────────────────────────────────────────────────

/**
 * Evaluate all signals for a market. Returns array of fired signals.
 * Also persists each fired signal to the database.
 */
function evaluateMarket(market, orderbook, fairValue = null) {
  const checks = [
    () => checkMomentum(market, orderbook),
    () => checkLiquidity(market, orderbook),
    () => checkMispricing(market, orderbook, fairValue),
  ];

  const fired = [];
  for (const check of checks) {
    try {
      const signal = check();
      if (!signal) continue;
      db.insertSignal({
        ticker: signal.ticker,
        title: signal.title,
        signal_type: signal.signalType,
        current_price: signal.currentPrice,
        fair_value: signal.fairValue,
        recommended: signal.recommended,
        detail: signal.detail,
      });
      fired.push(signal);
      logger.info('Signal fired', { type: signal.signalType, ticker: signal.ticker, rec: signal.recommended });
    } catch (err) {
      logger.warn('Signal check error', { err: err.message });
    }
  }
  return fired;
}

module.exports = { evaluateMarket, midPrice, bestPrice };
