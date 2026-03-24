/* ═══════════════════════════════════════════════════════════════════════════
   socket.js — Flask-SocketIO client bridge
   Connects once on page load, routes all events to Alpine stores.
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  const RECONNECT_DELAY = [1000, 2000, 4000, 8000];

  const socket = io('/dashboard', {
    transports: ['websocket'],
    reconnectionAttempts: 10,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 16000,
  });

  // Expose globally so page scripts can emit or subscribe to custom events
  window.pbSocket = socket;

  /* ── Connection lifecycle ───────────────────────────────────────────── */
  socket.on('connect', () => {
    console.debug('[socket] connected:', socket.id);
    document.dispatchEvent(new CustomEvent('pb:socket:connected'));
    setConnected(true);
  });

  socket.on('disconnect', (reason) => {
    console.warn('[socket] disconnected:', reason);
    setConnected(false);
  });

  socket.on('connect_error', (err) => {
    console.error('[socket] connect_error:', err.message);
  });

  function setConnected(ok) {
    const el = document.getElementById('clock-status');
    if (el) el.dataset.socketConnected = ok ? '1' : '0';
    // Update Alpine store for status bar
    if (typeof Alpine !== 'undefined' && Alpine.store('app')) {
      Alpine.store('app').wsConnected = ok;
    }
  }

  // Latency measurement via Socket.IO ping
  let _pingStart = 0;
  setInterval(() => {
    if (socket.connected) {
      _pingStart = performance.now();
      socket.emit('ping_check');
    }
  }, 10000);
  socket.on('pong_check', () => {
    const latency = Math.round(performance.now() - _pingStart);
    if (typeof Alpine !== 'undefined' && Alpine.store('app')) {
      Alpine.store('app').wsLatency = latency;
    }
  });

  /* ── Factor scores ──────────────────────────────────────────────────── */
  socket.on('factor_scores', (data) => {
    // data: {symbol: {factor_name: score, ...}, ...}
    Alpine.store('signals').updateScores(data);
    document.dispatchEvent(new CustomEvent('pb:factor_scores', { detail: data }));
  });

  /* ── Signals ────────────────────────────────────────────────────────── */
  socket.on('signals', (data) => {
    // data: {long:[], short:[], neutral:[], avoid:[], regime, confidence}
    Alpine.store('signals').update(data);
    if (data.regime !== undefined) {
      Alpine.store('app').setRegime(data.regime, data.confidence ?? 0);
    }
    document.dispatchEvent(new CustomEvent('pb:signals', { detail: data }));
  });

  /* ── Portfolio ──────────────────────────────────────────────────────── */
  socket.on('portfolio', (data) => {
    Alpine.store('portfolio').update(data);

    // Update nav PnL badge
    const badge = document.getElementById('nav-pnl');
    if (badge) {
      const pct  = data.pnl_day_pct ?? 0;
      badge.textContent = fmt.pct(pct, true);
      badge.className   = 'nav-badge ' + (pct >= 0 ? 'pos' : 'neg');
    }
    document.dispatchEvent(new CustomEvent('pb:portfolio', { detail: data }));
  });

  /* ── Risk alerts ────────────────────────────────────────────────────── */
  socket.on('risk_alert', (data) => {
    Alpine.store('alerts').push({ ...data, level: data.severity ?? 'warning' });
    Alpine.store('risk').update(data.risk_snapshot ?? {});
    document.dispatchEvent(new CustomEvent('pb:risk_alert', { detail: data }));
  });

  /* ── Trades / fills ─────────────────────────────────────────────────── */
  socket.on('trade', (data) => {
    Alpine.store('trades').push(data);
    document.dispatchEvent(new CustomEvent('pb:trade', { detail: data }));
    Toastify({
      text: `${data.side?.toUpperCase()} ${data.symbol} @ ${fmt.price(data.fill_price)}`,
      duration: 4000,
      style: {
        background: data.side === 'buy' ? 'var(--green)' : 'var(--red)',
        color: '#fff', fontFamily: 'var(--font-mono)', fontSize: '12px',
      },
      gravity: 'bottom', position: 'right',
    }).showToast();
  });

  /* ── System health ──────────────────────────────────────────────────── */
  socket.on('system_health', (data) => {
    // data: {data: 'ok'|'stale'|'error', factors: ..., ...}
    Alpine.store('app').setHealth(data);
    document.dispatchEvent(new CustomEvent('pb:system_health', { detail: data }));
  });

  /* ── Regime change ──────────────────────────────────────────────────── */
  socket.on('regime_change', (data) => {
    Alpine.store('app').setRegime(data.to_regime, data.confidence ?? 0);
    Alpine.store('alerts').push({
      level:   'info',
      type:    'REGIME CHANGE',
      message: `${data.from_regime} → ${data.to_regime} (${Math.round((data.confidence ?? 0) * 100)}% conf)`,
      timestamp: new Date().toISOString(),
    });
    document.dispatchEvent(new CustomEvent('pb:regime_change', { detail: data }));
  });

  /* ── Config change ──────────────────────────────────────────────────── */
  socket.on('config_change', (data) => {
    if (data.key === 'operational_mode') {
      Alpine.store('app').setMode(data.value);
    }
    document.dispatchEvent(new CustomEvent('pb:config_change', { detail: data }));
  });

  /* ── Kill switch ────────────────────────────────────────────────────── */
  socket.on('kill_switch', (data) => {
    const active = data.trading === '1' || data.all === '1';
    Alpine.store('app').setKillSwitch(active, active ? 'TRADING HALTED — kill switch active' : '');
    document.dispatchEvent(new CustomEvent('pb:kill_switch', { detail: data }));
  });

})();
