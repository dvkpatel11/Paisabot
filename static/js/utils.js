/* ═══════════════════════════════════════════════════════════════════════════
   utils.js — Shared formatting helpers, chart theme, color utilities
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── Number / currency formatting ───────────────────────────────────────── */
window.fmt = {
  price(v, decimals = 2) {
    if (v == null || isNaN(v)) return '--';
    return numeral(v).format(`$0,0.${'0'.repeat(decimals)}`);
  },
  pct(v, showSign = false) {
    if (v == null || isNaN(v)) return '--%';
    const sign = showSign && v > 0 ? '+' : '';
    return `${sign}${numeral(v / 100).format('0.00%')}`;
  },
  bps(v) {
    if (v == null || isNaN(v)) return '-- bps';
    return `${numeral(v).format('0.0')} bps`;
  },
  compact(v) {
    if (v == null || isNaN(v)) return '--';
    if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
    if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
    if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
    return numeral(v).format('$0,0');
  },
  score(v) {
    if (v == null || isNaN(v)) return '--';
    return numeral(v).format('0.000');
  },
  number(v, dec = 0) {
    if (v == null || isNaN(v)) return '--';
    return numeral(v).format(`0,0${ dec ? '.' + '0'.repeat(dec) : '' }`);
  },
};

/* ── Score → colour mapping (0→red, 0.5→yellow, 1→green) ──────────────── */
window.scoreColor = function(score) {
  if (score == null || isNaN(score)) return 'var(--text-muted)';
  if (score >= 0.65) return 'var(--green)';
  if (score >= 0.40) return 'var(--yellow)';
  return 'var(--red)';
};

window.scoreColorBg = function(score) {
  if (score == null || isNaN(score)) return 'var(--bg-overlay)';
  if (score >= 0.65) return 'var(--green-bg)';
  if (score >= 0.40) return 'var(--yellow-bg)';
  return 'var(--red-bg)';
};

/* ── PnL colour ──────────────────────────────────────────────────────────── */
window.pnlColor = function(v) {
  if (v > 0)  return 'var(--green)';
  if (v < 0)  return 'var(--red)';
  return 'var(--text-secondary)';
};

/* ── Plotly dark theme defaults ─────────────────────────────────────────── */
window.plotlyTheme = function(overrides = {}) {
  return Object.assign({
    paper_bgcolor: 'transparent',
    plot_bgcolor:  'transparent',
    font:          { family: "'JetBrains Mono', monospace", size: 11, color: '#7f9ab8' },
    margin:        { l: 48, r: 12, t: 12, b: 36 },
    xaxis: {
      gridcolor:    '#1e3050',
      linecolor:    '#1e3050',
      tickcolor:    '#1e3050',
      tickfont:     { size: 10 },
      zeroline:     false,
    },
    yaxis: {
      gridcolor:    '#1e3050',
      linecolor:    '#1e3050',
      tickcolor:    '#1e3050',
      tickfont:     { size: 10 },
      zeroline:     true,
      zerolinecolor:'#1e3050',
    },
    colorway: ['#3b82f6','#22c55e','#f59e0b','#ef4444','#a855f7','#06b6d4','#f97316'],
  }, overrides);
};

/* ── Chart.js dark theme defaults ───────────────────────────────────────── */
window.chartjsTheme = {
  color:          '#7f9ab8',
  backgroundColor:'transparent',
  plugins: {
    legend: { labels: { color: '#7f9ab8', font: { family: "'JetBrains Mono'", size: 11 } } },
    tooltip: {
      backgroundColor: '#162032',
      borderColor:     '#1e3050',
      borderWidth:     1,
      titleColor:      '#e2eaf6',
      bodyColor:       '#7f9ab8',
      titleFont:       { family: "'JetBrains Mono'", size: 11 },
      bodyFont:        { family: "'JetBrains Mono'", size: 11 },
    },
  },
  scales: {
    x: {
      grid:  { color: '#1e3050' },
      ticks: { color: '#7f9ab8', font: { family: "'JetBrains Mono'", size: 10 } },
    },
    y: {
      grid:  { color: '#1e3050' },
      ticks: { color: '#7f9ab8', font: { family: "'JetBrains Mono'", size: 10 } },
    },
  },
};

/* ── Lightweight Charts (TradingView) config ─────────────────────────────── */
window.lwcTheme = function(overrides = {}) {
  return Object.assign({
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor:  '#7f9ab8',
      fontFamily: "'JetBrains Mono', monospace",
      fontSize:   11,
    },
    grid: {
      vertLines:  { color: '#1e3050' },
      horzLines:  { color: '#1e3050' },
    },
    crosshair: {
      vertLine:   { color: '#3b82f6', labelBackgroundColor: '#162032' },
      horzLine:   { color: '#3b82f6', labelBackgroundColor: '#162032' },
    },
    rightPriceScale:  { borderColor: '#1e3050' },
    timeScale:        { borderColor: '#1e3050', timeVisible: true, secondsVisible: false },
    handleScroll:     true,
    handleScale:      true,
  }, overrides);
};

/* ── Tabulator dark theme column defaults ───────────────────────────────── */
window.tabulatorDefaults = {
  theme:        'midnight',
  layout:       'fitDataStretch',
  responsiveLayout: false,
  height:       '100%',
  pagination:   false,
  movableColumns: false,
  resizableColumns: false,
  tooltips:     true,
};

/* ── DOM helpers ─────────────────────────────────────────────────────────── */
window.$ = (sel, ctx = document) => ctx.querySelector(sel);
window.$$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

/* Flash a table row to indicate update */
window.flashRow = function(rowEl) {
  rowEl.classList.remove('row-flash');
  void rowEl.offsetWidth;  // reflow
  rowEl.classList.add('row-flash');
};

/* Debounce */
window.debounce = function(fn, wait) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
};

/* ═══════════════════════════════════════════════════════════════════════════
   PaisaChart — lifecycle wrapper for Plotly, Lightweight Charts, and Chart.js
   Usage:
     const chart = new PaisaChart('my-div', 'plotly');
     chart.plot(traces, layoutOverrides);   // first render
     chart.react(traces);                   // data update (no full redraw)
     chart.lwc(options);                    // for LWC type
     // auto-resizes via ResizeObserver
     chart.destroy();                       // cleanup
   ═══════════════════════════════════════════════════════════════════════════ */
class PaisaChart {
  /**
   * @param {string|HTMLElement} container  - id string or DOM element
   * @param {'plotly'|'lwc'|'chartjs'}  type
   */
  constructor(container, type = 'plotly') {
    this.el   = typeof container === 'string' ? document.getElementById(container) : container;
    this.type = type;
    this._instance        = null;
    this._resizeObserver  = null;
  }

  /* ── Plotly ────────────────────────────────────────────────────────────── */

  /** Full render (first paint or major structural change). */
  plot(traces, layoutOverrides = {}, configOverrides = {}) {
    if (!this.el) return Promise.resolve();
    const layout = plotlyTheme(layoutOverrides);
    const config = Object.assign({
      responsive:  true,
      displaylogo: false,
      modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
    }, configOverrides);
    return Plotly.newPlot(this.el, traces, layout, config)
      .then(() => this._attachResize());
  }

  /**
   * Efficient data-only update — keeps zoom/pan state.
   * Prefer this over plot() for live updates.
   */
  react(traces, layoutOverrides = {}) {
    if (!this.el) return Promise.resolve();
    return Plotly.react(this.el, traces, plotlyTheme(layoutOverrides));
  }

  /** Extend an existing trace by index (streaming append). */
  extend(traceUpdate, traceIndices, maxPoints) {
    if (!this.el) return;
    Plotly.extendTraces(this.el, traceUpdate, traceIndices, maxPoints);
  }

  /* ── Lightweight Charts ────────────────────────────────────────────────── */

  /**
   * Create a Lightweight Charts instance and return it for series setup.
   * @param {object} options - merged onto lwcTheme()
   * @returns {LightweightCharts.IChartApi}
   */
  lwc(options = {}) {
    if (!this.el) return null;
    this._instance = LightweightCharts.createChart(
      this.el,
      Object.assign(lwcTheme(), { width: this.el.clientWidth, height: this.el.clientHeight || 240 }, options)
    );
    this._attachResize();
    return this._instance;
  }

  /* ── Chart.js ──────────────────────────────────────────────────────────── */

  /**
   * Create a Chart.js instance.
   * @param {string} chartType - 'bar'|'doughnut'|'line'|etc.
   * @param {object} data      - Chart.js data object
   * @param {object} optOverrides - merged onto chartjsTheme
   * @returns {Chart}
   */
  chartjs(chartType, data, optOverrides = {}) {
    if (!this.el) return null;
    const ctx = this.el.getContext ? this.el : this.el.querySelector('canvas');
    const options = Object.assign({}, chartjsTheme, optOverrides);
    this._instance = new Chart(ctx, { type: chartType, data, options });
    return this._instance;
  }

  /* ── Shared ────────────────────────────────────────────────────────────── */

  /** Force a resize (call from layout changes or manually). */
  resize() {
    if (!this.el) return;
    if (this.type === 'plotly') {
      Plotly.Plots.resize(this.el);
    } else if (this.type === 'lwc' && this._instance) {
      this._instance.applyOptions({ width: this.el.clientWidth });
    } else if (this.type === 'chartjs' && this._instance) {
      this._instance.resize();
    }
  }

  /** Detach observers and purge underlying chart instance. */
  destroy() {
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = null;
    }
    if (this.type === 'plotly' && this.el) {
      Plotly.purge(this.el);
    } else if (this.type === 'lwc' && this._instance) {
      this._instance.remove();
    } else if (this.type === 'chartjs' && this._instance) {
      this._instance.destroy();
    }
    this._instance = null;
  }

  _attachResize() {
    if (this._resizeObserver || !window.ResizeObserver) return;
    this._resizeObserver = new ResizeObserver(debounce(() => this.resize(), 150));
    this._resizeObserver.observe(this.el);
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   PaisaTable — lifecycle wrapper for Tabulator
   Usage:
     const tbl = new PaisaTable('my-div', columns, { height: 400 });
     tbl.init(initialData);
     tbl.setData(newRows);         // full replace
     tbl.upsert(rows, 'symbol');   // add or update by key, flashes changed rows
     tbl.destroy();
   ═══════════════════════════════════════════════════════════════════════════ */
class PaisaTable {
  /**
   * @param {string|HTMLElement} container
   * @param {Array}  columns  - Tabulator column definitions
   * @param {object} options  - merged onto tabulatorDefaults
   */
  constructor(container, columns, options = {}) {
    this.el       = typeof container === 'string' ? document.getElementById(container) : container;
    this._columns = columns;
    this._options = options;
    this._table   = null;
  }

  /** Initialise Tabulator (call once after DOM is ready). */
  init(initialData = []) {
    if (!this.el) return this;
    this._table = new Tabulator(this.el, Object.assign({}, tabulatorDefaults, {
      columns: this._columns,
      data:    initialData,
    }, this._options));
    return this;
  }

  /** Full data replace (preserves sort/filter state). */
  setData(data) {
    this._table?.setData(data);
  }

  /**
   * Upsert rows by a key field — updates existing rows and flashes them,
   * prepends new rows. Avoids full re-render for streaming updates.
   * @param {Array}  rows - array of row objects
   * @param {string} key  - field name used as unique identifier
   */
  upsert(rows, key = 'id') {
    if (!this._table) return;
    rows.forEach(row => {
      const existing = this._table.getRow(row[key]);
      if (existing) {
        existing.update(row);
        flashRow(existing.getElement());
      } else {
        this._table.addRow(row, true);  // prepend
      }
    });
  }

  /** Scroll to the top of the table. */
  scrollTop() {
    this._table?.scrollToRow(this._table.getRows()[0], 'top', false);
  }

  /** Destroy Tabulator instance and free DOM. */
  destroy() {
    this._table?.destroy();
    this._table = null;
  }

  /** Expose the raw Tabulator instance for advanced operations. */
  get raw() { return this._table; }
}

/* ── Paisa — high-level chart & UI helpers ───────────────────────────────── */
window.Paisa = {

  /**
   * Drop-in Plotly.newPlot with dark theme pre-applied.
   * @param {string|HTMLElement} el   - DOM id or element
   * @param {Array}  traces           - Plotly trace array
   * @param {Object} layoutOverrides  - merged on top of plotlyTheme()
   * @param {Object} configOverrides  - merged on top of safe defaults
   */
  plot(el, traces, layoutOverrides = {}, configOverrides = {}) {
    const layout = plotlyTheme(layoutOverrides);
    const config = Object.assign({
      responsive:   true,
      displaylogo:  false,
      modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
    }, configOverrides);
    return Plotly.newPlot(el, traces, layout, config);
  },

  /** React to container resize (call from ResizeObserver or window resize). */
  resize(el) {
    Plotly.Plots.resize(typeof el === 'string' ? document.getElementById(el) : el);
  },

  /**
   * Score → badge class suffix (green / yellow / red / muted).
   * Matches the .badge-* variants in components.css.
   */
  scoreBadge(score) {
    if (score == null || isNaN(score)) return 'muted';
    if (score >= 0.65) return 'green';
    if (score >= 0.40) return 'yellow';
    return 'red';
  },

  /** Signal string → badge class suffix. */
  signalBadge(sig) {
    const map = { LONG: 'green', SHORT: 'red', NEUTRAL: 'muted', AVOID: 'orange' };
    return map[(sig || '').toUpperCase()] || 'muted';
  },

  /** Regime string → CSS class modifier (matches .regime-pill variants). */
  regimeClass(regime) {
    return 'regime-' + (regime || 'consolidation').toLowerCase().replace(/[^a-z_]/g, '');
  },
};

/* ── Expose manager classes globally ─────────────────────────────────────── */
window.PaisaChart = PaisaChart;
window.PaisaTable = PaisaTable;
