/**
 * PaisaTables — Tabulator wrapper with dark theme and custom formatters.
 */
const PaisaTables = (function() {
  const instances = {};

  function create(containerId, columns, options) {
    // Destroy existing instance if re-creating
    if (instances[containerId]) {
      try { instances[containerId].destroy(); } catch(e) {}
    }

    const opts = Object.assign({
      layout: 'fitColumns',
      height: 'auto',
      maxHeight: 400,
      placeholder: 'No data',
      columns: columns,
      renderVertical: 'virtual',
    }, options || {});

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

  // Custom formatters
  function pnlFormatter(cell) {
    const val = cell.getValue();
    if (val == null) return '--';
    const num = parseFloat(val);
    const color = num >= 0 ? 'var(--green)' : 'var(--red)';
    const sign = num >= 0 ? '+' : '';
    return '<span style="color:' + color + '">' + sign + num.toFixed(2) + '</span>';
  }

  function pctFormatter(cell) {
    const val = cell.getValue();
    if (val == null) return '--';
    return (parseFloat(val) * 100).toFixed(2) + '%';
  }

  function bpsFormatter(cell) {
    const val = cell.getValue();
    if (val == null) return '--';
    const num = parseFloat(val);
    const color = Math.abs(num) > 5 ? 'var(--yellow)' : 'var(--text-primary)';
    return '<span style="color:' + color + '">' + num.toFixed(1) + '</span>';
  }

  function scoreFormatter(cell) {
    const val = cell.getValue();
    if (val == null) return '--';
    const num = parseFloat(val);
    const color = num >= 0.65 ? 'var(--green)' : num >= 0.40 ? 'var(--yellow)' : 'var(--red)';
    return '<span style="color:' + color + '; font-weight:600">' + num.toFixed(4) + '</span>';
  }

  function timeFormatter(cell) {
    const val = cell.getValue();
    if (!val) return '--';
    return PaisaUtils.formatTime(val);
  }

  return {
    create, setData, getInstance,
    pnlFormatter, pctFormatter, bpsFormatter, scoreFormatter, timeFormatter,
  };
})();
