const BASE_PATH = (() => {
  const p = window.location.pathname;
  if (p.startsWith('/verifikasi-inanwp')) return '/verifikasi-inanwp';
  return '';
})();

const API = (() => {
  const { hostname, port } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return 'http://localhost:8013';
  }
  // Portal PSIMKG: same-origin via Apache subpath
  if (BASE_PATH) {
    return `${window.location.origin}${BASE_PATH}/api`;
  }
  return port ? `${window.location.protocol}//${hostname}:8013` : `http://${hostname}:8013`;
})();

let map, markers = [];
let paramsMeta = {};
let maxLeadTime = 168;

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
  if (h < 24) return `D+${(h/24).toFixed(1)} (${h} jam)`;
  return `D+${(h/24).toFixed(1)} (${h} jam)`;
}

async function init() {
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
  initMap();
  bindEvents();
  await refreshAll();

  // Auto-refresh status setiap 5 menit
  setInterval(loadPipelineStatus, 300000);
}

async function loadCycles() {
  try {
    const cycles = await api('/api/cycles');
    const sel = document.getElementById('initCycle');
    const opts = cycles.filter(c => c.status === 'done').map(c =>
      `<option value="${c.init_time}">${c.model} · ${c.init_time?.slice(0,16)} · ${c.nc_filename || ''}</option>`
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
    el.className = 'token-status ok';
    el.innerHTML = `✓ Auto-sync aktif<br>${status.verification_scores_count} skor tersimpan<br>${done} run selesai · ${pending} pending`;
    src.innerHTML = `Sumber NC: <code>${inanwp.path || 'litbangweb'}</code><br>${inanwp.count || 0} file NC · ${inanwp.local_access ? 'akses lokal ✓' : 'via SFTP'}`;
  } catch (e) {
    el.className = 'token-status err';
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
      <div class="model-name">${r.model}</div>
      <div class="metric">Mean RMSE: <strong>${r.mean_rmse?.toFixed(3)}</strong></div>
      <div class="metric">MAE: ${r.mean_mae?.toFixed(3)} · r: ${r.mean_correlation?.toFixed(3)}</div>
      <div class="metric">Skill: ${r.skill_score?.toFixed(4)}</div>
    </div>`).join('');

  Plotly.newPlot('rankingChart', [{
    type: 'bar', x: ranking.ranking.map(r => r.model), y: ranking.ranking.map(r => r.mean_rmse),
    marker: { color: ['#fbbf24','#94a3b8','#64748b','#475569'] },
    text: ranking.ranking.map(r => `#${r.rank} RMSE ${r.mean_rmse?.toFixed(3)}`), textposition: 'auto',
  }], {
    title: '🏆 Ranking Model HARP — Mean RMSE (semakin kecil semakin baik)',
    paper_bgcolor: '#1e293b', plot_bgcolor: '#1e293b', font: { color: '#e2e8f0' },
    yaxis: { title: 'Mean RMSE' },
  }, { responsive: true });

  document.getElementById('kpiGrid').innerHTML = scores.scores.map(s => `
    <div class="kpi">
      <div class="label">${s.model} · ${formatLeadTime(s.lead_time)}</div>
      <div class="value">RMSE ${s.rmse?.toFixed(2)}</div>
      <div class="label">Bias ${s.bias?.toFixed(2)} · MAE ${s.mae?.toFixed(2)} · N=${s.n_cases}</div>
    </div>`).join('');
}

async function loadScores() {
  const param = document.getElementById('parameter').value;
  const data = await api(`/api/verification/scores?${scoresQuery()}`);
  const traces = selectedModels().map(m => {
    const pts = data.scores.filter(s => s.model === m).sort((a,b) => a.lead_time - b.lead_time);
    return {
      name: m, x: pts.map(p => p.lead_time), y: pts.map(p => p.rmse),
      mode: 'lines+markers', type: 'scatter', marker: { size: 8 },
      customdata: pts.map(p => [p.bias, p.mae, p.n_cases, p.n_stations, p.correlation]),
      hovertemplate: `${m}<br>%{x} jam (%{x}h)<br>RMSE: %{y:.3f}<br>Bias: %{customdata[0]:.3f}<extra></extra>`,
    };
  });

  Plotly.newPlot('scoreChart', traces, {
    title: `RMSE vs Lead Time (D+0 → D+${maxLeadTime/24}) — ${paramsMeta[param]?.label}`,
    paper_bgcolor: '#1e293b', plot_bgcolor: '#1e293b', font: { color: '#e2e8f0' },
    xaxis: { title: 'Lead Time (jam)', dtick: 24 },
    yaxis: { title: 'RMSE' },
  }, { responsive: true });

  document.getElementById('scoreChart').on('plotly_click', ev => {
    const pt = ev.points[0];
    document.getElementById('scoreDetail').innerHTML =
      `<strong>${pt.data.name}</strong> · ${formatLeadTime(pt.x)}<br>
       RMSE: <strong>${pt.y.toFixed(4)}</strong> · Bias: ${pt.customdata[0].toFixed(4)} ·
       MAE: ${pt.customdata[1].toFixed(4)} · r: ${pt.customdata[4].toFixed(4)} · N: ${pt.customdata[2]}`;
  });
}

function initMap() {
  map = L.map('leafletMap').setView([-2.5, 118], 5);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { attribution: '© OSM © CARTO' }).addTo(map);
}

async function loadMap() {
  const lt = document.getElementById('leadTime').value;
  const param = document.getElementById('parameter').value;
  const model = selectedModels()[0] || 'InaNWP';
  const init = selectedInitTime();
  const q = `model=${model}&parameter=${param}&lead_time=${lt}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;
  const data = await api(`/api/verification/map?${q}`);
  markers.forEach(m => map.removeLayer(m)); markers = [];
  if (!data.length) { document.getElementById('mapDetail').textContent = 'Belum ada data peta.'; return; }
  const maxRmse = Math.max(...data.map(d => d.rmse || 0), 0.01);
  data.forEach(d => {
    if (!d.lat || !d.lon) return;
    const color = d.rmse < maxRmse*0.33 ? '#4ade80' : d.rmse < maxRmse*0.66 ? '#fbbf24' : '#f87171';
    const m = L.circleMarker([d.lat, d.lon], { radius: 8, fillColor: color, color: '#fff', weight: 1, fillOpacity: 0.85 }).addTo(map);
    m.bindPopup(`<b>${d.name||d.station_id}</b><br>RMSE: ${d.rmse?.toFixed(3)}`);
    m.on('click', () => showStationMapDetail(d.station_id, param, init));
    markers.push(m);
  });
}

async function showStationMapDetail(stationId, param, init) {
  const q = `parameter=${param}&models=${modelsQuery()}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;
  const data = await api(`/api/station/${stationId}/detail?${q}`);
  const latest = data.series.filter(s => s.obs != null).slice(-1)[0] || {};
  let html = `<strong>${data.station.name||stationId}</strong><table><tr><th>Model</th><th>Fcst</th><th>Obs</th><th>Err</th></tr>`;
  selectedModels().forEach(m => {
    const err = latest[m]!=null && latest.obs!=null ? (latest[m]-latest.obs).toFixed(3) : '—';
    html += `<tr><td>${m}</td><td>${latest[m]?.toFixed(2)??'—'}</td><td>${latest.obs?.toFixed(2)??'—'}</td><td>${err}</td></tr>`;
  });
  document.getElementById('mapDetail').innerHTML = html + '</table>';
}

async function loadStationDetail() {
  const stationId = document.getElementById('stationSelect').value;
  const init = selectedInitTime();
  const q = `parameter=${document.getElementById('parameter').value}&models=${modelsQuery()}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`;
  const data = await api(`/api/station/${stationId}/detail?${q}`);
  const traces = [{ name: 'Observasi', x: data.series.map(s=>s.valid_time), y: data.series.map(s=>s.obs), mode: 'markers', marker: { size: 8, color: '#fbbf24' } }];
  selectedModels().forEach(m => traces.push({ name: m, x: data.series.map(s=>s.valid_time), y: data.series.map(s=>s[m]), mode: 'lines+markers', connectgaps: false }));
  Plotly.newPlot('stationChart', traces, {
    title: `${data.station.name||stationId} — ${paramsMeta[document.getElementById('parameter').value]?.label}`,
    paper_bgcolor: '#1e293b', plot_bgcolor: '#1e293b', font: { color: '#e2e8f0' },
  }, { responsive: true });
}

init().catch(console.error);
