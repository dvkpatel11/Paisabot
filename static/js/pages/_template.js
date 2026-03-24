/* ═══════════════════════════════════════════════════════════════════════════
   _template.js — Copy this for every new page.

   Pattern
   ───────
   1. Register a page-scoped Alpine store (runs before Alpine initialises).
   2. On DOMContentLoaded:
        a. Create PaisaChart / PaisaTable instances (they self-attach ResizeObserver).
        b. Fetch initial data from REST API → seed store + render charts/tables.
        c. Listen to pb:* custom DOM events from socket.js for live updates.
   3. Mutations always go through the Alpine store first; charts/tables read from
      it on update — stores remain the single source of truth.

   Data flow
   ─────────
     socket event
       └─► socket.js  ──► Alpine.store('page').update(data)   ← DOM bindings react
                      ──► document.dispatchEvent('pb:event')  ← chart/table reacts

   Chart/table update rules
   ────────────────────────
   - Use chart.react() not chart.plot() for live updates (preserves zoom state).
   - Use table.upsert() not table.setData() for streaming row updates.
   - Debounce high-frequency events (e.g. 5-sec factor ticks) before chart.react().
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ── 1. Page store ────────────────────────────────────────────────────── */
  document.addEventListener('alpine:init', () => {
    Alpine.store('page', {
      loading: true,
      error:   null,
      data:    {},

      setData(d) {
        Object.assign(this.data, d);
        this.loading = false;
      },
      setError(msg) {
        this.error   = msg;
        this.loading = false;
      },
    });
  });

  /* ── 2. Init on DOM ready ─────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', () => {

    /* a. Instantiate charts and tables */
    const myChart = new PaisaChart('my-chart-div', 'plotly');
    const myTable = new PaisaTable('my-table-div', [
      /* Tabulator column defs */
      { title: 'Symbol', field: 'symbol', width: 80 },
      { title: 'Score',  field: 'score',  formatter: cell => fmt.score(cell.getValue()) },
    ]);
    myTable.init();

    /* b. Load initial data */
    fetch('/api/example')
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(data => {
        Alpine.store('page').setData(data);
        myChart.plot([{ type: 'bar', x: data.labels, y: data.values }]);
        myTable.setData(data.rows);
      })
      .catch(err => Alpine.store('page').setError(String(err)));

    /* c. Live updates via socket events (dispatched by socket.js) */
    const debouncedChartUpdate = debounce((data) => {
      myChart.react([{ type: 'bar', x: data.labels, y: data.values }]);
    }, 300);

    document.addEventListener('pb:signals', e => {
      Alpine.store('page').setData(e.detail);
      debouncedChartUpdate(e.detail);
      myTable.upsert(e.detail.rows ?? [], 'symbol');
    });

  });

})();
