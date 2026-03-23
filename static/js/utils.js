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
