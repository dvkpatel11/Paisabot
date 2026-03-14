/**
 * PaisaUtils — Shared formatting and helper utilities.
 */
const PaisaUtils = (function() {

  function formatTime(isoString) {
    if (!isoString) return '--';
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString('en-US', { hour12: false });
    } catch (e) {
      return isoString;
    }
  }

  function formatDate(isoString) {
    if (!isoString) return '--';
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch (e) {
      return isoString;
    }
  }

  function pct(value) {
    if (value == null) return '--%';
    return (parseFloat(value) * 100).toFixed(2) + '%';
  }

  function money(value) {
    if (value == null) return '--';
    return '$' + parseFloat(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function regimeColor(regime) {
    const map = {
      'trending': 'green',
      'rotation': 'yellow',
      'risk_off': 'red',
      'consolidation': 'blue',
    };
    return map[regime] || 'blue';
  }

  function regimeHex(regime) {
    const map = {
      'trending': '#3fb950',
      'rotation': '#d29922',
      'risk_off': '#f85149',
      'consolidation': '#58a6ff',
    };
    return map[regime] || '#8b949e';
  }

  return { formatTime, formatDate, pct, money, regimeColor, regimeHex };
})();
