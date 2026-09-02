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

let rankingChart, scoreChart, stationChart, stationMap;
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
    if (stationMap) stationMap.setCartoKey(cartoApiKey);
  } catch (e) {
    console.warn('public config', e);
  }
}

function initCharts() {
  rankingChart = new MonasChart('rankingChart');
  scoreChart = new MonasChart('scoreChart', {
    onClick(hit) {
      if (hit.type !== 'pt' || !hit.extra) return;
      const ex = hit.extra;
      document.getElementById('scoreDetail').innerHTML =
        `<strong>${hit.series}</strong> · ${formatLeadTime(+hit.x)}<br>
         RMSE: <strong>${hit.y.toFixed(4)}</strong> · Bias: ${ex.bias?.toFixed(4)} ·
         MAE: ${ex.mae?.toFixed(4)} · stde: ${ex.stde?.toFixed(4)} ·
         r: ${ex.correlation?.toFixed(4)} · N: ${ex.n_cases}`;
    },
  });
  stationChart = new MonasChart('stationChart');
  stationMap = new StationCanvasMap('leafletMap', {
    cartoKey: cartoApiKey,
    onStationClick(st) {
      const param = document.getElementById('parameter').value;
      const init = selectedInitTime();
      showStationMapDetail(st.station_id, param, init);
    },
  });
}

async function init() {
  initCharts();
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
      <div class="metric">Mean RMSE: <strong>${r.mean_rmse?.toFixed(3)}</strong> · MAE: ${r.mean_mae?.toFixed(3)}</div>
      <div class="metric">Bias: ${r.mean_bias?.toFixed(3)} · stde: ${r.mean_stde?.toFixed(3)} · r: ${r.mean_correlation?.toFixed(3)}</div>
    </div>`).join('');

  rankingChart.setBar({
    title: 'Ranking HARP det_verify — Mean RMSE (semakin kecil semakin baik)',
    yLabel: 'Mean RMSE',
    labels: ranking.ranking.map(r => r.model),
    values: ranking.ranking.map(r => r.mean_rmse),
    colors: ['#00529B', '#64748b', '#94a3b8', '#cbd5e1'],
  });

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
  const series = selectedModels().map(m => {
    const pts = data.scores.filter(s => s.model === m).sort((a, b) => a.lead_time - b.lead_time);
    return {
      name: m,
      color: MODEL_COLORS[m] || '#00529B',
      x: pts.map(p => p.lead_time),
      y: pts.map(p => p.rmse),
      extra: pts.map(p => ({ bias: p.bias, mae: p.mae, n_cases: p.n_cases, correlation: p.correlation, stde: p.stde })),
    };
  });

  scoreChart.setLines({
    title: `RMSE vs Lead Time (D+0 → D+${maxLeadTime / 24}) — ${paramsMeta[param]?.label}`,
    xLabel: 'Lead Time (jam)',
    yLabel: 'RMSE',
    xNumeric: true,
    series,
  });
}

async function loadMap() {
  if (stationMap) stationMap.invalidateSize();
  const lt = document.getElementById('leadTime').value;
  const param = document.getElementById('parameter').value;
  const model = selectedModels()[0] || 'InaNWP';
  const init = selectedInitTime();
  const q = `model=${model}&parameter=${param}&lead_time=${lt}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;
  const data = await api(`/api/verification/map?${q}`);
  if (!data.length) {
    stationMap.setStations([]);
    document.getElementById('mapDetail').textContent = 'Belum ada data peta untuk filter ini.';
    return;
  }
  const maxRmse = Math.max(...data.map(d => d.rmse || 0), 0.01);
  stationMap.setStations(data.map(d => ({
    station_id: d.station_id,
    name: d.name,
    lat: d.lat,
    lon: d.lon,
    rmse: d.rmse,
    color: d.rmse < maxRmse * 0.33 ? '#16a34a' : d.rmse < maxRmse * 0.66 ? '#ca8a04' : '#dc2626',
  })));
  document.getElementById('mapDetail').textContent = 'Klik stasiun pada peta untuk melihat fcst vs obs.';
}

function stationDetailQuery() {
  const init = selectedInitTime();
  const lt = document.getElementById('leadTime').value;
  return `parameter=${document.getElementById('parameter').value}&models=${modelsQuery()}&lead_time=${lt}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;
}

function downsampleSeries(series, maxPoints = 400) {
  if (series.length <= maxPoints) return series;
  const step = Math.ceil(series.length / maxPoints);
  return series.filter((_, i) => i % step === 0);
}

async function showStationMapDetail(stationId, param, init) {
  const lt = document.getElementById('leadTime').value;
  const q = `parameter=${param}&models=${modelsQuery()}&lead_time=${lt}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;
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
  const param = document.getElementById('parameter').value;
  const data = await api(`/api/station/${stationId}/detail?${stationDetailQuery()}`);
  const plotSeries = downsampleSeries(data.series);

  const series = [{
    name: 'Observasi',
    color: MODEL_COLORS.Observasi,
    width: 1,
    x: plotSeries.map(s => s.valid_time?.slice(0, 16)),
    y: plotSeries.map(s => s.obs),
  }];

  selectedModels().forEach(m => {
    series.push({
      name: m,
      color: MODEL_COLORS[m] || '#00529B',
      x: plotSeries.filter(s => s[m] != null).map(s => s.valid_time?.slice(0, 16)),
      y: plotSeries.filter(s => s[m] != null).map(s => s[m]),
    });
  });

  const initNote = data.init_time ? ` · init ${String(data.init_time).slice(0, 16)}` : '';
  const ltNote = data.lead_time != null ? ` · ${formatLeadTime(Number(data.lead_time))}` : '';
  stationChart.setLines({
    title: `${data.station.name || stationId} — ${paramsMeta[param]?.label}${initNote}${ltNote}`,
    xLabel: 'Valid Time (UTC)',
    yLabel: paramsMeta[param]?.unit || '',
    series,
  });

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
