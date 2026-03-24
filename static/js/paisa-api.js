/* ═══════════════════════════════════════════════════════════════════════════
   paisa-api.js — Application-level API layer

   Four namespaces consumed by every page template:

     PaisaTables  Tabulator registry + column formatter library
     PaisaUtils   fetch, format, toast, colour helpers
     PaisaCharts  Plotly chart factory (pre-themed chart types)
     PaisaSocket  Socket event subscription (wraps pb:* DOM events)

   Depends on: utils.js (PaisaChart, PaisaTable, fmt, plotlyTheme, Paisa)
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── PaisaTables ─────────────────────────────────────────────────────────── */
window.PaisaTables = (function () {
  'use strict';

  const _reg = new Map();   // id → PaisaTable instance

  /* ── Registry ────────────────────────────────────────────────────────── */

  /**
   * Create and register a Tabulator table.
   * @param {string} id       - DOM element id
   * @param {Array}  columns  - Tabulator column definitions
   * @param {object} opts     - merged onto PaisaTable defaults
   * @returns {PaisaTable}
   */
  function create(id, columns, opts = {}) {
    if (_reg.has(id)) _reg.get(id).destroy();
    const tbl = new PaisaTable(id, columns, opts);
    tbl.init();
    _reg.set(id, tbl);
    return tbl;
  }

  /** Replace all rows (preserves sort/filter state). */
  function setData(id, data) {
    _reg.get(id)?.setData(data ?? []);
  }

  /** Upsert rows by a key field; flashes changed rows. */
  function upsert(id, rows, key = 'symbol') {
    _reg.get(id)?.upsert(rows, key);
  }

  /** Raw Tabulator instance (for getRow, etc.). */
  function getInstance(id) {
    return _reg.get(id)?.raw ?? null;
  }

  /* ── Column formatters ───────────────────────────────────────────────── */

  function _val(cell) { return cell.getValue(); }
  function _muted(text) { return `<span style="color:var(--text-muted)">${text}</span>`; }

  const symbolFormatter = cell => {
    const v = _val(cell);
    return v ? `<span style="font-weight:600;color:var(--text-primary)">${v}</span>` : _muted('--');
  };

  const scoreFormatter = cell => {
    const v = _val(cell);
    if (v == null || isNaN(v)) return _muted('--');
    return `<span style="color:${scoreColor(v)};font-weight:600">${fmt.score(v)}</span>`;
  };

  /** Like scoreFormatter but shows factor name in tooltip. */
  const factorFormatter = cell => {
    const v = _val(cell);
    if (v == null || isNaN(v)) return _muted('--');
    const col = cell.getColumn().getDefinition();
    return `<span style="color:${scoreColor(v)}" title="${col.title}: ${fmt.score(v)}">${fmt.score(v)}</span>`;
  };

  const pnlFormatter = cell => {
    const v = _val(cell);
    if (v == null || isNaN(v)) return _muted('--');
    const color = v >= 0 ? 'var(--green)' : 'var(--red)';
    const sign  = v >= 0 ? '+' : '';
    return `<span style="color:${color};font-weight:600">${sign}${fmt.price(v)}</span>`;
  };

  const pctFormatter = cell => {
    const v = _val(cell);
    if (v == null || isNaN(v)) return _muted('--%');
    const color = v >= 0 ? 'var(--green)' : 'var(--red)';
    return `<span style="color:${color}">${fmt.pct(v, true)}</span>`;
  };

  const moneyFormatter = cell => {
    const v = _val(cell);
    return v != null ? fmt.price(v) : _muted('--');
  };

  const bpsFormatter = cell => {
    const v = _val(cell);
    return v != null ? fmt.bps(v) : _muted('--');
  };

  const sideFormatter = cell => {
    const v = (_val(cell) || '').toLowerCase();
    const color = (v === 'buy' || v === 'long')  ? 'var(--green)'
                : (v === 'sell' || v === 'short') ? 'var(--red)'
                : 'var(--text-muted)';
    return v ? `<span style="color:${color};font-weight:600;letter-spacing:.04em">${v.toUpperCase()}</span>`
             : _muted('--');
  };

  const signalFormatter = cell => {
    const v = (_val(cell) || '').toUpperCase();
    const cls = Paisa.signalBadge(v);
    return v ? `<span class="badge badge-${cls}">${v}</span>` : _muted('--');
  };

  const regimeFormatter = cell => {
    const v = (_val(cell) || '').toLowerCase();
    const cls = v === 'trending'      ? 'green'
              : v === 'rotation'      ? 'yellow'
              : v === 'risk_off'      ? 'red'
              : v === 'consolidation' ? 'muted' : 'muted';
    return v ? `<span class="badge badge-${cls}">${v.replace('_', ' ').toUpperCase()}</span>` : _muted('--');
  };

  const statusFormatter = cell => {
    const v = (_val(cell) || '').toLowerCase();
    const cls = (v === 'filled' || v === 'ok' || v === 'active')   ? 'green'
              : (v === 'pending' || v === 'submitted' || v === 'new') ? 'yellow'
              : (v === 'cancelled' || v === 'rejected' || v === 'error') ? 'red'
              : 'muted';
    return v ? `<span class="badge badge-${cls}">${v.toUpperCase()}</span>` : _muted('--');
  };

  const timeFormatter = cell => {
    const v = _val(cell);
    if (!v) return _muted('--');
    try { return dayjs(v).tz('America/New_York').format('HH:mm:ss'); }
    catch { return _muted('--'); }
  };

  const dateTimeFormatter = cell => {
    const v = _val(cell);
    if (!v) return _muted('--');
    try { return dayjs(v).tz('America/New_York').format('MM-DD HH:mm:ss'); }
    catch { return _muted('--'); }
  };

  const dateFormatter = cell => {
    const v = _val(cell);
    if (!v) return _muted('--');
    try { return dayjs(v).format('YYYY-MM-DD'); }
    catch { return _muted('--'); }
  };

  return {
    create, setData, upsert, getInstance,
    symbolFormatter, scoreFormatter, factorFormatter,
    pnlFormatter, pctFormatter, moneyFormatter, bpsFormatter,
    sideFormatter, signalFormatter, regimeFormatter,
    statusFormatter, timeFormatter, dateTimeFormatter, dateFormatter,
  };
})();


/* ── PaisaUtils ──────────────────────────────────────────────────────────── */
window.PaisaUtils = (function () {
  'use strict';

  /* ── Fetch ───────────────────────────────────────────────────────────── */

  /**
   * Fetch JSON from the server. Rejects on non-OK HTTP status.
   * @param {string} url
   * @param {RequestInit} opts
   * @returns {Promise<any>}
   */
  function fetchJSON(url, opts = {}) {
    const defaults = {
      headers: Object.assign({ 'X-Requested-With': 'XMLHttpRequest' },
                              opts.headers ?? {}),
    };
    return fetch(url, Object.assign({}, opts, defaults))
      .then(r => {
        if (!r.ok) return Promise.reject(`HTTP ${r.status} — ${url}`);
        return r.json();
      });
  }

  /* ── Number / currency formatting ───────────────────────────────────── */

  const money   = v  => fmt.price(v);
  const pct     = (v, sign = false) => fmt.pct(v, sign);
  const score   = v  => fmt.score(v);
  const compact = v  => fmt.compact(v);
  const bps     = v  => fmt.bps(v);

  /* ── Date / time ─────────────────────────────────────────────────────── */

  /** Format a UTC timestamp as HH:mm:ss ET. */
  function formatTime(ts) {
    if (!ts) return '--';
    try { return dayjs(ts).tz('America/New_York').format('HH:mm:ss'); }
    catch { return '--'; }
  }

  /** Format a UTC timestamp as YYYY-MM-DD. */
  function formatDate(ts) {
    if (!ts) return '--';
    try { return dayjs(ts).format('YYYY-MM-DD'); }
    catch { return '--'; }
  }

  /** Human-relative time (e.g. "2 minutes ago"). */
  function relTime(ts) {
    if (!ts) return '--';
    try { return dayjs(ts).fromNow(); }
    catch { return '--'; }
  }

  /* ── Regime colours ──────────────────────────────────────────────────── */

  /** Returns a badge colour suffix (matches .badge-* CSS classes). */
  function regimeColor(regime) {
    const map = {
      trending:      'green',
      rotation:      'yellow',
      risk_off:      'red',
      consolidation: 'muted',
    };
    return map[(regime || '').toLowerCase()] ?? 'muted';
  }

  /** Returns a hex colour string for use in Plotly traces. */
  function regimeHex(regime) {
    const map = {
      trending:      '#22c55e',
      rotation:      '#f59e0b',
      risk_off:      '#ef4444',
      consolidation: '#3b82f6',
    };
    return map[(regime || '').toLowerCase()] ?? '#7f9ab8';
  }

  /* ── Toast notifications ─────────────────────────────────────────────── */

  /**
   * Show a toast notification.
   * @param {string} msg
   * @param {'info'|'success'|'warning'|'error'} type
   */
  function toast(msg, type = 'info') {
    const bg = {
      info:    'var(--accent)',
      success: 'var(--green)',
      warning: 'var(--yellow)',
      error:   'var(--red)',
    }[type] ?? 'var(--accent)';

    Toastify({
      text:     msg,
      duration: 4000,
      style:    { background: bg, color: '#fff', fontFamily: 'var(--font-mono)', fontSize: '12px' },
      gravity:  'bottom',
      position: 'right',
    }).showToast();
  }

  return {
    fetchJSON,
    money, pct, score, compact, bps,
    formatTime, formatDate, relTime,
    regimeColor, regimeHex,
    toast,
  };
})();


/* ── PaisaCharts ─────────────────────────────────────────────────────────── */
window.PaisaCharts = (function () {
  'use strict';

  const _reg = new Map();   // id → PaisaChart instance

  function _get(id, type = 'plotly') {
    if (!_reg.has(id)) _reg.set(id, new PaisaChart(id, type));
    return _reg.get(id);
  }

  /* ── Chart types ─────────────────────────────────────────────────────── */

  /**
   * Annotated factor/correlation heatmap.
   * @param {string} id
   * @param {{z, x, y, title?, colorscale?}} opts
   */
  function heatmap(id, { z, x, y, title = '', colorscale } = {}) {
    const chart = _get(id);
    const scale = colorscale ?? [
      [0,   '#ef4444'],
      [0.25,'#f97316'],
      [0.5, '#f59e0b'],
      [0.75,'#84cc16'],
      [1,   '#22c55e'],
    ];
    return chart.plot([{
      type:          'heatmap',
      z, x, y,
      colorscale:    scale,
      zmin:          0,
      zmax:          1,
      text:          (z || []).map(row => (row || []).map(v => fmt.score(v))),
      texttemplate:  '%{text}',
      showscale:     true,
      hovertemplate: '<b>%{y} / %{x}</b><br>Score: %{z:.3f}<extra></extra>',
    }], {
      title:  title ? { text: title, font: { size: 12 } } : undefined,
      xaxis:  { side: 'bottom', tickangle: -30 },
      margin: { l: 110, r: 20, t: title ? 36 : 12, b: 60 },
    });
  }

  /**
   * Correlation heatmap variant — symmetric, diverging blue-white-red.
   */
  function correlationHeatmap(id, { z, x, y, title = '' } = {}) {
    const chart = _get(id);
    return chart.plot([{
      type:          'heatmap',
      z, x, y,
      colorscale:    'RdBu',
      reversescale:  true,
      zmid:          0,
      zmin:          -1,
      zmax:          1,
      text:          (z || []).map(row => (row || []).map(v => (v == null ? '--' : v.toFixed(2)))),
      texttemplate:  '%{text}',
      showscale:     true,
      hovertemplate: '<b>%{y} / %{x}</b><br>Corr: %{z:.3f}<extra></extra>',
    }], {
      title:  title ? { text: title, font: { size: 12 } } : undefined,
      xaxis:  { side: 'bottom', tickangle: -30 },
      margin: { l: 80, r: 20, t: title ? 36 : 12, b: 80 },
    });
  }

  /**
   * Multi-trace time-series line chart.
   * @param {string} id
   * @param {Array}  traces  - Plotly trace array (type/mode default to scatter/lines)
   * @param {string} [title]
   */
  function timeSeries(id, traces, title = '') {
    const chart = _get(id);
    const normalised = traces.map(t => Object.assign({ type: 'scatter', mode: 'lines' }, t));
    return chart.react(normalised, {
      title:  title ? { text: title, font: { size: 12 } } : undefined,
      legend: { orientation: 'h', y: -0.15, font: { size: 10 } },
    });
  }

  /**
   * Vertical bar chart — used for regime timeline, throughput, etc.
   * @param {string} id
   * @param {{x, y, colors?, title?}} opts
   */
  function bar(id, { x, y, colors, title = '' } = {}) {
    const chart = _get(id);
    const trace = { type: 'bar', x, y };
    if (colors) trace.marker = { color: colors };
    return chart.plot([trace], {
      title:  title ? { text: title, font: { size: 12 } } : undefined,
      margin: { l: 48, r: 12, t: title ? 36 : 8, b: 24 },
      bargap: 0.02,
    });
  }

  /**
   * Horizontal bar chart — sector rankings, factor rankings, etc.
   * @param {string} id
   * @param {{labels, values, title?}} opts
   */
  function horizontalBar(id, { labels, values, title = '' } = {}) {
    const chart = _get(id);
    const colors = (values || []).map(v =>
      v >= 0.65 ? '#22c55e' : v >= 0.40 ? '#f59e0b' : '#ef4444'
    );
    return chart.plot([{
      type:        'bar',
      orientation: 'h',
      x:           values,
      y:           labels,
      marker:      { color: colors },
      hovertemplate: '<b>%{y}</b>: %{x:.3f}<extra></extra>',
    }], {
      title:  title ? { text: title, font: { size: 12 } } : undefined,
      xaxis:  { range: [0, 1], gridcolor: 'var(--border)' },
      margin: { l: 70, r: 20, t: title ? 36 : 12, b: 36 },
    });
  }

  /**
   * Donut / pie chart — sector exposure, allocation.
   * @param {string} id
   * @param {{labels, values, title?}} opts
   */
  function pie(id, { labels, values, title = '' } = {}) {
    const chart = _get(id);
    return chart.plot([{
      type:   'pie',
      labels,
      values,
      hole:   0.4,
      textinfo:     'label+percent',
      textfont:     { size: 11 },
      hovertemplate: '<b>%{label}</b><br>%{value:.1%}<extra></extra>',
      marker: {
        colors: ['#3b82f6','#22c55e','#f59e0b','#ef4444','#a855f7','#06b6d4','#f97316','#ec4899'],
        line:   { color: 'var(--bg-base)', width: 2 },
      },
    }], {
      title:   title ? { text: title, font: { size: 12 } } : undefined,
      legend:  { orientation: 'v', font: { size: 10 } },
      margin:  { l: 20, r: 120, t: title ? 36 : 12, b: 20 },
      showlegend: true,
    });
  }

  /**
   * Scatter / bubble chart — rotation quadrant etc.
   * Full Plotly trace control; just applies theme.
   */
  function scatter(id, traces, layoutOverrides = {}) {
    return _get(id).plot(traces, layoutOverrides);
  }

  /** Efficient data update on an existing chart (keeps zoom). */
  function update(id, traces, layoutOverrides = {}) {
    return _get(id).react(traces, layoutOverrides);
  }

  /** Force resize on a managed chart. */
  function resize(id) {
    _reg.get(id)?.resize();
  }

  return {
    heatmap, correlationHeatmap,
    timeSeries, bar, horizontalBar,
    pie, scatter, update, resize,
  };
})();


/* ── PaisaSocket ─────────────────────────────────────────────────────────── */
window.PaisaSocket = (function () {
  'use strict';

  /**
   * Subscribe to a socket event.
   * socket.js dispatches all server events as 'pb:<event>' CustomEvents so
   * page scripts stay decoupled from the raw Socket.IO connection.
   *
   * @param {string}   event    - server event name (e.g. 'signals', 'trade')
   * @param {Function} handler  - called with event.detail (the payload)
   */
  function on(event, handler) {
    document.addEventListener(`pb:${event}`, e => handler(e.detail));
  }

  /**
   * Remove a previously registered handler.
   * You must pass the *same function reference* used in on().
   */
  function off(event, handler) {
    const wrapped = e => handler(e.detail);
    document.removeEventListener(`pb:${event}`, wrapped);
  }

  /**
   * Emit an event to the server via Socket.IO.
   * @param {string} event
   * @param {*}      data
   */
  function emit(event, data) {
    if (window.pbSocket) {
      window.pbSocket.emit(event, data);
    } else {
      console.warn('[PaisaSocket] pbSocket not ready — cannot emit', event);
    }
  }

  return { on, off, emit };
})();
