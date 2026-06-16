/* === Topic Trends — "The Narrowing" ===
 *
 * Two homepage-branded views built on the same pure-SVG + vanilla-JS toolkit
 * as the homepage trajectory chart (no chart library):
 *
 *   1. The Narrowing Index — a year-over-year line of the effective number of
 *      topics/themes (2^H). Falling line = the field is narrowing. Mirrors the
 *      trajectory chart's padded-viewBox + gridlines + spring draw-in.
 *   2. The Topic River — a normalized share streamgraph (silhouette baseline,
 *      Catmull-Rom smoothing) of prevalence over time.
 *
 * A Topics/Themes toggle rolls the 25 fine topics up into their 7 primary
 * themes (see ETHER_TOPIC_PRIMARY in the backend taxonomy). The rollup uses
 * PRIMARY theme only, so shares always sum cleanly; secondary facets (`also`)
 * are surfaced in tooltips, never summed.
 *
 * Colors are a CATEGORICAL palette (taxonomy / theme order), NOT the 5 Compass
 * tier colors — topics are a different axis.
 *
 * Data: GET /api/topic-trends (backend/app/routers/topic_trends.py).
 */
(() => {
  'use strict';

  const RIVER_TOP_N = 12;   // distinct topic bands; remainder folds into "other"

  let DATA = null;          // raw API payload
  let MODE = 'themes';      // 'themes' | 'topics'
  let TOPIC_MAP = {};       // slug -> {primary, also:[]}
  let THEME_LABEL = {};     // theme slug -> label
  let THEME_ORDER = [];     // theme slugs in canonical order
  let TOPIC_COLOR = {};     // slug -> hsl
  let THEME_COLOR = {};     // theme slug -> hsl

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function labelize(slug) { return String(slug).replace(/-/g, ' '); }

  // ---- Color indexes (stable, keyed to canonical order) --------------------
  function buildColors(taxonomy, themes) {
    TOPIC_COLOR = {};
    const n = taxonomy.length || 25;
    taxonomy.forEach((t, i) => {
      const hue = Math.round((i * 360) / n);
      const light = i % 2 === 0 ? 62 : 52;
      TOPIC_COLOR[t.slug] = `hsl(${hue}, 58%, ${light}%)`;
    });
    THEME_COLOR = {};
    const m = themes.length || 7;
    themes.forEach((t, i) => {
      const hue = Math.round((i * 360) / m + 12);
      THEME_COLOR[t.slug] = `hsl(${hue}, 60%, 58%)`;
    });
  }
  function colorForKey(key) {
    if (key === 'other') return '#5a5a72';
    return MODE === 'themes' ? (THEME_COLOR[key] || '#7a7a92') : (TOPIC_COLOR[key] || '#7a7a92');
  }
  function labelForKey(key) {
    if (key === 'other') return 'other';
    return MODE === 'themes' ? (THEME_LABEL[key] || key) : labelize(key);
  }

  // ---- Diversity math ------------------------------------------------------
  function effectiveCount(counts) {
    const total = counts.reduce((a, b) => a + b, 0);
    if (total <= 0) return 0;
    let h = 0;
    for (const c of counts) { if (c > 0) { const p = c / total; h -= p * Math.log2(p); } }
    return Math.pow(2, h);
  }

  // Roll one year's topic distribution into the current mode's buckets.
  // Returns { items: [{key, count}], total }.
  function rollupYear(yearObj) {
    if (MODE === 'topics') {
      return {
        items: yearObj.distribution.map((d) => ({ key: d.topic, count: d.count })),
        total: yearObj.total_pairs,
      };
    }
    const acc = {};
    yearObj.distribution.forEach((d) => {
      const theme = (TOPIC_MAP[d.topic] && TOPIC_MAP[d.topic].primary) || 'other';
      acc[theme] = (acc[theme] || 0) + d.count;
    });
    const items = Object.keys(acc).map((k) => ({ key: k, count: acc[k] }));
    return { items, total: items.reduce((s, i) => s + i.count, 0) };
  }

  function maxEffective() { return MODE === 'themes' ? THEME_ORDER.length : 25; }
  function unitNoun(n) {
    const base = MODE === 'themes' ? 'theme' : 'topic';
    return n === 1 ? base : base + 's';
  }

  // ---- Catmull-Rom -> cubic bezier smoothing -------------------------------
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

  function xLabelYears(years) {
    const lo = years[0].year, hi = years[years.length - 1].year;
    const span = hi - lo;
    const step = span > 40 ? 10 : span > 15 ? 5 : span > 8 ? 2 : 1;
    const set = new Set([lo, hi]);
    for (let y = Math.ceil(lo / step) * step; y <= hi; y += step) set.add(y);
    return set;
  }

  // =========================================================================
  // 1. THE NARROWING INDEX
  // =========================================================================
  function renderIndex() {
    const status = document.getElementById('index-status');
    const chartEl = document.getElementById('index-chart');
    const tooltip = document.getElementById('index-tooltip');
    if (!chartEl) return;
    const years = DATA.years;
    if (!years.length) { if (status) status.textContent = 'No tagged years yet.'; return; }
    if (status) status.hidden = true;

    const W = 960, H = 380, padL = 46, padR = 22, padT = 24, padB = 40;
    const chartW = W - padL - padR, chartH = H - padT - padB, maxIdx = years.length - 1;
    const yMax = maxEffective();

    const pts = years.map((d, i) => {
      const { items } = rollupYear(d);
      const eff = effectiveCount(items.map((it) => it.count));
      const distinct = items.filter((it) => it.count > 0).length;
      return {
        x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
        y: padT + (1 - eff / yMax) * chartH,
        year: d.year, eff, distinct, songs: d.songs_with_topics,
      };
    });

    const linePath = smoothPath(pts);
    const areaPath = (maxIdx > 0)
      ? linePath + ` L ${pts[maxIdx].x.toFixed(2)} ${(padT + chartH).toFixed(2)} L ${pts[0].x.toFixed(2)} ${(padT + chartH).toFixed(2)} Z`
      : '';

    let svg = `<svg class="tt-index-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Effective number of ${unitNoun(2)} per year">`;
    svg += `<defs><linearGradient id="tt-index-area" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stop-color="#00d4aa" stop-opacity="0.28"/><stop offset="100%" stop-color="#00d4aa" stop-opacity="0"/>
      </linearGradient></defs>`;

    const gridStep = yMax <= 8 ? 1 : 5;
    for (let v = 0; v <= yMax; v += gridStep) {
      const y = padT + (1 - v / yMax) * chartH;
      svg += `<line class="tt-grid" x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}"/>`;
      svg += `<text class="tt-y-label" x="${padL - 8}" y="${(y + 3.5).toFixed(1)}" text-anchor="end">${v}</text>`;
    }

    if (areaPath) svg += `<path class="tt-index-area" d="${areaPath}" fill="url(#tt-index-area)"/>`;
    svg += `<path class="tt-index-line" d="${linePath}" fill="none"/>`;

    const labels = xLabelYears(years);
    pts.forEach((p) => {
      if (labels.has(p.year)) {
        svg += `<text class="tt-x-label" x="${p.x.toFixed(1)}" y="${H - 14}" text-anchor="middle">${p.year}</text>`;
        svg += `<line class="tt-tick" x1="${p.x.toFixed(1)}" y1="${padT + chartH}" x2="${p.x.toFixed(1)}" y2="${padT + chartH + 4}"/>`;
      }
    });
    pts.forEach((p, i) => { svg += `<circle class="tt-index-dot" data-i="${i}" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4"/>`; });
    svg += `<line id="tt-index-hline" class="tt-hover-line" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" style="display:none"/>`;
    svg += `<rect x="${padL}" y="${padT}" width="${chartW}" height="${chartH}" fill="transparent" class="tt-hover-area"/></svg>`;
    chartEl.innerHTML = svg;

    // Spring draw-in
    const lineNode = chartEl.querySelector('.tt-index-line');
    if (lineNode && lineNode.getTotalLength) {
      const len = lineNode.getTotalLength();
      lineNode.style.strokeDasharray = len;
      lineNode.style.strokeDashoffset = len;
      lineNode.style.transition = 'stroke-dashoffset 1.5s cubic-bezier(0.34, 1.56, 0.64, 1)';
      requestAnimationFrame(() => requestAnimationFrame(() => { lineNode.style.strokeDashoffset = '0'; }));
    }

    const svgEl = chartEl.querySelector('.tt-index-svg');
    const hline = chartEl.querySelector('#tt-index-hline');
    const wrap = document.getElementById('index-wrap');
    function showAt(clientX) {
      const rect = svgEl.getBoundingClientRect();
      const svgX = ((clientX - rect.left) / rect.width) * W;
      let near = 0, best = Infinity;
      pts.forEach((p, i) => { const dx = Math.abs(p.x - svgX); if (dx < best) { best = dx; near = i; } });
      const p = pts[near];
      hline.setAttribute('x1', p.x.toFixed(1)); hline.setAttribute('x2', p.x.toFixed(1)); hline.style.display = '';
      if (tooltip) {
        const wr = wrap.getBoundingClientRect();
        const px = clientX - wr.left;
        tooltip.innerHTML =
          `<div class="tt-tt-head">${p.year}</div>`
          + `<div class="tt-tt-big">${p.eff.toFixed(1)} <span>effective ${unitNoun(2)}</span></div>`
          + `<div class="tt-tt-sub">${p.distinct} of ${yMax} ${unitNoun(yMax)} present · ${p.songs} song${p.songs === 1 ? '' : 's'}</div>`;
        tooltip.hidden = false;
        tooltip.style.left = px + 'px';
        tooltip.style.transform = px > wr.width * 0.7 ? 'translateX(-100%)' : px < wr.width * 0.3 ? 'translateX(0)' : 'translateX(-50%)';
      }
    }
    wrap.onmousemove = (e) => showAt(e.clientX);
    wrap.onmouseleave = () => { hline.style.display = 'none'; if (tooltip) tooltip.hidden = true; };
  }

  // =========================================================================
  // 2. THE TOPIC RIVER (normalized share streamgraph)
  // =========================================================================
  function bandKeysForMode(years) {
    if (MODE === 'themes') return { keys: THEME_ORDER.slice(), hasOther: false };
    const totals = {};
    years.forEach((y) => y.distribution.forEach((d) => { totals[d.topic] = (totals[d.topic] || 0) + d.count; }));
    const ranked = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
    return { keys: ranked.slice(0, RIVER_TOP_N), hasOther: ranked.length > RIVER_TOP_N };
  }

  function renderRiver() {
    const status = document.getElementById('river-status');
    const chartEl = document.getElementById('river-chart');
    const legend = document.getElementById('river-legend');
    const tooltip = document.getElementById('river-tooltip');
    if (!chartEl) return;
    const years = DATA.years;

    if (years.length < 2) { renderSingleYearField(years[0], status, chartEl, legend); return; }
    if (status) status.hidden = true;

    const { keys, hasOther } = bandKeysForMode(years);
    const bandKeys = hasOther ? keys.concat(['other']) : keys.slice();
    const keepSet = new Set(keys);

    const valuesByYear = years.map((y) => {
      const row = {}; bandKeys.forEach((k) => (row[k] = 0));
      const { items } = rollupYear(y);
      items.forEach((it) => {
        if (keepSet.has(it.key)) row[it.key] += it.count;
        else if (hasOther) row['other'] += it.count;
      });
      return row;
    });
    const yearTotals = valuesByYear.map((r) => bandKeys.reduce((s, k) => s + r[k], 0));

    const W = 960, H = 420, padL = 16, padR = 16, padT = 18, padB = 34;
    const chartW = W - padL - padR, chartH = H - padT - padB, maxIdx = years.length - 1;
    const xs = years.map((_, i) => padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2));

    // Inside-out stacking: largest band toward the middle.
    const totalFor = (k) => valuesByYear.reduce((s, r) => s + r[k], 0);
    const byTotal = bandKeys.slice().sort((a, b) => totalFor(b) - totalFor(a));
    const order = [];
    byTotal.forEach((k, i) => { if (i % 2 === 0) order.push(k); else order.unshift(k); });

    // Normalize each year to share (100% stacked) — sampling rate differs by
    // era, so raw volume isn't comparable; share is the narrowing axis.
    const bandEdges = {};
    order.forEach((k) => (bandEdges[k] = []));
    valuesByYear.forEach((row, yi) => {
      const total = yearTotals[yi] || 1;
      let cursor = padT;
      order.forEach((k) => {
        const h = (row[k] / total) * chartH;
        bandEdges[k].push({ x: xs[yi], y0: cursor, y1: cursor + h });
        cursor += h;
      });
    });

    let svg = `<svg class="tt-river-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${MODE === 'themes' ? 'Theme' : 'Topic'} prevalence over time">`;
    const labels = xLabelYears(years);
    years.forEach((y, i) => {
      if (labels.has(y.year)) svg += `<text class="tt-x-label" x="${xs[i].toFixed(1)}" y="${H - 12}" text-anchor="middle">${y.year}</text>`;
    });
    order.forEach((k) => {
      const edges = bandEdges[k];
      const top = edges.map((e) => ({ x: e.x, y: e.y0 }));
      const botRev = edges.map((e) => ({ x: e.x, y: e.y1 })).reverse();
      const d = `${smoothPath(top)} ${smoothPath(botRev).replace(/^M/, 'L')} Z`;
      svg += `<path class="tt-band" data-key="${escapeHtml(k)}" d="${d}" fill="${colorForKey(k)}"/>`;
    });
    svg += '</svg>';
    chartEl.innerHTML = svg;

    if (legend) {
      legend.hidden = false;
      legend.innerHTML = order.slice().sort((a, b) => totalFor(b) - totalFor(a))
        .map((k) => `<span class="tt-legend-item" data-key="${escapeHtml(k)}">`
          + `<span class="tt-legend-swatch" style="background:${colorForKey(k)}"></span>${escapeHtml(labelForKey(k))}</span>`)
        .join('');
    }

    const bands = Array.from(chartEl.querySelectorAll('.tt-band'));
    const wrap = document.getElementById('river-wrap');
    function highlight(key) { bands.forEach((b) => b.classList.toggle('is-dim', key != null && b.getAttribute('data-key') !== key)); }
    function showTip(key, clientX, clientY) {
      if (!tooltip) return;
      const last = valuesByYear[maxIdx], total = yearTotals[maxIdx] || 1;
      const cnt = last[key] || 0, pct = (cnt / total) * 100, yr = years[maxIdx].year;
      let extra = '';
      if (MODE === 'topics' && TOPIC_MAP[key]) {
        const prim = THEME_LABEL[TOPIC_MAP[key].primary] || '';
        const also = (TOPIC_MAP[key].also || []).map((t) => THEME_LABEL[t] || t);
        extra = `<div class="tt-tt-theme">${escapeHtml(prim)}${also.length ? ' · also ' + escapeHtml(also.join(', ')) : ''}</div>`;
      }
      tooltip.innerHTML =
        `<div class="tt-tt-head"><span class="tt-legend-swatch" style="background:${colorForKey(key)}"></span>${escapeHtml(labelForKey(key))}</div>`
        + extra
        + `<div class="tt-tt-sub">${pct.toFixed(1)}% of ${yr} · ${cnt} song${cnt === 1 ? '' : 's'}</div>`;
      tooltip.hidden = false;
      const wr = wrap.getBoundingClientRect();
      const px = clientX - wr.left, py = clientY - wr.top;
      tooltip.style.left = px + 'px'; tooltip.style.top = py + 'px';
      tooltip.style.transform = px > wr.width * 0.7 ? 'translate(-100%, -120%)' : 'translate(12px, -120%)';
    }
    chartEl.onmousemove = (e) => {
      const k = e.target && e.target.getAttribute && e.target.getAttribute('data-key');
      if (k) { highlight(k); showTip(k, e.clientX, e.clientY); }
    };
    chartEl.onmouseleave = () => { highlight(null); if (tooltip) tooltip.hidden = true; };
    if (legend) {
      legend.onmouseover = (e) => { const it = e.target.closest('.tt-legend-item'); if (it) highlight(it.getAttribute('data-key')); };
      legend.onmouseleave = () => highlight(null);
    }
  }

  function renderSingleYearField(year, status, chartEl, legend) {
    if (!year) { if (status) status.textContent = 'No tagged years yet.'; return; }
    if (status) {
      status.hidden = false; status.classList.add('tt-chart-note');
      status.innerHTML = `Only ${year.year} is tagged so far, so the river can&rsquo;t flow yet. Here is this year&rsquo;s field &mdash; the stream forms as more years are tagged.`;
    }
    const { items, total } = rollupYear(year);
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
    if (legend) legend.hidden = true;
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

  function renderAll() { renderIndex(); renderRiver(); }

  function wireToggle() {
    const btns = Array.from(document.querySelectorAll('.tt-toggle-btn'));
    btns.forEach((b) => b.addEventListener('click', () => {
      const mode = b.getAttribute('data-mode');
      if (mode === MODE) return;
      MODE = mode;
      btns.forEach((x) => {
        const active = x === b;
        x.classList.toggle('is-active', active);
        x.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      renderAll();
    }));
  }

  // ---- Boot ----------------------------------------------------------------
  async function boot() {
    try {
      DATA = await API.getTopicTrends();
    } catch (err) {
      console.error('Failed to load /topic-trends:', err);
      const is = document.getElementById('index-status'); if (is) is.textContent = 'Could not load the index.';
      const rs = document.getElementById('river-status'); if (rs) rs.textContent = 'Could not load the river.';
      return;
    }
    DATA.years = DATA.years || [];
    const taxonomy = DATA.taxonomy || [];
    const themes = DATA.themes || [];
    TOPIC_MAP = {};
    taxonomy.forEach((t) => { TOPIC_MAP[t.slug] = { primary: t.primary, also: t.also || [] }; });
    THEME_LABEL = {}; THEME_ORDER = [];
    themes.forEach((t) => { THEME_LABEL[t.slug] = t.label; THEME_ORDER.push(t.slug); });
    buildColors(taxonomy, themes);

    renderCaveat(DATA.coverage);
    wireToggle();
    renderAll();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
