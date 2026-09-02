const BASE_PATH = (() => {
  const p = window.location.pathname;
  if (p.startsWith('/monas')) return '/monas';
  return '';
})();

const API = (() => {
  const { hostname, port } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return 'http://localhost:8013';
  }
  if (BASE_PATH) {
    return `${window.location.origin}${BASE_PATH}/api`;
  }
  return port ? `${window.location.protocol}//${hostname}:8013` : `http://${hostname}:8013`;
})();

const MODEL_COLORS = {
  Observasi: '#ca8a04',
  InaNWP: '#ea580c',
  InaCAWO: '#16a34a',
  GFS: '#dc2626',
  IFS: '#7c3aed',
};

const PLOT_LAYOUT = {
  paper_bgcolor: '#ffffff',
  plot_bgcolor: '#f8fafc',
  font: { color: '#334155', family: 'Segoe UI, system-ui, sans-serif' },
  xaxis: { gridcolor: '#e2e8f0', linecolor: '#cbd5e1' },
  yaxis: { gridcolor: '#e2e8f0', linecolor: '#cbd5e1' },
};

let map, mapTileLayer, markers = [];
let paramsMeta = {};
let maxLeadTime = 168;
let modelSources = { InaNWP: 'real', InaCAWO: 'dummy', GFS: 'dummy', IFS: 'dummy' };
let cartoApiKey = '';

async function api(path, opts = {}) {
  const res = await fetch(`${API}${path}`, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function selectedModels() { return [...document.querySelectorAll('.model-cb:checked')].map(el => el.value); }
function modelsQuery() { return selectedModels().join(','); }
function selectedInitTime() { return document.getElementById('initCycle').value || ''; }

function formatLeadTime(h) {
  if (h === 0) return 'D+0 (analysis)';
  if (h < 24) return `D+${(h / 24).toFixed(1)} (${h} jam)`;
  return `D+${(h / 24).toFixed(1)} (${h} jam)`;
}

async function loadPublicConfig() {
  try {
    const cfg = await api('/api/config/public');
    cartoApiKey = cfg.carto_api_key || '';
  } catch (e) {
    console.warn('public config', e);
  }
}

async function init() {
  await loadPublicConfig();
  const data = await api('/api/parameters');
  paramsMeta = data.verify_parameters;
  maxLeadTime = data.max_lead_time_hours || 168;

  const ltSlider = document.getElementById('leadTime');
  ltSlider.max = maxLeadTime;

  document.getElementById('parameter').innerHTML = Object.entries(paramsMeta).map(([k, v]) =>
    `<option value="${k}">${v.label} (${v.unit})</option>`).join('');

  const stations = await api('/api/stations');
  document.getElementById('stationSelect').innerHTML = stations.map(s =>
    `<option value="${s.station_id}">${s.station_id} — ${s.name || s.station_id}</option>`).join('');

  await loadCycles();
  await loadPipelineStatus();
  await loadModelSources();
  initMap();
  bindEvents();
  await refreshAll();

  setInterval(loadPipelineStatus, 300000);
}

async function loadModelSources() {
  try {
    const data = await api('/api/models/sources');
    modelSources = data.sources || modelSources;
    document.querySelectorAll('.model-cb').forEach(cb => {
      const badge = cb.parentElement.querySelector('.badge');
      if (!badge) return;
      const src = modelSources[cb.value] || 'real';
      badge.textContent = src;
      badge.className = `badge ${src}`;
    });
  } catch (e) { console.warn('model sources', e); }
}

function modelBadge(model) {
  const src = modelSources[model] || 'real';
  return `<span class="badge ${src}">${src}</span>`;
}

async function loadMethodology() {
  const el = document.getElementById('harpMethodology');
  try {
    const m = await api('/api/harp/methodology');
    el.innerHTML = `
      <h2>${m.title}</h2>
      <p>${m.subtitle}</p>
      <div class="note-box">${m.python_equivalence || ''}</div>
      <h3>Referensi HARP</h3>
      <div class="refs">${m.references.map(r =>
        `<a href="${r.url}" target="_blank" rel="noopener">${r.title}</a> — ${r.description}`
      ).join('<br>')}</div>
      <h3>Alur kerja (harpPoint)</h3>
      <ol>${m.workflow.map(w => `<li><strong>${w.name}</strong> — ${w.detail}</li>`).join('')}</ol>
      <h3>Skor deterministik (det_verify)</h3>
      <p>Semua skor dihitung <strong>paired</strong>: hanya pasangan (fcst, obs) yang lengkap setelah join & QC.</p>
      <table><tr><th>Skor</th><th>Formula</th><th>Catatan</th></tr>
      ${m.scores.map(s => `<tr><td>${s.id}</td><td><code>${s.formula}</code></td><td>${s.note}</td></tr>`).join('')}
      </table>
      <h3>Quality Control</h3>
      <p>${m.qc}</p>
      <h3>Ranking model</h3>
      <p>${m.ranking || ''}</p>
      <h3>Implementasi MONAS</h3>
      <ul>
        <li>Interpolasi: ${m.implementation.interpolation}</li>
        <li>Verifikasi: ${m.implementation.verification}</li>
        <li>Lead time: ${m.implementation.lead_time}</li>
      </ul>
      <h3>Sumber data model</h3>
      <ul>${Object.entries(m.data_sources).map(([k, v]) => `<li><strong>${k}</strong>: ${v}</li>`).join('')}</ul>
    `;
  } catch (e) {
    el.textContent = 'Gagal memuat metode HARP: ' + e.message;
  }
}

async function loadCycles() {
  try {
    const cycles = await api('/api/cycles');
    const sel = document.getElementById('initCycle');
    const opts = cycles.filter(c => c.status === 'done').map(c =>
      `<option value="${c.init_time}">${c.model} · ${c.init_time?.slice(0, 16)} · ${c.nc_filename || ''}</option>`
    );
    sel.innerHTML = '<option value="">Terbaru (semua cycle)</option>' + opts.join('');
  } catch (e) { console.warn('cycles', e); }
}

async function loadPipelineStatus() {
  const el = document.getElementById('pipelineStatus');
  const src = document.getElementById('dataSource');
  try {
    const [status, inventory] = await Promise.all([
      api('/api/pipeline/status'),
      api('/api/pipeline/inventory'),
    ]);
    const inanwp = inventory.InaNWP || {};
    const done = status.model_runs?.filter(r => r.status === 'done').length || 0;
    const pending = status.model_runs?.filter(r => r.status === 'pending').length || 0;
    el.textContent = `Pipeline: ${status.verification_scores_count} skor · ${done} run selesai · ${pending} pending · auto-sync aktif`;
    src.innerHTML = `NC: <code>${inanwp.path || 'litbangweb'}</code> · ${inanwp.count || 0} file`;
  } catch (e) {
    el.textContent = 'Pipeline: ' + e.message;
  }
}

function bindEvents() {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab, .panel').forEach(el => el.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
      refreshAll();
    });
  });

  ['parameter', 'initCycle'].forEach(id => document.getElementById(id).addEventListener('change', refreshAll));
  document.getElementById('leadTime').addEventListener('input', () => {
    document.getElementById('leadTimeLabel').textContent = formatLeadTime(+document.getElementById('leadTime').value);
    refreshAll();
  });
  document.querySelectorAll('.model-cb').forEach(cb => cb.addEventListener('change', refreshAll));
  document.getElementById('stationSelect').addEventListener('change', loadStationDetail);
}

function scoresQuery(extra = '') {
  const init = selectedInitTime();
  return `models=${modelsQuery()}&parameter=${document.getElementById('parameter').value}${init ? `&init_time=${encodeURIComponent(init)}` : ''}${extra}`;
}

async function refreshAll() {
  const tab = document.querySelector('.tab.active')?.dataset.tab;
  if (tab === 'overview') await loadOverview();
  if (tab === 'scores') await loadScores();
  if (tab === 'map') await loadMap();
  if (tab === 'method') await loadMethodology();
  if (tab === 'station') await loadStationDetail();
}

async function loadOverview() {
  const lt = document.getElementById('leadTime').value;
  const init = selectedInitTime();
  const rankQ = `models=${modelsQuery()}&score=rmse${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;

  const [ranking, scores] = await Promise.all([
    api(`/api/verification/ranking?${rankQ}`),
    api(`/api/verification/scores?${scoresQuery(`&lead_time=${lt}`)}`),
  ]);

  document.getElementById('rankingCards').innerHTML = ranking.ranking.map(r => `
    <div class="rank-card rank-${r.rank}">
      <div class="rank-num">#${r.rank}</div>
      <div class="model-name">${r.model} ${modelBadge(r.model)}</div>
      <div class="metric">Mean RMSE: <strong>${r.mean_rmse?.toFixed(3)}</strong></div>
      <div class="metric">MAE: ${r.mean_mae?.toFixed(3)} · r: ${r.mean_correlation?.toFixed(3)}</div>
      <div class="metric">Skill: ${r.skill_score?.toFixed(4)}</div>
    </div>`).join('');

  Plotly.newPlot('rankingChart', [{
    type: 'bar',
    x: ranking.ranking.map(r => r.model),
    y: ranking.ranking.map(r => r.mean_rmse),
    marker: { color: ['#00529B', '#64748b', '#94a3b8', '#cbd5e1'] },
    text: ranking.ranking.map(r => `#${r.rank} RMSE ${r.mean_rmse?.toFixed(3)}`),
    textposition: 'auto',
  }], {
    ...PLOT_LAYOUT,
    title: 'Ranking Model HARP — Mean RMSE (semakin kecil semakin baik)',
    yaxis: { ...PLOT_LAYOUT.yaxis, title: 'Mean RMSE' },
  }, { responsive: true });

  document.getElementById('kpiGrid').innerHTML = scores.scores.map(s => `
    <div class="kpi">
      <div class="label">${s.model} · ${formatLeadTime(s.lead_time)}</div>
      <div class="value">RMSE ${s.rmse?.toFixed(2)}</div>
      <div class="label">Bias ${s.bias?.toFixed(2)} · MAE ${s.mae?.toFixed(2)} · stde ${s.stde?.toFixed(2)} · r ${s.correlation?.toFixed(2)} · N=${s.n_cases}</div>
    </div>`).join('');
}

async function loadScores() {
  const param = document.getElementById('parameter').value;
  const data = await api(`/api/verification/scores?${scoresQuery()}`);
  const traces = selectedModels().map(m => {
    const pts = data.scores.filter(s => s.model === m).sort((a, b) => a.lead_time - b.lead_time);
    return {
      name: m,
      x: pts.map(p => p.lead_time),
      y: pts.map(p => p.rmse),
      mode: 'lines+markers',
      type: 'scatter',
      line: { color: MODEL_COLORS[m] || '#00529B', width: 2 },
      marker: { size: 7, color: MODEL_COLORS[m] },
      customdata: pts.map(p => [p.bias, p.mae, p.n_cases, p.n_stations, p.correlation, p.stde]),
      hovertemplate: `${m}<br>%{x} jam<br>RMSE: %{y:.3f}<br>Bias: %{customdata[0]:.3f}<extra></extra>`,
    };
  });

  Plotly.newPlot('scoreChart', traces, {
    ...PLOT_LAYOUT,
    title: `RMSE vs Lead Time (D+0 → D+${maxLeadTime / 24}) — ${paramsMeta[param]?.label}`,
    xaxis: { ...PLOT_LAYOUT.xaxis, title: 'Lead Time (jam)', dtick: 24 },
    yaxis: { ...PLOT_LAYOUT.yaxis, title: 'RMSE' },
  }, { responsive: true });

  document.getElementById('scoreChart').on('plotly_click', ev => {
    const pt = ev.points[0];
    document.getElementById('scoreDetail').innerHTML =
      `<strong>${pt.data.name}</strong> · ${formatLeadTime(pt.x)}<br>
       RMSE: <strong>${pt.y.toFixed(4)}</strong> · Bias: ${pt.customdata[0].toFixed(4)} ·
       MAE: ${pt.customdata[1].toFixed(4)} · stde: ${pt.customdata[5].toFixed(4)} ·
       r: ${pt.customdata[4].toFixed(4)} · N: ${pt.customdata[2]}`;
  });
}

function initMap() {
  map = L.map('leafletMap').setView([-2.5, 118], 5);
  const keyParam = cartoApiKey ? `?key=${encodeURIComponent(cartoApiKey)}` : '';
  mapTileLayer = L.tileLayer(
    `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png${keyParam}`,
    {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    },
  ).addTo(map);
}

async function loadMap() {
  if (!map) initMap();
  else setTimeout(() => map.invalidateSize(), 0);
  const lt = document.getElementById('leadTime').value;
  const param = document.getElementById('parameter').value;
  const model = selectedModels()[0] || 'InaNWP';
  const init = selectedInitTime();
  const q = `model=${model}&parameter=${param}&lead_time=${lt}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;
  const data = await api(`/api/verification/map?${q}`);
  markers.forEach(m => map.removeLayer(m));
  markers = [];
  if (!data.length) {
    document.getElementById('mapDetail').textContent = 'Belum ada data peta untuk filter ini.';
    return;
  }
  const maxRmse = Math.max(...data.map(d => d.rmse || 0), 0.01);
  data.forEach(d => {
    if (!d.lat || !d.lon) return;
    const color = d.rmse < maxRmse * 0.33 ? '#16a34a' : d.rmse < maxRmse * 0.66 ? '#ca8a04' : '#dc2626';
    const m = L.circleMarker([d.lat, d.lon], { radius: 8, fillColor: color, color: '#fff', weight: 1, fillOpacity: 0.85 }).addTo(map);
    m.bindPopup(`<b>${d.name || d.station_id}</b><br>RMSE: ${d.rmse?.toFixed(3)}`);
    m.on('click', () => showStationMapDetail(d.station_id, param, init));
    markers.push(m);
  });
}

async function showStationMapDetail(stationId, param, init) {
  const q = `parameter=${param}&models=${modelsQuery()}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;
  const data = await api(`/api/station/${stationId}/detail?${q}`);
  const paired = data.series.filter(s => s.obs != null && selectedModels().some(m => s[m] != null));
  const latest = paired.slice(-1)[0] || {};
  let html = `<strong>${data.station.name || stationId}</strong><table><tr><th>Model</th><th>Fcst</th><th>Obs</th><th>Err</th></tr>`;
  selectedModels().forEach(m => {
    const err = latest[m] != null && latest.obs != null ? (latest[m] - latest.obs).toFixed(3) : '—';
    html += `<tr><td>${m}</td><td>${latest[m]?.toFixed(2) ?? '—'}</td><td>${latest.obs?.toFixed(2) ?? '—'}</td><td>${err}</td></tr>`;
  });
  document.getElementById('mapDetail').innerHTML = html + '</table>';
}

async function loadStationDetail() {
  const stationId = document.getElementById('stationSelect').value;
  const init = selectedInitTime();
  const param = document.getElementById('parameter').value;
  const q = `parameter=${param}&models=${modelsQuery()}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;
  const data = await api(`/api/station/${stationId}/detail?${q}`);

  const traces = [{
    name: 'Observasi',
    x: data.series.map(s => s.valid_time),
    y: data.series.map(s => s.obs),
    mode: 'lines+markers',
    line: { color: MODEL_COLORS.Observasi, width: 1, dash: 'dot' },
    marker: { size: 6, color: MODEL_COLORS.Observasi },
    connectgaps: false,
  }];

  selectedModels().forEach(m => {
    const xs = [];
    const ys = [];
    data.series.forEach(s => {
      if (s[m] != null) { xs.push(s.valid_time); ys.push(s[m]); }
    });
    traces.push({
      name: m,
      x: xs,
      y: ys,
      mode: 'lines+markers',
      line: { color: MODEL_COLORS[m] || '#00529B', width: 2 },
      marker: { size: 5 },
      connectgaps: true,
    });
  });

  Plotly.newPlot('stationChart', traces, {
    ...PLOT_LAYOUT,
    title: `${data.station.name || stationId} — ${paramsMeta[param]?.label}`,
    xaxis: { ...PLOT_LAYOUT.xaxis, title: 'Valid Time (UTC)' },
    yaxis: { ...PLOT_LAYOUT.yaxis, title: paramsMeta[param]?.unit || '' },
    legend: { orientation: 'h', y: -0.15 },
  }, { responsive: true });

  const paired = data.series.filter(s =>
    s.obs != null && selectedModels().some(m => s[m] != null)
  ).slice(-20).reverse();

  if (!paired.length) {
    document.getElementById('stationTable').innerHTML =
      '<em>Belum ada pasangan fcst+obs untuk stasiun/parameter/init cycle ini.</em>';
    return;
  }

  let html = '<table><tr><th>Valid Time</th><th>Obs</th>';
  selectedModels().forEach(m => { html += `<th>${m}</th><th>Err ${m}</th>`; });
  html += '</tr>';
  paired.forEach(s => {
    html += `<tr><td>${s.valid_time?.slice(0, 16)}</td><td>${s.obs?.toFixed(2) ?? '—'}</td>`;
    selectedModels().forEach(m => {
      const err = s[m] != null && s.obs != null ? (s[m] - s.obs).toFixed(2) : '—';
      html += `<td>${s[m]?.toFixed(2) ?? '—'}</td><td>${err}</td>`;
    });
    html += '</tr>';
  });
  document.getElementById('stationTable').innerHTML = html + '</table>';
}

init().catch(console.error);
