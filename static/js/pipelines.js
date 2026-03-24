/* ═══════════════════════════════════════════════════════════════════════════
   pipelines.js — Realtime Pipelines page logic

   Handles:
     - Pipeline flow diagram (module status arrows)
     - Kill switch summary grid
     - Events feed table (Tabulator)
     - Throughput chart (Plotly, last 60 min rolling)
     - Module metric card live updates
     - Polling: health every 10s, metrics every 30s
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ── Pipeline module definitions (display order) ─────────────────────── */
  const MODULES = [
    { id: 'market_data',       label: 'Data',       icon: 'fa-database',      color: '#3b82f6' },
    { id: 'factor_engine',     label: 'Factors',    icon: 'fa-layer-group',   color: '#f59e0b' },
    { id: 'signal_engine',     label: 'Signals',    icon: 'fa-bolt',          color: '#a855f7' },
    { id: 'portfolio_engine',  label: 'Portfolio',  icon: 'fa-briefcase',     color: '#22c55e' },
    { id: 'risk_engine',       label: 'Risk',       icon: 'fa-shield-halved', color: '#ef4444' },
    { id: 'execution_engine',  label: 'Execution',  icon: 'fa-rocket',        color: '#06b6d4' },
  ];

  /* ── Events feed ─────────────────────────────────────────────────────── */
  const _events = [];
  let _eventsTable = null;

  /* ── Throughput chart data ───────────────────────────────────────────── */
  const _throughput = {
    timestamps: [],
    data_items:   [],
    factor_items: [],
    signal_items: [],
  };

  /* ════════════════════════════════════════════════════════════════════════
     Render — Pipeline flow diagram
     ════════════════════════════════════════════════════════════════════════ */
  function renderFlow(health = {}) {
    const el = document.getElementById('pipeline-flow');
    if (!el) return;

    el.innerHTML = MODULES.map((mod, i) => {
      const status = health[mod.id] ?? health[mod.label.toLowerCase()] ?? 'unknown';
      const dotCls = status === 'ok'    ? 'ok'
                   : status === 'stale' ? 'stale'
                   : status === 'error' ? 'error'
                   : 'unknown';

      const arrow = i < MODULES.length - 1
        ? `<div class="flow-arrow"><i class="fa-solid fa-arrow-right"></i></div>`
        : '';

      return `
        <div class="flow-node">
          <div class="flow-dot ${dotCls}"></div>
          <div class="flow-icon" style="color:${mod.color}">
            <i class="fa-solid ${mod.icon}"></i>
          </div>
          <div class="flow-label">${mod.label}</div>
        </div>
        ${arrow}`;
    }).join('');
  }

  /* ════════════════════════════════════════════════════════════════════════
     Render — Kill switch summary grid
     ════════════════════════════════════════════════════════════════════════ */
  function renderKillSwitches(switches = {}) {
    const el = document.getElementById('kill-switch-grid');
    if (!el) return;

    if (!Object.keys(switches).length) {
      el.innerHTML = '<span class="text-muted" style="font-size:12px;padding:8px">No kill switches configured</span>';
      return;
    }

    el.innerHTML = Object.entries(switches).map(([name, active]) => {
      const label = name.replace(/_/g, ' ').toUpperCase();
      const cls   = active ? 'switch-active' : 'switch-inactive';
      return `
        <div class="switch-item ${cls}" onclick="window._pbToggleSwitch('${name}', ${!active})">
          <span class="switch-name">${label}</span>
          <span class="switch-status">${active ? 'ACTIVE' : 'OFF'}</span>
        </div>`;
    }).join('');
  }

  /* Expose toggle handler globally (called from onclick in rendered HTML). */
  window._pbToggleSwitch = function (name, active) {
    PaisaUtils.fetchJSON(`/api/control/${name}`, {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ active }),
    })
      .then(() => loadHealth())
      .catch(() => PaisaUtils.toast('Failed to toggle kill switch', 'error'));
  };

  /* ════════════════════════════════════════════════════════════════════════
     Render — Module metric cards
     ════════════════════════════════════════════════════════════════════════ */
  function applyModuleMetrics(metrics = {}) {
    MODULES.forEach(mod => {
      const m = metrics[mod.id] ?? {};

      _setEl(`val-${mod.id}-items`,  m.items_processed ?? m.symbols_computed ?? m.signals_generated ?? m.portfolios_built ?? m.orders_reviewed ?? m.orders_filled ?? 0);
      _setEl(`val-${mod.id}-time`,   m.compute_ms != null ? `${m.compute_ms} ms` : '--');
      _setEl(`val-${mod.id}-status`, m.status ?? 'idle');
      _setEl(`val-${mod.id}-last`,   m.last_activity ? PaisaUtils.relTime(m.last_activity) : '--');

      if (m.queue_depth != null) _setEl(`val-${mod.id}-queue`, m.queue_depth);

      const dot = document.getElementById(`dot-${mod.id}`);
      if (dot) {
        const s = m.status ?? 'idle';
        dot.className = 'health-dot ' + (s === 'running' || s === 'ok' ? 'ok' : s === 'error' ? 'error' : s === 'stale' ? 'stale' : '');
      }
    });
  }

  function _setEl(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  /* ════════════════════════════════════════════════════════════════════════
     Events feed table
     ════════════════════════════════════════════════════════════════════════ */
  function initEventsTable() {
    _eventsTable = PaisaTables.create('events-table', [
      { title: 'Time',    field: 'timestamp', width: 120, formatter: PaisaTables.timeFormatter },
      { title: 'Module',  field: 'module',    width: 110 },
      { title: 'Event',   field: 'event',     width: 140 },
      { title: 'Detail',  field: 'detail',    minWidth: 200 },
      { title: 'Status',  field: 'status',    width: 100, formatter: PaisaTables.statusFormatter },
    ], { maxHeight: '340px', placeholder: 'Waiting for pipeline events…' });
  }

  function pushEvent(evt) {
    _events.unshift(evt);
    if (_events.length > 500) _events.pop();
    PaisaTables.setData('events-table', _events.slice(0, 200));
    const badge = document.getElementById('event-total');
    if (badge) badge.textContent = _events.length;
  }

  /* ════════════════════════════════════════════════════════════════════════
     Throughput chart
     ════════════════════════════════════════════════════════════════════════ */
  function updateThroughputChart() {
    const el = document.getElementById('throughput-chart');
    if (!el) return;

    PaisaCharts.timeSeries('throughput-chart', [
      { x: _throughput.timestamps, y: _throughput.data_items,   name: 'Data items',    line: { color: '#3b82f6' } },
      { x: _throughput.timestamps, y: _throughput.factor_items, name: 'Factor symbols', line: { color: '#f59e0b' } },
      { x: _throughput.timestamps, y: _throughput.signal_items, name: 'Signals',        line: { color: '#22c55e' } },
    ], 'Pipeline Throughput (Last 60 min)');
  }

  function recordThroughputTick(data) {
    const now = new Date().toISOString();
    _throughput.timestamps.push(now);
    _throughput.data_items.push(data.data_items ?? 0);
    _throughput.factor_items.push(data.factor_items ?? 0);
    _throughput.signal_items.push(data.signal_items ?? 0);

    // Keep only last 60 minutes (1 tick per minute → 60 points)
    const cutoff = 60;
    if (_throughput.timestamps.length > cutoff) {
      _throughput.timestamps.shift();
      _throughput.data_items.shift();
      _throughput.factor_items.shift();
      _throughput.signal_items.shift();
    }

    updateThroughputChart();
  }

  /* ════════════════════════════════════════════════════════════════════════
     Data loads
     ════════════════════════════════════════════════════════════════════════ */
  function loadHealth() {
    PaisaUtils.fetchJSON('/api/health')
      .then(data => {
        const components = data.components ?? data;
        renderFlow(components);
        renderKillSwitches(data.kill_switches ?? {});
        applyModuleMetrics(data.module_metrics ?? {});

        const modeEl = document.getElementById('pipeline-mode');
        const modeLabelEl = document.getElementById('pipeline-mode-label');
        if (modeEl && data.mode) {
          modeEl.textContent = (data.mode || '').toUpperCase();
          modeEl.className = 'badge badge-' + (data.mode === 'live' ? 'red' : data.mode === 'simulation' ? 'yellow' : 'muted');
        }
        if (modeLabelEl && data.mode) {
          modeLabelEl.textContent = {
            research:   'Historical data · Simulated fills',
            simulation: 'Live feeds · No orders sent',
            live:       'Live feeds · Real orders',
          }[data.mode] ?? '';
        }

        const ts = document.getElementById('pipeline-last-update');
        if (ts) ts.textContent = 'Last update: ' + PaisaUtils.formatTime(new Date().toISOString());
      })
      .catch(() => {});
  }

  /* ════════════════════════════════════════════════════════════════════════
     Socket event subscriptions
     ════════════════════════════════════════════════════════════════════════ */
  function wireSocket() {
    PaisaSocket.on('system_health', data => {
      renderFlow(data.components ?? data);
      applyModuleMetrics(data.module_metrics ?? {});
      renderKillSwitches(data.kill_switches ?? {});
      recordThroughputTick(data);

      const ts = document.getElementById('pipeline-last-update');
      if (ts) ts.textContent = 'Last update: ' + PaisaUtils.formatTime(new Date().toISOString());
    });

    PaisaSocket.on('kill_switch', data => {
      PaisaUtils.fetchJSON('/api/health').then(d => renderKillSwitches(d.kill_switches ?? {}));
    });

    PaisaSocket.on('regime_change', data => {
      pushEvent({
        timestamp: data.timestamp ?? new Date().toISOString(),
        module:    'Signal Engine',
        event:     'REGIME CHANGE',
        detail:    `${data.from_regime ?? '?'} → ${data.to_regime ?? '?'} (${Math.round((data.confidence ?? 0) * 100)}% conf)`,
        status:    'ok',
      });
    });

    PaisaSocket.on('trade', data => {
      pushEvent({
        timestamp: data.fill_time ?? data.timestamp ?? new Date().toISOString(),
        module:    'Execution Engine',
        event:     'FILL',
        detail:    `${(data.side ?? '').toUpperCase()} ${data.symbol} @ ${data.fill_price != null ? '$' + parseFloat(data.fill_price).toFixed(2) : '--'}`,
        status:    'filled',
      });
    });

    PaisaSocket.on('risk_alert', data => {
      pushEvent({
        timestamp: data.timestamp ?? new Date().toISOString(),
        module:    'Risk Engine',
        event:     data.alert_type ?? 'ALERT',
        detail:    data.action ?? data.message ?? JSON.stringify(data),
        status:    data.severity === 'critical' ? 'error' : 'warning',
      });
    });

    PaisaSocket.on('signals', data => {
      const n = (data.long?.length ?? 0) + (data.neutral?.length ?? 0) + (data.avoid?.length ?? 0);
      if (n > 0) {
        pushEvent({
          timestamp: new Date().toISOString(),
          module:    'Signal Engine',
          event:     'SIGNALS',
          detail:    `${n} signals generated — ${data.long?.length ?? 0} long, ${data.avoid?.length ?? 0} avoid`,
          status:    'ok',
        });
      }
    });

    PaisaSocket.on('factor_scores', () => {
      pushEvent({
        timestamp: new Date().toISOString(),
        module:    'Factor Engine',
        event:     'FACTOR SCORES',
        detail:    'Factor scores updated',
        status:    'ok',
      });
    });
  }

  /* ════════════════════════════════════════════════════════════════════════
     Boot
     ════════════════════════════════════════════════════════════════════════ */
  document.addEventListener('DOMContentLoaded', () => {
    initEventsTable();
    loadHealth();
    wireSocket();

    // Poll health every 10s, module metrics every 30s
    setInterval(loadHealth, 10_000);
  });

})();
