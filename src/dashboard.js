'use strict';
const blessed = require('blessed');
const config = require('./config');

const MODE_COLOR = config.DRY_RUN ? '{yellow-fg}DRY RUN{/yellow-fg}' : '{red-fg}LIVE{/red-fg}';

const SIGNAL_COLORS = {
  MOMENTUM: '{yellow-fg}',
  LIQUIDITY: '{cyan-fg}',
  MISPRICING: '{magenta-fg}',
};

function fmt(val, decimals = 2) {
  if (val == null) return '  --  ';
  return Number(val).toFixed(decimals);
}

function fmtTime(isoStr) {
  if (!isoStr) return '--:--';
  const t = new Date(isoStr.replace(' ', 'T') + 'Z');
  return t.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function padR(str, len) {
  str = String(str ?? '');
  return str.length >= len ? str.slice(0, len) : str + ' '.repeat(len - str.length);
}

function padL(str, len) {
  str = String(str ?? '');
  return str.length >= len ? str.slice(0, len) : ' '.repeat(len - str.length) + str;
}

class Dashboard {
  constructor() {
    this.screen = blessed.screen({
      smartCSR: true,
      title: 'Kalshi Trading Bot',
      fullUnicode: true,
    });

    this.startTime = Date.now();
    this.pollCount = 0;
    this.errorCount = 0;
    this.lastError = null;

    this._buildLayout();
    this._bindKeys();
  }

  _buildLayout() {
    const s = this.screen;

    // ── Header bar ────────────────────────────────────────────────────────
    this.header = blessed.box({
      top: 0,
      left: 0,
      width: '100%',
      height: 1,
      tags: true,
      content: ` {bold}Kalshi Trading Bot{/bold}  Mode: ${MODE_COLOR}`,
      style: { fg: 'white', bg: 'blue' },
    });

    // ── Markets panel (top-left) ──────────────────────────────────────────
    this.marketsBox = blessed.box({
      label: ' Watched Markets ',
      top: 1,
      left: 0,
      width: '70%',
      height: '50%',
      border: { type: 'line' },
      tags: true,
      scrollable: true,
      alwaysScroll: true,
      style: { border: { fg: 'cyan' }, label: { fg: 'cyan', bold: true } },
    });

    // ── Status panel (top-right) ─────────────────────────────────────────
    this.statusBox = blessed.box({
      label: ' Bot Status ',
      top: 1,
      left: '70%',
      width: '30%',
      height: '50%',
      border: { type: 'line' },
      tags: true,
      style: { border: { fg: 'green' }, label: { fg: 'green', bold: true } },
    });

    // ── Signals panel (bottom-left) ──────────────────────────────────────
    this.signalsBox = blessed.box({
      label: ' Signals Today ',
      top: '50%',
      left: 0,
      width: '70%',
      height: '50%-1',
      border: { type: 'line' },
      tags: true,
      scrollable: true,
      alwaysScroll: true,
      style: { border: { fg: 'yellow' }, label: { fg: 'yellow', bold: true } },
    });

    // ── Positions panel (bottom-right) ──────────────────────────────────
    this.positionsBox = blessed.box({
      label: ' Open Positions ',
      top: '50%',
      left: '70%',
      width: '30%',
      height: '50%-1',
      border: { type: 'line' },
      tags: true,
      scrollable: true,
      style: { border: { fg: 'magenta' }, label: { fg: 'magenta', bold: true } },
    });

    s.append(this.header);
    s.append(this.marketsBox);
    s.append(this.statusBox);
    s.append(this.signalsBox);
    s.append(this.positionsBox);
  }

  _bindKeys() {
    this.screen.key(['q', 'C-c'], () => {
      this.destroy();
      process.exit(0);
    });
  }

  // ── Render helpers ────────────────────────────────────────────────────────

  _renderMarkets(markets) {
    const header =
      `{bold}${padR('Ticker', 18)} ${padR('Title', 32)} ${padL('Bid', 5)} ${padL('Ask', 5)} ${padL('Vol', 7)}{/bold}\n` +
      `${'-'.repeat(72)}\n`;

    const rows = markets.slice(0, 60).map(m => {
      const bid = m.yes_bid != null ? fmt(m.yes_bid * 100, 0) : '--';
      const ask = m.yes_ask != null ? fmt(m.yes_ask * 100, 0) : '--';
      const vol = m.volume ?? '--';
      const title = (m.title || '').slice(0, 32);
      return `${padR(m.ticker, 18)} ${padR(title, 32)} ${padL(bid, 5)} ${padL(ask, 5)} ${padL(vol, 7)}`;
    });

    this.marketsBox.setContent(header + rows.join('\n'));
  }

  _renderSignals(signals) {
    const header =
      `{bold}${padR('Time', 6)} ${padR('Type', 12)} ${padR('Ticker', 18)} ${padL('Price', 6)} ${padR('Action', 10)} Detail{/bold}\n` +
      `${'-'.repeat(80)}\n`;

    const rows = signals.slice(0, 40).map(s => {
      const color = SIGNAL_COLORS[s.signal_type] || '';
      const end = color ? '{/}' : '';
      const time = fmtTime(s.triggered_at);
      const price = fmt(s.current_price, 2);
      const detail = (s.detail || '').slice(0, 35);
      return `${padR(time, 6)} ${color}${padR(s.signal_type, 12)}${end} ${padR(s.ticker, 18)} ${padL(price, 6)} ${padR(s.recommended, 10)} ${detail}`;
    });

    this.signalsBox.setContent(header + rows.join('\n'));
  }

  _renderPositions(positions) {
    const header =
      `{bold}${padR('Ticker', 18)} ${padL('Qty', 5)} ${padL('AvgPx', 6)}{/bold}\n` +
      `${'-'.repeat(32)}\n`;

    const rows = positions.map(p => {
      const qty = p.contracts ?? '--';
      const avg = fmt(p.avgPrice, 2);
      return `${padR(p.ticker, 18)} ${padL(qty, 5)} ${padL(avg, 6)}`;
    });

    const content = rows.length ? header + rows.join('\n') : header + '  No open positions';
    this.positionsBox.setContent(content);
  }

  _renderStatus(extra = {}) {
    const uptimeSec = Math.floor((Date.now() - this.startTime) / 1000);
    const mm = Math.floor(uptimeSec / 60);
    const ss = String(uptimeSec % 60).padStart(2, '0');
    const uptime = `${mm}m ${ss}s`;

    const lines = [
      `Mode:      ${MODE_COLOR}`,
      `Uptime:    ${uptime}`,
      `Polls:     ${this.pollCount}`,
      `Errors:    ${this.errorCount}`,
      '',
      `MaxSize:   ${config.MAX_POSITION_SIZE} contracts`,
      `MaxExp:    $${config.MAX_PORTFOLIO_EXPOSURE}`,
      '',
      `Interval:  ${config.POLL_INTERVAL_SECONDS}s`,
      '',
      ...(this.lastError
        ? [`{red-fg}Last err:{/red-fg}`, `  ${this.lastError.slice(0, 26)}`]
        : ['{green-fg}No errors{/green-fg}']),
    ];

    this.statusBox.setContent(lines.join('\n'));
  }

  // ── Public API ────────────────────────────────────────────────────────────

  update({ markets = [], signals = [], positions = [], error = null } = {}) {
    this.pollCount++;
    if (error) {
      this.errorCount++;
      this.lastError = String(error);
    }

    this._renderMarkets(markets);
    this._renderSignals(signals);
    this._renderPositions(positions);
    this._renderStatus();
    this.screen.render();
  }

  destroy() {
    try { this.screen.destroy(); } catch (_) {}
  }
}

module.exports = Dashboard;
