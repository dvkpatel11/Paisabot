/* ═══════════════════════════════════════════════════════════════════════════
   stores.js — Alpine.js global stores (single source of truth for live state)
   Loaded before Alpine initialises so x-data components can access $store.*
   ═══════════════════════════════════════════════════════════════════════════ */

document.addEventListener('alpine:init', () => {

  /* ── App-wide state ─────────────────────────────────────────────────── */
  Alpine.store('app', {
    // Operational mode: research | simulation | live
    mode: 'research',

    // Market regime
    regime: 'consolidation',           // trending|rotation|risk_off|consolidation
    regimeLabel: '--',
    regimeConf:  '--%',

    // Kill switch
    killSwitchActive: false,
    killSwitchMsg: 'TRADING HALTED',

    // WebSocket / status bar state
    wsConnected: false,
    wsLatency: null,
    lastTick: null,

    // System health: keyed by module initial
    health: {
      data:      'unknown',
      factors:   'unknown',
      signals:   'unknown',
      portfolio: 'unknown',
      risk:      'unknown',
      execution: 'unknown',
      monitoring:'unknown',
    },

    init() {
      dayjs.extend(window.dayjs_plugin_relativeTime);
      dayjs.extend(window.dayjs_plugin_utc);
      dayjs.extend(window.dayjs_plugin_timezone);
    },

    setRegime(regime, confidence) {
      this.regime = regime;
      this.regimeLabel = {
        trending:      'TRENDING',
        rotation:      'ROTATION',
        risk_off:      'RISK OFF',
        consolidation: 'CONSOLIDATION',
      }[regime] ?? regime.toUpperCase();
      this.regimeConf = `${Math.round(confidence * 100)}%`;
    },

    setHealth(data) {
      Object.assign(this.health, data);
    },

    setMode(mode) {
      this.mode = mode;
    },

    setKillSwitch(active, msg) {
      this.killSwitchActive = active;
      if (msg) this.killSwitchMsg = msg;
    },
  });

  /* ── Portfolio state ────────────────────────────────────────────────── */
  Alpine.store('portfolio', {
    value:       0,
    pnlDay:      0,
    pnlDayPct:   0,
    pnlTotal:    0,
    drawdown:    0,   // 0–1 (negative direction)
    cash:        0,
    positions:   [],  // [{symbol, qty, side, entry, current, pnl, pnl_pct, weight}]
    weights:     {},
    exposure:    {},  // {sector: weight}

    get pnlDayColor()  { return this.pnlDay  >= 0 ? 'var(--green)' : 'var(--red)'; },
    get pnlDaySign()   { return this.pnlDay  >= 0 ? '+' : ''; },
    get drawdownColor(){
      const d = Math.abs(this.drawdown);
      if (d > 0.12) return 'var(--red)';
      if (d > 0.07) return 'var(--yellow)';
      return 'var(--green)';
    },
    get drawdownWidth(){ return `${Math.min(Math.abs(this.drawdown) / 0.20 * 100, 100)}%`; },

    update(data) {
      if (data.value     !== undefined) this.value     = data.value;
      if (data.pnl_day   !== undefined) this.pnlDay    = data.pnl_day;
      if (data.pnl_day_pct !== undefined) this.pnlDayPct = data.pnl_day_pct;
      if (data.pnl_total !== undefined) this.pnlTotal  = data.pnl_total;
      if (data.drawdown  !== undefined) this.drawdown  = data.drawdown;
      if (data.cash      !== undefined) this.cash      = data.cash;
      if (data.positions !== undefined) this.positions = data.positions;
      if (data.weights   !== undefined) this.weights   = data.weights;
      if (data.exposure  !== undefined) this.exposure  = data.exposure;
    },
  });

  /* ── Signals state ──────────────────────────────────────────────────── */
  Alpine.store('signals', {
    long:    [],   // [{symbol, score, rank}]
    short:   [],
    neutral: [],
    avoid:   [],
    scores:  {},   // {symbol: {composite, trend, volatility, sentiment, breadth, liquidity}}
    lastUpdated: null,

    update(data) {
      if (data.long    !== undefined) this.long    = data.long;
      if (data.short   !== undefined) this.short   = data.short;
      if (data.neutral !== undefined) this.neutral = data.neutral;
      if (data.avoid   !== undefined) this.avoid   = data.avoid;
      this.lastUpdated = new Date();
    },

    updateScores(data) {
      Object.assign(this.scores, data);
      this.lastUpdated = new Date();
    },
  });

  /* ── Risk state ─────────────────────────────────────────────────────── */
  Alpine.store('risk', {
    varValue:     0,   // Portfolio VaR (pct)
    varLimit:     0.02,
    drawdown:     0,
    drawdownLimit:0.15,
    stopHits:     [],  // [{symbol, type, value, timestamp}]
    correlShock:  false,
    liquidityAlerts: [],

    get varColor() {
      const ratio = this.varValue / this.varLimit;
      if (ratio > 0.9) return 'var(--red)';
      if (ratio > 0.7) return 'var(--yellow)';
      return 'var(--green)';
    },

    update(data) {
      if (data.var_value   !== undefined) this.varValue    = data.var_value;
      if (data.var_limit   !== undefined) this.varLimit    = data.var_limit;
      if (data.drawdown    !== undefined) this.drawdown    = data.drawdown;
      if (data.stop_hits   !== undefined) this.stopHits    = data.stop_hits;
      if (data.correl_shock !== undefined) this.correlShock = data.correl_shock;
    },
  });

  /* ── Alerts state ───────────────────────────────────────────────────── */
  Alpine.store('alerts', {
    items:     [],   // [{id, level, type, message, time}]
    unread:    0,
    panelOpen: false,

    push(alert) {
      alert.id   = alert.id ?? Date.now();
      alert.time = dayjs(alert.timestamp ?? new Date()).fromNow();
      this.items.unshift(alert);
      if (this.items.length > 200) this.items = this.items.slice(0, 200);
      if (!this.panelOpen) this.unread++;

      // Toast for critical
      if (alert.level === 'critical') {
        Toastify({
          text: `⚠ ${alert.type}: ${alert.message}`,
          duration: 8000,
          style: { background: 'var(--red)', color: '#fff', fontFamily: 'var(--font-mono)', fontSize: '12px' },
          gravity: 'top', position: 'right',
        }).showToast();
      }
    },

    togglePanel() {
      this.panelOpen = !this.panelOpen;
      if (this.panelOpen) this.unread = 0;
    },
  });

  /* ── Trades state (execution log) ───────────────────────────────────── */
  Alpine.store('trades', {
    recent: [],  // [{symbol, side, qty, fill_price, notional, slippage_bps, timestamp}]

    push(trade) {
      this.recent.unshift(trade);
      if (this.recent.length > 500) this.recent = this.recent.slice(0, 500);
    },
  });

});
