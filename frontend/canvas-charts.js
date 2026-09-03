/** Lightweight Canvas 2D charts — PSIIDN-style, no Plotly dependency. */
(function (global) {
  const FONT = 'Segoe UI, system-ui, sans-serif';
  const GRID = '#e2e8f0';
  const TEXT = '#334155';
  const MUTED = '#64748b';

  function niceTicks(min, max, count = 5) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0];
    if (min === max) return [min];
    const range = niceNum(max - min, false);
    const step = niceNum(range / (count - 1), true);
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step * 0.5; v += step) ticks.push(v);
    return ticks;
  }

  function niceNum(x, round) {
    const exp = Math.floor(Math.log10(Math.abs(x) || 1));
    const f = x / 10 ** exp;
    let nf;
    if (round) nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
    else nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return nf * 10 ** exp;
  }

  function hexAlpha(color, a) {
    if (!color || a >= 0.999) return color;
    if (color.startsWith('#') && (color.length === 7 || color.length === 4)) {
      let r; let g; let b;
      if (color.length === 4) {
        r = parseInt(color[1] + color[1], 16);
        g = parseInt(color[2] + color[2], 16);
        b = parseInt(color[3] + color[3], 16);
      } else {
        r = parseInt(color.slice(1, 3), 16);
        g = parseInt(color.slice(3, 5), 16);
        b = parseInt(color.slice(5, 7), 16);
      }
      return `rgba(${r},${g},${b},${a})`;
    }
    return color;
  }

  class MonasChart {
    constructor(container, opts = {}) {
      this.el = typeof container === 'string' ? document.getElementById(container) : container;
      this.canvas = document.createElement('canvas');
      this.canvas.style.display = 'block';
      this.canvas.style.width = '100%';
      this.canvas.style.height = '100%';
      this.el.innerHTML = '';
      this.el.appendChild(this.canvas);
      this.ctx = this.canvas.getContext('2d');
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.pad = { top: 44, right: 16, bottom: 48, left: 56 };
      this.title = opts.title || '';
      this.yLabel = '';
      this.xLabel = '';
      this.mode = 'bar';
      this.bar = null;
      this.lines = null;
      this.xNumeric = false;
      this.xTime = false;
      this.highlightX = null;
      this.hover = null;
      this.onClick = opts.onClick || null;
      this.zoomable = !!opts.zoomable;
      this._meta = [];
      this._xFull = null;
      this._xView = null;
      this._drag = null;

      this.canvas.addEventListener('mousemove', e => this._onMove(e));
      this.canvas.addEventListener('mouseleave', () => {
        this.hover = null;
        this._drag = null;
        this.draw();
      });
      this.canvas.addEventListener('click', e => this._onClick(e));
      this.canvas.addEventListener('mousedown', e => this._onDown(e));
      window.addEventListener('mouseup', () => { this._drag = null; });
      this.canvas.addEventListener('wheel', e => this._onWheel(e), { passive: false });
      this.canvas.addEventListener('dblclick', () => { if (this.zoomable) this.resetZoom(); });

      this._ro = new ResizeObserver(() => this._resize());
      this._ro.observe(this.el);
      this._resize();
    }

    destroy() {
      this._ro.disconnect();
    }

    _resize() {
      const w = this.el.clientWidth || 400;
      const h = this.el.clientHeight || 300;
      this.w = w;
      this.h = h;
      this.canvas.width = Math.round(w * this.dpr);
      this.canvas.height = Math.round(h * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.draw();
    }

    setBar({ labels, values, colors, title, yLabel }) {
      this.mode = 'bar';
      this.title = title || this.title;
      this.yLabel = yLabel || '';
      this.bar = { labels, values, colors: colors || values.map((_, i) => ['#00529B', '#64748b', '#94a3b8', '#cbd5e1'][i % 4]) };
      this.lines = null;
      this._xFull = null;
      this._xView = null;
      requestAnimationFrame(() => this.draw());
    }

    setLines({ series, title, xLabel, yLabel, xNumeric = false, highlightX = null, xTime = false, scatterOnly = false, keepZoom = false }) {
      this.mode = 'line';
      this.title = title || this.title;
      this.xLabel = xLabel || '';
      this.yLabel = yLabel || '';
      this.xNumeric = xNumeric;
      this.xTime = xTime;
      this.highlightX = highlightX;
      this.scatterOnly = scatterOnly;
      this.lines = series;
      this.bar = null;
      const xs = [];
      (series || []).forEach(s => (s.x || []).forEach(x => {
        const v = xNumeric ? +x : null;
        if (v != null && Number.isFinite(v)) xs.push(v);
      }));
      if (xs.length) {
        const lo = Math.min(...xs);
        const hi = Math.max(...xs);
        this._xFull = { min: lo, max: hi === lo ? lo + 1 : hi };
        if (!keepZoom || !this._xView) this._xView = { ...this._xFull };
        else {
          this._xView.min = Math.max(this._xFull.min, Math.min(this._xView.min, this._xFull.max));
          this._xView.max = Math.min(this._xFull.max, Math.max(this._xView.max, this._xFull.min));
          if (this._xView.max <= this._xView.min) this._xView = { ...this._xFull };
        }
      } else {
        this._xFull = null;
        this._xView = null;
      }
      requestAnimationFrame(() => this.draw());
    }

    redraw() {
      this._resize();
    }

    resetZoom() {
      if (this._xFull) this._xView = { ...this._xFull };
      this.draw();
    }

    zoomBy(factor, anchorX = null) {
      if (!this.zoomable || !this._xView || !this._xFull) return;
      const plot = this._plot();
      const { min, max } = this._xView;
      const span = max - min;
      const mid = anchorX != null ? anchorX : (min + max) / 2;
      let newSpan = span * factor;
      const fullSpan = this._xFull.max - this._xFull.min;
      newSpan = Math.max(fullSpan * 0.02, Math.min(fullSpan, newSpan));
      let nmin = mid - (mid - min) / span * newSpan;
      let nmax = nmin + newSpan;
      if (nmin < this._xFull.min) { nmin = this._xFull.min; nmax = nmin + newSpan; }
      if (nmax > this._xFull.max) { nmax = this._xFull.max; nmin = nmax - newSpan; }
      this._xView = { min: nmin, max: nmax };
      this.draw();
    }

    _plot() {
      const p = this.pad;
      return {
        x0: p.left,
        y0: p.top,
        w: this.w - p.left - p.right,
        h: this.h - p.top - p.bottom,
      };
    }

    draw() {
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.w, this.h);
      ctx.fillStyle = '#f8fafc';
      ctx.fillRect(0, 0, this.w, this.h);

      if (this.title) {
        ctx.fillStyle = TEXT;
        ctx.font = `600 13px ${FONT}`;
        ctx.textAlign = 'left';
        ctx.fillText(this.title, this.pad.left, 22);
      }

      if (this.mode === 'bar' && this.bar) this._drawBar();
      else if (this.mode === 'line' && this.lines) this._drawLines();

      if (this.hover) this._drawTooltip(this.hover);
    }

    _drawGrid(x0, y0, w, h, yTicks, mapY) {
      const ctx = this.ctx;
      ctx.strokeStyle = GRID;
      ctx.lineWidth = 1;
      ctx.fillStyle = MUTED;
      ctx.font = `11px ${FONT}`;
      ctx.textAlign = 'right';
      yTicks.forEach(t => {
        const y = mapY(t);
        ctx.beginPath();
        ctx.moveTo(x0, y);
        ctx.lineTo(x0 + w, y);
        ctx.stroke();
        ctx.fillText(Number.isInteger(t) ? t : t.toFixed(2), x0 - 6, y + 4);
      });
    }

    _drawBar() {
      const ctx = this.ctx;
      const { labels, values, colors } = this.bar;
      const plot = this._plot();
      const minY = 0;
      const maxY = Math.max(...values, 0.01) * 1.08;
      const yTicks = niceTicks(minY, maxY);
      const mapY = v => plot.y0 + plot.h - ((v - minY) / (maxY - minY)) * plot.h;

      this._meta = [];
      this._drawGrid(plot.x0, plot.y0, plot.w, plot.h, yTicks, mapY);

      const n = labels.length;
      const gap = plot.w * 0.08;
      const barW = (plot.w - gap * (n + 1)) / n;

      labels.forEach((lab, i) => {
        const v = values[i];
        const x = plot.x0 + gap + i * (barW + gap);
        const y = mapY(v);
        const bh = plot.y0 + plot.h - y;
        ctx.fillStyle = colors[i];
        ctx.fillRect(x, y, barW, bh);
        ctx.fillStyle = TEXT;
        ctx.font = `11px ${FONT}`;
        ctx.textAlign = 'center';
        ctx.fillText(lab, x + barW / 2, plot.y0 + plot.h + 18);
        ctx.font = `600 10px ${FONT}`;
        ctx.fillText(v.toFixed(3), x + barW / 2, y - 4);
        this._meta.push({ type: 'bar', i, x, y, w: barW, h: bh, label: lab, value: v });
      });

      if (this.yLabel) {
        ctx.save();
        ctx.translate(14, plot.y0 + plot.h / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.fillStyle = MUTED;
        ctx.font = `11px ${FONT}`;
        ctx.textAlign = 'center';
        ctx.fillText(this.yLabel, 0, 0);
        ctx.restore();
      }
    }

    _drawMarker(ctx, x, y, style, r) {
      ctx.beginPath();
      if (style === 'square') {
        ctx.rect(x - r, y - r, r * 2, r * 2);
      } else if (style === 'diamond') {
        ctx.moveTo(x, y - r);
        ctx.lineTo(x + r, y);
        ctx.lineTo(x, y + r);
        ctx.lineTo(x - r, y);
        ctx.closePath();
      } else if (style === 'triangle') {
        ctx.moveTo(x, y - r);
        ctx.lineTo(x + r, y + r);
        ctx.lineTo(x - r, y + r);
        ctx.closePath();
      } else {
        ctx.arc(x, y, r, 0, Math.PI * 2);
      }
      ctx.fill();
    }

    _drawLines() {
      const ctx = this.ctx;
      const plot = this._plot();
      if (!this.lines?.length) return;
      const allY = [];
      const allX = [];
      this.lines.forEach(s => {
        s.x.forEach(x => allX.push(this.xNumeric ? +x : 0));
        s.y.forEach(y => { if (y != null && !Number.isNaN(y)) allY.push(y); });
      });
      if (!allY.length) return;

      let minX; let maxX; let mapX;
      if (this.xNumeric) {
        minX = this._xView?.min ?? Math.min(...allX);
        maxX = this._xView?.max ?? Math.max(...allX);
        if (minX === maxX) maxX = minX + 1;
        mapX = v => plot.x0 + ((v - minX) / (maxX - minX)) * plot.w;
      } else {
        const cats = [...new Set(this.lines.flatMap(s => s.x))].sort();
        minX = 0;
        maxX = Math.max(cats.length - 1, 1);
        const idx = new Map(cats.map((c, i) => [c, i]));
        mapX = v => {
          const i = idx.get(v) ?? 0;
          return plot.x0 + (i / Math.max(maxX, 1)) * plot.w;
        };
      }

      const minY = Math.min(...allY);
      const maxY = Math.max(...allY);
      const yTicks = niceTicks(minY, maxY);
      const mapY = v => plot.y0 + plot.h - ((v - minY) / (maxY - minY || 1)) * plot.h;

      this._meta = [];
      this._drawGrid(plot.x0, plot.y0, plot.w, plot.h, yTicks, mapY);

      if (this.highlightX != null && this.xNumeric) {
        const hx = mapX(+this.highlightX);
        ctx.save();
        ctx.strokeStyle = '#00529B';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        ctx.moveTo(hx, plot.y0);
        ctx.lineTo(hx, plot.y0 + plot.h);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();
      }

      this.lines.forEach(s => {
        const alpha = s.alpha != null ? s.alpha : 1;
        const col = hexAlpha(s.color, alpha);
        ctx.strokeStyle = col;
        ctx.fillStyle = col;
        ctx.lineWidth = s.width || 2;
        const dotsOnly = this.scatterOnly || s.dotsOnly;
        if (!dotsOnly) {
          ctx.setLineDash(s.dash || []);
          let started = false;
          ctx.beginPath();
          for (let i = 0; i < s.x.length; i++) {
            const y = s.y[i];
            if (y == null || Number.isNaN(y)) { started = false; continue; }
            const xv = this.xNumeric ? +s.x[i] : s.x[i];
            if (this.xNumeric && this._xView && (xv < this._xView.min || xv > this._xView.max)) {
              started = false;
              continue;
            }
            const x = this.xNumeric ? mapX(xv) : mapX(xv);
            const py = mapY(y);
            if (!started) { ctx.moveTo(x, py); started = true; }
            else ctx.lineTo(x, py);
          }
          ctx.stroke();
          ctx.setLineDash([]);
        }
        for (let i = 0; i < s.x.length; i++) {
          const y = s.y[i];
          if (y == null || Number.isNaN(y)) continue;
          const xv = this.xNumeric ? +s.x[i] : s.x[i];
          if (this.xNumeric && this._xView && (xv < this._xView.min || xv > this._xView.max)) continue;
          const x = this.xNumeric ? mapX(xv) : mapX(xv);
          const py = mapY(y);
          this._meta.push({
            type: 'pt', series: s.name, x: s.x[i], y, px: x, py, color: col,
            extra: s.extra?.[i],
          });
          const drawDot = dotsOnly || s.markers
            || (s.y.filter(v => v != null && !Number.isNaN(v)).length <= 200);
          if (drawDot) {
            this._drawMarker(ctx, x, py, s.marker || 'circle', dotsOnly ? 2 : (this.xTime ? 2.8 : 4));
          }
        }
      });

      if (this.xNumeric) {
        ctx.fillStyle = MUTED;
        ctx.font = `11px ${FONT}`;
        ctx.textAlign = 'center';
        const xt = niceTicks(minX, maxX, 8);
        xt.forEach(t => {
          let label;
          if (this.xTime) {
            try {
              label = new Date(t).toLocaleString('id-ID', {
                timeZone: 'Asia/Jakarta', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
              });
            } catch { label = String(Math.round(t)); }
          } else {
            label = String(Math.round(t));
          }
          ctx.fillText(label, mapX(t), plot.y0 + plot.h + 16);
        });
      }

      if (this.yLabel) {
        ctx.save();
        ctx.translate(14, plot.y0 + plot.h / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.fillStyle = MUTED;
        ctx.font = `11px ${FONT}`;
        ctx.textAlign = 'center';
        ctx.fillText(this.yLabel, 0, 0);
        ctx.restore();
      }
      if (this.xLabel) {
        ctx.fillStyle = MUTED;
        ctx.font = `11px ${FONT}`;
        ctx.textAlign = 'center';
        ctx.fillText(this.xLabel, plot.x0 + plot.w / 2, this.h - 8);
      }

      // Legend: dedupe by legendName (default = name) — satu entri per model
      let lx = plot.x0;
      const ly = this.title ? 38 : 12;
      const seen = new Set();
      this.lines.forEach(s => {
        const key = s.legendName || s.name;
        if (seen.has(key)) return;
        seen.add(key);
        ctx.fillStyle = s.color;
        ctx.fillRect(lx, ly, 14, 3);
        ctx.fillStyle = TEXT;
        ctx.font = `11px ${FONT}`;
        ctx.textAlign = 'left';
        ctx.fillText(key, lx + 18, ly + 4);
        lx += ctx.measureText(key).width + 40;
      });
    }

    _pick(mx, my) {
      let best = null;
      let bestD = 14;
      for (const m of this._meta) {
        if (m.type === 'bar') {
          if (mx >= m.x && mx <= m.x + m.w && my >= m.y && my <= m.y + m.h) best = m;
        } else if (m.type === 'pt') {
          const d = Math.hypot(mx - m.px, my - m.py);
          if (d < bestD) { bestD = d; best = m; }
        }
      }
      return best;
    }

    _clientToPlotX(clientX) {
      const r = this.canvas.getBoundingClientRect();
      const mx = clientX - r.left;
      const plot = this._plot();
      if (!this._xView) return null;
      const t = (mx - plot.x0) / plot.w;
      return this._xView.min + t * (this._xView.max - this._xView.min);
    }

    _onWheel(e) {
      if (!this.zoomable || this.mode !== 'line' || !this.xNumeric) return;
      e.preventDefault();
      const anchor = this._clientToPlotX(e.clientX);
      const factor = e.deltaY > 0 ? 1.2 : 0.8;
      this.zoomBy(factor, anchor);
    }

    _onDown(e) {
      if (!this.zoomable || this.mode !== 'line' || !this.xNumeric || !this._xView) return;
      if (e.button !== 0) return;
      const r = this.canvas.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      const plot = this._plot();
      if (mx < plot.x0 || mx > plot.x0 + plot.w || my < plot.y0 || my > plot.y0 + plot.h) return;
      this._drag = {
        x: e.clientX,
        view: { ...this._xView },
        span: this._xView.max - this._xView.min,
      };
    }

    _onMove(e) {
      const r = this.canvas.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;

      if (this._drag && this._xView && this._xFull) {
        const dx = e.clientX - this._drag.x;
        const plot = this._plot();
        const shift = -(dx / plot.w) * this._drag.span;
        let nmin = this._drag.view.min + shift;
        let nmax = this._drag.view.max + shift;
        const span = nmax - nmin;
        if (nmin < this._xFull.min) { nmin = this._xFull.min; nmax = nmin + span; }
        if (nmax > this._xFull.max) { nmax = this._xFull.max; nmin = nmax - span; }
        this._xView = { min: nmin, max: nmax };
        this.canvas.style.cursor = 'grabbing';
        this.draw();
        return;
      }

      const hit = this._pick(mx, my);
      this.hover = hit ? { mx, my, hit } : null;
      this.canvas.style.cursor = hit ? 'pointer' : (this.zoomable ? 'crosshair' : 'default');
      this.draw();
    }

    _onClick(e) {
      if (!this.onClick) return;
      if (this._drag) return;
      const r = this.canvas.getBoundingClientRect();
      const hit = this._pick(e.clientX - r.left, e.clientY - r.top);
      if (hit) this.onClick(hit);
    }

    _drawTooltip(h) {
      const ctx = this.ctx;
      const hit = h.hit;
      let lines;
      if (hit.type === 'bar') {
        lines = [`${hit.label}`, `RMSE: ${hit.value.toFixed(4)}`];
      } else {
        let xLabel = hit.x;
        if (this.xTime && typeof hit.x === 'number') {
          try {
            xLabel = new Date(hit.x).toLocaleString('id-ID', {
              timeZone: 'Asia/Jakarta', day: '2-digit', month: 'short',
              hour: '2-digit', minute: '2-digit', hour12: false,
            }) + ' WIB';
          } catch { /* keep raw */ }
        }
        lines = [hit.series, `x: ${xLabel}`, `y: ${hit.y?.toFixed?.(4) ?? hit.y}`];
      }
      ctx.font = `12px ${FONT}`;
      const tw = Math.max(...lines.map(l => ctx.measureText(l).width)) + 16;
      const th = lines.length * 16 + 10;
      let tx = h.mx + 12;
      let ty = h.my - th - 8;
      if (tx + tw > this.w) tx = h.mx - tw - 12;
      if (ty < 4) ty = h.my + 12;
      ctx.fillStyle = 'rgba(30,41,59,0.92)';
      ctx.fillRect(tx, ty, tw, th);
      ctx.fillStyle = '#fff';
      ctx.textAlign = 'left';
      lines.forEach((l, i) => ctx.fillText(l, tx + 8, ty + 18 + i * 16));
    }
  }

  global.MonasChart = MonasChart;
})(window);
