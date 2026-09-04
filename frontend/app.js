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

const INIT_DASHES = [
  [],                 // terbaru: solid
  [10, 5],
  [3, 4],
  [12, 4, 2, 4],
  [2, 3],
  [14, 4, 2, 4, 2, 4],
];
const INIT_MARKERS = ['circle', 'square', 'diamond', 'triangle', 'circle', 'square'];

const MODEL_COLORS = {
  Observasi: '#ca8a04',
  InaNWP: '#ea580c',
  InaCAWO: '#16a34a',
  GFS: '#dc2626',
  IFS: '#7c3aed',
};

let rankingChart, scoreChart, stationChart, stationMap;
let paramsMeta = {};
let paramsAvailableByModel = {};
let paramsUnavailableNotes = {};
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
function requireModels(emptyHtmlId, emptyMsg) {
  if (selectedModels().length) return true;
  if (emptyHtmlId) {
    const el = document.getElementById(emptyHtmlId);
    if (el) el.innerHTML = emptyMsg || 'Centang minimal satu model di sidebar.';
  }
  return false;
}

/** Param tersedia jika ≥1 model tercentang punya field NC-nya (InaNWP asim list).
 *  Jika API belum kirim available_by_model (backend lama / belum restart), jangan kunci UI. */
function paramAvailableForSelection(param) {
  const models = selectedModels();
  if (!models.length) return true;
  const known = models.filter(m => Array.isArray(paramsAvailableByModel[m]));
  if (!known.length) return true;
  return known.some(m => paramsAvailableByModel[m].includes(param));
}

function refreshParameterOptions() {
  const sel = document.getElementById('parameter');
  if (!sel || !Object.keys(paramsMeta).length) return;
  const prev = sel.value;
  const notes = paramsUnavailableNotes.InaNWP || {};
  sel.innerHTML = Object.entries(paramsMeta).map(([k, v]) => {
    const ok = paramAvailableForSelection(k);
    const hint = !ok && notes[k] ? ` — tidak di NC` : (!ok ? ' — tidak di NC model' : '');
    return `<option value="${k}" ${ok ? '' : 'disabled'}>${v.label} (${v.unit})${hint}</option>`;
  }).join('');
  if (prev && [...sel.options].some(o => o.value === prev && !o.disabled)) {
    sel.value = prev;
  } else {
    const first = [...sel.options].find(o => !o.disabled);
    if (first) sel.value = first.value;
  }
}
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
    cartoApiKey = (cfg.carto_api_key || '').trim();
    if (stationMap) stationMap.setCartoKey(cartoApiKey);
    if (!cartoApiKey) {
      console.warn('CARTO_API_KEY kosong — /api/config/public.has_carto_key=', cfg.has_carto_key);
    }
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
  stationChart = new MonasChart('stationChart', { zoomable: true });
  document.getElementById('chartZoomIn')?.addEventListener('click', () => stationChart.zoomBy(0.7));
  document.getElementById('chartZoomOut')?.addEventListener('click', () => stationChart.zoomBy(1.35));
  document.getElementById('chartZoomReset')?.addEventListener('click', () => stationChart.resetZoom());
  stationMap = new StationCanvasMap('leafletMap', {
    cartoKey: cartoApiKey,
    onStationClick(st) {
      showStationMapDetail(st);
    },
  });
}

async function init() {
  await loadPublicConfig();
  initCharts();
  try {
    const data = await api('/api/parameters');
    paramsMeta = data.verify_parameters || {};
    paramsAvailableByModel = data.available_by_model || {};
    paramsUnavailableNotes = data.unavailable_notes || {};
    maxLeadTime = data.max_lead_time_hours || 168;

    const ltSlider = document.getElementById('leadTime');
    ltSlider.max = maxLeadTime;

    refreshParameterOptions();
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
  updateSidebarForTab(document.querySelector('.tab.active')?.dataset.tab || 'overview');
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
    refreshParameterOptions();
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

function updateSidebarForTab(tab) {
  const leadSec = document.getElementById('leadTimeSection');
  if (leadSec) leadSec.style.display = (tab === 'overview' || tab === 'map') ? '' : 'none';
}

function bindEvents() {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab, .panel').forEach(el => el.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
      updateSidebarForTab(btn.dataset.tab);
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
    else if (tab === 'overview') loadOverview();
  });
  document.getElementById('stationRangeMonths')?.addEventListener('change', () => {
    stationDetailCache.key = '';
    loadStationDetail();
  });
  document.querySelectorAll('.model-cb').forEach(cb => cb.addEventListener('change', () => {
    mapBulkCache.key = '';
    stationDetailCache.key = '';
    refreshParameterOptions();
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
  if (!requireModels('rankingCards', '<em>Centang minimal satu model di sidebar.</em>')) {
    document.getElementById('kpiGrid').innerHTML = '';
    rankingChart?.setBar({ title: 'Pilih model', yLabel: 'RMSE', labels: ['—'], values: [0], colors: ['#e2e8f0'] });
    return;
  }
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
  if (!selectedModels().length) {
    scoreChart.setLines({ title: 'Centang minimal satu model', xLabel: 'Lead Time (jam)', yLabel: 'RMSE', xNumeric: true, series: [] });
    return;
  }
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
  const model = selectedModels()[0];
  if (!model) return null;
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
  if (!selectedModels().length) {
    stationMap?.setStations([]);
    document.getElementById('mapDetail').textContent = 'Centang minimal satu model di sidebar.';
    return;
  }
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
    fcst: d.fcst_mean,
    obs: d.obs_mean,
    lead_time: d.lead_time,
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
    if (!selectedModels().length) {
      stationMap?.setStations([]);
      document.getElementById('mapDetail').textContent = 'Centang minimal satu model di sidebar.';
      return;
    }
    await ensureMapBulk();
    renderMapFromCache();
  } catch (e) {
    document.getElementById('mapDetail').textContent = 'Gagal memuat peta: ' + e.message;
  }
}

async function showStationMapDetail(st) {
  const stationId = st.station_id || st;
  const model = selectedModels()[0];
  const ltSlider = +document.getElementById('leadTime').value;
  const available = mapBulkCache.data?.available_lead_times || [];
  const resolvedLt = st.lead_time
    ?? (available.includes(ltSlider) ? ltSlider : nearestLeadTime(available, ltSlider));
  const recs = (mapBulkCache.data?.records || []).filter(
    d => String(d.station_id) === String(stationId) && (resolvedLt == null || d.lead_time === resolvedLt),
  );
  const rec = recs[0];
  const fcst = rec?.fcst_mean ?? st.fcst;
  const obs = rec?.obs_mean ?? st.obs;
  const name = rec?.name || st.name || stationId;
  const initIso = mapBulkCache.data?.init_time;

  let html = `<strong>${name}</strong> · ${formatLeadTime(resolvedLt ?? ltSlider)}`;
  if (initIso) html += `<br><span class="time-utc">Init ${formatTimeDual(initIso)}</span>`;
  html += '<table><tr><th>Model</th><th>Fcst</th><th>Obs</th><th>Err</th></tr>';
  if (model) {
    const err = fcst != null && obs != null && !Number.isNaN(fcst) && !Number.isNaN(obs)
      ? (fcst - obs).toFixed(3) : '—';
    html += `<tr><td>${model}</td><td>${Number.isFinite(fcst) ? fcst.toFixed(2) : '—'}</td>`;
    html += `<td>${Number.isFinite(obs) ? obs.toFixed(2) : '—'}</td><td>${err}</td></tr>`;
  }
  html += '</table>';
  document.getElementById('mapDetail').innerHTML = html;
}

function stationDetailQuery() {
  const months = document.getElementById('stationRangeMonths')?.value || 3;
  const init = selectedInitTime();
  let q = `parameter=${document.getElementById('parameter').value}&models=${modelsQuery()}&series_mode=by_init&months=${months}`;
  if (init) q += `&init_time=${encodeURIComponent(init)}`;
  return q;
}

function toEpochMs(isoUtc) {
  if (!isoUtc) return null;
  const s = String(isoUtc).endsWith('Z') ? isoUtc : `${isoUtc}Z`;
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : t;
}

function downsamplePoints(points, maxPoints = 400) {
  if (points.length <= maxPoints) return points;
  const step = Math.ceil(points.length / maxPoints);
  const out = points.filter((_, i) => i % step === 0);
  if (out[out.length - 1] !== points[points.length - 1]) out.push(points[points.length - 1]);
  return out;
}

function renderStationFromCache() {
  const data = stationDetailCache.data;
  if (!data) return loadStationDetail();
  const param = document.getElementById('parameter').value;
  const stationId = document.getElementById('stationSelect').value;
  const months = +document.getElementById('stationRangeMonths')?.value || 3;

  if (data.series_mode === 'by_init' || data.inits) {
    const obsPts = downsamplePoints(data.obs || [], 900);
    const series = [{
      name: 'Observasi',
      color: MODEL_COLORS.Observasi,
      width: 2,
      dotsOnly: false,
      x: obsPts.map(p => toEpochMs(p.valid_time)),
      y: obsPts.map(p => (p.obs != null ? p.obs : null)),
    }];
    const inits = (data.inits || []).slice().sort((a, b) => String(b.init_time).localeCompare(String(a.init_time)));
    const dashIdxByModel = {};
    inits.forEach((run) => {
      const n = dashIdxByModel[run.model] || 0;
      dashIdxByModel[run.model] = n + 1;
      const pts = downsamplePoints(run.points || [], 200);
      // Tooltip: model + init. Legend: nama model saja.
      const tip = `${run.model} · init ${formatTimeWIB(run.init_time)}`;
      series.push({
        name: tip,
        legendName: run.model,
        color: MODEL_COLORS[run.model] || '#00529B',
        dash: INIT_DASHES[n % INIT_DASHES.length],
        marker: INIT_MARKERS[n % INIT_MARKERS.length],
        // Terbaru lebih tebal & pekat; lama lebih tipis/transparan + dash beda
        alpha: Math.max(0.4, 1 - n * 0.12),
        width: n === 0 ? 2.4 : 1.6,
        markers: true,
        dotsOnly: false,
        x: pts.map(p => toEpochMs(p.valid_time)),
        y: pts.map(p => (p.fcst != null ? p.fcst : null)),
      });
    });

    stationChart.setLines({
      title: `${data.station.name || stationId} — ${paramsMeta[param]?.label} · ${months} bln · per init cycle`,
      xLabel: 'Waktu valid (WIB)',
      yLabel: paramsMeta[param]?.unit || '',
      xNumeric: true,
      xTime: true,
      series,
    });

    const flat = [];
    for (const run of inits) {
      for (const p of run.points || []) {
        flat.push({
          valid_time: p.valid_time,
          init_time: run.init_time,
          model: run.model,
          lead_time: p.lead_time,
          fcst: p.fcst,
          obs: p.obs,
        });
      }
    }
    flat.sort((a, b) => String(a.valid_time).localeCompare(String(b.valid_time)) || String(a.init_time).localeCompare(String(b.init_time)));

    if (!flat.length && !obsPts.length) {
      document.getElementById('stationTable').innerHTML =
        `<em>Belum ada data untuk stasiun/parameter ini (window ${months} bulan).</em>`;
      return;
    }

    let html = `<p class="lt-note">${inits.length} init cycle · ${obsPts.length}+ titik obs · ${flat.length} titik fcst · ${formatTimeWIB(data.date_from)} → ${formatTimeWIB(data.date_to)}</p>`;
    html += '<div class="table-scroll"><table class="station-ts-table"><thead><tr><th>Valid (WIB)</th><th>Init</th><th>Model</th><th>Lead</th><th>Fcst</th><th>Obs</th><th>Err</th></tr></thead><tbody>';
    const tableRows = flat.slice(-200);
    tableRows.forEach(r => {
      const err = r.fcst != null && r.obs != null ? (r.fcst - r.obs).toFixed(2) : '—';
      html += `<tr><td>${formatTimeDual(r.valid_time)}</td><td>${formatTimeWIB(r.init_time)}</td><td>${r.model}</td>`;
      html += `<td>${formatLeadTime(r.lead_time)}</td><td>${r.fcst?.toFixed(2) ?? '—'}</td><td>${r.obs?.toFixed(2) ?? '—'}</td><td>${err}</td></tr>`;
    });
    html += '</tbody></table></div>';
    if (flat.length > 200) html = `<p class="lt-note">200 baris terakhir dari ${flat.length} titik fcst.</p>` + html;
    document.getElementById('stationTable').innerHTML = html;
    return;
  }

  // legacy by_lead (sqlite / fallback)
  const lt = data.lead_time ?? 12;
  const rows = data.series || [];
  const models = selectedModels();
  const plotRows = rows.length > 800 ? downsamplePoints(rows, 800) : rows;
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
  document.getElementById('stationTable').innerHTML = `<em>Mode by_lead (legacy).</em>`;
}

async function loadStationDetail() {
  const stationId = document.getElementById('stationSelect').value;
  if (!stationId) return;
  if (!selectedModels().length) {
    stationChart.setLines({
      title: 'Centang minimal satu model di sidebar',
      xLabel: 'Waktu valid (WIB)', yLabel: '', xNumeric: true, xTime: true, series: [],
    });
    document.getElementById('stationTable').innerHTML = '<em>Centang minimal satu model di sidebar.</em>';
    return;
  }
  const key = `${stationId}|${stationDetailQuery()}`;
  if (stationDetailCache.key !== key) {
    stationDetailCache.data = await api(`/api/station/${stationId}/detail?${stationDetailQuery()}`);
    stationDetailCache.key = key;
  }
  renderStationFromCache();
}

init().catch(console.error);
