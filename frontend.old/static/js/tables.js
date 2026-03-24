/**
 * PaisaTables — Tabulator wrapper with dark theme and rich, color-coded formatters.
 *
 * Formatter catalog:
 *   moneyFormatter      — $1,234.56
 *   pnlFormatter        — +$123.45 green / -$67.89 red
 *   pctFormatter        — +12.34% green / -5.67% red
 *   bpsFormatter        — 3.2 bps (yellow > 5, red > 10)
 *   scoreFormatter      — 0.72 with inline score bar (green/yellow/red zones)
 *   sideFormatter       — BUY / SELL badge
 *   statusFormatter     — filled / pending / rejected badge
 *   regimeFormatter     — trending / rotation / risk_off / consolidation badge
 *   signalFormatter     — LONG / NEUTRAL / AVOID colored text
 *   factorFormatter     — 0.00–1.00 with heatmap background
 *   timeFormatter       — HH:MM:SS
 *   dateFormatter       — Mar 16
 *   dateTimeFormatter   — Mar 16 14:30
 *   symbolFormatter     — bold white text
 */
var PaisaTables = (function() {
  var instances = {};

  function create(containerId, columns, options) {
    if (instances[containerId]) {
      try { instances[containerId].destroy(); } catch(e) {}
    }

    var opts = {
      layout: 'fitColumns',
      maxHeight: 400,
      placeholder: 'No data',
      columns: columns,
      renderVertical: 'virtual',
      rowFormatter: function(row) {
        var data = row.getData();
        var el = row.getElement();
        if (data.signal_type === 'long' || data.side === 'buy') {
          el.style.borderLeft = '2px solid rgba(63,185,80,0.3)';
        } else if (data.signal_type === 'avoid' || data.side === 'sell') {
          el.style.borderLeft = '2px solid rgba(248,81,73,0.3)';
        } else {
          el.style.borderLeft = '2px solid transparent';
        }
      },
    };

    if (options) {
      for (var k in options) {
        if (options.hasOwnProperty(k)) opts[k] = options[k];
      }
    }

    instances[containerId] = new Tabulator('#' + containerId, opts);
    return instances[containerId];
  }

  function setData(containerId, data) {
    if (instances[containerId]) {
      instances[containerId].setData(data);
    }
  }

  function getInstance(containerId) {
    return instances[containerId];
  }

  // ── Helpers ──────────────────────────────────────────────────────

  function _num(val) {
    if (val == null || val === '' || val === '--') return null;
    var n = parseFloat(val);
    return isNaN(n) ? null : n;
  }

  function _span(text, cls) {
    return '<span class="' + cls + '">' + text + '</span>';
  }

  // ── Money: $1,234.56 ────────────────────────────────────────────

  function moneyFormatter(cell) {
    var n = _num(cell.getValue());
    if (n == null) return _span('--', 'cell-muted');
    return _span(
      '$' + n.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}),
      'cell-money'
    );
  }

  // ── P&L: +$123.45 / -$67.89 ────────────────────────────────────

  function pnlFormatter(cell) {
    var n = _num(cell.getValue());
    if (n == null) return _span('--', 'cell-muted');
    var cls = n >= 0 ? 'cell-positive' : 'cell-negative';
    var sign = n >= 0 ? '+$' : '-$';
    var abs = Math.abs(n);
    return _span(
      sign + abs.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}),
      cls + ' cell-bold'
    );
  }

  // ── Percent: +12.34% / -5.67% ──────────────────────────────────

  function pctFormatter(cell) {
    var n = _num(cell.getValue());
    if (n == null) return _span('--', 'cell-muted');
    var pct = n * 100;
    var cls = pct >= 0 ? 'cell-positive' : 'cell-negative';
    var sign = pct >= 0 ? '+' : '';
    return _span(sign + pct.toFixed(2) + '%', cls);
  }

  // ── Basis points: 3.2 bps ──────────────────────────────────────

  function bpsFormatter(cell) {
    var n = _num(cell.getValue());
    if (n == null) return _span('--', 'cell-muted');
    var cls = n > 10 ? 'cell-negative' : n > 5 ? 'cell-neutral' : 'cell-muted';
    return _span(n.toFixed(1), cls);
  }

  // ── Score: 0.72 with background bar ────────────────────────────

  function scoreFormatter(cell) {
    var n = _num(cell.getValue());
    if (n == null) return _span('--', 'cell-muted');
    var color = n >= 0.65 ? 'var(--green)' : n >= 0.40 ? 'var(--yellow)' : 'var(--red)';
    var pct = Math.min(n, 1) * 100;
    return '<span class="cell-score-wrap">' +
      '<span class="cell-score-bg" style="width:' + pct + '%;background:' + color + '"></span>' +
      '<span class="cell-score-val" style="color:' + color + '">' + n.toFixed(2) + '</span>' +
      '</span>';
  }

  // ── Side: BUY / SELL badge ─────────────────────────────────────

  function sideFormatter(cell) {
    var v = (cell.getValue() || '').toLowerCase();
    if (!v || v === '--') return _span('--', 'cell-muted');
    var cls = (v === 'buy' || v === 'long') ? 'cell-side cell-side-buy' : 'cell-side cell-side-sell';
    return _span(v.toUpperCase(), cls);
  }

  // ── Status: filled / pending / rejected badge ──────────────────

  function statusFormatter(cell) {
    var v = (cell.getValue() || '').toLowerCase();
    if (!v) return _span('--', 'cell-muted');
    var positives = ['filled', 'ok', 'open', 'active', 'complete', 'completed', 'accepted'];
    var warnings  = ['partial', 'pending', 'new', 'partially_filled', 'held', 'suspended'];
    var negatives = ['rejected', 'cancelled', 'canceled', 'error', 'failed', 'expired', 'stopped'];
    var cls = 'cell-status ';
    if (positives.indexOf(v) !== -1) cls += 'cell-status-ok';
    else if (warnings.indexOf(v) !== -1) cls += 'cell-status-pending';
    else if (negatives.indexOf(v) !== -1) cls += 'cell-status-error';
    else cls += 'cell-status-pending';
    return _span(v.replace(/_/g, ' '), cls);
  }

  // ── Regime: trending / rotation / risk_off / consolidation ─────

  function regimeFormatter(cell) {
    var v = (cell.getValue() || '').toLowerCase();
    if (!v || v === 'unknown') return _span('--', 'cell-muted');
    var cls = 'cell-regime cell-regime-' + v;
    return _span(v.replace(/_/g, ' '), cls);
  }

  // ── Signal: LONG / NEUTRAL / AVOID ─────────────────────────────

  function signalFormatter(cell) {
    var v = (cell.getValue() || '').toLowerCase();
    if (!v || v === '-') return _span('-', 'cell-muted');
    var cls = 'cell-signal-' + (v === 'long' ? 'long' : v === 'avoid' ? 'avoid' : 'neutral');
    return _span(v.toUpperCase(), cls);
  }

  // ── Factor: 0.00–1.00 with heatmap background ─────────────────

  function factorFormatter(cell) {
    var n = _num(cell.getValue());
    if (n == null) return _span('--', 'cell-muted');
    var r, g, b;
    if (n < 0.5) {
      var t = n / 0.5;
      r = Math.round(248 + (210 - 248) * t);
      g = Math.round(81  + (153 - 81)  * t);
      b = Math.round(73  + (34  - 73)  * t);
    } else {
      var t2 = (n - 0.5) / 0.5;
      r = Math.round(210 + (63  - 210) * t2);
      g = Math.round(153 + (185 - 153) * t2);
      b = Math.round(34  + (80  - 34)  * t2);
    }
    var bg = 'rgba(' + r + ',' + g + ',' + b + ',0.15)';
    var fg = 'rgb(' + r + ',' + g + ',' + b + ')';
    return '<span class="cell-factor" style="background:' + bg + ';color:' + fg + '">' + n.toFixed(3) + '</span>';
  }

  // ── Time: HH:MM:SS ─────────────────────────────────────────────

  function timeFormatter(cell) {
    var val = cell.getValue();
    if (!val) return _span('--', 'cell-muted');
    return _span(PaisaUtils.formatTime(val), 'cell-muted');
  }

  // ── Date: Mar 16 ───────────────────────────────────────────────

  function dateFormatter(cell) {
    var val = cell.getValue();
    if (!val) return _span('--', 'cell-muted');
    return PaisaUtils.formatDate(val);
  }

  // ── DateTime: Mar 16 14:30 ─────────────────────────────────────

  function dateTimeFormatter(cell) {
    var val = cell.getValue();
    if (!val) return _span('--', 'cell-muted');
    return PaisaUtils.formatDate(val) + ' ' + PaisaUtils.formatTime(val);
  }

  // ── Symbol: bold white ─────────────────────────────────────────

  function symbolFormatter(cell) {
    var v = cell.getValue();
    if (!v) return _span('--', 'cell-muted');
    return _span(v, 'cell-bold');
  }

  return {
    create: create,
    setData: setData,
    getInstance: getInstance,
    moneyFormatter: moneyFormatter,
    pnlFormatter: pnlFormatter,
    pctFormatter: pctFormatter,
    bpsFormatter: bpsFormatter,
    scoreFormatter: scoreFormatter,
    sideFormatter: sideFormatter,
    statusFormatter: statusFormatter,
    regimeFormatter: regimeFormatter,
    signalFormatter: signalFormatter,
    factorFormatter: factorFormatter,
    timeFormatter: timeFormatter,
    dateFormatter: dateFormatter,
    dateTimeFormatter: dateTimeFormatter,
    symbolFormatter: symbolFormatter,
  };
})();
