/** Canvas station map — tiles + vector dots in one layer (no Leaflet). */
(function (global) {
  const TILE = 256;

  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function latLonToWorld(lat, lon, zoom) {
    const scale = TILE * 2 ** zoom;
    const x = ((lon + 180) / 360) * scale;
    const sin = Math.sin((lat * Math.PI) / 180);
    const y = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale;
    return { x, y };
  }

  function worldToLatLon(x, y, zoom) {
    const scale = TILE * 2 ** zoom;
    const lon = (x / scale) * 360 - 180;
    const n = Math.PI - (2 * Math.PI * y) / scale;
    const lat = (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
    return { lat, lon };
  }

  class StationCanvasMap {
    constructor(container, opts = {}) {
      this.el = typeof container === 'string' ? document.getElementById(container) : container;
      this.cartoKey = opts.cartoKey || '';
      this.onStationClick = opts.onStationClick || null;
      this.center = { lat: -2.5, lon: 118 };
      this.zoom = 5;
      this.stations = [];
      this.tileCache = new Map();
      this.drag = null;
      this.hover = null;

      this.wrap = document.createElement('div');
      this.wrap.style.cssText = 'position:relative;width:100%;height:100%;overflow:hidden;border-radius:10px;';
      this.canvas = document.createElement('canvas');
      this.canvas.style.display = 'block';
      this.canvas.style.width = '100%';
      this.canvas.style.height = '100%';
      this.canvas.style.cursor = 'grab';
      this.wrap.appendChild(this.canvas);
      this.el.innerHTML = '';
      this.el.appendChild(this.wrap);

      this.attribution = document.createElement('div');
      this.attribution.style.cssText = 'position:absolute;right:6px;bottom:4px;font:10px sans-serif;color:#64748b;background:rgba(255,255,255,0.8);padding:2px 6px;border-radius:4px;';
      this.attribution.textContent = '© OSM © CARTO';
      this.wrap.appendChild(this.attribution);

      this.ctx = this.canvas.getContext('2d');
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);

      this.canvas.addEventListener('mousedown', e => this._onDown(e));
      window.addEventListener('mouseup', () => { this.drag = null; this.canvas.style.cursor = 'grab'; });
      window.addEventListener('mousemove', e => this._onMove(e));
      this.canvas.addEventListener('wheel', e => this._onWheel(e), { passive: false });
      this.canvas.addEventListener('click', e => this._onClick(e));

      this._ro = new ResizeObserver(() => this._resize());
      this._ro.observe(this.el);
      this._resize();
    }

    setCartoKey(key) {
      this.cartoKey = (key || '').trim();
      this.tileCache.clear();
      this.attribution.textContent = this.cartoKey
        ? '© OSM © CARTO'
        : '© OSM · CARTO_API_KEY belum terbaca API — cek .env + pm2 restart monas-api';
      this.draw();
    }

    setStations(list) {
      this.stations = list.filter(s => s.lat != null && s.lon != null);
      this.draw();
    }

    invalidateSize() {
      this._resize();
    }

    destroy() {
      this._ro.disconnect();
    }

    _resize() {
      const w = this.el.clientWidth || 600;
      const h = this.el.clientHeight || 520;
      this.w = w;
      this.h = h;
      this.canvas.width = Math.round(w * this.dpr);
      this.canvas.height = Math.round(h * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.draw();
    }

    _centerWorld() {
      return latLonToWorld(this.center.lat, this.center.lon, this.zoom);
    }

    _project(lat, lon) {
      const c = this._centerWorld();
      const p = latLonToWorld(lat, lon, this.zoom);
      return { x: this.w / 2 + (p.x - c.x), y: this.h / 2 + (p.y - c.y) };
    }

    _unproject(sx, sy) {
      const c = this._centerWorld();
      const wx = c.x + (sx - this.w / 2);
      const wy = c.y + (sy - this.h / 2);
      return worldToLatLon(wx, wy, this.zoom);
    }

    _tileUrl(z, x, y) {
      const s = 'abcd'[(x + y) % 4];
      const key = this.cartoKey ? `?key=${encodeURIComponent(this.cartoKey)}` : '';
      return `https://${s}.basemaps.cartocdn.com/rastertiles/voyager/${z}/${x}/${y}.png${key}`;
    }

    _loadTile(z, x, y) {
      const n = 2 ** z;
      const tx = ((x % n) + n) % n;
      const key = `${z}/${tx}/${y}`;
      if (this.tileCache.has(key)) return this.tileCache.get(key);
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.src = this._tileUrl(z, tx, y);
      const entry = { img, loaded: false };
      img.onload = () => { entry.loaded = true; this.draw(); };
      img.onerror = () => { entry.loaded = false; };
      this.tileCache.set(key, entry);
      if (this.tileCache.size > 120) {
        const first = this.tileCache.keys().next().value;
        this.tileCache.delete(first);
      }
      return entry;
    }

    draw() {
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.w, this.h);
      ctx.fillStyle = '#e2e8f0';
      ctx.fillRect(0, 0, this.w, this.h);

      const z = Math.max(3, Math.min(12, Math.round(this.zoom)));
      const c = this._centerWorld();
      const tl = worldToLatLon(c.x - this.w / 2, c.y - this.h / 2, this.zoom);
      const br = worldToLatLon(c.x + this.w / 2, c.y + this.h / 2, this.zoom);
      const tMin = latLonToWorld(tl.lat, tl.lon, z);
      const tMax = latLonToWorld(br.lat, br.lon, z);

      const x0 = Math.floor(tMin.x / TILE);
      const x1 = Math.floor(tMax.x / TILE);
      const y0 = Math.floor(tMin.y / TILE);
      const y1 = Math.floor(tMax.y / TILE);

      for (let ty = y0; ty <= y1; ty++) {
        for (let tx = x0; tx <= x1; tx++) {
          const entry = this._loadTile(z, tx, ty);
          if (!entry.loaded) continue;
          const wx = tx * TILE;
          const wy = ty * TILE;
          const sx = this.w / 2 + (wx - c.x);
          const sy = this.h / 2 + (wy - c.y);
          ctx.drawImage(entry.img, sx, sy, TILE, TILE);
        }
      }

      this._stationPx = [];
      this.stations.forEach(st => {
        const p = this._project(st.lat, st.lon);
        const r = st === this.hover ? 10 : 7;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fillStyle = st.color || '#00529B';
        ctx.globalAlpha = 0.88;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1.5;
        ctx.stroke();
        this._stationPx.push({ ...st, px: p.x, py: p.y, r: 10 });
      });

      if (this.hover) {
        const h = this.hover;
        const lines = [h.name || h.station_id, `RMSE: ${h.rmse?.toFixed?.(3) ?? '—'}`];
        ctx.font = '12px Segoe UI, system-ui, sans-serif';
        const tw = Math.max(...lines.map(l => ctx.measureText(l).width)) + 14;
        const th = lines.length * 16 + 8;
        let tx = h.px + 12;
        let ty = h.py - th - 6;
        if (tx + tw > this.w) tx = h.px - tw - 8;
        ctx.fillStyle = 'rgba(30,41,59,0.92)';
        ctx.fillRect(tx, ty, tw, th);
        ctx.fillStyle = '#fff';
        lines.forEach((l, i) => ctx.fillText(l, tx + 7, ty + 16 + i * 16));
      }
    }

    _pick(sx, sy) {
      for (let i = this._stationPx.length - 1; i >= 0; i--) {
        const s = this._stationPx[i];
        if (Math.hypot(sx - s.px, sy - s.py) <= s.r) return s;
      }
      return null;
    }

    _onDown(e) {
      this.drag = { x: e.clientX, y: e.clientY, clat: this.center.lat, clon: this.center.lon };
      this.canvas.style.cursor = 'grabbing';
    }

    _onMove(e) {
      const r = this.canvas.getBoundingClientRect();
      const sx = e.clientX - r.left;
      const sy = e.clientY - r.top;
      if (this.drag) {
        const dx = e.clientX - this.drag.x;
        const dy = e.clientY - this.drag.y;
        const scale = TILE * 2 ** this.zoom;
        const dlon = (-dx / scale) * 360;
        const clatRad = (this.drag.clat * Math.PI) / 180;
        const dlat = (dy / scale) * 360 * Math.cos(clatRad);
        this.center.lat = clamp(this.drag.clat + dlat, -85, 85);
        this.center.lon = this.drag.clon + dlon;
        this.draw();
        return;
      }
      const hit = this._pick(sx, sy);
      this.hover = hit;
      this.canvas.style.cursor = hit ? 'pointer' : 'grab';
      this.draw();
    }

    _onWheel(e) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? -1 : 1;
      this.zoom = clamp(this.zoom + delta, 3, 12);
      this.draw();
    }

    _onClick(e) {
      const r = this.canvas.getBoundingClientRect();
      const hit = this._pick(e.clientX - r.left, e.clientY - r.top);
      if (hit && this.onStationClick) this.onStationClick(hit);
    }
  }

  global.StationCanvasMap = StationCanvasMap;
})(window);
