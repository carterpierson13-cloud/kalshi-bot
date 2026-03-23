'use strict';
const crypto = require('crypto');
const fs = require('fs');
const axios = require('axios');
const config = require('./config');
const logger = require('./logger');

class KalshiClient {
  constructor() {
    this.base = config.KALSHI_API_BASE;
    this.keyId = config.KALSHI_API_KEY_ID;

    const keyPath = config.KALSHI_PRIVATE_KEY_PATH;
    if (!fs.existsSync(keyPath)) {
      throw new Error(`Private key not found: ${keyPath}`);
    }
    this.privateKey = fs.readFileSync(keyPath, 'utf8');

    logger.info('KalshiClient initialised', { keyId: this.keyId, mode: config.DRY_RUN ? 'DRY_RUN' : 'LIVE' });
  }

  // ── Auth ───────────────────────────────────────────────────────────────────

  _sign(method, path) {
    const ts = Date.now().toString();
    // Strip query string from path before signing
    const cleanPath = path.split('?')[0];
    const message = ts + method.toUpperCase() + cleanPath;
    const sig = crypto
      .createSign('RSA-SHA256')
      .update(message)
      .sign(this.privateKey, 'base64');
    return { ts, sig };
  }

  _headers(method, path) {
    const { ts, sig } = this._sign(method, path);
    return {
      'Content-Type': 'application/json',
      'KALSHI-ACCESS-KEY': this.keyId,
      'KALSHI-ACCESS-TIMESTAMP': ts,
      'KALSHI-ACCESS-SIGNATURE': sig,
    };
  }

  // ── HTTP helpers ───────────────────────────────────────────────────────────

  async _get(path, params = {}) {
    const query = new URLSearchParams(params).toString();
    const fullPath = query ? `${path}?${query}` : path;
    const headers = this._headers('GET', path); // sign the base path only
    try {
      const res = await axios.get(`${this.base}${fullPath}`, {
        headers,
        timeout: config.REQUEST_TIMEOUT,
      });
      return res.data;
    } catch (err) {
      logger.error('GET failed', { path: fullPath, status: err.response?.status, msg: err.message });
      throw err;
    }
  }

  async _post(path, body = {}) {
    const headers = this._headers('POST', path);
    try {
      const res = await axios.post(`${this.base}${path}`, body, {
        headers,
        timeout: config.REQUEST_TIMEOUT,
      });
      return res.data;
    } catch (err) {
      logger.error('POST failed', { path, status: err.response?.status, msg: err.message });
      throw err;
    }
  }

  async _delete(path) {
    const headers = this._headers('DELETE', path);
    try {
      const res = await axios.delete(`${this.base}${path}`, {
        headers,
        timeout: config.REQUEST_TIMEOUT,
      });
      return res.data;
    } catch (err) {
      logger.error('DELETE failed', { path, status: err.response?.status, msg: err.message });
      throw err;
    }
  }

  // ── Markets ────────────────────────────────────────────────────────────────

  async getMarkets({ status = 'open', seriesTicker = null, limit = 200 } = {}) {
    const params = { status, limit };
    if (seriesTicker) params.series_ticker = seriesTicker;

    const markets = [];
    let cursor = null;

    do {
      if (cursor) params.cursor = cursor;
      const data = await this._get('/markets', params);
      const page = data.markets || [];
      markets.push(...page);
      cursor = data.cursor || null;
    } while (cursor && markets.length < 1000);

    return markets;
  }

  async getMarket(ticker) {
    const data = await this._get(`/markets/${ticker}`);
    return data.market;
  }

  async getOrderbook(ticker, depth = 10) {
    const data = await this._get(`/markets/${ticker}/orderbook`, { depth });
    return data.orderbook;
  }

  // ── Account ────────────────────────────────────────────────────────────────

  async getBalance() {
    const data = await this._get('/portfolio/balance');
    // API returns cents; convert to dollars
    return (data.balance || 0) / 100;
  }

  async getPositions() {
    const data = await this._get('/portfolio/positions');
    return data.market_positions || [];
  }

  async getOrders(status = 'resting') {
    const data = await this._get('/portfolio/orders', { status });
    return data.orders || [];
  }

  // ── Trading ────────────────────────────────────────────────────────────────

  async placeOrder({ ticker, side, action, contracts, limitPriceCents }) {
    const body = {
      ticker,
      side,       // 'yes' | 'no'
      action,     // 'buy' | 'sell'
      count: contracts,
      type: 'limit',
      yes_price: side === 'yes' ? limitPriceCents : undefined,
      no_price: side === 'no' ? limitPriceCents : undefined,
    };
    // Remove undefined keys
    Object.keys(body).forEach(k => body[k] === undefined && delete body[k]);
    const data = await this._post('/portfolio/orders', body);
    return data.order;
  }

  async cancelOrder(orderId) {
    return this._delete(`/portfolio/orders/${orderId}`);
  }
}

module.exports = KalshiClient;
