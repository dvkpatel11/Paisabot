/**
 * PaisaCharts — Plotly.js wrapper with dark theme defaults.
 */
const PaisaCharts = (function() {
  const darkLayout = {
    paper_bgcolor: '#161b22',
    plot_bgcolor: '#0d1117',
    font: { color: '#8b949e', family: 'Inter, system-ui, sans-serif', size: 12 },
    margin: { t: 40, r: 20, b: 40, l: 50 },
    xaxis: { gridcolor: '#21262d', zerolinecolor: '#30363d' },
    yaxis: { gridcolor: '#21262d', zerolinecolor: '#30363d' },
    legend: { bgcolor: 'transparent', font: { size: 11 } },
  };

  const config = { responsive: true, displayModeBar: false };

  function timeSeries(containerId, traces, title) {
    const layout = Object.assign({}, darkLayout, {
      title: { text: title || '', font: { size: 14 } },
    });
    Plotly.newPlot(containerId, traces, layout, config);
  }

  function heatmap(containerId, opts) {
    const trace = {
      z: opts.z,
      x: opts.x,
      y: opts.y,
      type: 'heatmap',
      colorscale: [
        [0, '#0d1117'],
        [0.25, '#1a3a5c'],
        [0.5, '#2d6a8e'],
        [0.75, '#3fb950'],
        [1, '#56d364'],
      ],
      showscale: true,
    };

    const layout = Object.assign({}, darkLayout, {
      title: { text: opts.title || '', font: { size: 14 } },
      yaxis: { automargin: true },
      xaxis: { automargin: true },
    });

    Plotly.newPlot(containerId, [trace], layout, config);
  }

  function pie(containerId, opts) {
    const trace = {
      labels: opts.labels,
      values: opts.values,
      type: 'pie',
      hole: 0.4,
      textfont: { size: 11, color: '#e6edf3' },
      marker: {
        colors: ['#3fb950', '#58a6ff', '#d29922', '#f85149', '#bc8cff', '#39d2c0', '#e3b341', '#8b949e'],
      },
    };

    const layout = Object.assign({}, darkLayout, {
      title: { text: opts.title || '', font: { size: 14 } },
      showlegend: true,
    });

    Plotly.newPlot(containerId, [trace], layout, config);
  }

  function bar(containerId, opts) {
    const trace = {
      x: opts.x,
      y: opts.y,
      type: 'bar',
      marker: { color: opts.colors || '#58a6ff' },
    };

    const layout = Object.assign({}, darkLayout, {
      title: { text: opts.title || '', font: { size: 14 } },
      showlegend: false,
      yaxis: { visible: false },
    });

    Plotly.newPlot(containerId, [trace], layout, config);
  }

  function horizontalBar(containerId, opts) {
    const trace = {
      y: opts.labels,
      x: opts.values,
      type: 'bar',
      orientation: 'h',
      marker: {
        color: opts.values.map(function(v) {
          return v >= 0.65 ? '#3fb950' : v >= 0.40 ? '#d29922' : '#f85149';
        }),
      },
    };

    const layout = Object.assign({}, darkLayout, {
      title: { text: opts.title || '', font: { size: 14 } },
      xaxis: { range: [0, 1], title: 'Score' },
      yaxis: { automargin: true },
    });

    Plotly.newPlot(containerId, [trace], layout, config);
  }

  return { timeSeries, heatmap, pie, bar, horizontalBar };
})();
