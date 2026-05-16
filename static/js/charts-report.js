// ── Shared chart infrastructure for report pages ─────────────────────
// Loaded by base.html after Chart.js; must be present before any
// template-level <script> block that calls mkChart or initChartModalListeners.

const chartRegistry  = {};
const chartInstances = {};
const cssGreen  = getComputedStyle(document.documentElement).getPropertyValue('--green').trim();
const cssRed    = getComputedStyle(document.documentElement).getPropertyValue('--red').trim();
const cssBorder = getComputedStyle(document.documentElement).getPropertyValue('--border').trim();

// Plugin: draws player-group headers and separator lines in split mode
const splitGroupPlugin = {
  id: "splitGroup",
  afterDraw(chart) {
    const groups = chart.options.splitGroups;
    if (!groups || !groups.length) return;
    const { ctx, scales: { x }, chartArea: { top, bottom } } = chart;
    const totalBars = chart.data.labels.length;
    if (!totalBars) return;
    const step = (x.right - x.left) / totalBars;
    ctx.save();
    groups.forEach((g, gi) => {
      const x0 = x.getPixelForValue(g.start) - step / 2;
      const x1 = x.getPixelForValue(g.start + g.count - 1) + step / 2;
      if (gi % 2 === 1) {
        ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--split-fill').trim();
        ctx.fillRect(x0, top, x1 - x0, bottom - top);
      }
      if (gi > 0) {
        ctx.strokeStyle = cssBorder;
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(x0, top - 14);
        ctx.lineTo(x0, bottom);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.fillStyle = xAxis.ticks.color;
      ctx.font = "bold 10px 'Segoe UI', system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(g.name, (x0 + x1) / 2, top - 4);
    });
    ctx.restore();
  }
};

let _splitGroups = null;
function mkChart(id, cfg) {
  if (_splitGroups) {
    cfg = {
      ...cfg,
      plugins: [...(cfg.plugins || []), splitGroupPlugin],
      options: {
        ...cfg.options,
        splitGroups: _splitGroups,
        layout: { padding: { top: 18 } },
      },
    };
  }
  chartRegistry[id] = cfg;
  const c = new Chart(document.getElementById(id), cfg);
  chartInstances[id] = c;
  return c;
}

function destroyAllCharts() {
  Object.values(chartInstances).forEach(c => c.destroy());
  Object.keys(chartInstances).forEach(k => delete chartInstances[k]);
  Object.keys(chartRegistry).forEach(k => delete chartRegistry[k]);
}

function cloneCfg(cfg) {
  return { ...cfg, data: { ...cfg.data, datasets: cfg.data.datasets.map(ds => ({ ...ds })) } };
}

// ── Plugin: net total label above/below the stacked diverging bar ────
const netTotalPlugin = {
  id: "netTotal",
  afterDatasetsDraw(chart) {
    const { ctx, data, scales: { x, y } } = chart;
    const n = data.labels.length;
    ctx.save();
    ctx.font = "bold 11px 'Segoe UI', system-ui, sans-serif";
    ctx.textAlign = "center";
    for (let i = 0; i < n; i++) {
      let net = 0;
      data.datasets.forEach(ds => { if (!ds.neutral) net += (ds.data[i] || 0); });
      const xPos = x.getPixelForValue(i);
      const bottom = chart.chartArea.bottom;
      const yPos = bottom - 5;
      const label = net > 0 ? `+${net}` : `${net}`;
      ctx.fillStyle = net >= 0 ? cssGreen : cssRed;
      ctx.fillText(label, xPos, yPos);
    }
    ctx.restore();
  }
};

// ── Full-screen modal ─────────────────────────────────────────────────
let modalCharts = [];
function openChartModal(card) {
  const title = card.querySelector('.chart-title').textContent.trim();
  document.getElementById('chart-modal-title').textContent = title;
  const body = document.getElementById('chart-modal-body');
  body.innerHTML = '';
  modalCharts.forEach(c => c.destroy());
  modalCharts = [];
  if (card.classList.contains('dual-chart-card')) {
    const topId  = card.querySelector('.dual-top canvas').id;
    const qualId = card.querySelector('.dual-bottom canvas').id;
    const topWrap = Object.assign(document.createElement('div'), { className:'modal-chart-top' });
    const tc = document.createElement('canvas');
    tc.id = topId + '-modal';
    topWrap.appendChild(tc);
    body.appendChild(topWrap);
      const sep = Object.assign(document.createElement('div'), { className:'dual-sep', textContent:'Kwaliteit' });
    body.appendChild(sep);
    const qualWrap = Object.assign(document.createElement('div'), { className:'modal-chart-qual' });
    const qc = document.createElement('canvas');
    qc.id = qualId + '-modal';
    qualWrap.appendChild(qc);
    body.appendChild(qualWrap);
    if (chartRegistry[topId])  modalCharts.push(mkChart(topId + '-modal', cloneCfg(chartRegistry[topId])));
    if (chartRegistry[qualId]) modalCharts.push(mkChart(qualId + '-modal', cloneCfg(chartRegistry[qualId])));
  } else {
    const srcId = card.querySelector('canvas').id;
    const wrap = Object.assign(document.createElement('div'), { className:'modal-chart-wrap' });
    const c = document.createElement('canvas');
    c.id = srcId + '-modal';
    wrap.appendChild(c);
    body.appendChild(wrap);
    if (chartRegistry[srcId]) modalCharts.push(mkChart(srcId + '-modal', cloneCfg(chartRegistry[srcId])));
  }
  document.getElementById('chart-modal').hidden = false;
  document.body.classList.add('modal-open');
}
function closeChartModal() {
  document.getElementById('chart-modal').hidden = true;
  document.body.classList.remove('modal-open');
  modalCharts.forEach(c => c.destroy());
  modalCharts = [];
  document.getElementById('chart-modal-body').innerHTML = '';
}

function initChartModalListeners() {
  document.getElementById('chart-modal').addEventListener('click', e => {
    if (e.target.id === 'chart-modal') closeChartModal();
  });
  document.getElementById('chart-modal-close').addEventListener('click', closeChartModal);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeChartModal(); });

  document.querySelectorAll('.chart-card, .dual-chart-card').forEach(card => {
    const btn = document.createElement('button');
    btn.className = 'chart-expand-btn';
    btn.title = 'Volledig scherm';
    btn.setAttribute('aria-label', 'Volledig scherm');
    btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M1 5V1h4M8 1h4v4M12 8v4H8M5 12H1V8"/></svg>`;
    btn.addEventListener('click', e => { e.stopPropagation(); openChartModal(card); });
    card.appendChild(btn);
  });
}
