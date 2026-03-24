/**
 * PaisaSocket — Socket.IO client for real-time dashboard updates.
 * Connects to the /dashboard namespace and relays events.
 */
const PaisaSocket = (function() {
  let socket = null;
  const listeners = {};

  function connect() {
    socket = io('/dashboard', {
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionAttempts: Infinity,
    });

    socket.on('connect', function() {
      const dot = document.getElementById('ws-status');
      if (dot) { dot.className = 'status-dot connected'; dot.title = 'Connected'; }
    });

    socket.on('disconnect', function() {
      const dot = document.getElementById('ws-status');
      if (dot) { dot.className = 'status-dot disconnected'; dot.title = 'Disconnected'; }
    });

    socket.on('connected', function() {
      console.log('[PaisaSocket] connected to /dashboard');
    });

    // Route events to registered listeners
    const events = [
      'factor_scores', 'signals', 'portfolio', 'risk_alert',
      'trade', 'regime_change', 'system_health', 'config_change',
    ];

    events.forEach(function(event) {
      socket.on(event, function(data) {
        if (listeners[event]) {
          listeners[event].forEach(function(fn) { fn(data); });
        }
      });
    });
  }

  function on(event, callback) {
    if (!listeners[event]) listeners[event] = [];
    listeners[event].push(callback);
  }

  function emit(event, data) {
    if (socket) socket.emit(event, data);
  }

  // Auto-connect on load
  if (typeof io !== 'undefined') {
    document.addEventListener('DOMContentLoaded', connect);
  }

  return { connect, on, emit };
})();
