const BASE_PATH = (() => {
  const p = window.location.pathname;
  if (p.startsWith('/monas')) return '/monas';
  return '';
})();

// Path calls are always `/api/...`. Under BASE_PATH=/monas Apache proxies
// `/monas/api` → `:8013/api`, so the origin prefix must be `/monas` (NOT `/monas/api`)
// or fetch becomes `/monas/api/api/...` and the dashboard stays blank.
const API = (() => {
  const { hostname, port } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return 'http://localhost:8013';
  }
  if (BASE_PATH) {
    return `${window.location.origin}${BASE_PATH}`;
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
let mapBulkCache = { key: '', data: null };
let stationDetailCache = { key: '', data: null };

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

/** UTC ISO → teks WIB (Asia/Jakarta, UTC+7). */
function formatTimeWIB(isoUtc) {
  if (!isoUtc) return '—';
  const s = String(isoUtc).endsWith('Z') ? isoUtc : `${isoUtc}Z`;
  try {
    return new Date(s).toLocaleString('id-ID', {
      timeZone: 'Asia/Jakarta',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }) + ' WIB';
  } catch {
    return String(isoUtc).slice(0, 16);
  }
}

function formatTimeDual(isoUtc) {
  if (!isoUtc) return '—';
  const utc = String(isoUtc).replace('Z', '').slice(0, 16).replace('T', ' ');
  return `${formatTimeWIB(isoUtc)} <span class="time-utc">(${utc} UTC)</span>`;
}

function nearestLeadTime(available, target) {
  if (!available?.length) return null;
  let best = available[0];
  let bestD = Math.abs(best - target);
  for (const lt of available) {
    const d = Math.abs(lt - target);
    if (d < bestD) { best = lt; bestD = d; }
  }
  return best;
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
  await loadPublicConfig();
  initCharts();
  try {
    const data = await api('/api/parameters');
    paramsMeta = data.verify_parameters || {};
    maxLeadTime = data.max_lead_time_hours || 168;

    const ltSlider = document.getElementById('leadTime');
    ltSlider.max = maxLeadTime;

    document.getElementById('parameter').innerHTML = Object.entries(paramsMeta).map(([k, v]) =>
      `<option value="${k}">${v.label} (${v.unit})</option>`).join('');
  } catch (e) {
    document.getElementById('pipelineStatus').textContent =
      `Gagal load /api/parameters (${API}): ${e.message}`;
    console.error('parameters', e);
    return;
  }

  try {
    const stations = await api('/api/stations');
    document.getElementById('stationSelect').innerHTML = stations.map(s =>
      `<option value="${s.station_id}">${s.station_id} — ${s.name || s.station_id}</option>`).join('');
  } catch (e) {
    console.warn('stations', e);
  }

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
      // Model tanpa run di store: uncheck agar chart tidak penuh kolom kosong
      if (src === 'none') cb.checked = false;
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
      `<option value="${c.init_time}">${c.model} · ${formatTimeWIB(c.init_time)} · ${c.nc_filename || ''}</option>`
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
      requestAnimationFrame(() => {
        rankingChart?.redraw();
        scoreChart?.redraw();
        stationChart?.redraw();
        stationMap?.invalidateSize();
      });
    });
  });

  ['parameter', 'initCycle'].forEach(id => document.getElementById(id).addEventListener('change', () => {
    mapBulkCache.key = '';
    stationDetailCache.key = '';
    refreshAll();
  }));
  document.getElementById('leadTime').addEventListener('input', () => {
    document.getElementById('leadTimeLabel').textContent = formatLeadTime(+document.getElementById('leadTime').value);
    const tab = document.querySelector('.tab.active')?.dataset.tab;
    if (tab === 'map') renderMapFromCache();
    else if (tab === 'station') {
      stationDetailCache.key = '';
      loadStationDetail();
    } else refreshAll();
  });
  document.getElementById('stationRangeMonths')?.addEventListener('change', () => {
    stationDetailCache.key = '';
    loadStationDetail();
  });
  document.querySelectorAll('.model-cb').forEach(cb => cb.addEventListener('change', () => {
    mapBulkCache.key = '';
    stationDetailCache.key = '';
    refreshAll();
  }));
  document.getElementById('stationSelect').addEventListener('change', () => {
    stationDetailCache.key = '';
    loadStationDetail();
  });
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

async function ensureMapBulk() {
  const model = selectedModels()[0] || 'InaNWP';
  const param = document.getElementById('parameter').value;
  const init = selectedInitTime();
  const key = `${model}|${param}|${init}`;
  if (mapBulkCache.key !== key) {
    mapBulkCache.data = await api(
      `/api/verification/map/bulk?model=${model}&parameter=${param}${init ? `&init_time=${encodeURIComponent(init)}` : ''}`
    );
    mapBulkCache.key = key;
  }
  return mapBulkCache.data;
}

function renderMapFromCache() {
  if (!mapBulkCache.data) return loadMap();
  if (stationMap) stationMap.invalidateSize();
  const lt = +document.getElementById('leadTime').value;
  const available = mapBulkCache.data.available_lead_times || [];
  const resolvedLt = available.includes(lt) ? lt : nearestLeadTime(available, lt);
  const data = resolvedLt != null
    ? (mapBulkCache.data.records || []).filter(d => d.lead_time === resolvedLt)
    : [];

  if (!data.length) {
    stationMap.setStations([]);
    const detail = document.getElementById('mapDetail');
    if (!available.length) {
      detail.textContent = 'Belum ada data peta untuk filter ini (parameter/init cycle).';
    } else {
      const minLt = Math.min(...available);
      const maxLt = Math.max(...available);
      detail.innerHTML = `<strong>Data kosong</strong> untuk ${formatLeadTime(lt)}.<br>
        Lead time tersedia: ${formatLeadTime(minLt)} – ${formatLeadTime(maxLt)} (${minLt}–${maxLt} jam).
        ${lt > maxLt ? 'Perluas data NC/pipeline untuk lead lebih jauh.' : 'Geser slider ke rentang tersebut.'}`;
    }
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
  const detail = document.getElementById('mapDetail');
  if (resolvedLt !== lt) {
    detail.textContent = `Menampilkan lead time terdekat: ${formatLeadTime(resolvedLt)} (slider: ${formatLeadTime(lt)}). Klik stasiun untuk detail.`;
  } else {
    detail.textContent = `${data.length} stasiun · ${formatLeadTime(lt)}. Klik stasiun untuk fcst vs obs.`;
  }
}

async function loadMap() {
  try {
    await ensureMapBulk();
    renderMapFromCache();
  } catch (e) {
    document.getElementById('mapDetail').textContent = 'Gagal memuat peta: ' + e.message;
  }
}

async function showStationMapDetail(stationId, param, init) {
  const lt = +document.getElementById('leadTime').value;
  const months = +document.getElementById('stationRangeMonths')?.value || 3;
  const q = `parameter=${param}&models=${modelsQuery()}&lead_time=${lt}&months=${months}`;
  const data = await api(`/api/station/${stationId}/detail?${q}`);
  const rows = data.series || [];
  const row = rows.filter(s => selectedModels().some(m => s[m] != null)).slice(-1)[0] || rows.slice(-1)[0] || {};
  let html = `<strong>${data.station.name || stationId}</strong> · ${formatLeadTime(lt)}<br>`;
  html += `<span class="time-utc">${formatTimeDual(row.valid_time)}</span><table><tr><th>Model</th><th>Fcst</th><th>Obs</th><th>Err</th></tr>`;
  selectedModels().forEach(m => {
    const err = row[m] != null && row.obs != null ? (row[m] - row.obs).toFixed(3) : '—';
    html += `<tr><td>${m}</td><td>${row[m]?.toFixed(2) ?? '—'}</td><td>${row.obs?.toFixed(2) ?? '—'}</td><td>${err}</td></tr>`;
  });
  document.getElementById('mapDetail').innerHTML = html + '</table>';
}

function stationDetailQuery() {
  const lt = document.getElementById('leadTime').value;
  const months = document.getElementById('stationRangeMonths')?.value || 3;
  return `parameter=${document.getElementById('parameter').value}&models=${modelsQuery()}&lead_time=${lt}&months=${months}`;
}

function toEpochMs(isoUtc) {
  if (!isoUtc) return null;
  const s = String(isoUtc).endsWith('Z') ? isoUtc : `${isoUtc}Z`;
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : t;
}

function downsampleSeries(rows, maxPoints = 800, models = []) {
  if (rows.length <= maxPoints) return rows;
  const keep = new Set();
  // Jangan buang titik yang punya forecast (jarang vs obs jam-jaman)
  rows.forEach((r, i) => {
    if (models.some(m => r[m] != null)) keep.add(i);
  });
  const step = Math.ceil(rows.length / Math.max(maxPoints - keep.size, 1));
  for (let i = 0; i < rows.length; i += step) keep.add(i);
  keep.add(rows.length - 1);
  return rows.filter((_, i) => keep.has(i));
}

function renderStationFromCache() {
  const data = stationDetailCache.data;
  if (!data) return loadStationDetail();
  const param = document.getElementById('parameter').value;
  const stationId = document.getElementById('stationSelect').value;
  const lt = +document.getElementById('leadTime').value;
  const months = +document.getElementById('stationRangeMonths')?.value || 3;
  const rows = data.series || [];
  const models = selectedModels();
  const plotRows = downsampleSeries(rows, 800, models);

  // Obs = garis kontinu kuning; model = titik scatter (dots) warna per model.
  // Gap di model langsung terlihat = area tanpa titik.
  const series = [{
    name: 'Observasi',
    color: MODEL_COLORS.Observasi,
    width: 1.5,
    dotsOnly: false,
    x: plotRows.map(s => toEpochMs(s.valid_time)),
    y: plotRows.map(s => (s.obs != null ? s.obs : null)),
  }];
  models.forEach(m => {
    series.push({
      name: m,
      color: MODEL_COLORS[m] || '#00529B',
      width: 1,
      dotsOnly: true,
      x: plotRows.map(s => toEpochMs(s.valid_time)),
      y: plotRows.map(s => (s[m] != null ? s[m] : null)),
    });
  });

  stationChart.setLines({
    title: `${data.station.name || stationId} — ${paramsMeta[param]?.label} · ${months} bln · ${formatLeadTime(lt)}`,
    xLabel: 'Waktu valid (WIB)',
    yLabel: paramsMeta[param]?.unit || '',
    xNumeric: true,
    xTime: true,
    series,
  });

  if (!rows.length) {
    document.getElementById('stationTable').innerHTML =
      `<em>Belum ada data time series untuk stasiun/parameter ini (window ${months} bulan, lead ${formatLeadTime(lt)}).</em>`;
    return;
  }

  const withModel = rows.filter(s => models.some(m => s[m] != null));
  const gaps = rows.filter(s => s.obs != null && !models.some(m => s[m] != null)).length;

  let html = `<p class="lt-note">${rows.length} titik · <strong>${withModel.length} ada forecast</strong> · ~${gaps} obs tanpa model (gap) · ${formatTimeWIB(data.date_from)} → ${formatTimeWIB(data.date_to)}</p>`;
  html += `<p class="lt-note">Garis = observasi sinoptik. Titik warna = prakiraan model per init cycle. Area kosong = tidak ada pasangan fcst+obs.</p>`;
  html += '<div class="table-scroll"><table class="station-ts-table"><thead><tr><th>Waktu Valid (WIB)</th><th>Obs</th>';
  models.forEach(m => { html += `<th>${m}</th><th>Err</th>`; });
  html += '</tr></thead><tbody>';

  // Utamakan baris yang punya forecast, lalu sisanya (maks ~200)
  const prefer = withModel.slice(-100);
  const preferSet = new Set(prefer);
  const rest = rows.filter(r => !preferSet.has(r)).slice(-(200 - prefer.length));
  const tableRows = [...prefer, ...rest].sort((a, b) => String(a.valid_time).localeCompare(String(b.valid_time)));
  tableRows.forEach(s => {
    const hasModel = models.some(m => s[m] != null);
    html += `<tr class="${hasModel ? '' : 'row-gap'}">`;
    html += `<td>${formatTimeDual(s.valid_time)}</td>`;
    html += `<td>${s.obs?.toFixed(2) ?? '—'}</td>`;
    models.forEach(m => {
      const err = s[m] != null && s.obs != null ? (s[m] - s.obs).toFixed(2) : '—';
      html += `<td>${s[m]?.toFixed(2) ?? '—'}</td><td>${err}</td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  document.getElementById('stationTable').innerHTML = html;
}

async function loadStationDetail() {
  const stationId = document.getElementById('stationSelect').value;
  if (!stationId) return;
  const key = `${stationId}|${stationDetailQuery()}`;
  if (stationDetailCache.key !== key) {
    stationDetailCache.data = await api(`/api/station/${stationId}/detail?${stationDetailQuery()}`);
    stationDetailCache.key = key;
  }
  renderStationFromCache();
}

init().catch(console.error);
