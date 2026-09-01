const API = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? 'http://localhost:8013'
  : `http://${window.location.hostname}:8013`;

let map, markers = [];
let paramsMeta = {};
let stations = [];

async function api(path, opts = {}) {
  const res = await fetch(`${API}${path}`, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function selectedModels() {
  return [...document.querySelectorAll('.model-cb:checked')].map(el => el.value);
}

function modelsQuery() {
  return selectedModels().join(',');
}

async function init() {
  const data = await api('/api/parameters');
  paramsMeta = data.verify_parameters;
  const sel = document.getElementById('parameter');
  sel.innerHTML = Object.entries(paramsMeta).map(([k, v]) =>
    `<option value="${k}">${v.label} (${v.unit})</option>`
  ).join('');

  stations = await api('/api/stations');
  const stSel = document.getElementById('stationSelect');
  stSel.innerHTML = stations.map(s =>
    `<option value="${s.station_id}">${s.station_id} — ${s.name || s.station_id}</option>`
  ).join('');

  initMap();
  bindEvents();
  await refreshAll();
}

function bindEvents() {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
      refreshAll();
    });
  });

  ['parameter', 'leadTime'].forEach(id => {
    document.getElementById(id).addEventListener('change', refreshAll);
    document.getElementById(id).addEventListener('input', () => {
      document.getElementById('leadTimeLabel').textContent = document.getElementById('leadTime').value;
      refreshAll();
    });
  });

  document.querySelectorAll('.model-cb').forEach(cb => cb.addEventListener('change', refreshAll));
  document.getElementById('stationSelect').addEventListener('change', loadStationDetail);

  document.getElementById('uploadNc').addEventListener('click', async () => {
    const file = document.getElementById('ncFile').files[0];
    if (!file) return alert('Pilih file NC terlebih dahulu');
    const model = document.getElementById('ncModel').value;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetch(`${API}/api/models/upload-nc?model=${model}`, { method: 'POST', body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail);
      alert(`Upload berhasil: ${data.forecast_records} records`);
      await refreshAll();
    } catch (e) { alert('Upload gagal: ' + e.message); }
  });

  document.getElementById('syncSftp').addEventListener('click', async () => {
    try {
      const r = await api('/api/obs/sync-sftp', { method: 'POST' });
      alert(`SFTP sync: ${r.downloaded_records} records`);
      await refreshAll();
    } catch (e) { alert('SFTP: ' + e.message); }
  });

  document.getElementById('seedDemo').addEventListener('click', async () => {
    await api('/api/demo/seed', { method: 'POST' });
    await refreshAll();
  });
}

async function refreshAll() {
  const tab = document.querySelector('.tab.active').dataset.tab;
  if (tab === 'overview') await loadOverview();
  if (tab === 'scores') await loadScores();
  if (tab === 'map') await loadMap();
  if (tab === 'station') await loadStationDetail();
}

async function loadOverview() {
  const param = document.getElementById('parameter').value;
  const lt = document.getElementById('leadTime').value;

  const [ranking, scores] = await Promise.all([
    api(`/api/verification/ranking?models=${modelsQuery()}&score=rmse`),
    api(`/api/verification/scores?models=${modelsQuery()}&parameter=${param}&lead_time=${lt}`),
  ]);

  const cards = document.getElementById('rankingCards');
  cards.innerHTML = ranking.ranking.map(r => `
    <div class="rank-card rank-${r.rank}">
      <div class="rank-num">#${r.rank}</div>
      <div class="model-name">${r.model}</div>
      <div class="metric">Mean RMSE: <strong>${r.mean_rmse?.toFixed(3) ?? '—'}</strong></div>
      <div class="metric">Mean MAE: ${r.mean_mae?.toFixed(3) ?? '—'} · r: ${r.mean_correlation?.toFixed(3) ?? '—'}</div>
      <div class="metric">Skill Score: ${r.skill_score?.toFixed(4) ?? '—'}</div>
    </div>
  `).join('');

  Plotly.newPlot('rankingChart', [{
    type: 'bar',
    x: ranking.ranking.map(r => r.model),
    y: ranking.ranking.map(r => r.mean_rmse),
    marker: { color: ['#fbbf24', '#94a3b8', '#64748b', '#475569'] },
    text: ranking.ranking.map(r => `Rank #${r.rank}<br>RMSE: ${r.mean_rmse?.toFixed(3)}`),
    textposition: 'auto',
  }], {
    title: '🏆 Ranking Model — Mean RMSE (semua parameter, semakin kecil semakin baik)',
    paper_bgcolor: '#1e293b', plot_bgcolor: '#1e293b', font: { color: '#e2e8f0' },
    yaxis: { title: 'Mean RMSE' },
  }, { responsive: true });

  const kpi = document.getElementById('kpiGrid');
  kpi.innerHTML = scores.scores.map(s => `
    <div class="kpi">
      <div class="label">${s.model} · LT+${s.lead_time}h</div>
      <div class="value">RMSE ${s.rmse?.toFixed(2)}</div>
      <div class="label">Bias ${s.bias?.toFixed(2)} · MAE ${s.mae?.toFixed(2)} · N=${s.n_cases}</div>
    </div>
  `).join('');
}

async function loadScores() {
  const param = document.getElementById('parameter').value;
  const data = await api(`/api/verification/scores?models=${modelsQuery()}&parameter=${param}`);
  const models = selectedModels();
  const traces = models.map((m, i) => {
    const pts = data.scores.filter(s => s.model === m).sort((a, b) => a.lead_time - b.lead_time);
    return {
      name: m,
      x: pts.map(p => p.lead_time),
      y: pts.map(p => p.rmse),
      mode: 'lines+markers',
      type: 'scatter',
      marker: { size: 10 },
      customdata: pts.map(p => [p.bias, p.mae, p.n_cases, p.n_stations, p.correlation]),
      hovertemplate: `${m}<br>LT+%{x}h<br>RMSE: %{y:.3f}<br>Bias: %{customdata[0]:.3f}<br>MAE: %{customdata[1]:.3f}<br>N: %{customdata[2]}<extra></extra>`,
    };
  });

  Plotly.newPlot('scoreChart', traces, {
    title: `RMSE vs Lead Time — ${paramsMeta[param]?.label || param}`,
    paper_bgcolor: '#1e293b', plot_bgcolor: '#1e293b', font: { color: '#e2e8f0' },
    xaxis: { title: 'Lead Time (jam)' },
    yaxis: { title: 'RMSE' },
  }, { responsive: true });

  document.getElementById('scoreChart').on('plotly_click', async (ev) => {
    const pt = ev.points[0];
    const model = pt.data.name;
    const lt = pt.x;
    document.getElementById('scoreDetail').innerHTML = `
      <strong>${model}</strong> · Lead Time +${lt}h · Parameter: ${paramsMeta[param]?.label}<br>
      RMSE: <strong>${pt.y.toFixed(4)}</strong> ·
      Bias: ${pt.customdata[0].toFixed(4)} · MAE: ${pt.customdata[1].toFixed(4)} ·
      r: ${pt.customdata[4].toFixed(4)} ·
      Kasus: ${pt.customdata[2]} · Stasiun: ${pt.customdata[3]}
    `;
  });
}

function initMap() {
  map = L.map('leafletMap').setView([-2.5, 118], 5);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '© OpenStreetMap © CARTO'
  }).addTo(map);
}

async function loadMap() {
  const param = document.getElementById('parameter').value;
  const lt = document.getElementById('leadTime').value;
  const model = selectedModels()[0] || 'InaNWP';

  const data = await api(`/api/verification/map?model=${model}&parameter=${param}&lead_time=${lt}`);
  markers.forEach(m => map.removeLayer(m));
  markers = [];

  if (!data.length) {
    document.getElementById('mapDetail').textContent = 'Tidak ada data untuk kombinasi ini.';
    return;
  }

  const maxRmse = Math.max(...data.map(d => d.rmse || 0), 0.01);

  data.forEach(d => {
    if (!d.lat || !d.lon) return;
    const color = d.rmse < maxRmse * 0.33 ? '#4ade80' : d.rmse < maxRmse * 0.66 ? '#fbbf24' : '#f87171';
    const marker = L.circleMarker([d.lat, d.lon], {
      radius: 8 + (d.n_cases || 1) * 0.5,
      fillColor: color,
      color: '#fff',
      weight: 1,
      fillOpacity: 0.85,
    }).addTo(map);
    marker.bindPopup(`<b>${d.name || d.station_id}</b><br>RMSE: ${d.rmse?.toFixed(3)}<br>Bias: ${d.bias?.toFixed(3)}`);
    marker.on('click', () => showStationMapDetail(d.station_id, param));
    markers.push(marker);
  });
}

async function showStationMapDetail(stationId, param) {
  const data = await api(`/api/station/${stationId}/detail?parameter=${param}&models=${modelsQuery()}`);
  const lt = document.getElementById('leadTime').value;
  const latest = data.series.filter(s => s.obs != null).slice(-1)[0] || {};

  let html = `<strong>${data.station.name || stationId}</strong> (${stationId})<br>
    Parameter: ${paramsMeta[param]?.label}<br><table>
    <tr><th>Model</th><th>Fcst</th><th>Obs</th><th>Error</th></tr>`;

  selectedModels().forEach(m => {
    const fcst = latest[m];
    const obs = latest.obs;
    const err = fcst != null && obs != null ? (fcst - obs).toFixed(3) : '—';
    html += `<tr><td>${m}</td><td>${fcst?.toFixed(2) ?? '—'}</td><td>${obs?.toFixed(2) ?? '—'}</td><td>${err}</td></tr>`;
  });
  html += '</table>';
  document.getElementById('mapDetail').innerHTML = html;
}

async function loadStationDetail() {
  const stationId = document.getElementById('stationSelect').value;
  const param = document.getElementById('parameter').value;
  const data = await api(`/api/station/${stationId}/detail?parameter=${param}&models=${modelsQuery()}`);

  const traces = [{ name: 'Observasi', x: data.series.map(s => s.valid_time), y: data.series.map(s => s.obs), mode: 'markers', marker: { size: 8, color: '#fbbf24' } }];
  selectedModels().forEach(m => {
    traces.push({
      name: m,
      x: data.series.map(s => s.valid_time),
      y: data.series.map(s => s[m]),
      mode: 'lines+markers',
      connectgaps: false,
    });
  });

  Plotly.newPlot('stationChart', traces, {
    title: `${data.station.name || stationId} — ${paramsMeta[param]?.label}`,
    paper_bgcolor: '#1e293b', plot_bgcolor: '#1e293b', font: { color: '#e2e8f0' },
    xaxis: { title: 'Valid Time' },
    yaxis: { title: paramsMeta[param]?.unit || '' },
  }, { responsive: true });

  document.getElementById('stationChart').on('plotly_click', ev => {
    const idx = ev.points[0].pointIndex;
    const row = data.series[idx];
    let html = `<table><tr><th>Waktu</th><th>Obs</th>${selectedModels().map(m => `<th>${m}</th>`).join('')}<th>Error</th></tr>`;
    html += `<tr><td>${row.valid_time}</td><td>${row.obs?.toFixed(2) ?? '—'}</td>`;
    selectedModels().forEach(m => { html += `<td>${row[m]?.toFixed(2) ?? '—'}</td>`; });
    const ref = selectedModels()[0];
    const err = row[ref] != null && row.obs != null ? (row[ref] - row.obs).toFixed(3) : '—';
    html += `<td>${err}</td></tr></table>`;
    document.getElementById('stationTable').innerHTML = html;
  });

  let tbl = `<table><tr><th>Waktu</th><th>Obs</th>${selectedModels().map(m => `<th>${m}</th><th>Err</th>`).join('')}</tr>`;
  data.series.slice(-8).forEach(row => {
    tbl += `<tr><td>${row.valid_time?.slice(0, 16)}</td><td>${row.obs?.toFixed(2) ?? '—'}</td>`;
    selectedModels().forEach(m => {
      const err = row[m] != null && row.obs != null ? (row[m] - row.obs).toFixed(2) : '—';
      tbl += `<td>${row[m]?.toFixed(2) ?? '—'}</td><td>${err}</td>`;
    });
    tbl += '</tr>';
  });
  tbl += '</table>';
  document.getElementById('stationTable').innerHTML = tbl;
}

init().catch(err => console.error(err));
