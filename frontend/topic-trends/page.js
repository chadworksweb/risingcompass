/* === Topic Trends — unified explorer panel ===
 *
 * One chart area driven by three sibling filters (pure SVG + vanilla JS, no
 * chart library):
 *
 *   - Chart type : Stream (the Topic River, a 100% stacked-area "carving") or
 *                  Point (the Narrowing Index, the effective-topic-count line).
 *   - Period     : Trailing 365 days (12 monthly buckets, /api/topic-trends/
 *                  trailing) or Historical (per-year, /api/topic-trends) with a
 *                  span/segment control.
 *   - Group      : Themes (the 9 primary themes) or all 30 topics.
 *
 * The three combine freely. Colours are a CATEGORICAL palette (taxonomy / theme
 * order), NOT the 5 Compass tier colours — topics are a different axis.
 */
(() => {
  'use strict';

  const RIVER_TOP_N = 12;   // distinct topic bands in stream mode; rest -> "other"

  // ---- State ---------------------------------------------------------------
  const STATE = { chart: 'stream', period: 'trailing', mode: 'themes', line: 'index' };

  let YEARLY = null;        // /api/topic-trends payload
  let TRAIL = null;         // /api/topic-trends/trailing payload

  // Historical zoom window (year values) + Time Machine, mirroring the homepage
  // trajectory toolkit: a draggable brace over a mini-overview sets the window;
  // the Time Machine scrubs a clip across the windowed chart.
  let ALLYEARS = [];        // historical years that have data, sorted
  let zoomLo = null, zoomHi = null;
  let TMGEO = null;         // geometry of the last historical render, for TM clip
  let tmPos = 0, tmPlaying = false, tmDir = 1, tmAnim = null, tmSpeedIdx = 1;
  const TM_SPEEDS = [0.5, 1, 2, 4], TM_BASE = 1.6;
  let TOPIC_MAP = {};       // slug -> {primary, also:[]}
  let THEME_LABEL = {};     // theme slug -> label
  let THEME_ORDER = [];     // theme slugs in canonical order
  let BANDCOLOR = {};       // key -> hsl, assigned per render from the stack order
  let TAX_N = 30;           // taxonomy size (for the topic-mode index scale)
  let BASIS_N = 20;         // fixed per-year basis; overwritten from the payload

  const MONTHS = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function labelize(slug) { return String(slug).replace(/-/g, ' '); }

  // ---- Colours -------------------------------------------------------------
  // Riff on the two Rising Compass baselines -- teal (~170deg) and blue (~214deg).
  // Walk the stack from teal to blue with a 3-step lightness cycle so NO two
  // bands repeat and adjacent bands stay clearly distinct on the dark field.
  function shade(i, n) {
    const t = n > 1 ? i / (n - 1) : 0;
    const hue = Math.round(170 + 44 * t);   // 170 teal -> 214 blue
    const light = 40 + (i % 3) * 13;        // 40 / 53 / 66 cycle
    const sat = 70 - Math.round(20 * t);    // 70 -> 50
    return `hsl(${hue}, ${sat}%, ${light}%)`;
  }
  // Assign each band a distinct colour for THIS render, from its stack order.
  function assignBandColors(keys) {
    BANDCOLOR = {};
    const n = keys.length;
    keys.forEach((k, i) => { BANDCOLOR[k] = k === 'other' ? '#5a5a72' : shade(i, n); });
  }
  function colorForKey(key) {
    if (key === 'other') return '#5a5a72';
    return BANDCOLOR[key] || 'hsl(190, 60%, 52%)';
  }
  function labelForKey(key) {
    if (key === 'other') return 'other';
    return STATE.mode === 'themes' ? (THEME_LABEL[key] || key) : labelize(key);
  }

  // ---- Diversity math ------------------------------------------------------
  function effectiveCount(counts) {
    const total = counts.reduce((a, b) => a + b, 0);
    if (total <= 0) return 0;
    let h = 0;
    for (const c of counts) { if (c > 0) { const p = c / total; h -= p * Math.log2(p); } }
    return Math.pow(2, h);
  }
  function maxEffective() { return STATE.mode === 'themes' ? THEME_ORDER.length : TAX_N; }

  // Recalibration Steps 3+4: in Point mode the Index reads the server's
  // dominant-basis measures (first-listed topic, one vote per song), which
  // removes the tags-per-song drift confound. Themes grouping (the default)
  // reads the dominant topic rolled to its primary theme -- the altitude
  // immune to the romance shelf holding 7 of 31 slugs. Stream is untouched.
  function dominantBasis() { return STATE.chart === 'point'; }
  function dominantOf(col) {
    // Step 5: historical years read the FIXED-BASIS measures (top BASIS_N
    // songs by prominence), so no year out-votes another on sample size.
    // Trailing months carry no basis fields and fall through to the
    // whole-set dominant measures.
    if (STATE.period === 'historical') {
      const vb = STATE.mode === 'themes'
        ? col.effective_themes_dominant_basis
        : col.effective_topics_dominant_basis;
      if (Number.isFinite(vb)) return vb;
    }
    const v = STATE.mode === 'themes'
      ? col.effective_themes_dominant
      : col.effective_topics_dominant;
    return Number.isFinite(v) ? v : null;
  }
  // Axis ceiling for the dominant basis. Themes: the full 9-theme scale
  // (stable, conceptually "out of 9"). Topics: a stable nice ceiling over the
  // FULL active series (not the zoom window), so zooming never rescales.
  function dominantCeiling() {
    if (STATE.mode === 'themes') return THEME_ORDER.length || 9;
    const src = STATE.period === 'historical' ? ALLYEARS : (TRAIL.periods || []);
    let m = 1;
    src.forEach((c) => { const v = dominantOf(c); if (v != null && v > m) m = v; });
    return Math.max(5, Math.ceil(m / 5) * 5);
  }
  // Recalibration Step 7: the Romance-share line -- percent of songs whose
  // DOMINANT topic files on the romance shelf. Basis-disciplined like the
  // Index (historical years read the top-BASIS_N share).
  function shareView() { return STATE.chart === 'point' && STATE.line === 'romance'; }
  function shareOf(col) {
    if (STATE.period === 'historical' && Number.isFinite(col.romance_share_dominant_basis)) {
      return col.romance_share_dominant_basis;
    }
    return Number.isFinite(col.romance_share_dominant) ? col.romance_share_dominant : null;
  }

  // Distinct-unit count on the dominant basis (for the tooltip): themes mode
  // rolls the dominant-topic distribution to primary themes client-side.
  function dominantDistinct(col) {
    const raw = (STATE.period === 'historical' && col.distribution_dominant_basis)
      ? col.distribution_dominant_basis
      : (col.distribution_dominant || []);
    const dist = raw.filter((d) => d.count > 0);
    if (STATE.mode !== 'themes') return dist.length;
    const seen = new Set();
    dist.forEach((d) => {
      const th = TOPIC_MAP[d.topic] && TOPIC_MAP[d.topic].primary;
      if (th) seen.add(th);
    });
    return seen.size;
  }
  function unitNoun(n) {
    const base = STATE.mode === 'themes' ? 'theme' : 'topic';
    return n === 1 ? base : base + 's';
  }

  // Fractional roll-up for the river (recalibration Step 6): each song is 1.0
  // of mass, split 1/k across its tags, so band shares sum honestly per song.
  // Falls back to the all-pairs distribution when the field is absent.
  function rollupColFrac(col) {
    const frac = col.distribution_fractional;
    const src = (Array.isArray(frac) && frac.length)
      ? frac.map((d) => ({ topic: d.topic, count: d.weight }))
      : (col.distribution || []);
    if (STATE.mode === 'topics') {
      const items = src.map((d) => ({ key: d.topic, count: d.count }));
      return { items, total: items.reduce((s, i) => s + i.count, 0) };
    }
    const acc = {};
    src.forEach((d) => {
      const theme = (TOPIC_MAP[d.topic] && TOPIC_MAP[d.topic].primary) || 'other';
      acc[theme] = (acc[theme] || 0) + d.count;
    });
    const items = Object.keys(acc).map((k) => ({ key: k, count: acc[k] }));
    return { items, total: items.reduce((s, i) => s + i.count, 0) };
  }

  // Roll one column's topic distribution into the current group's buckets.
  function rollupCol(col) {
    if (STATE.mode === 'topics') {
      return {
        items: col.distribution.map((d) => ({ key: d.topic, count: d.count })),
        total: col.total_pairs,
      };
    }
    const acc = {};
    col.distribution.forEach((d) => {
      const theme = (TOPIC_MAP[d.topic] && TOPIC_MAP[d.topic].primary) || 'other';
      acc[theme] = (acc[theme] || 0) + d.count;
    });
    const items = Object.keys(acc).map((k) => ({ key: k, count: acc[k] }));
    return { items, total: items.reduce((s, i) => s + i.count, 0) };
  }

  // ---- Path builders -------------------------------------------------------
  function straightPath(points) {
    if (!points.length) return '';
    let d = `M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`;
    for (let i = 1; i < points.length; i++) d += ` L ${points[i].x.toFixed(2)} ${points[i].y.toFixed(2)}`;
    return d;
  }
  // Per-segment Catmull-Rom beziers (same control points as smoothPath), so
  // below-basis segments can carry their own dashed styling without changing
  // the curve's shape.
  function smoothSegments(points) {
    const segs = [];
    for (let i = 0; i < points.length - 1; i++) {
      const p0 = points[i - 1] || points[i];
      const p1 = points[i];
      const p2 = points[i + 1];
      const p3 = points[i + 2] || p2;
      const cp1x = p1.x + (p2.x - p0.x) / 6, cp1y = p1.y + (p2.y - p0.y) / 6;
      const cp2x = p2.x - (p3.x - p1.x) / 6, cp2y = p2.y - (p3.y - p1.y) / 6;
      segs.push(`M ${p1.x.toFixed(2)} ${p1.y.toFixed(2)} C ${cp1x.toFixed(2)} ${cp1y.toFixed(2)}, ${cp2x.toFixed(2)} ${cp2y.toFixed(2)}, ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`);
    }
    return segs;
  }

  // Catmull-Rom -> cubic bezier (used by the Point/Index line only).
  function smoothPath(points) {
    if (!points.length) return '';
    if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
    if (points.length === 2) return `M ${points[0].x} ${points[0].y} L ${points[1].x} ${points[1].y}`;
    let d = `M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`;
    for (let i = 0; i < points.length - 1; i++) {
      const p0 = points[i - 1] || points[i];
      const p1 = points[i];
      const p2 = points[i + 1];
      const p3 = points[i + 2] || p2;
      const cp1x = p1.x + (p2.x - p0.x) / 6, cp1y = p1.y + (p2.y - p0.y) / 6;
      const cp2x = p2.x - (p3.x - p1.x) / 6, cp2y = p2.y - (p3.y - p1.y) / 6;
      d += ` C ${cp1x.toFixed(2)} ${cp1y.toFixed(2)}, ${cp2x.toFixed(2)} ${cp2y.toFixed(2)}, ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`;
    }
    return d;
  }

  // Position the rotated (vertical) year/period label that rides the hover
  // crosshair. It sits in the half OPPOSITE the cursor -- cursor in the top half
  // -> label at the floor reading upward; cursor in the bottom half -> label at
  // the ceiling reading downward -- so it never sits under the pointer. Flips to
  // the left of the line near the right edge so it never clips the right padding.
  function placeHoverYear(el, label, lineX, padT, chartH, cursorSvgY, W, padR) {
    el.textContent = label;
    const x = lineX > W - padR - 22 ? lineX - 5 : lineX + 5;
    const cursorInTopHalf = cursorSvgY < padT + chartH / 2;
    const y = cursorInTopHalf ? padT + chartH - 6 : padT + 6;
    const rot = cursorInTopHalf ? -90 : 90;   // read up from the floor / down from the ceiling
    el.setAttribute('x', x.toFixed(1));
    el.setAttribute('y', y.toFixed(1));
    el.setAttribute('text-anchor', 'start');
    el.setAttribute('transform', `rotate(${rot} ${x.toFixed(1)} ${y.toFixed(1)})`);
  }

  function xLabelYears(cols) {
    const lo = cols[0].year, hi = cols[cols.length - 1].year;
    const span = hi - lo;
    const step = span > 40 ? 10 : span > 15 ? 5 : span > 8 ? 2 : 1;
    const set = new Set([lo, hi]);
    for (let y = Math.ceil(lo / step) * step; y <= hi; y += step) set.add(y);
    return set;
  }

  // ---- Bin per-year rows into N-year buckets (stream historical) -----------
  function binYears(years, size) {
    const groups = new Map();
    years.forEach((y) => {
      const start = Math.floor(y.year / size) * size;
      let g = groups.get(start);
      if (!g) { g = { start, lo: y.year, hi: y.year, dist: {}, frac: {}, songs: 0, pairs: 0 }; groups.set(start, g); }
      g.lo = Math.min(g.lo, y.year); g.hi = Math.max(g.hi, y.year);
      g.songs += y.songs_with_topics || 0;
      g.pairs += y.total_pairs || 0;
      (y.distribution || []).forEach((d) => { g.dist[d.topic] = (g.dist[d.topic] || 0) + d.count; });
      (y.distribution_fractional || []).forEach((d) => { g.frac[d.topic] = (g.frac[d.topic] || 0) + d.weight; });
    });
    return Array.from(groups.values()).sort((a, b) => a.start - b.start).map((g) => ({
      year: g.start,
      label: g.lo === g.hi ? `${g.lo}` : `${g.lo}–${String(g.hi).slice(-2)}`,
      axisLabel: String(g.start),
      distribution: Object.keys(g.dist).map((t) => ({ topic: t, count: g.dist[t] })),
      distribution_fractional: Object.keys(g.frac).map((t) => ({ topic: t, weight: g.frac[t] })),
      total_pairs: g.pairs,
      songs_with_topics: g.songs,
    }));
  }

  // ---- Build the column set for the current (period, chart) ----------------
  // Returns { cols, unit } where unit is 'year' | 'month'. Empty columns are
  // dropped so a sparse trailing window shows only its populated buckets.
  function buildCols() {
    if (STATE.period === 'trailing') {
      const cols = (TRAIL.periods || [])
        .filter((p) => p.total_pairs > 0)
        .map((p) => ({
          year: parseInt(p.key.slice(0, 4), 10),
          label: p.label,
          axisLabel: `${MONTHS[parseInt(p.key.slice(5, 7), 10)]} '${p.key.slice(2, 4)}`,
          distribution: p.distribution,
          total_pairs: p.total_pairs,
          songs_with_topics: p.songs_with_topics,
          effective_topics_dominant: p.effective_topics_dominant,
          effective_themes_dominant: p.effective_themes_dominant,
          distribution_dominant: p.distribution_dominant,
          distribution_fractional: p.distribution_fractional,
          romance_share_dominant: p.romance_share_dominant,
        }));
      return { cols, unit: 'month' };
    }
    let years = ALLYEARS.filter((y) => y.year >= zoomLo && y.year <= zoomHi);
    if (!years.length) years = ALLYEARS.slice();
    if (STATE.chart === 'stream') {
      const bin = years.length > 30 ? 5 : years.length > 15 ? 2 : 1;
      return { cols: binYears(years, bin), unit: 'year' };
    }
    // Point historical: per-year, no binning (the index is a per-period measure).
    const cols = years.map((y) => ({
      year: y.year, label: String(y.year), axisLabel: String(y.year),
      distribution: y.distribution, total_pairs: y.total_pairs,
      songs_with_topics: y.songs_with_topics,
      effective_topics_dominant: y.effective_topics_dominant,
      effective_themes_dominant: y.effective_themes_dominant,
      distribution_dominant: y.distribution_dominant,
      effective_topics_dominant_basis: y.effective_topics_dominant_basis,
      effective_themes_dominant_basis: y.effective_themes_dominant_basis,
      distribution_dominant_basis: y.distribution_dominant_basis,
      n_available: y.n_available,
      below_basis: y.below_basis,
      romance_share_dominant: y.romance_share_dominant,
      romance_share_dominant_basis: y.romance_share_dominant_basis,
    }));
    return { cols, unit: 'year' };
  }

  function bandKeysForMode(cols) {
    if (STATE.mode === 'themes') return { keys: THEME_ORDER.slice(), hasOther: false };
    const totals = {};
    cols.forEach((c) => rollupColFrac(c).items.forEach((it) => { totals[it.key] = (totals[it.key] || 0) + it.count; }));
    const ranked = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
    return { keys: ranked.slice(0, RIVER_TOP_N), hasOther: ranked.length > RIVER_TOP_N };
  }

  function xAxisSvg(cols, xs, unit, W, H, padT, chartH, padL, axisTitle) {
    let svg = '';
    if (unit === 'year') {
      const labels = xLabelYears(cols);
      cols.forEach((c, i) => {
        if (labels.has(c.year)) svg += `<text class="tt-x-label" x="${xs[i].toFixed(1)}" y="${H - 12}" text-anchor="middle">${c.axisLabel}</text>`;
      });
    } else {
      const step = cols.length > 9 ? 2 : 1;
      cols.forEach((c, i) => {
        if (i % step === 0 || i === cols.length - 1) svg += `<text class="tt-x-label" x="${xs[i].toFixed(1)}" y="${H - 12}" text-anchor="middle">${c.axisLabel}</text>`;
      });
    }
    if (axisTitle) {
      const cy = padT + chartH / 2;
      svg += `<text class="tt-axis-title" transform="rotate(-90 14 ${cy.toFixed(1)})" x="14" y="${cy.toFixed(1)}" text-anchor="middle">${axisTitle}</text>`;
    }
    return svg;
  }

  // =========================================================================
  // STREAM (the Topic River — 100% stacked carving)
  // =========================================================================
  function renderStream(cols, unit) {
    const chartEl = document.getElementById('tt-chart');
    const legend = document.getElementById('tt-legend');
    const tooltip = document.getElementById('tt-tooltip');
    const wrap = document.getElementById('tt-wrap');

    const { keys, hasOther } = bandKeysForMode(cols);
    const bandKeys = hasOther ? keys.concat(['other']) : keys.slice();
    const keepSet = new Set(keys);

    const valuesByCol = cols.map((c) => {
      const row = {}; bandKeys.forEach((k) => (row[k] = 0));
      rollupColFrac(c).items.forEach((it) => {
        if (keepSet.has(it.key)) row[it.key] += it.count;
        else if (hasOther) row['other'] += it.count;
      });
      return row;
    });
    const colTotals = valuesByCol.map((r) => bandKeys.reduce((s, k) => s + r[k], 0));

    // Left gutter: label text occupies the first ~13%, then a clear gap before
    // the bands begin (padL) so the leader lines have room to point.
    const W = 960, H = 420, padL = Math.round(W * 0.20), padR = 16, padT = 18, padB = 34;
    const chartW = W - padL - padR, chartH = H - padT - padB, maxIdx = cols.length - 1;
    const xs = cols.map((_, i) => padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2));

    // Stable order: largest share carved at the top, "other" pinned to the floor.
    const totalFor = (k) => valuesByCol.reduce((s, r) => s + r[k], 0);
    const order = bandKeys.slice().sort((a, b) => {
      if (a === 'other') return 1;
      if (b === 'other') return -1;
      return totalFor(b) - totalFor(a);
    });
    assignBandColors(order);   // distinct teal->blue shade per band, no reuse

    // Each column normalized to 100%. No smoothing: a zero is a true zero.
    const bandEdges = {};
    order.forEach((k) => (bandEdges[k] = []));
    valuesByCol.forEach((row, ci) => {
      const total = colTotals[ci] || 1;
      let cursor = padT;
      order.forEach((k) => {
        const h = (row[k] / total) * chartH;
        bandEdges[k].push({ x: xs[ci], y0: cursor, y1: cursor + h });
        cursor += h;
      });
    });

    let svg = `<svg class="tt-river-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${STATE.mode === 'themes' ? 'Theme' : 'Topic'} prevalence over time">`;
    svg += `<defs><clipPath id="tt-clip"><rect id="tt-clip-rect" x="0" y="0" width="${W}" height="${H}"/></clipPath></defs>`;
    svg += xAxisSvg(cols, xs, unit, W, H, padT, chartH, padL, null);
    svg += '<g clip-path="url(#tt-clip)">';
    order.forEach((k) => {
      const edges = bandEdges[k];
      const top = edges.map((e) => ({ x: e.x, y: e.y0 }));
      const botRev = edges.map((e) => ({ x: e.x, y: e.y1 })).reverse();
      const d = `${straightPath(top)} ${straightPath(botRev).replace(/^M/, 'L')} Z`;
      svg += `<path class="tt-band" data-key="${escapeHtml(k)}" d="${d}" fill="${colorForKey(k)}"/>`;
    });
    svg += '</g>';
    svg += `<line id="tt-tm-marker" class="tt-tm-marker" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" style="display:none"/>`;
    svg += `<line id="tt-hover-line" class="tt-hover-line" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" style="display:none"/>`;
    svg += `<text id="tt-hover-year" class="tt-hover-year" style="display:none"></text>`;

    // ---- Left label gutter: each band named at its level, with a leader line
    // when bands are too thin / crowded to sit at their own center. (Replaces
    // the bottom legend; anchored to the band centers in the leftmost column.)
    {
      const LBL_X = Math.round(W * 0.13);   // right edge of the label text
      const ELBOW = padL - 8;               // where the leader turns toward the band
      const SP = 16;
      const top = padT + 6, bot = padT + chartH - 6;
      const items = order.map((k) => {
        const e = bandEdges[k][0];
        return { key: k, anchorY: (e.y0 + e.y1) / 2 };
      }).sort((a, b) => a.anchorY - b.anchorY);
      let prev = top - SP;
      items.forEach((it) => { it.labelY = Math.max(it.anchorY, prev + SP); prev = it.labelY; });
      if (items.length && items[items.length - 1].labelY > bot) {
        let next = bot + SP;
        for (let i = items.length - 1; i >= 0; i--) { items[i].labelY = Math.min(items[i].labelY, next - SP); next = items[i].labelY; }
      }
      items.forEach((it) => {
        it.labelY = Math.max(top, Math.min(bot, it.labelY));
        const col = colorForKey(it.key);
        // Always connect the label to its band: a run out from the text, then an
        // elbow down/up into the band's left-edge centre.
        svg += `<polyline class="tt-lbl-lead" points="${LBL_X + 6},${it.labelY.toFixed(1)} ${ELBOW},${it.labelY.toFixed(1)} ${padL},${it.anchorY.toFixed(1)}" fill="none" stroke="${col}"/>`;
        svg += `<circle class="tt-lbl-dot" cx="${padL}" cy="${it.anchorY.toFixed(1)}" r="2" fill="${col}"/>`;
        svg += `<text class="tt-lbl" data-key="${escapeHtml(it.key)}" x="${LBL_X}" y="${(it.labelY + 3.5).toFixed(1)}" text-anchor="end" fill="${col}">${escapeHtml(labelForKey(it.key))}</text>`;
      });
    }

    svg += '</svg>';
    chartEl.innerHTML = svg;
    TMGEO = { xs, maxIdx, padT, chartH, padL, W };

    if (legend) legend.hidden = true;

    const bands = Array.from(chartEl.querySelectorAll('.tt-band'));
    const labelEls = Array.from(chartEl.querySelectorAll('.tt-lbl'));
    function highlight(key) {
      bands.forEach((b) => b.classList.toggle('is-dim', key != null && b.getAttribute('data-key') !== key));
      labelEls.forEach((l) => l.classList.toggle('is-dim', key != null && l.getAttribute('data-key') !== key));
    }
    const hline = chartEl.querySelector('#tt-hover-line');
    const yearEl = chartEl.querySelector('#tt-hover-year');
    function colAt(clientX) {
      let ci = maxIdx;
      const svgEl = chartEl.querySelector('.tt-river-svg');
      if (svgEl) {
        const r = svgEl.getBoundingClientRect();
        const svgX = ((clientX - r.left) / r.width) * W;
        let best = Infinity;
        xs.forEach((x, i) => { const dx = Math.abs(x - svgX); if (dx < best) { best = dx; ci = i; } });
      }
      return ci;
    }
    function moveCrosshair(ci, clientY) {
      const x = xs[ci];
      if (hline) { hline.setAttribute('x1', x.toFixed(1)); hline.setAttribute('x2', x.toFixed(1)); hline.style.display = ''; }
      if (yearEl) {
        let svgY = padT + chartH;
        const svgEl = chartEl.querySelector('.tt-river-svg');
        if (svgEl) { const r = svgEl.getBoundingClientRect(); svgY = ((clientY - r.top) / r.height) * H; }
        placeHoverYear(yearEl, cols[ci].label, x, padT, chartH, svgY, W, padR);
        yearEl.style.display = '';
      }
    }
    function hideCrosshair() {
      if (hline) hline.style.display = 'none';
      if (yearEl) yearEl.style.display = 'none';
    }
    function showTip(key, clientX, clientY) {
      if (!tooltip) return;
      const ci = colAt(clientX);
      const row = valuesByCol[ci], total = colTotals[ci] || 1;
      const cnt = row[key] || 0, pct = (cnt / total) * 100, when = cols[ci].label;
      let extra = '';
      if (STATE.mode === 'topics' && TOPIC_MAP[key]) {
        const prim = THEME_LABEL[TOPIC_MAP[key].primary] || '';
        const also = (TOPIC_MAP[key].also || []).map((t) => THEME_LABEL[t] || t);
        extra = `<div class="tt-tt-theme">${escapeHtml(prim)}${also.length ? ' · also ' + escapeHtml(also.join(', ')) : ''}</div>`;
      }
      const fmtW = (v) => (Math.abs(v - Math.round(v)) < 0.001 ? String(Math.round(v)) : v.toFixed(1));
      tooltip.innerHTML =
        `<div class="tt-tt-head"><span class="tt-legend-swatch" style="background:${colorForKey(key)}"></span>${escapeHtml(labelForKey(key))}</div>`
        + extra
        + `<div class="tt-tt-sub">${pct.toFixed(1)}% of ${escapeHtml(when)} · ${fmtW(cnt)} of ${fmtW(total)} song${total === 1 ? '' : 's'}' weight</div>`;
      tooltip.hidden = false;
      const wr = wrap.getBoundingClientRect();
      const px = clientX - wr.left, py = clientY - wr.top;
      tooltip.style.left = px + 'px'; tooltip.style.top = py + 'px';
      tooltip.style.transform = px > wr.width * 0.7 ? 'translate(-100%, -120%)' : 'translate(12px, -120%)';
    }
    chartEl.onmousemove = (e) => {
      moveCrosshair(colAt(e.clientX), e.clientY);
      const k = e.target && e.target.getAttribute && e.target.getAttribute('data-key');
      if (k) { highlight(k); showTip(k, e.clientX, e.clientY); }
      else { highlight(null); if (tooltip) tooltip.hidden = true; }
    };
    chartEl.onmouseleave = () => { highlight(null); hideCrosshair(); if (tooltip) tooltip.hidden = true; };
  }

  // =========================================================================
  // POINT (the Narrowing Index — effective topic/theme count line)
  // =========================================================================
  function renderPoint(cols, unit) {
    const chartEl = document.getElementById('tt-chart');
    const legend = document.getElementById('tt-legend');
    const tooltip = document.getElementById('tt-tooltip');
    const wrap = document.getElementById('tt-wrap');
    legend.hidden = true;

    const W = 960, H = 420, padL = 46, padR = 22, padT = 24, padB = 40;
    const chartW = W - padL - padR, chartH = H - padT - padB, maxIdx = cols.length - 1;
    const isShare = shareView();
    const useDom = dominantBasis();
    const yMax = isShare ? 100 : (useDom ? dominantCeiling() : maxEffective());

    const pts = cols.map((c, i) => {
      let eff, distinct;
      if (isShare) {
        eff = (shareOf(c) || 0) * 100;
        distinct = dominantDistinct(c);
      } else if (useDom && dominantOf(c) != null) {
        eff = dominantOf(c);
        distinct = dominantDistinct(c);
      } else {
        const { items } = rollupCol(c);
        eff = effectiveCount(items.map((it) => it.count));
        distinct = items.filter((it) => it.count > 0).length;
      }
      const basisMode = useDom && STATE.period === 'historical'
        && Number.isFinite(c.effective_topics_dominant_basis);
      return {
        x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
        y: padT + (1 - Math.min(eff, yMax) / yMax) * chartH,
        label: c.label, axisLabel: c.axisLabel, year: c.year,
        eff, distinct,
        songs: basisMode ? c.n_available : c.songs_with_topics,
        basisMode, below: basisMode && !!c.below_basis,
      };
    });

    const linePath = smoothPath(pts);
    const areaPath = (maxIdx > 0)
      ? linePath + ` L ${pts[maxIdx].x.toFixed(2)} ${(padT + chartH).toFixed(2)} L ${pts[0].x.toFixed(2)} ${(padT + chartH).toFixed(2)} Z`
      : '';

    let svg = `<svg class="tt-index-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Effective number of ${unitNoun(2)} over time">`;
    svg += `<defs><linearGradient id="tt-index-area" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stop-color="#00d4aa" stop-opacity="0.28"/><stop offset="100%" stop-color="#00d4aa" stop-opacity="0"/>
      </linearGradient>
      <clipPath id="tt-clip"><rect id="tt-clip-rect" x="0" y="0" width="${W}" height="${H}"/></clipPath></defs>`;

    const gridStep = isShare ? 25 : (yMax <= 12 ? 1 : 5);
    for (let v = 0; v <= yMax; v += gridStep) {
      const y = padT + (1 - v / yMax) * chartH;
      svg += `<line class="tt-grid" x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}"/>`;
      svg += `<text class="tt-y-label" x="${padL - 8}" y="${(y + 3.5).toFixed(1)}" text-anchor="end">${v}${isShare ? '%' : ''}</text>`;
    }
    svg += xAxisSvg(cols, pts.map((p) => p.x), unit, W, H, padT, chartH, padL,
      isShare ? 'Romance share' : `Effective ${unitNoun(2)}`);

    svg += '<g clip-path="url(#tt-clip)">';
    if (areaPath) svg += `<path class="tt-index-area" d="${areaPath}" fill="url(#tt-index-area)"/>`;
    const anyBelow = pts.some((p) => p.below);
    if (anyBelow && pts.length > 1) {
      // Below-basis honesty: segments touching a short year render dashed.
      smoothSegments(pts).forEach((d, i) => {
        const dashed = pts[i].below || pts[i + 1].below;
        svg += `<path class="tt-index-line${dashed ? ' tt-index-line--below' : ''}" d="${d}" fill="none"/>`;
      });
    } else {
      svg += `<path class="tt-index-line" d="${linePath}" fill="none"/>`;
    }
    pts.forEach((p, i) => { svg += `<circle class="tt-index-dot${p.below ? ' tt-index-dot--below' : ''}" data-i="${i}" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4"/>`; });
    svg += '</g>';
    svg += `<line id="tt-tm-marker" class="tt-tm-marker" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" style="display:none"/>`;
    svg += `<line id="tt-index-hline" class="tt-hover-line" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" style="display:none"/>`;
    svg += `<text id="tt-hover-year" class="tt-hover-year" style="display:none"></text>`;
    svg += `<rect x="${padL}" y="${padT}" width="${chartW}" height="${chartH}" fill="transparent" class="tt-hover-area"/></svg>`;
    chartEl.innerHTML = svg;
    TMGEO = { xs: pts.map((p) => p.x), maxIdx, padT, chartH, padL, W };

    const lineNodes = chartEl.querySelectorAll('.tt-index-line');
    const lineNode = lineNodes.length === 1 ? lineNodes[0] : null;   // skip draw-in when segmented
    if (lineNode && lineNode.getTotalLength) {
      const len = lineNode.getTotalLength();
      lineNode.style.strokeDasharray = len;
      lineNode.style.strokeDashoffset = len;
      lineNode.style.transition = 'stroke-dashoffset 1.2s cubic-bezier(0.34, 1.56, 0.64, 1)';
      requestAnimationFrame(() => requestAnimationFrame(() => { lineNode.style.strokeDashoffset = '0'; }));
    }

    const svgEl = chartEl.querySelector('.tt-index-svg');
    const hline = chartEl.querySelector('#tt-index-hline');
    const yearEl = chartEl.querySelector('#tt-hover-year');
    function showAt(clientX, clientY) {
      const rect = svgEl.getBoundingClientRect();
      const svgX = ((clientX - rect.left) / rect.width) * W;
      const svgY = ((clientY - rect.top) / rect.height) * H;
      let near = 0, best = Infinity;
      pts.forEach((p, i) => { const dx = Math.abs(p.x - svgX); if (dx < best) { best = dx; near = i; } });
      const p = pts[near];
      hline.setAttribute('x1', p.x.toFixed(1)); hline.setAttribute('x2', p.x.toFixed(1)); hline.style.display = '';
      if (yearEl) { placeHoverYear(yearEl, p.label, p.x, padT, chartH, svgY, W, padR); yearEl.style.display = ''; }
      if (tooltip) {
        const wr = wrap.getBoundingClientRect();
        const px = clientX - wr.left;
        tooltip.innerHTML =
          `<div class="tt-tt-head">${escapeHtml(p.label)}</div>`
          + (isShare
            ? `<div class="tt-tt-big">${p.eff.toFixed(0)}% <span>led by a romance-shelf topic</span></div>`
            : `<div class="tt-tt-big">${p.eff.toFixed(1)} <span>effective ${unitNoun(2)}${useDom ? ' (dominant)' : ''}</span></div>`)
          + (isShare
            ? `<div class="tt-tt-sub">${p.songs} song${p.songs === 1 ? '' : 's'}${p.basisMode ? (p.below ? ` · below basis (${p.songs} of ${BASIS_N} tagged)` : ` · top-${BASIS_N} basis`) : ''}</div>`
            : '')
          + (isShare ? '' : (useDom
            ? `<div class="tt-tt-sub">${p.distinct}${STATE.mode === 'themes' ? ` of ${yMax}` : ''} ${unitNoun(p.distinct)} carried as dominant · ${p.songs} song${p.songs === 1 ? '' : 's'}${p.basisMode ? (p.below ? ` · below basis (${p.songs} of ${BASIS_N} tagged)` : ` · top-${BASIS_N} basis`) : ''}</div>`
            : `<div class="tt-tt-sub">${p.distinct} of ${yMax} ${unitNoun(yMax)} present · ${p.songs} song${p.songs === 1 ? '' : 's'}</div>`));
        tooltip.hidden = false;
        tooltip.style.left = px + 'px';
        // Sit on the cursor's half so it stays opposite the flipped year label.
        if (svgY < padT + chartH / 2) { tooltip.style.top = '12px'; tooltip.style.bottom = 'auto'; }
        else { tooltip.style.top = 'auto'; tooltip.style.bottom = '12px'; }
        tooltip.style.transform = px > wr.width * 0.7 ? 'translateX(-100%)' : px < wr.width * 0.3 ? 'translateX(0)' : 'translateX(-50%)';
      }
    }
    chartEl.onmousemove = (e) => showAt(e.clientX, e.clientY);
    chartEl.onmouseleave = () => { hline.style.display = 'none'; if (yearEl) yearEl.style.display = 'none'; if (tooltip) tooltip.hidden = true; };
  }

  // ---- Sparse / empty fallbacks -------------------------------------------
  function renderField(cols) {
    const chartEl = document.getElementById('tt-chart');
    const legend = document.getElementById('tt-legend');
    if (legend) legend.hidden = true;
    if (!cols.length) {
      chartEl.innerHTML = '<p class="tt-chart-note">No tagged data in this window yet.</p>';
      return;
    }
    const col = cols[cols.length - 1];
    const { items, total } = rollupCol(col);
    const rows = items.slice().sort((a, b) => b.count - a.count);
    const denom = total || 1;
    const maxShare = (rows[0] ? rows[0].count : 1) / denom;
    chartEl.innerHTML = `<div class="tt-field">${rows.map((d) => {
      const pct = (d.count / denom) * 100, w = (d.count / denom) / maxShare * 100;
      return `<div class="tt-field-row">
        <span class="tt-field-label">${escapeHtml(labelForKey(d.key))}</span>
        <span class="tt-field-bar"><span class="tt-field-fill" style="width:${w.toFixed(1)}%;background:${colorForKey(d.key)}"></span></span>
        <span class="tt-field-pct">${pct.toFixed(1)}%</span>
      </div>`;
    }).join('')}</div>`;
  }

  // ---- Chrome (mirrors the homepage trajectory panel exactly) --------------
  let ovDrag = null, panDrag = null, gWired = false, tmDrawerOpen = false, rerafPending = false;

  function fullLo() { return ALLYEARS[0].year; }
  function fullHi() { return ALLYEARS[ALLYEARS.length - 1].year; }
  function snapPreset(zoom) {
    zoomHi = fullHi();
    zoomLo = zoom === 'all' ? fullLo() : Math.max(fullLo(), zoomHi - (parseInt(zoom, 10) - 1));
  }
  function matchedPreset() {
    if (zoomLo === fullLo() && zoomHi === fullHi()) return 'all';
    if (zoomHi !== fullHi()) return null;
    const span = zoomHi - zoomLo + 1;
    return (span === 30 || span === 20 || span === 10) ? String(span) : null;
  }
  function markPresetActive() {
    const active = matchedPreset();
    document.querySelectorAll('#tt-presets .traj-zoom-btn').forEach((b) => {
      b.classList.toggle('active', b.getAttribute('data-zoom') === active);
    });
  }
  function updateZoomWindowLabel() {
    const lbl = document.getElementById('tt-zoom-window');
    if (lbl) lbl.textContent = `${zoomLo} – ${zoomHi}`;
  }

  // Build the inner chrome into the era-content host, mirroring the homepage
  // historical container: zoom bar (window + presets + inline overview), the
  // chart area, then the collapsible Time Machine drawer.
  function buildHost() {
    const host = document.getElementById('tt-host');
    if (!host) return;
    const isHist = STATE.period === 'historical';
    let html = '';
    if (isHist) {
      html += '<div class="traj-zoom-bar">'
        + '<span class="traj-zoom-window" id="tt-zoom-window" aria-live="polite"></span>'
        + '<div class="traj-zoom-presets" id="tt-presets">'
        + '<button class="traj-zoom-btn active" data-zoom="all" type="button">All</button>'
        + '<button class="traj-zoom-btn" data-zoom="30" type="button">30Y</button>'
        + '<button class="traj-zoom-btn" data-zoom="20" type="button">20Y</button>'
        + '<button class="traj-zoom-btn" data-zoom="10" type="button">10Y</button>'
        + '</div>'
        + '<div class="traj-overview" id="tt-overview" role="slider" aria-label="Year range locator: drag the box to pan, drag the edges to zoom" tabindex="-1"></div>'
        + '</div>';
    }
    html += '<div class="traj-chart-area" id="tt-wrap"><div id="tt-chart"></div><div class="tt-tooltip" id="tt-tooltip" hidden></div></div>';
    if (isHist) {
      html += '<button class="traj-tm-toggle" id="tt-tm-toggle" type="button" aria-expanded="false" aria-controls="tt-tm-drawer">'
        + '<svg class="traj-tm-icon" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><circle cx="12" cy="12" r="9.5" fill="none" stroke="currentColor" stroke-width="1.5"/><line class="traj-tm-clock-min" x1="12" y1="12" x2="12" y2="6.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line class="traj-tm-clock-hr" x1="12" y1="12" x2="15.5" y2="13.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="12" cy="12" r="1" fill="currentColor"/></svg>'
        + '<span class="traj-tm-label">Time Machine</span>'
        + '<svg class="traj-tm-chevron" viewBox="0 0 24 24" width="10" height="10" aria-hidden="true"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        + '</button>'
        + '<div class="traj-tm-drawer" id="tt-tm-drawer" inert><div class="traj-tm-drawer-inner"><div class="timemachine-controls" id="tt-tm"></div></div></div>';
    }
    html += '<div class="tt-legend" id="tt-legend" hidden></div>';
    host.innerHTML = html;
  }

  // ---- Mini-overview locator + draggable brace (homepage classes) ----------
  function renderOverview() {
    const host = document.getElementById('tt-overview');
    if (!host || !ALLYEARS.length) return;
    const W = 320, H = 28, padT = 4, chartH = H - 8;
    const isShare = shareView();
    const useDom = dominantBasis();
    const yMax = isShare ? 100 : (useDom ? dominantCeiling() : maxEffective()), maxI = ALLYEARS.length - 1;
    const pts = ALLYEARS.map((y, i) => {
      const eff = isShare
        ? (shareOf(y) || 0) * 100
        : (useDom && dominantOf(y) != null
          ? dominantOf(y)
          : effectiveCount(rollupCol(y).items.map((it) => it.count)));
      return {
        x: maxI > 0 ? (i / maxI) * W : W / 2,
        y: padT + (1 - Math.min(eff, yMax) / yMax) * chartH,
      };
    });
    const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    host.innerHTML =
      `<svg class="traj-overview-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path class="traj-overview-line" d="${d}" fill="none" stroke-width="1"/></svg>`
      + `<div class="traj-overview-viewport" data-handle="pan">`
      + `<div class="traj-overview-handle traj-overview-handle--left" data-handle="left"></div>`
      + `<div class="traj-overview-handle traj-overview-handle--right" data-handle="right"></div>`
      + `<div class="traj-overview-grip" aria-hidden="true"></div></div>`;
  }
  function updateBrace() {
    const host = document.getElementById('tt-overview');
    const vp = host && host.querySelector('.traj-overview-viewport');
    if (!vp) return;
    const span = Math.max(1, fullHi() - fullLo());
    vp.style.left = (((zoomLo - fullLo()) / span) * 100) + '%';
    vp.style.width = Math.max(2, ((zoomHi - zoomLo) / span) * 100) + '%';
  }

  // Re-render the chart only (during a drag), keeping brace + label synced.
  function scheduleRerender() {
    if (rerafPending) return;
    rerafPending = true;
    requestAnimationFrame(() => {
      rerafPending = false;
      const { cols, unit } = buildCols();
      if (cols.length < 2) renderField(cols);
      else if (STATE.chart === 'stream') renderStream(cols, unit);
      else renderPoint(cols, unit);
      updateBrace(); updateZoomWindowLabel(); resetClip();
    });
  }

  // ---- Drag handlers (window listeners wired once; targets per render) -----
  function ovStart(e) {
    if (e.button !== 0) return;
    const host = document.getElementById('tt-overview');
    const rect = host.getBoundingClientRect();
    const el = e.target.closest('[data-handle]');
    if (el) { ovDrag = { handle: el.dataset.handle, x: e.clientX, s: zoomLo, e: zoomHi, width: rect.width || 1 }; host.classList.add('is-active'); }
    else {
      const lo = fullLo(), hi = fullHi();
      const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const center = Math.round(lo + pct * (hi - lo)), w = zoomHi - zoomLo;
      let ns = center - Math.floor(w / 2), ne = ns + w;
      if (ns < lo) { ns = lo; ne = ns + w; } if (ne > hi) { ne = hi; ns = ne - w; }
      zoomLo = ns; zoomHi = ne; render();
    }
    e.preventDefault();
  }
  function ovMove(clientX) {
    if (!ovDrag) return;
    const lo = fullLo(), hi = fullHi(), span = Math.max(1, hi - lo);
    const dy = Math.round((clientX - ovDrag.x) / (ovDrag.width / span));
    let ns = ovDrag.s, ne = ovDrag.e;
    if (ovDrag.handle === 'pan') {
      const w = ne - ns; ns += dy; ne = ns + w;
      if (ns < lo) { ns = lo; ne = ns + w; }
      if (ne > hi) { ne = hi; ns = ne - w; }
    } else if (ovDrag.handle === 'left') ns = Math.max(lo, Math.min(ne - 1, ns + dy));
    else if (ovDrag.handle === 'right') ne = Math.min(hi, Math.max(ns + 1, ne + dy));
    if (ns !== zoomLo || ne !== zoomHi) { zoomLo = ns; zoomHi = ne; scheduleRerender(); }
  }
  function ovEnd() {
    if (!ovDrag) return;
    ovDrag = null;
    const host = document.getElementById('tt-overview'); if (host) host.classList.remove('is-active');
    render();
  }
  function panStart(e) {
    if (e.button !== 0 || STATE.period !== 'historical') return;
    const chartEl = document.getElementById('tt-chart');
    panDrag = { x: e.clientX, y: e.clientY, s: zoomLo, e: zoomHi, width: chartEl.getBoundingClientRect().width || 1, moved: false };
    e.preventDefault();
  }
  function panMove(clientX, clientY) {
    if (!panDrag) return;
    const dx = clientX - panDrag.x, dyv = clientY - panDrag.y;
    if (!panDrag.moved && Math.abs(dx) < 4 && Math.abs(dyv) < 4) return;
    panDrag.moved = true;
    const lo = fullLo(), hi = fullHi(), initSpan = panDrag.e - panDrag.s + 1;
    const shift = -dx / (panDrag.width / initSpan);
    const target = Math.max(1, Math.round(initSpan * Math.pow(2, dyv / 200)));
    const center = (panDrag.s + panDrag.e) / 2 + shift;
    let ns = Math.round(center - target / 2), ne = ns + target - 1;
    if (ns < lo) { ns = lo; ne = Math.min(hi, ns + target - 1); }
    if (ne > hi) { ne = hi; ns = Math.max(lo, ne - target + 1); }
    ns = Math.max(lo, ns); ne = Math.min(hi, ne);
    if (ns !== zoomLo || ne !== zoomHi) { zoomLo = ns; zoomHi = ne; scheduleRerender(); }
  }
  function panEnd() {
    if (panDrag && panDrag.moved) { panDrag = null; render(); } else panDrag = null;
  }
  function wireGlobalDrag() {
    if (gWired) return;
    gWired = true;
    window.addEventListener('mousemove', (e) => { if (ovDrag) ovMove(e.clientX); else if (panDrag) panMove(e.clientX, e.clientY); });
    window.addEventListener('mouseup', () => { if (ovDrag) ovEnd(); else if (panDrag) panEnd(); });
  }
  function attachHistoricalChrome() {
    const ov = document.getElementById('tt-overview'); if (ov) ov.onmousedown = ovStart;
    const chartEl = document.getElementById('tt-chart'); if (chartEl) chartEl.onmousedown = panStart;
    document.querySelectorAll('#tt-presets .traj-zoom-btn').forEach((btn) => {
      btn.onclick = () => { snapPreset(btn.getAttribute('data-zoom')); render(); };
    });
    const toggle = document.getElementById('tt-tm-toggle');
    if (toggle) toggle.onclick = () => { tmDrawerOpen = !tmDrawerOpen; applyDrawerState(); };
    applyDrawerState();
  }
  function applyDrawerState() {
    const d = document.getElementById('tt-tm-drawer'), t = document.getElementById('tt-tm-toggle');
    if (d) { d.classList.toggle('is-open', tmDrawerOpen); if (tmDrawerOpen) d.removeAttribute('inert'); else d.setAttribute('inert', ''); }
    if (t) t.setAttribute('aria-expanded', String(tmDrawerOpen));
  }

  // ---- Time Machine (homepage timemachine-* markup + ids) ------------------
  function resetClip() {
    const rectEl = document.getElementById('tt-clip-rect');
    if (rectEl && TMGEO) rectEl.setAttribute('width', TMGEO.W);
    const marker = document.getElementById('tt-tm-marker');
    if (marker) marker.style.display = 'none';
  }
  function renderTM() {
    const host = document.getElementById('tt-tm');
    if (!host) return;
    tmStop();
    if (!TMGEO || TMGEO.maxIdx < 1) { host.innerHTML = ''; return; }
    const max = TMGEO.maxIdx;
    tmPos = max;
    host.innerHTML = `
      <div class="timemachine-wrap">
        <input type="range" class="timemachine-slider" id="timemachine-slider" min="0" max="${max}" value="${max}" step="1" aria-label="Time machine slider">
      </div>
      <div class="timemachine-playback">
        <button class="timemachine-play-btn" id="timemachine-rev" title="Play backward" aria-label="Play backward"><svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M11 18V6l-8.5 6 8.5 6zm.5-6l8.5 6V6l-8.5 6z"/></svg></button>
        <button class="timemachine-play-btn" id="timemachine-play" title="Play forward" aria-label="Play forward"><svg viewBox="0 0 24 24" width="14" height="14" id="timemachine-play-icon" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg></button>
        <button class="timemachine-play-btn" id="timemachine-fwd" title="Play forward fast" aria-label="Play forward fast"><svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M4 18l8.5-6L4 6v12zm9-12v12l8.5-6L13 6z"/></svg></button>
        <div class="timemachine-progress"><div class="timemachine-progress-fill" id="timemachine-progress" style="width:100%"></div></div>
        <button class="timemachine-speed-btn" id="timemachine-speed" aria-label="Playback speed">1x</button>
        <button class="timemachine-reset" id="timemachine-reset" aria-label="Reset time machine" style="display:none;">Reset</button>
      </div>`;
    const slider = document.getElementById('timemachine-slider');
    document.getElementById('timemachine-play').onclick = () => { if (tmPlaying) tmStop(); else tmPlay(1); };
    document.getElementById('timemachine-rev').onclick = () => { if (tmPlaying && tmDir === -1) { tmStop(); return; } tmStop(); tmPlay(-1); };
    document.getElementById('timemachine-fwd').onclick = () => { if (tmPlaying && tmDir === 1) { tmStop(); return; } tmStop(); tmPlay(1); };
    document.getElementById('timemachine-speed').onclick = (e) => { tmSpeedIdx = (tmSpeedIdx + 1) % TM_SPEEDS.length; e.currentTarget.textContent = TM_SPEEDS[tmSpeedIdx] + 'x'; };
    document.getElementById('timemachine-reset').onclick = () => { tmStop(); tmPos = max; updateTM(tmPos); document.getElementById('timemachine-reset').style.display = 'none'; };
    slider.oninput = () => { tmStop(); tmPos = parseInt(slider.value, 10); updateTM(tmPos); };
    resetClip();
  }
  function updateTM(pos) {
    if (!TMGEO) return;
    const max = TMGEO.maxIdx, i = Math.floor(pos), frac = pos - i;
    const a = TMGEO.xs[Math.min(i, max)], b = TMGEO.xs[Math.min(i + 1, max)];
    const px = a + (b - a) * frac;
    const rectEl = document.getElementById('tt-clip-rect');
    if (rectEl) rectEl.setAttribute('width', (px + 2).toFixed(1));
    const marker = document.getElementById('tt-tm-marker');
    if (marker) { marker.setAttribute('x1', px.toFixed(1)); marker.setAttribute('x2', px.toFixed(1)); marker.style.display = ''; }
    const prog = document.getElementById('timemachine-progress');
    if (prog) prog.style.width = (max > 0 ? (pos / max) * 100 : 100) + '%';
    const slider = document.getElementById('timemachine-slider');
    if (slider) slider.value = Math.round(pos);
    const reset = document.getElementById('timemachine-reset');
    if (reset) reset.style.display = pos < max ? '' : 'none';
  }
  function tmAnimate(ts) {
    if (!tmPlaying) return;
    if (!tmAnimate.last) tmAnimate.last = ts;
    const dt = (ts - tmAnimate.last) / 1000; tmAnimate.last = ts;
    const max = TMGEO ? TMGEO.maxIdx : 0;
    tmPos += TM_BASE * TM_SPEEDS[tmSpeedIdx] * tmDir * dt;
    if (tmDir === 1 && tmPos >= max) { tmPos = max; updateTM(tmPos); tmStop(); return; }
    if (tmDir === -1 && tmPos <= 0) { tmPos = 0; updateTM(tmPos); tmStop(); return; }
    updateTM(tmPos);
    tmAnim = requestAnimationFrame(tmAnimate);
  }
  function tmPlay(dir) {
    const max = TMGEO ? TMGEO.maxIdx : 0;
    tmDir = dir;
    if (dir === 1 && tmPos >= max) tmPos = 0;
    if (dir === -1 && tmPos <= 0) tmPos = max;
    tmPlaying = true; tmAnimate.last = null;
    const playBtn = document.getElementById('timemachine-play');
    const playIcon = document.getElementById('timemachine-play-icon');
    if (playBtn) playBtn.classList.add('active');
    if (playIcon) playIcon.innerHTML = '<rect fill="currentColor" x="6" y="4" width="4" height="16"/><rect fill="currentColor" x="14" y="4" width="4" height="16"/>';
    tmAnim = requestAnimationFrame(tmAnimate);
  }
  function tmStop() {
    tmPlaying = false;
    if (tmAnim) cancelAnimationFrame(tmAnim);
    const playBtn = document.getElementById('timemachine-play');
    const playIcon = document.getElementById('timemachine-play-icon');
    if (playBtn) playBtn.classList.remove('active');
    if (playIcon) playIcon.innerHTML = '<path fill="currentColor" d="M8 5v14l11-7z"/>';
  }

  // ---- Dispatch ------------------------------------------------------------
  function render() {
    tmStop();
    buildHost();   // rebuild the era-content chrome for the active period
    const isHist = STATE.period === 'historical';
    const sub = document.getElementById('tt-chart-sub');
    const { cols, unit } = buildCols();

    const periodPhrase = !isHist
      ? 'the last 12 months'
      : (zoomLo === fullLo() && zoomHi === fullHi() ? 'every tagged year' : `${zoomLo}–${zoomHi}`);
    if (sub) {
      const basisSentence = dominantBasis() && isHist
        ? ` Every year is measured on its top ${BASIS_N} songs; dashed years fall short of that basis.`
        : '';
      sub.textContent = STATE.chart === 'stream'
        ? `Share of each ${unit === 'month' ? 'month' : 'period'}'s ${STATE.mode === 'themes' ? 'themes' : 'topics'}, across ${periodPhrase}. Each song carries one unit of weight, split across its topics.`
        : shareView()
          ? `Percent of each ${unit === 'month' ? 'month' : 'year'}'s songs whose dominant topic sits on the romance shelf (romance, breakup, longing, sex, betrayal, infidelity, obsession), across ${periodPhrase}.${basisSentence} The higher the line, the more the music is about romance and its aftermath.`
          : `Effective number of ${unitNoun(2)} per ${unit === 'month' ? 'month' : 'year'}, across ${periodPhrase}.${dominantBasis() ? (STATE.mode === 'themes' ? ' Each song votes once, by its dominant topic, rolled to its primary theme.' : ' Each song votes once, by its dominant topic.') : ''}${basisSentence} When the line falls, fewer ${unitNoun(2)} carry more of the music.`;
    }

    // Line selector only applies to Point mode -- dimmed (not hidden) while
    // Stream is active. The Group filter has no effect on the Romance-share
    // line (it is theme-level by definition), so it dims there.
    const lineCtl = document.getElementById('tt-line-control');
    if (lineCtl) {
      lineCtl.hidden = false;
      lineCtl.classList.toggle('is-inert', STATE.chart !== 'point');
    }
    const groupCtl = document.getElementById('tt-group-control');
    if (groupCtl) groupCtl.classList.toggle('is-inert', shareView());

    if (cols.length < 2) { renderField(cols); TMGEO = null; }
    else if (STATE.chart === 'stream') renderStream(cols, unit);
    else renderPoint(cols, unit);

    if (isHist) {
      renderOverview(); updateBrace(); attachHistoricalChrome();
      renderTM(); markPresetActive(); updateZoomWindowLabel();
    }
  }

  // ---- Coverage caveat -----------------------------------------------------
  function renderCaveat(coverage) {
    const box = document.getElementById('tt-caveat');
    if (!box || !coverage) return;
    if (!coverage.is_early_signal) { box.hidden = true; return; }
    const tagged = coverage.topic_year_range, corpus = coverage.corpus_year_range;
    let msg = '<strong>Early signal.</strong> ';
    if (tagged && tagged[0] === tagged[1]) msg += `Only ${tagged[0]} is tagged so far. `;
    else if (tagged) msg += `Topics are tagged for ${tagged[0]}–${tagged[1]} (${coverage.years_with_topics} year${coverage.years_with_topics === 1 ? '' : 's'}). `;
    if (corpus && (!tagged || corpus[0] < tagged[0])) msg += `The Compass holds chart history back to ${corpus[0]}; tagging it is queued, and these views fill in across the decades as it runs. `;
    msg += 'Read the trend as provisional until the span is wide enough to prove a decades-long narrowing.';
    box.innerHTML = msg; box.hidden = false;
  }

  // ---- Filters -------------------------------------------------------------
  // The era-tabs (period) + the Chart/Group controls are static in the markup,
  // so they're bound once. The zoom presets live in the per-render chrome and
  // are wired in attachHistoricalChrome().
  function wireFilters() {
    function bind(attr, key) {
      document.querySelectorAll(`[data-${attr}]`).forEach((btn) => {
        btn.addEventListener('click', () => {
          const val = btn.getAttribute(`data-${attr}`);
          if (STATE[key] === val) return;
          STATE[key] = val;
          document.querySelectorAll(`[data-${attr}]`).forEach((b) => {
            const on = b === btn;
            b.classList.toggle('active', on);
            if (b.hasAttribute('aria-selected')) b.setAttribute('aria-selected', on ? 'true' : 'false');
          });
          render();
        });
      });
    }
    bind('chart', 'chart');
    bind('mode', 'mode');
    bind('period', 'period');
    bind('line', 'line');
  }

  // ---- Boot ----------------------------------------------------------------
  async function boot() {
    try {
      [YEARLY, TRAIL] = await Promise.all([API.getTopicTrends(), API.getTopicTrendsTrailing()]);
    } catch (err) {
      console.error('Failed to load /topic-trends:', err);
      const s = document.getElementById('tt-status'); if (s) s.textContent = 'Could not load topic trends.';
      return;
    }
    YEARLY.years = YEARLY.years || [];
    TRAIL = TRAIL || { periods: [] };
    const taxonomy = YEARLY.taxonomy || [];
    const themes = YEARLY.themes || [];
    TAX_N = taxonomy.length || 30;
    BASIS_N = YEARLY.basis_n || 20;
    TOPIC_MAP = {};
    taxonomy.forEach((t) => { TOPIC_MAP[t.slug] = { primary: t.primary, also: t.also || [] }; });
    THEME_LABEL = {}; THEME_ORDER = [];
    themes.forEach((t) => { THEME_LABEL[t.slug] = t.label; THEME_ORDER.push(t.slug); });

    ALLYEARS = YEARLY.years.filter((y) => y.total_pairs > 0);
    if (ALLYEARS.length) { zoomLo = ALLYEARS[0].year; zoomHi = ALLYEARS[ALLYEARS.length - 1].year; }

    renderCaveat(YEARLY.coverage);
    wireGlobalDrag();
    wireFilters();
    render();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
