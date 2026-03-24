/**
 * PaisaPipelines — Realtime pipeline monitoring UI.
 * Renders 7-module flow diagram, metric cards, throughput chart, and event feed.
 */
const PaisaPipelines = (function() {
  // Module metadata
  const MODULES = [
    { id: 'market_data',      label: 'Market Data',         letter: 'D', color: '#58a6ff' },
    { id: 'factor_engine',    label: 'Factor Engine',       letter: 'F', color: '#bc8cff' },
    { id: 'signal_engine',    label: 'Signal Engine',       letter: 'S', color: '#d29922' },
    { id: 'portfolio_engine', label: 'Portfolio Construction', letter: 'P', color: '#39d2c0' },
    { id: 'risk_engine',      label: 'Risk Engine',         letter: 'R', color: '#f85149' },
    { id: 'execution_engine', label: 'Execution Engine',    letter: 'E', color: '#3fb950' },
    { id: 'dashboard',        label: 'Dashboard',           letter: 'M', color: '#8b949e' },
  ];

  const STATUS_COLORS = {
    ok:       '#3fb950',
    idle:     '#8b949e',
    degraded: '#d29922',
    stale:    '#d29922',
    error:    '#f85149',
  };

  // Throughput history (rolling 60 data points)
  const throughputHistory = { timestamps: [], items: [] };
  const MAX_HISTORY = 60;

  // Event feed
  const events = [];
  const MAX_EVENTS = 100;
  let eventsTable = null;

  // ── Pipeline Flow Diagram ──────────────────────────────────────

  function renderFlowDiagram(modules) {
    const container = document.getElementById('pipeline-flow');
    if (!container) return;

    let html = '<div class="pipeline-nodes">';
    modules.forEach(function(mod, i) {
      const meta = MODULES.find(function(m) { return m.id === mod.id; }) || MODULES[0];
      const statusColor = STATUS_COLORS[mod.status] || STATUS_COLORS.idle;

      html += '<div class="pipeline-node" id="node-' + mod.id + '">';
      html += '  <div class="pipeline-node-header">';
      html += '    <span class="pipeline-node-index" style="background:' + meta.color + '">' + mod.index + '</span>';
      html += '    <span class="pipeline-node-name">' + mod.name + '</span>';
      html += '    <span class="pipeline-node-status" style="color:' + statusColor + '">';
      html += '      <span class="pipeline-status-dot" style="background:' + statusColor + '"></span> ' + mod.status.toUpperCase();
      html += '    </span>';
      html += '  </div>';
      html += '  <div class="pipeline-node-metrics">';
      html += '    <span>' + (mod.items_processed || 0) + ' processed</span>';
      if (mod.compute_time_ms != null) {
        html += '    <span>' + mod.compute_time_ms + ' ms</span>';
      }
      if (mod.queue_depth != null) {
        html += '    <span>queue: ' + mod.queue_depth + '</span>';
      }
      html += '  </div>';
      html += '</div>';

      if (i < modules.length - 1) {
        html += '<div class="pipeline-arrow">&#9654;</div>';
      }
    });
    html += '</div>';
    container.innerHTML = html;
  }

  // ── Metric Cards ───────────────────────────────────────────────

  function updateMetricCard(mod) {
    var dot = document.getElementById('dot-' + mod.id);
    if (dot) {
      var sc = STATUS_COLORS[mod.status] || STATUS_COLORS.idle;
      dot.style.background = (mod.status === 'ok' || mod.status === 'idle') ? '' : sc;
      if (mod.status === 'error') dot.classList.add('error');
      else dot.classList.remove('error');
    }

    setVal(mod.id + '-items', mod.items_processed || 0);
    if (mod.compute_time_ms != null) setVal(mod.id + '-time', mod.compute_time_ms + ' ms');
    if (mod.queue_depth != null) setVal(mod.id + '-queue', mod.queue_depth);
    setVal(mod.id + '-status', mod.status);
    setVal(mod.id + '-last', mod.last_activity ? PaisaUtils.formatTime(mod.last_activity) : '--');
  }

  function setVal(suffix, value) {
    var el = document.getElementById('val-' + suffix);
    if (el) el.textContent = value;
  }

  // ── Throughput Chart ───────────────────────────────────────────

  function initThroughputChart() {
    var trace = {
      x: [],
      y: [],
      type: 'scatter',
      mode: 'lines+markers',
      name: 'Items/min',
      line: { color: '#58a6ff', width: 2 },
      marker: { size: 4 },
    };

    PaisaCharts.timeSeries('throughput-chart', [trace], 'Pipeline Throughput');
  }

  function updateThroughputChart(totalItems) {
    var now = new Date().toISOString();
    throughputHistory.timestamps.push(now);
    throughputHistory.items.push(totalItems);

    if (throughputHistory.timestamps.length > MAX_HISTORY) {
      throughputHistory.timestamps.shift();
      throughputHistory.items.shift();
    }

    Plotly.update('throughput-chart', {
      x: [throughputHistory.timestamps],
      y: [throughputHistory.items],
    }, {}, [0]);
  }

  // ── Kill Switches ──────────────────────────────────────────────

  function renderKillSwitches(switches) {
    var container = document.getElementById('kill-switch-grid');
    if (!container) return;

    var html = '';
    Object.keys(switches).forEach(function(name) {
      var active = switches[name];
      var cls = active ? 'switch-item switch-active' : 'switch-item switch-inactive';
      html += '<div class="' + cls + '">';
      html += '  <span class="switch-name">' + name.toUpperCase() + '</span>';
      html += '  <span class="switch-status">' + (active ? 'ACTIVE' : 'OFF') + '</span>';
      html += '</div>';
    });
    container.innerHTML = html;
  }

  // ── Events Feed ────────────────────────────────────────────────

  function initEventsTable() {
    eventsTable = PaisaTables.create('events-table', [
      { title: 'Time', field: 'time', width: 100, formatter: PaisaTables.timeFormatter },
      { title: 'Module', field: 'module', width: 160 },
      { title: 'Event', field: 'event' },
      { title: 'Status', field: 'status', width: 80 },
    ], { maxHeight: 350 });
  }

  function addEvent(module, eventText, status) {
    events.unshift({
      time: new Date().toISOString(),
      module: module,
      event: eventText,
      status: status || 'ok',
    });

    if (events.length > MAX_EVENTS) events.pop();

    PaisaTables.setData('events-table', events);
    document.getElementById('event-total').textContent = events.length;
  }

  // ── Data Loading ───────────────────────────────────────────────

  function loadPipelineStatus() {
    fetch('/api/pipelines/status')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        // Update flow diagram
        renderFlowDiagram(data.modules || []);

        // Update metric cards
        (data.modules || []).forEach(function(mod) {
          updateMetricCard(mod);
        });

        // Update throughput chart
        var totalItems = (data.modules || []).reduce(function(sum, m) {
          return sum + (m.items_processed || 0);
        }, 0);
        updateThroughputChart(totalItems);

        // Kill switches
        renderKillSwitches(data.kill_switches || {});

        // Mode badge
        var modeBadge = document.getElementById('pipeline-mode');
        if (modeBadge) {
          var mode = data.operational_mode || 'simulation';
          modeBadge.textContent = mode.toUpperCase();
          modeBadge.className = 'badge badge-' + (mode === 'live' ? 'red' : mode === 'simulation' ? 'yellow' : 'blue');
        }

        // Timestamp
        document.getElementById('pipeline-last-update').textContent =
          'Last update: ' + PaisaUtils.formatTime(data.timestamp);
      })
      .catch(function(err) {
        console.error('[Pipelines] Failed to load status:', err);
      });
  }

  // ── WebSocket Handlers ─────────────────────────────────────────

  function setupWebSocket() {
    PaisaSocket.on('system_health', function(data) {
      if (data.module) {
        updateMetricCard(data);
        addEvent(data.module, 'Status: ' + (data.status || 'ok') + ', items: ' + (data.items_processed || 0), data.status);
      }
      // Refresh full status every health event
      loadPipelineStatus();
    });

    PaisaSocket.on('factor_scores', function(data) {
      var count = Object.keys(data).length;
      addEvent('Factor Engine', 'Computed ' + count + ' symbols', 'ok');
    });

    PaisaSocket.on('signals', function(data) {
      var long = (data.long || []).length;
      var avoid = (data.avoid || []).length;
      addEvent('Signal Engine', long + ' long, ' + avoid + ' avoid signals', 'ok');
    });

    PaisaSocket.on('portfolio', function(data) {
      var positions = (data.positions || []).length;
      addEvent('Portfolio Construction', 'Updated with ' + positions + ' positions', 'ok');
    });

    PaisaSocket.on('trade', function(data) {
      var desc = (data.symbol || '???') + ' ' + (data.side || 'unknown') + ' @ ' + (data.fill_price || '--');
      addEvent('Execution Engine', desc, data.status || 'ok');
    });

    PaisaSocket.on('risk_alert', function(data) {
      var msg = (data.type || 'alert') + ': ' + (data.switch || data.message || JSON.stringify(data));
      addEvent('Risk Engine', msg, 'warning');
    });

    PaisaSocket.on('regime_change', function(data) {
      addEvent('Signal Engine', 'Regime change: ' + (data.from_regime || '?') + ' -> ' + (data.to_regime || '?'), 'ok');
    });
  }

  // ── Init ───────────────────────────────────────────────────────

  function init() {
    initThroughputChart();
    initEventsTable();
    setupWebSocket();
    loadPipelineStatus();

    // Poll every 30 seconds
    setInterval(loadPipelineStatus, 30000);
  }

  document.addEventListener('DOMContentLoaded', init);

  return { loadPipelineStatus, addEvent };
})();
