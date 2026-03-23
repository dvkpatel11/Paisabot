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

  // ── Toast notifications ──────────────────────────────────────────
  function toast(msg, type) {
    type = type || 'error';
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = 'toast toast-' + type;
    const text = document.createElement('span');
    text.textContent = msg;
    const close = document.createElement('button');
    close.className = 'toast-close';
    close.textContent = '×';
    close.onclick = function() { el.remove(); };
    el.appendChild(text);
    el.appendChild(close);
    container.appendChild(el);
    setTimeout(function() { if (el.parentNode) el.remove(); }, 5000);
  }

  // ── Safe fetch wrapper ───────────────────────────────────────────
  // Replaces bare fetch().then(r => r.json()) — shows a toast on any error.
  function fetchJSON(url, opts) {
    return fetch(url, opts)
      .then(function(r) {
        if (!r.ok) {
          return r.json()
            .catch(function() { return {}; })
            .then(function(d) { return Promise.reject(d.error || ('HTTP ' + r.status)); });
        }
        return r.json();
      })
      .catch(function(err) {
        var msg = (typeof err === 'string') ? err
                : (err && err.error) ? err.error
                : (err && err.message) ? err.message
                : 'Request failed';
        toast(msg);
        throw err;
      });
  }

  return { formatTime, formatDate, pct, money, regimeColor, regimeHex, toast, fetchJSON };
})();
