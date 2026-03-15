/**
 * PaisaConfig — Config view tab loading and form handling.
 */
const PaisaConfig = (function() {
  const container = function() { return document.getElementById('tab-content'); };

  function loadCategory(category) {
    const el = container();
    el.innerHTML = '<div class="loading">Loading...</div>';

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

  return { loadCategory, save, loadAudit };
})();
