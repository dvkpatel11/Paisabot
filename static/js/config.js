/**
 * PaisaConfig — Config view tab loading and form handling.
 */
const PaisaConfig = (function() {
  const container = function() { return document.getElementById('tab-content'); };

  function loadCategory(category) {
    const el = container();
    el.innerHTML = '<div class="loading">Loading...</div>';

    // Universe tab: show ETF table with active-set toggles
    if (category === 'universe') {
      loadUniverse();
      return;
    }

    fetch('/api/config/' + category)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (Object.keys(data).length === 0) {
          el.innerHTML = '<div class="empty-state">No config entries for "' + category + '"</div>';
          return;
        }

        let html = '<form class="config-form" id="config-form-' + category + '">';
        // Special dropdown fields for known config keys
        var DROPDOWN_FIELDS = {
          'broker': ['alpaca', 'mt5'],
          'default_broker': ['alpaca', 'mt5'],
          'operational_mode': ['research', 'simulation', 'live'],
          'rebalance_frequency': ['daily', 'weekly', 'monthly'],
        };

        Object.entries(data).forEach(function(entry) {
          const key = entry[0];
          const info = entry[1];
          const value = info.value || '';
          const desc = info.description || '';

          html += '<label class="config-key" title="' + desc + '">' + key + '</label>';

          var options = DROPDOWN_FIELDS[key];
          if (options) {
            html += '<select class="config-input" name="' + key + '" title="' + desc + '">';
            options.forEach(function(opt) {
              html += '<option value="' + opt + '"' + (opt === value ? ' selected' : '') + '>' + opt + '</option>';
            });
            html += '</select>';
          } else if (info.type === 'bool') {
            html += '<select class="config-input" name="' + key + '" title="' + desc + '">';
            html += '<option value="true"' + (value === 'true' ? ' selected' : '') + '>true</option>';
            html += '<option value="false"' + (value === 'false' ? ' selected' : '') + '>false</option>';
            html += '</select>';
          } else {
            html += '<input class="config-input" name="' + key + '" value="' + value + '" title="' + desc + '">';
          }

          html += '<span class="text-muted" style="font-size:11px">' + (info.type || '') + (desc ? ' — ' + desc : '') + '</span>';
        });
        html += '</form>';
        html += '<div style="margin-top:12px">';
        html += '<button class="btn" onclick="PaisaConfig.save(\'' + category + '\')">Save Changes</button>';
        html += '</div>';

        el.innerHTML = html;
      })
      .catch(function() {
        el.innerHTML = '<div class="empty-state">Failed to load config</div>';
      });
  }

  function save(category) {
    const form = document.getElementById('config-form-' + category);
    if (!form) return;

    const data = {};
    const inputs = form.querySelectorAll('.config-input');
    inputs.forEach(function(input) {
      data[input.name] = input.value;
    });

    fetch('/api/config/' + category, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    .then(function(r) { return r.json(); })
    .then(function(result) {
      if (result.error) {
        alert('Error: ' + result.error);
      } else {
        alert('Saved ' + result.updated + ' config entries');
      }
    })
    .catch(function() {
      alert('Failed to save config');
    });
  }

  function loadAudit() {
    const el = container();
    el.innerHTML = '<div class="loading">Loading audit log...</div>';

    fetch('/api/config/audit?limit=50')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!data || data.length === 0) {
          el.innerHTML = '<div class="empty-state">No config changes recorded</div>';
          return;
        }

        let html = '<table style="width:100%; border-collapse:collapse; font-family:var(--font-mono); font-size:12px;">';
        html += '<thead><tr style="border-bottom:1px solid var(--border);">';
        html += '<th style="padding:8px; text-align:left;">Category</th>';
        html += '<th style="padding:8px; text-align:left;">Key</th>';
        html += '<th style="padding:8px; text-align:left;">Value</th>';
        html += '<th style="padding:8px; text-align:left;">Updated By</th>';
        html += '<th style="padding:8px; text-align:left;">Updated At</th>';
        html += '</tr></thead><tbody>';

        data.forEach(function(row) {
          html += '<tr style="border-bottom:1px solid var(--border);">';
          html += '<td style="padding:6px 8px;">' + row.category + '</td>';
          html += '<td style="padding:6px 8px;">' + row.key + '</td>';
          html += '<td style="padding:6px 8px;">' + row.value + '</td>';
          html += '<td style="padding:6px 8px;">' + (row.updated_by || '--') + '</td>';
          html += '<td style="padding:6px 8px;">' + PaisaUtils.formatTime(row.updated_at) + '</td>';
          html += '</tr>';
        });

        html += '</tbody></table>';
        el.innerHTML = html;
      });
  }

  function loadUniverse() {
    const el = container();
    el.innerHTML = '<div class="loading">Loading universe...</div>';

    fetch('/api/universe')
      .then(function(r) { return r.json(); })
      .then(function(etfs) {
        var activeCount = etfs.filter(function(e) { return e.in_active_set; }).length;

        var html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">';
        html += '<span style="font-size:13px;color:var(--text-secondary)">' + etfs.length + ' ETFs tracked, <strong>' + activeCount + '</strong> in trading set</span>';
        html += '</div>';

        html += '<table style="width:100%;border-collapse:collapse;font-size:12px;font-family:var(--font-mono)">';
        html += '<thead><tr style="border-bottom:2px solid var(--border);font-size:11px;color:var(--text-muted)">';
        html += '<th style="padding:6px 8px;text-align:center;width:60px">Trading</th>';
        html += '<th style="padding:6px 8px;text-align:left">Symbol</th>';
        html += '<th style="padding:6px 8px;text-align:left">Name</th>';
        html += '<th style="padding:6px 8px;text-align:left">Sector</th>';
        html += '<th style="padding:6px 8px;text-align:right">AUM ($B)</th>';
        html += '<th style="padding:6px 8px;text-align:right">Spread</th>';
        html += '<th style="padding:6px 8px;text-align:center">Signal</th>';
        html += '<th style="padding:6px 8px;text-align:right">Score</th>';
        html += '<th style="padding:6px 8px;text-align:left">Reason</th>';
        html += '<th style="padding:6px 8px;text-align:left">Notes</th>';
        html += '</tr></thead><tbody>';

        etfs.forEach(function(e) {
          var checked = e.in_active_set ? ' checked' : '';
          var rowStyle = e.in_active_set ? '' : 'opacity:0.6;';
          var sigColor = e.last_signal_type === 'long' ? 'var(--green)' : e.last_signal_type === 'avoid' ? 'var(--red)' : 'var(--text-muted)';

          html += '<tr style="border-bottom:1px solid var(--border);' + rowStyle + '">';
          html += '<td style="padding:6px 8px;text-align:center">';
          html += '<input type="checkbox"' + checked + ' onchange="PaisaConfig.toggleActiveSet(\'' + e.symbol + '\', this.checked)" style="cursor:pointer;width:16px;height:16px">';
          html += '</td>';
          html += '<td style="padding:6px 8px;font-weight:600">' + e.symbol + '</td>';
          html += '<td style="padding:6px 8px">' + (e.name || '') + '</td>';
          html += '<td style="padding:6px 8px">' + (e.sector || '') + '</td>';
          html += '<td style="padding:6px 8px;text-align:right">' + (e.aum_bn || '-') + '</td>';
          html += '<td style="padding:6px 8px;text-align:right">' + (e.spread_bps || '-') + ' bps</td>';
          html += '<td style="padding:6px 8px;text-align:center;color:' + sigColor + '">' + (e.last_signal_type || '-') + '</td>';
          html += '<td style="padding:6px 8px;text-align:right">' + (e.last_composite_score ? e.last_composite_score.toFixed(3) : '-') + '</td>';
          html += '<td style="padding:6px 8px;font-size:11px;color:var(--text-muted)">' + (e.active_set_reason || '') + '</td>';
          html += '<td style="padding:6px 8px;font-size:11px;color:var(--text-muted)">' + (e.notes || '') + '</td>';
          html += '</tr>';
        });

        html += '</tbody></table>';
        el.innerHTML = html;
      })
      .catch(function() {
        el.innerHTML = '<div class="empty-state">Failed to load universe</div>';
      });
  }

  function toggleActiveSet(symbol, activate) {
    var reason = activate ? prompt('Reason for adding ' + symbol + ' to trading set (optional):') : prompt('Reason for removing ' + symbol + ' from trading set (optional):');
    if (reason === null) {
      // User cancelled — revert checkbox
      loadUniverse();
      return;
    }

    fetch('/api/universe/' + symbol + '/active-set', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ in_active_set: activate, reason: reason || '' }),
    })
    .then(function(r) { return r.json(); })
    .then(function(result) {
      if (result.error) {
        alert('Error: ' + result.error);
        loadUniverse();
        return;
      }
      var msg = activate
        ? symbol + ' added to trading set. Backfill: ' + (result.onboarding && result.onboarding.backfill || 'unknown')
        : symbol + ' removed from trading set.';
      // Subtle notification instead of alert
      var notice = document.createElement('div');
      notice.style.cssText = 'position:fixed;top:12px;right:12px;background:var(--bg-panel);border:1px solid var(--green);padding:8px 16px;border-radius:6px;font-size:12px;z-index:9999;color:var(--text-primary)';
      notice.textContent = msg;
      document.body.appendChild(notice);
      setTimeout(function() { notice.remove(); }, 3000);
      loadUniverse();
    })
    .catch(function() {
      alert('Failed to update active set');
      loadUniverse();
    });
  }

  return { loadCategory, save, loadAudit, loadUniverse, toggleActiveSet };
})();
