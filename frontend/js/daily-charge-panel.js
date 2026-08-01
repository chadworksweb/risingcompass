/* === Charge Panel (capsule) ===
   The WHOLE homepage trajectory panel (id `trajectory-panel`), extracted VERBATIM
   from js/app.js so the homepage and the chart pages share ONE implementation (no
   drift). It owns both era tabs -- Daily Charge (trailing-N-days) and Historical
   Charge Index (long-range drift) -- the shared scale toggle, the shared Time
   Machine drawer, and the era-tab switching. Mount:

     DailyChargePanel.mount(panelEl, {
       loadDaily,        // () => Promise<[{date, compass_degree, charge_level}]>
       loadAnomalies,    // optional () => anomalyMap keyed by date
       loadHistorical,   // optional () => Promise<[{year, compass_degree, charge_level, chart_song_count}]>
                         //   null/omitted -> the Historical tab ships DARK
       // host hooks (homepage supplies; chart pages omit -> safe no-ops):
       onDateSelect,     // (isoDate) daily point click  -> load that day's reading
       onYearSelect,     // (year, degree, color) historical point click -> load year songs
       syncCalendar,     // (year) keep the compass calendar in step
       setCompassMode,   // (mode, opts) reframe the compass header
       eraTaglines,      // { daily, historical } tab tagline copy
     });

   `loadSeries` is still accepted as an alias for `loadDaily` (Stage 1 callers).
   Styling is the global .trajectory / .traj / .era rules already in css/main.css,
   so the capsule ships no CSS. */
(function () {
  'use strict';

  function announce(msg) {
    const el = document.getElementById('sr-announce');
    if (!el) return;
    el.textContent = '';
    requestAnimationFrame(() => { el.textContent = msg; });
  }

  function anomalyLabel(a) {
    if (a.anomaly_type === 'album_release') {
      const named = [a.artist, a.album].filter(Boolean).join(' - ');
      return named || a.note || 'Album release';
    }
    return a.note || 'Chart anomaly';
  }

  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };

  const CHARGE_LABELS = {
    violet: 'Ascended',
    blue: 'Elevated',
    green: 'Decent',
    orange: 'Degraded',
    red: 'Corrupted',
  };

  const SCALE_FIT_PAD = 5;        // units of headroom past the max and the min
  const SCALE_FIT_MIN_SPAN = 20;  // never zoom to a band tighter than 20 units total
  const TM_SPEEDS = [0.5, 1, 2, 4];
  const TM_BASE_SPEED = 1.5;

  function degreeToScore(degree) {
    const s = Math.round((90 - degree) * 100 / 90);
    return (s > 0 ? '+' : '') + s;
  }

  function formatDate(dateStr) {
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  }

  function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function degreeToCharge(degree) {
    return (90 - degree) / 0.9;
  }

  // charge value -> fractional plot position [0,1] within the domain, clamped so
  // out-of-band charges pin to the top/bottom edge.
  function chargeToFrac(charge, dom) {
    const span = (dom.hi - dom.lo) || 1;
    return Math.max(0, Math.min(1, (dom.hi - charge) / span));
  }
  function chargeDegreeToY(degree, padT, chartH, dom) {
    return padT + chargeToFrac(degreeToCharge(degree), dom) * chartH;
  }
  // Grid rows for the domain: top + bottom labeled with their charge value, a
  // labeled 0 reference line when it falls inside the band, quarters unlabeled.
  function chargeGridRows(dom) {
    const span = (dom.hi - dom.lo) || 1;
    const signed = v => { v = Math.round(v); return v > 0 ? '+' + v : String(v); };
    // Draw a labeled 0 reference line only when it sits clearly inside the band.
    // When 0 hugs a bound (e.g. a segment topping out near zero, hi = +3) its
    // label would overlap the bound's, and the bound already marks near-neutral,
    // so suppress it rather than crowd the two labels.
    const zeroClear = dom.lo < 0 && dom.hi > 0 && Math.min(dom.hi, -dom.lo) > span * 0.1;
    const rows = [{ charge: dom.hi, label: signed(dom.hi) }];
    for (let q = 1; q <= 3; q++) {
      const c = dom.hi - (q / 4) * span;
      if (zeroClear && Math.abs(c) < span * 0.06) continue; // don't double a filler on ~0
      rows.push({ charge: c, label: '' });
    }
    rows.push({ charge: dom.lo, label: signed(dom.lo) });
    if (zeroClear) rows.push({ charge: 0, label: '0' });
    return rows;
  }

  function degreeToTier(deg) {
    // Mirrors backend charge_calc.py::degree_to_charge -- symmetric about the
    // neutral center, so -25 -> Degraded mirrors +25 -> Elevated (positive
    // thresholds inclusive, negative exclusive). Keep in lockstep.
    if (deg <= 22.5) return 'violet';   // Ascended  (+75 to +100)
    if (deg <= 67.5) return 'blue';     // Elevated  (+25 to +74)
    if (deg < 112.5) return 'green';    // Decent    (-24 to +24)
    if (deg < 157.5) return 'orange';   // Degraded  (-25 to -74)
    return 'red';                       // Corrupted (-75 to -100)
  }

  // Chart click pinch effect: nudge nearby points toward the clicked one, then
  // spring back. Pure geometry -- no per-panel state.
  function pinchLine(points, nearestIdx, lineEl, areaEl, padT, chartH, ytdSplit) {
    const radius = 20; // SVG units (~25px)
    const strength = 0.35;
    const target = points[nearestIdx];
    const pinched = points.map(p => {
      const dx = Math.abs(p.x - target.x);
      if (dx > radius) return p;
      const factor = strength * (1 - dx / radius);
      return { ...p, x: p.x + (target.x - p.x) * factor };
    });

    const buildLine = pts => pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');

    // If YTD split, solid line only goes up to second-to-last point
    const solidPinched = ytdSplit ? pinched.slice(0, -1) : pinched;
    const pinchedPath = buildLine(solidPinched);
    const fullPinched = pinched;
    const last = fullPinched[fullPinched.length - 1];
    const first = fullPinched[0];
    const pinchedArea = buildLine(fullPinched) + ` L ${last.x.toFixed(1)} ${padT + chartH} L ${first.x.toFixed(1)} ${padT + chartH} Z`;

    if (lineEl) lineEl.setAttribute('d', pinchedPath);
    if (areaEl) areaEl.setAttribute('d', pinchedArea);

    // Restore
    const solidPoints = ytdSplit ? points.slice(0, -1) : points;
    const origPath = buildLine(solidPoints);
    const origArea = buildLine(points) + ` L ${points[points.length - 1].x.toFixed(1)} ${padT + chartH} L ${points[0].x.toFixed(1)} ${padT + chartH} Z`;
    setTimeout(() => {
      if (lineEl) lineEl.setAttribute('d', origPath);
      if (areaEl) areaEl.setAttribute('d', origArea);
    }, 100);
  }

  // The Time Machine drives the page's compass gauge as you scrub. On the
  // homepage these reach the live compass; the capsule uses safe, self-contained
  // versions so it drives whatever dial is present and no-ops the charge bar /
  // header reframe on pages that don't have them. (Compass is a top-level const,
  // so guard with typeof; Charge may be absent entirely.)
  function isTodayDate(isoDate) {
    if (!isoDate) return false;
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    return isoDate === `${y}-${m}-${d}`;
  }
  function setCompassDate(text) {
    const el = document.getElementById('compass-date-svg') || document.getElementById('compass-date');
    if (el) el.textContent = text;
  }
  var _CHARGE = (typeof Charge !== 'undefined') ? Charge : { setLevel: function () {} };
  var _COMPASS = (typeof Compass !== 'undefined') ? Compass : { setDegree: function () {} };

  function createPanel(panelEl, opts) {
    opts = opts || {};
    var _loadSeries = opts.loadDaily || opts.loadSeries;
    var _loadAnomalies = opts.loadAnomalies || null;
    var _loadHistorical = opts.loadHistorical || null;
    // Host hooks -- the homepage supplies these to reach its own reading /
    // year-songs / calendar / compass-header logic; chart pages omit them and
    // get safe no-ops (the historical tab is dark there anyway).
    var _onDateSelect = opts.onDateSelect || function () {};
    var _onYearSelect = opts.onYearSelect || function () {};
    var _syncCalendar = opts.syncCalendar || function () {};
    var _setCompassMode = opts.setCompassMode || function () {};
    var eraTaglines = opts.eraTaglines || {};
    var _dailyTagline = eraTaglines.daily || 'trailing 365 days, day by day';
    var _histTagline = eraTaglines.historical || "where we've been and where we are";

    // --- Shared panel state (both era tabs) ---
    let scaleMode = 'fit';
    let CHART_ANOMALIES = {};
    let tmDrawerOpen = false;
    // Last domain bound each chart rendered with, tracked per edge so a re-fit
    // pulses ONLY the axis label(s) whose value actually changed. null = first
    // render (never pulses).
    let lastTrajHi = null, lastTrajLo = null;
    let lastDailyHi = null, lastDailyLo = null;

    // --- Daily state ---
    let dailyChartLoaded = false;
    let dailyChartData = [];
    let dailyChartPoints = [];
    // Daily zoom is index-based (start/end into dailyChartData chronologically).
    // Preset buttons (Year/Q/M/W) snap the window to common spans, brace + drag
    // operate on the same start/end pair. null = full range default.
    let dailyZoomStartIdx = null;
    let dailyZoomEndIdx = null;
    let dtmPlaying = false;
    let dtmAnimFrame = null;
    let dtmPosition = 0;
    let dtmDirection = 1;
    let dtmSpeedIdx = 1;

    // --- Historical state ---
    let allYearData = [];
    let zoomStartYear = null;
    let zoomEndYear = null;
    let chartPoints = [];
    let chartData = [];
    let chartHasYTD = false;
    let tmPlaying = false;
    let tmAnimFrame = null;
    let tmPosition = 0;
    let tmDirection = 1;
    let tmSpeedIdx = 1;

    // --- Saved compass state (Time Machine restore) ---
    let savedDegree = null;
    let savedCharge = null;
    let savedDateText = null;

    // --- Build the panel structure (era-tabs + both era-contents), preserving
    // any pre-existing expand button so trajectory-expand.js keeps its handle. ---
    var histDark = !_loadHistorical;
    var tabsHTML =
      '<div class="era-tabs">'
      + '<button class="era-tab active" data-era="daily" type="button">'
      + '<span class="era-tab-title">Daily Charge</span>'
      + '<span class="era-tab-tagline">' + escapeHtml(_dailyTagline) + '</span>'
      + '</button>'
      + '<button class="era-tab" data-era="historical" type="button"'
      + (histDark ? ' disabled aria-disabled="true" title="Coming soon for this chart"' : '') + '>'
      + '<span class="era-tab-title">Historical Charge Index</span>'
      + '<span class="era-tab-tagline">' + escapeHtml(histDark ? 'coming soon' : _histTagline) + '</span>'
      + '</button>'
      + '</div>';
    var dailyHTML = '<div class="era-content active" id="era-daily"><div id="daily-chart-container"></div></div>';
    var histHTML = '<div class="era-content" id="era-historical"><div id="trajectory-container"></div></div>';
    // Adopt pre-existing era markup when the host already ships it (the homepage
    // keeps its static tabs/contents so trajectory-expand.js -- which wires the
    // era tabs before this mount runs -- keeps its handles). Build the structure
    // only when the panel is empty (the chart pages ship an empty #trajectory-panel).
    if (!panelEl.querySelector('.era-tabs')) {
      var frag = document.createElement('div');
      frag.innerHTML = tabsHTML + dailyHTML + histHTML;
      while (frag.firstChild) panelEl.appendChild(frag.firstChild);
    }

    var dailyContainer = panelEl.querySelector('#daily-chart-container');
    var histContainer = panelEl.querySelector('#trajectory-container');

    // Reframe the compass header (host-specific); no-op on pages without one.
    function setCompassMode(mode, modeOpts) { _setCompassMode(mode, modeOpts); }

    // Resolve the visible charge domain {hi, lo} for a dataset under the current
    // mode. Callers pass whichever slice the axis should fit: the historical chart
    // passes its current zoom window (so the axis tightens to the selected
    // segment), while the full-range overview locator passes allYearData.
    function resolveDomain(data) {
      if (scaleMode === 'full' || !data || !data.length) return { hi: 100, lo: -100 };
      let mn = Infinity, mx = -Infinity;
      for (const d of data) {
        const c = degreeToCharge(d.compass_degree);
        if (c < mn) mn = c;
        if (c > mx) mx = c;
      }
      let hi = Math.min(100, Math.ceil(mx) + SCALE_FIT_PAD);
      let lo = Math.max(-100, Math.floor(mn) - SCALE_FIT_PAD);
      // Enforce a minimum band so a flat segment doesn't magnify tiny wiggles.
      if (hi - lo < SCALE_FIT_MIN_SPAN) {
        const mid = (hi + lo) / 2;
        hi = Math.min(100, mid + SCALE_FIT_MIN_SPAN / 2);
        lo = Math.max(-100, hi - SCALE_FIT_MIN_SPAN);
        hi = Math.min(100, lo + SCALE_FIT_MIN_SPAN);
      }
      return { hi, lo };
    }

    function saveCompassState() {
      if (savedDegree !== null) return; // already saved
      const scoreText = document.getElementById('compass-score')?.textContent;
      const chargeText = document.getElementById('compass-charge-text')?.textContent;
      const dateEl = document.getElementById('compass-date-svg');
      if (scoreText && chargeText) {
        for (const [color, label] of Object.entries(CHARGE_LABELS)) {
          if (label.toUpperCase() === chargeText) { savedCharge = color; break; }
        }
        savedDegree = 90 - (parseInt(scoreText) * 90 / 100);
      }
      if (dateEl) savedDateText = dateEl.textContent;
    }

    function restoreCompassState() {
      if (savedDegree !== null) {
        _COMPASS.setDegree(savedDegree, savedCharge);
        _CHARGE.setLevel(savedCharge, 0, 0, savedDegree);
      }
      if (savedDateText) {
        const dateEl = document.getElementById('compass-date-svg');
        if (dateEl) dateEl.textContent = savedDateText;
      }
      savedDegree = null;
      savedCharge = null;
      savedDateText = null;
    }

    // Apply the shared tmDrawerOpen flag to every TM drawer + toggle in the
    // panel. Both era tabs render their own copies, but they share state.
    function applyTmDrawerState() {
      panelEl.querySelectorAll('.traj-tm-drawer').forEach((drawer) => {
        drawer.classList.toggle('is-open', tmDrawerOpen);
        if (tmDrawerOpen) drawer.removeAttribute('inert');
        else drawer.setAttribute('inert', '');
      });
      panelEl.querySelectorAll('.traj-tm-toggle').forEach((toggle) => {
        toggle.setAttribute('aria-expanded', String(tmDrawerOpen));
      });
    }

    // Redraw both trajectory charts (main + mini-overview, both tabs) at the
    // current scale, preserving each tab's zoom window and viewport position.
    function rerenderChartsForScale() {
      if (histContainer && chartData.length) {
        applyZoomChartOnly(histContainer);
        renderOverview(histContainer);
        updateOverviewViewport(histContainer);
      }
      if (dailyContainer && dailyChartLoaded) {
        applyDailyZoomChartOnly(dailyContainer);
        renderDailyOverview(dailyContainer);
        updateDailyOverviewViewport(dailyContainer);
      }
    }
  async function loadDailyChart() {
    const container = document.getElementById('daily-chart-container');
    if (!container) return;

    container.innerHTML = `
      <div class="trajectory-loading" role="status" aria-label="Loading daily chart">
        <svg class="rc-loader" viewBox="0 0 64 64" aria-hidden="true">
          <defs>
            <linearGradient id="rc-loader-grad-daily" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stop-color="#9933ff"/>
              <stop offset="25%" stop-color="#3388ff"/>
              <stop offset="50%" stop-color="#33cc55"/>
              <stop offset="75%" stop-color="#ffbb33"/>
              <stop offset="100%" stop-color="#ff3333"/>
            </linearGradient>
          </defs>
          <circle class="rc-loader-track" cx="32" cy="32" r="26" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="3"/>
          <circle class="rc-loader-arc" cx="32" cy="32" r="26" fill="none" stroke="url(#rc-loader-grad-daily)" stroke-width="3" stroke-linecap="round" stroke-dasharray="60 200"/>
          <line class="rc-loader-needle" x1="32" y1="32" x2="32" y2="12" stroke="#eeeef4" stroke-width="2" stroke-linecap="round" transform-origin="32 32"/>
          <circle cx="32" cy="32" r="3" fill="#00d4aa"/>
        </svg>
        <div class="rc-loader-label">Loading daily chart</div>
        <div class="rc-loader-sub">reading the last 365 days…</div>
      </div>`;

    try {
      const results = await Promise.all([_loadSeries(), _loadAnomalies ? _loadAnomalies() : Promise.resolve(null)]);
      const data = results[0];
      if (results[1]) CHART_ANOMALIES = results[1];
      dailyChartLoaded = true;

      if (!data.length) {
        container.innerHTML = '<div class="daily-empty">No daily readings yet.</div>';
        return;
      }

      dailyChartData = data;
      dailyZoomStartIdx = 0;
      dailyZoomEndIdx = dailyChartData.length - 1;

      container.innerHTML = `
        <div class="traj-zoom-bar">
          <span class="traj-zoom-window daily-zoom-window" aria-live="polite"></span>
          <div class="traj-zoom-presets">
            <button class="traj-zoom-btn active" data-zoom="year">Year</button>
            <button class="traj-zoom-btn" data-zoom="q">Q</button>
            <button class="traj-zoom-btn" data-zoom="m">M</button>
            <button class="traj-zoom-btn" data-zoom="w">W</button>
          </div>
          <div class="traj-overview daily-overview" role="slider" aria-label="Date range locator: drag the box to pan, drag the edges to zoom" tabindex="-1"></div>
        </div>
        <div class="traj-chart-area"></div>
        <button class="traj-tm-toggle" type="button" aria-expanded="false" aria-controls="daily-traj-tm-drawer">
          <svg class="traj-tm-icon" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
            <circle cx="12" cy="12" r="9.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
            <line class="traj-tm-clock-min" x1="12" y1="12" x2="12" y2="6.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line class="traj-tm-clock-hr" x1="12" y1="12" x2="15.5" y2="13.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <circle cx="12" cy="12" r="1" fill="currentColor"/>
          </svg>
          <span class="traj-tm-label">Time Machine</span>
          <svg class="traj-tm-chevron" viewBox="0 0 24 24" width="10" height="10" aria-hidden="true">
            <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <div class="traj-tm-drawer" id="daily-traj-tm-drawer" inert>
          <div class="traj-tm-drawer-inner">
            <div class="timemachine-controls"></div>
          </div>
        </div>
      `;

      container.querySelectorAll('.traj-zoom-presets .traj-zoom-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          snapDailyPreset(btn.dataset.zoom);
          applyDailyZoom(container);
        });
      });

      const tmToggle = container.querySelector('.traj-tm-toggle');
      tmToggle.addEventListener('click', () => {
        tmDrawerOpen = !tmDrawerOpen;
        applyTmDrawerState();
      });
      applyTmDrawerState();

      renderDailyOverview(container);
      applyDailyZoom(container);
      initDailyChartPanZoom(container);
      initDailyOverviewControls(container);
    } catch (err) {
      container.innerHTML = '<p style="color:var(--rc-text-dim);font-size:0.8rem;">Could not load daily chart</p>';
    }
  }

  function matchedDailyPreset() {
    if (!dailyChartData.length) return null;
    const lastIdx = dailyChartData.length - 1;
    if (dailyZoomEndIdx !== lastIdx) return null;
    if (dailyZoomStartIdx === 0) return 'year';
    const span = dailyZoomEndIdx - dailyZoomStartIdx + 1;
    if (span === 90) return 'q';
    if (span === 30) return 'm';
    if (span === 7) return 'w';
    return null;
  }

  function snapDailyPreset(zoom) {
    if (!dailyChartData.length) return;
    const lastIdx = dailyChartData.length - 1;
    dailyZoomEndIdx = lastIdx;
    if (zoom === 'year') dailyZoomStartIdx = 0;
    else if (zoom === 'q') dailyZoomStartIdx = Math.max(0, lastIdx - 89);
    else if (zoom === 'm') dailyZoomStartIdx = Math.max(0, lastIdx - 29);
    else if (zoom === 'w') dailyZoomStartIdx = Math.max(0, lastIdx - 6);
  }

  function formatDateShort(iso) {
    if (!iso) return '';
    const [y, m, d] = iso.split('-');
    const dt = new Date(parseInt(y), parseInt(m) - 1, parseInt(d));
    return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function updateDailyZoomWindowLabel(container) {
    const lbl = container.querySelector('.daily-zoom-window');
    if (lbl && dailyChartData.length) {
      const s = dailyChartData[dailyZoomStartIdx];
      const e = dailyChartData[dailyZoomEndIdx];
      lbl.textContent = `${formatDateShort(s && s.date)} – ${formatDateShort(e && e.date)}`;
    }
    const active = matchedDailyPreset();
    container.querySelectorAll('.traj-zoom-presets .traj-zoom-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.zoom === active);
    });
    updateDailyOverviewViewport(container);
  }

  function applyDailyZoomChartOnly(container) {
    const filtered = dailyChartData.slice(dailyZoomStartIdx, dailyZoomEndIdx + 1);
    if (!filtered.length) return;
    renderDailyChart(filtered, container);
    updateDailyZoomWindowLabel(container);
  }

  function applyDailyZoom(container) {
    const filtered = dailyChartData.slice(dailyZoomStartIdx, dailyZoomEndIdx + 1);
    if (!filtered.length) return;
    dtmStopPlayback();
    renderDailyChart(filtered, container);
    initDailyTimeMachineControls(container);
    updateDailyZoomWindowLabel(container);
  }

  function renderDailyOverview(container) {
    const overviewEl = container.querySelector('.daily-overview');
    if (!overviewEl || !dailyChartData.length) return;
    const W = 320, H = 28;
    const padT = 4, padB = 4;
    const chartH = H - padT - padB;
    const dom = resolveDomain(dailyChartData);
    const maxIdx = dailyChartData.length - 1;
    const pts = dailyChartData.map((d, i) => ({
      x: maxIdx > 0 ? (i / maxIdx) * W : W / 2,
      y: chargeDegreeToY(d.compass_degree, padT, chartH, dom),
    }));
    const linePath = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    overviewEl.innerHTML = `
      <svg class="traj-overview-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
        <path class="traj-overview-line" d="${linePath}" fill="none" stroke-width="1"/>
      </svg>
      <div class="traj-overview-viewport" data-handle="pan">
        <div class="traj-overview-handle traj-overview-handle--left" data-handle="left" aria-label="Drag to set start date"></div>
        <div class="traj-overview-handle traj-overview-handle--right" data-handle="right" aria-label="Drag to set end date"></div>
        <div class="traj-overview-grip" aria-hidden="true"></div>
      </div>`;
  }

  function updateDailyOverviewViewport(container) {
    const overviewEl = container.querySelector('.daily-overview');
    if (!overviewEl || !dailyChartData.length) return;
    const vp = overviewEl.querySelector('.traj-overview-viewport');
    if (!vp) return;
    const lastIdx = dailyChartData.length - 1 || 1;
    const leftPct = (dailyZoomStartIdx / lastIdx) * 100;
    const widthPct = Math.max(2, ((dailyZoomEndIdx - dailyZoomStartIdx) / lastIdx) * 100);
    vp.style.left = leftPct + '%';
    vp.style.width = widthPct + '%';
  }

  function initDailyOverviewControls(container) {
    const overviewEl = container.querySelector('.daily-overview');
    if (!overviewEl) return;
    let drag = null;
    let rafPending = false;
    const scheduleRender = () => {
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        applyDailyZoomChartOnly(container);
      });
    };
    const overviewRect = () => overviewEl.getBoundingClientRect();
    const idxAtX = (clientX) => {
      const rect = overviewRect();
      const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      return Math.round(pct * (dailyChartData.length - 1));
    };
    const start = (handle, clientX) => {
      drag = {
        handle, startX: clientX,
        startZoom: { s: dailyZoomStartIdx, e: dailyZoomEndIdx },
        width: overviewRect().width || 1,
      };
      overviewEl.classList.add('is-active');
    };
    const move = (clientX) => {
      if (!drag) return;
      const lastIdx = dailyChartData.length - 1;
      const totalSpan = Math.max(1, lastIdx);
      const pxPerIdx = drag.width / totalSpan;
      const idxDelta = Math.round((clientX - drag.startX) / pxPerIdx);
      let newStart = drag.startZoom.s;
      let newEnd = drag.startZoom.e;
      if (drag.handle === 'pan') {
        const span = newEnd - newStart;
        newStart += idxDelta;
        newEnd = newStart + span;
        if (newStart < 0) { newStart = 0; newEnd = newStart + span; }
        if (newEnd > lastIdx) { newEnd = lastIdx; newStart = newEnd - span; }
      } else if (drag.handle === 'left') {
        newStart = Math.max(0, Math.min(newEnd - 1, newStart + idxDelta));
      } else if (drag.handle === 'right') {
        newEnd = Math.min(lastIdx, Math.max(newStart + 1, newEnd + idxDelta));
      }
      if (newStart !== dailyZoomStartIdx || newEnd !== dailyZoomEndIdx) {
        dailyZoomStartIdx = newStart;
        dailyZoomEndIdx = newEnd;
        scheduleRender();
      }
    };
    const end = () => {
      const wasDrag = !!drag;
      drag = null;
      overviewEl.classList.remove('is-active');
      if (wasDrag) {
        dtmStopPlayback();
        initDailyTimeMachineControls(container);
      }
    };
    overviewEl.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      const el = e.target.closest('[data-handle]');
      if (el) {
        start(el.dataset.handle, e.clientX);
      } else {
        const target = idxAtX(e.clientX);
        const lastIdx = dailyChartData.length - 1;
        const span = dailyZoomEndIdx - dailyZoomStartIdx;
        let ns = target - Math.floor(span / 2);
        let ne = ns + span;
        if (ns < 0) { ns = 0; ne = ns + span; }
        if (ne > lastIdx) { ne = lastIdx; ns = ne - span; }
        dailyZoomStartIdx = ns;
        dailyZoomEndIdx = ne;
        applyDailyZoom(container);
      }
      e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => { if (drag) move(e.clientX); });
    window.addEventListener('mouseup', () => { if (drag) end(); });
    overviewEl.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      const tgt = document.elementFromPoint(t.clientX, t.clientY);
      const el = tgt && tgt.closest('[data-handle]');
      if (el) start(el.dataset.handle, t.clientX);
    }, { passive: true });
    overviewEl.addEventListener('touchmove', (e) => {
      if (!drag || e.touches.length !== 1) return;
      move(e.touches[0].clientX);
      e.preventDefault();
    }, { passive: false });
    overviewEl.addEventListener('touchend', () => { if (drag) end(); });
    overviewEl.addEventListener('touchcancel', () => { if (drag) end(); });
  }

  function initDailyChartPanZoom(container) {
    const chartArea = container.querySelector('.traj-chart-area');
    if (!chartArea) return;
    // A pan must START inside the actual plot box (the transparent
    // .traj-hover-area rect), not the surrounding axis-label gutters/margins.
    const insidePlot = (clientX, clientY) => {
      const r = chartArea.querySelector('.traj-hover-area');
      if (!r) return true;
      const b = r.getBoundingClientRect();
      return clientX >= b.left && clientX <= b.right && clientY >= b.top && clientY <= b.bottom;
    };
    let drag = null;
    let rafPending = false;
    const scheduleRender = () => {
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        applyDailyZoomChartOnly(container);
      });
    };
    const startDrag = (clientX, clientY) => {
      drag = {
        x: clientX, y: clientY,
        startZoom: { s: dailyZoomStartIdx, e: dailyZoomEndIdx },
        width: chartArea.getBoundingClientRect().width || 1,
        moved: false,
      };
      chartArea.classList.add('traj-dragging');
    };
    const moveDrag = (clientX, clientY) => {
      if (!drag) return;
      const dx = clientX - drag.x;
      const dy = clientY - drag.y;
      if (!drag.moved && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) drag.moved = true;
      if (!drag.moved) return;
      const lastIdx = dailyChartData.length - 1;
      const initSpan = drag.startZoom.e - drag.startZoom.s + 1;
      const pxPerIdx = drag.width / initSpan;
      const idxShift = -dx / pxPerIdx;
      const zoomFactor = Math.pow(2, dy / 200);
      const targetSpan = Math.max(1, Math.round(initSpan * zoomFactor));
      const center = (drag.startZoom.s + drag.startZoom.e) / 2 + idxShift;
      let newStart = Math.round(center - targetSpan / 2);
      let newEnd = newStart + targetSpan - 1;
      if (newStart < 0) { newStart = 0; newEnd = Math.min(lastIdx, newStart + targetSpan - 1); }
      if (newEnd > lastIdx) { newEnd = lastIdx; newStart = Math.max(0, newEnd - targetSpan + 1); }
      newStart = Math.max(0, newStart);
      newEnd = Math.min(lastIdx, newEnd);
      if (newStart !== dailyZoomStartIdx || newEnd !== dailyZoomEndIdx) {
        dailyZoomStartIdx = newStart;
        dailyZoomEndIdx = newEnd;
        scheduleRender();
      }
    };
    const endDrag = () => {
      if (!drag) return;
      const wasDrag = drag.moved;
      drag = null;
      chartArea.classList.remove('traj-dragging');
      if (wasDrag) {
        chartArea.addEventListener('click', (e) => {
          e.stopPropagation();
          e.preventDefault();
        }, { once: true, capture: true });
        dtmStopPlayback();
        initDailyTimeMachineControls(container);
      }
    };
    chartArea.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (!insidePlot(e.clientX, e.clientY)) return;
      startDrag(e.clientX, e.clientY);
      e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => { if (drag) moveDrag(e.clientX, e.clientY); });
    window.addEventListener('mouseup', () => { if (drag) endDrag(); });
    // --- Pinch zoom (two fingers) ---
    let pinch = null;
    const distance = (t0, t1) => Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
    const midX = (t0, t1) => (t0.clientX + t1.clientX) / 2;

    const startPinch = (t0, t1) => {
      const rect = chartArea.getBoundingClientRect();
      const mx = midX(t0, t1);
      const pctX = Math.max(0, Math.min(1, (mx - rect.left) / rect.width));
      const span = dailyZoomEndIdx - dailyZoomStartIdx + 1;
      pinch = {
        d0: distance(t0, t1) || 1,
        startMidX: mx,
        anchorIdx: dailyZoomStartIdx + pctX * (span - 1),
        pctX,
        startSpan: span,
        rectWidth: rect.width || 1,
      };
      if (drag) {
        drag = null;
        chartArea.classList.remove('traj-dragging');
      }
    };

    const movePinch = (t0, t1) => {
      if (!pinch) return;
      const d = distance(t0, t1);
      if (Math.abs(d - pinch.d0) < 8) return;
      const ratio = Math.max(0.05, Math.min(20, d / pinch.d0));
      const lastIdx = dailyChartData.length - 1;
      const newSpan = Math.max(1, Math.round(pinch.startSpan / ratio));
      let newStart = Math.round(pinch.anchorIdx - pinch.pctX * (newSpan - 1));
      const mx = midX(t0, t1);
      const pxPerIdxAtStart = pinch.rectWidth / pinch.startSpan;
      const idxShift = Math.round(-(mx - pinch.startMidX) / pxPerIdxAtStart);
      newStart += idxShift;
      let newEnd = newStart + newSpan - 1;
      if (newStart < 0) { newStart = 0; newEnd = Math.min(lastIdx, newStart + newSpan - 1); }
      if (newEnd > lastIdx) { newEnd = lastIdx; newStart = Math.max(0, newEnd - newSpan + 1); }
      newStart = Math.max(0, newStart);
      newEnd = Math.min(lastIdx, newEnd);
      if (newStart !== dailyZoomStartIdx || newEnd !== dailyZoomEndIdx) {
        dailyZoomStartIdx = newStart;
        dailyZoomEndIdx = newEnd;
        scheduleRender();
      }
    };

    const endPinch = () => {
      if (!pinch) return;
      pinch = null;
      dtmStopPlayback();
      initDailyTimeMachineControls(container);
    };

    chartArea.addEventListener('touchstart', (e) => {
      if (e.touches.length === 1) {
        const t = e.touches[0];
        if (!insidePlot(t.clientX, t.clientY)) return;
        startDrag(t.clientX, t.clientY);
      } else if (e.touches.length === 2) {
        startPinch(e.touches[0], e.touches[1]);
      }
    }, { passive: true });
    chartArea.addEventListener('touchmove', (e) => {
      if (pinch && e.touches.length === 2) {
        movePinch(e.touches[0], e.touches[1]);
        e.preventDefault();
        return;
      }
      if (drag && e.touches.length === 1) {
        const t = e.touches[0];
        // Touch single-finger: pan only — pass start Y as clientY so
        // dy is 0. Vertical gestures pass through to the page.
        moveDrag(t.clientX, drag.y);
        if (drag.moved) e.preventDefault();
      }
    }, { passive: false });
    chartArea.addEventListener('touchend', (e) => {
      if (pinch && e.touches.length < 2) endPinch();
      if (drag && e.touches.length === 0) endDrag();
    });
    chartArea.addEventListener('touchcancel', () => {
      if (pinch) endPinch();
      if (drag) endDrag();
    });
  }

  // Skipped-day visual placement: a reading with charge_level === 'skipped'
  // (cron didn't run, no data) sits at the mean of its nearest non-skipped
  // neighbors so the trajectory line stays smooth. The original sentinel
  // degree is preserved on _originalDegree for handlers that drive the
  // compass / charge bar — those still read "no data" when the user clicks
  // through to that day. Visual-only; never feeds aggregation.
  function interpolateSkippedDegrees(data) {
    const n = data.length;
    if (!n) return data;
    const prev = new Array(n).fill(-1);
    const next = new Array(n).fill(-1);
    let last = -1;
    for (let i = 0; i < n; i++) {
      prev[i] = last;
      if (data[i].charge_level !== 'skipped') last = i;
    }
    last = -1;
    for (let i = n - 1; i >= 0; i--) {
      next[i] = last;
      if (data[i].charge_level !== 'skipped') last = i;
    }
    return data.map((d, i) => {
      if (d.charge_level !== 'skipped') return d;
      const p = prev[i], x = next[i];
      let interp = d.compass_degree;
      if (p >= 0 && x >= 0) interp = (data[p].compass_degree + data[x].compass_degree) / 2;
      else if (p >= 0) interp = data[p].compass_degree;
      else if (x >= 0) interp = data[x].compass_degree;
      return { ...d, _originalDegree: d.compass_degree, compass_degree: interp };
    });
  }

  function renderDailyChart(data, container) {
    if (!data.length) return;
    data = interpolateSkippedDegrees(data);
    // Fit to the CURRENTLY-ZOOMED window (`data` is the filtered slice), not the
    // full 365-day set, so the axis top/bottom tighten to max+PAD / min-PAD of the
    // selected segment -- matching the historical chart's dynamic Y axis. The
    // overview mini-map (renderDailyOverview) still fits dailyChartData so the
    // locator line stays put while you drag.
    const dom = resolveDomain(data.length ? data : dailyChartData);
    // Same per-edge axis cue as the historical chart: pulse a bound label only
    // when its value changed (here the domain re-fits on pan/zoom/scale toggle).
    const hiChanged = lastDailyHi !== null && lastDailyHi !== dom.hi;
    const loChanged = lastDailyLo !== null && lastDailyLo !== dom.lo;
    lastDailyHi = dom.hi; lastDailyLo = dom.lo;

    const H = 120;
    // Default viewBox width keeps the established 320x120 (2.667:1) shape, which
    // scales uniformly in its normal column (height is auto there). When the
    // trajectory panel is EXPANDED its chart box is much wider but height-locked,
    // so a fixed 320 width would force preserveAspectRatio="none" to stretch the
    // chart non-uniformly (oval dots, smeared labels -- see the distortion bug).
    // Match the viewBox width to the box's real pixel aspect ratio so scaling
    // stays uniform: the chart gains genuine horizontal resolution, not distortion.
    let W = 320;
    const area = container.querySelector('.traj-chart-area');
    // While expanded (or mid-animation either direction) the panel carries
    // .traj-chart-locked and the chart area is height-locked (CSS var). Match the
    // viewBox width to the box's live pixel aspect so the chart re-renders
    // undistorted at the current width -- wider, never xy-stretched.
    if (area && container.closest('.traj-chart-locked')) {
      const pxW = area.clientWidth, pxH = area.clientHeight;
      if (pxW > 0 && pxH > 0) W = Math.max(320, Math.round(H * pxW / pxH));
    }
    const padL = 30, padR = 16, padT = 10, padB = 22;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const maxIdx = data.length - 1;

    dailyChartPoints = data.map((d, i) => ({
      x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
      y: chargeDegreeToY(d.compass_degree, padT, chartH, dom),
      degree: d.compass_degree,
      date: d.date,
      color: d.charge_level,
      originalDegree: (typeof d._originalDegree === 'number') ? d._originalDegree : d.compass_degree,
    }));

    // Charge-anchored gradient (see renderTrajectoryChart): keep tier colors
    // correct for an off-center band.
    const gy = (c) => padT + ((dom.hi - c) / (dom.hi - dom.lo)) * chartH;
    const gyTop = gy(100).toFixed(1), gyBot = gy(-100).toFixed(1);

    const linePath = dailyChartPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const areaPath = linePath + ` L ${dailyChartPoints[maxIdx].x.toFixed(1)} ${padT + chartH} L ${dailyChartPoints[0].x.toFixed(1)} ${padT + chartH} Z`;

    let svg = `<svg class="trajectory-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Charge trajectory chart">`;
    svg += `<defs>
      <linearGradient id="daily-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${gyTop}" x2="0" y2="${gyBot}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" />
        <stop offset="25%" stop-color="${COLOR_HEX.blue}" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" />
        <stop offset="75%" stop-color="${COLOR_HEX.orange}" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" />
      </linearGradient>
      <linearGradient id="daily-area-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${gyTop}" x2="0" y2="${gyBot}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" stop-opacity="0.2" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" stop-opacity="0.05" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" stop-opacity="0.2" />
      </linearGradient>
      <clipPath id="daily-clip"><rect id="daily-clip-rect" x="0" y="0" width="${W}" height="${H}" /></clipPath>
    </defs>`;

    // Grid lines
    // Full-height transparent strip over the y-axis gutter -> the whole axis is
    // the scale toggle's tap target (the labels alone are <17px, and "0" is ~5px
    // wide, far below a touch target). Rendered before the labels so the text's
    // own :hover still fires when pointed directly.
    svg += `<rect class="traj-y-hit" x="0" y="0" width="${padL}" height="${H}" fill="transparent" />`;
    chargeGridRows(dom).forEach(({ charge, label }) => {
      const y = padT + chargeToFrac(charge, dom) * chartH;
      svg += `<line class="trajectory-grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" />`;
      const pulse = (charge === dom.hi && hiChanged) || (charge === dom.lo && loChanged);
      if (label) svg += `<text class="trajectory-y-label${pulse ? ' traj-y-pulse' : ''}" x="${padL - 4}" y="${y + 3}"><title>${scaleMode === 'fit' ? 'Click to show the full +/-100 range' : 'Click to auto-fit the range'}</title>${label}</text>`;
    });

    // Clipped line + area
    svg += `<g clip-path="url(#daily-clip)">`;
    svg += `<path class="trajectory-area" d="${areaPath}" fill="url(#daily-area-grad)" />`;
    svg += `<path class="trajectory-line" d="${linePath}" stroke="url(#daily-grad)" />`;
    svg += `</g>`;

    // Anomaly markers: a faint guide + top marker for any annotated date in the
    // current zoom window. The hover tooltip (which snaps to the nearest date by
    // x) names the anomaly when the column is hovered.
    dailyChartPoints.forEach((p, i) => {
      if (!CHART_ANOMALIES[data[i].date]) return;
      svg += `<line class="daily-annot-line" x1="${p.x.toFixed(1)}" y1="${padT}" x2="${p.x.toFixed(1)}" y2="${(padT + chartH).toFixed(1)}" />`;
      svg += `<circle class="daily-annot-flag" cx="${p.x.toFixed(1)}" cy="${padT}" r="2.4" />`;
    });

    // X-axis labels — boundary-based (stock chart style)
    // Labels snap to natural time boundaries, then thin to fit.
    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const daySpan = maxIdx > 0 ? (new Date(data[maxIdx].date) - new Date(data[0].date)) / 86400000 : 0;
    const minLabelGap = 40; // minimum viewBox units between labels

    // Collect boundary candidates from the data
    const boundaries = [];
    let prevDate = null;
    dailyChartPoints.forEach((p, i) => {
      const d = new Date(data[i].date + 'T00:00:00');
      if (daySpan > 90) {
        // Month boundaries: label first data point of each month
        if (!prevDate || d.getMonth() !== prevDate.getMonth() || d.getFullYear() !== prevDate.getFullYear()) {
          boundaries.push({ x: p.x, label: MONTHS[d.getMonth()], i });
        }
      } else if (daySpan > 14) {
        // Week boundaries: label Mondays (or first data point of each week)
        if (!prevDate || Math.floor((d - new Date(d.getFullYear(), 0, 1)) / 604800000) !== Math.floor((prevDate - new Date(prevDate.getFullYear(), 0, 1)) / 604800000)) {
          boundaries.push({ x: p.x, label: `${MONTHS[d.getMonth()]} ${d.getDate()}`, i });
        }
      } else {
        // Short spans: every data point is a candidate
        boundaries.push({ x: p.x, label: `${String(d.getMonth()+1).padStart(2,'0')}/${String(d.getDate()).padStart(2,'0')}`, i });
      }
      prevDate = d;
    });

    // Thin boundaries: greedily place labels with minimum gap
    let lastPlacedX = -Infinity;
    boundaries.forEach(b => {
      if (b.x - lastPlacedX >= minLabelGap) {
        const anchor = b.x <= padL + 10 ? 'start' : b.x >= W - padR - 10 ? 'end' : 'middle';
        svg += `<text class="trajectory-label" x="${b.x.toFixed(1)}" y="${H - 4}" text-anchor="${anchor}">${b.label}</text>`;
        lastPlacedX = b.x;
      }
    });

    // Moving dot
    const lastPt = dailyChartPoints[maxIdx];
    svg += `<circle id="daily-timemachine-dot" class="trajectory-dot" cx="${lastPt.x.toFixed(1)}" cy="${lastPt.y.toFixed(1)}" fill="var(--rc-bg-dark)" stroke="${COLOR_HEX[lastPt.color] || '#888'}" />`;

    // Hover elements
    svg += `<line id="daily-hover-line" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" class="traj-hover-line" style="display:none" />`;
    svg += `<circle id="daily-hover-dot" cx="0" cy="0" class="traj-hover-dot" style="display:none" />`;
    svg += `<rect x="${padL}" y="${padT}" width="${chartW}" height="${chartH}" fill="transparent" class="traj-hover-area" />`;
    svg += '</svg>';

    const chartEl = container.querySelector('.traj-chart-area');
    chartEl.innerHTML = `<div class="traj-wrap">${svg}<div class="traj-tooltip" id="daily-tooltip"></div></div>`;

    // Hover interaction
    const wrap = chartEl.querySelector('.traj-wrap');
    const svgEl = chartEl.querySelector('.trajectory-svg');

    function showHoverAt(clientX) {
      if (dtmPlaying) return;
      const hoverLine = document.getElementById('daily-hover-line');
      const hoverDot = document.getElementById('daily-hover-dot');
      const tooltip = document.getElementById('daily-tooltip');
      if (!hoverLine) return;

      const rect = svgEl.getBoundingClientRect();
      const relX = (clientX - rect.left) / rect.width;
      const svgX = relX * W;

      let nearest = 0, minDist = Infinity;
      for (let i = 0; i <= maxIdx; i++) {
        const dist = Math.abs(dailyChartPoints[i].x - svgX);
        if (dist < minDist) { minDist = dist; nearest = i; }
      }

      const p = dailyChartPoints[nearest];
      const d = data[nearest];
      const hex = COLOR_HEX[p.color] || '#888';

      hoverLine.setAttribute('x1', p.x.toFixed(1));
      hoverLine.setAttribute('x2', p.x.toFixed(1));
      hoverLine.style.display = '';
      hoverDot.setAttribute('cx', p.x.toFixed(1));
      hoverDot.setAttribute('cy', p.y.toFixed(1));
      hoverDot.setAttribute('stroke', hex);
      hoverDot.style.display = '';

      const fdate = formatDate(d.date);
      const wrapRect = wrap.getBoundingClientRect();
      const pixelX = clientX - wrapRect.left;
      const wrapW = wrapRect.width;
      tooltip.style.left = pixelX + 'px';
      tooltip.style.transform = pixelX > wrapW * 0.7 ? 'translateX(-100%)' : pixelX < wrapW * 0.3 ? 'translateX(0)' : 'translateX(-50%)';
      let tipHtml = `<strong>${fdate}</strong><br><span style="color:${hex}">${degreeToScore(p.degree)}</span> ${CHARGE_LABELS[p.color]}`;
      const annots = CHART_ANOMALIES[d.date];
      if (annots && annots.length) {
        tipHtml += annots.map(a => `<div class="traj-tooltip-annot">${escapeHtml(anomalyLabel(a))}</div>`).join('');
      }
      tooltip.innerHTML = tipHtml;
      tooltip.style.display = 'block';
    }
    function hideHover() {
      const hoverLine = document.getElementById('daily-hover-line');
      const hoverDot = document.getElementById('daily-hover-dot');
      const tooltip = document.getElementById('daily-tooltip');
      if (hoverLine) hoverLine.style.display = 'none';
      if (hoverDot) hoverDot.style.display = 'none';
      if (tooltip) tooltip.style.display = 'none';
    }

    wrap.addEventListener('mousemove', (e) => {
      // Only show the tooltip inside the plot box -- not over the y-label
      // gutters or the margins left/right of the data.
      const r = svgEl.getBoundingClientRect();
      const inX = e.clientX >= r.left + (padL / W) * r.width && e.clientX <= r.left + ((W - padR) / W) * r.width;
      const inY = e.clientY >= r.top + (padT / H) * r.height && e.clientY <= r.top + ((padT + chartH) / H) * r.height;
      if (!inX || !inY) { hideHover(); return; }
      showHoverAt(e.clientX);
    });
    wrap.addEventListener('mouseleave', hideHover);

    // Touch: single-finger drag on the chart body scrubs the tooltip across the
    // points (read values), no tap-lift-tap. stopPropagation keeps the body's
    // pan handler (.traj-chart-area) from firing -- pan/zoom stays on the overview
    // mini-map + two-finger pinch. A tap (no move -> no preventDefault) still
    // falls through to the click handler to load that day's reading.
    let scrubHideTO = null, scrubbing = false;
    // Only start a scrub when the finger lands on the PLOT area, not the x-axis
    // label row (months/years) below it -- so the labels stay scroll-safe.
    const inPlot = (clientY) => {
      const rct = svgEl.getBoundingClientRect();
      const top = rct.top + (padT / H) * rct.height;
      const bot = rct.top + ((padT + chartH) / H) * rct.height;
      return clientY >= top && clientY <= bot;
    };
    wrap.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1 || !inPlot(e.touches[0].clientY)) { scrubbing = false; return; }
      scrubbing = true;
      e.stopPropagation();
      clearTimeout(scrubHideTO);
      showHoverAt(e.touches[0].clientX);
    }, { passive: true });
    wrap.addEventListener('touchmove', (e) => {
      if (!scrubbing || e.touches.length !== 1) return;
      e.stopPropagation();
      e.preventDefault();
      showHoverAt(e.touches[0].clientX);
    }, { passive: false });
    const endScrub = () => { if (!scrubbing) return; scrubbing = false; clearTimeout(scrubHideTO); scrubHideTO = setTimeout(hideHover, 1500); };
    wrap.addEventListener('touchend', endScrub);
    wrap.addEventListener('touchcancel', endScrub);

    // Click: move compass + needle + charge bar, load full reading
    wrap.addEventListener('click', (e) => {
      const rect = svgEl.getBoundingClientRect();
      const relX = (e.clientX - rect.left) / rect.width;
      const svgX = relX * W;

      let nearest = 0, minDist = Infinity;
      for (let i = 0; i <= maxIdx; i++) {
        const dist = Math.abs(dailyChartPoints[i].x - svgX);
        if (dist < minDist) { minDist = dist; nearest = i; }
      }

      const p = dailyChartPoints[nearest];
      const d = data[nearest];
      setCompassDate(formatDate(d.date));
      setCompassMode(isTodayDate(d.date) ? 'today' : 'date');
      const driveDeg = p.originalDegree;
      _COMPASS.setDegree(driveDeg, p.color);
      _CHARGE.setLevel(p.color, 0, 0, driveDeg);
      _onDateSelect(d.date);

      const dot = document.getElementById('daily-hover-dot');
      if (dot) { dot.classList.add('click-pulse'); setTimeout(() => dot.classList.remove('click-pulse'), 100); }
      pinchLine(dailyChartPoints, nearest, svgEl.querySelector('.trajectory-line'), svgEl.querySelector('.trajectory-area'), padT, chartH, false);
    });

    dtmPosition = maxIdx;
  }

  // --- Daily Time Machine ---
  function updateDailyTimeMachine(pos) {
    const pts = dailyChartPoints;
    const ptMax = pts.length - 1;
    if (ptMax < 0) return;

    const i = Math.floor(Math.min(pos, ptMax));
    const frac = pos - i;
    const a = pts[Math.min(i, ptMax)];
    const b = pts[Math.min(i + 1, ptMax)];

    const deg = a.degree + (b.degree - a.degree) * frac;
    const tier = degreeToTier(deg);
    const hex = COLOR_HEX[tier] || '#888';
    const nearestPt = pts[Math.round(Math.min(pos, ptMax))];

    const slider = document.getElementById('daily-timemachine-slider');
    const progressFill = document.getElementById('daily-timemachine-progress');
    const resetBtn = document.getElementById('daily-timemachine-reset');

    if (progressFill) progressFill.style.width = (pos / ptMax * 100) + '%';
    if (slider) {
      slider.value = Math.round(pos);
      slider.setAttribute('aria-valuetext', `${formatDate(nearestPt.date)}, ${CHARGE_LABELS[tier]}`);
    }
    if (resetBtn) resetBtn.style.display = '';

    // Clip chart
    const clipRect = document.getElementById('daily-clip-rect');
    if (clipRect && pts.length) {
      const px = a.x + (b.x - a.x) * frac;
      clipRect.setAttribute('width', px + 2);
    }

    // Move dot
    const tmDot = document.getElementById('daily-timemachine-dot');
    if (tmDot && pts.length) {
      const px = a.x + (b.x - a.x) * frac;
      const py = a.y + (b.y - a.y) * frac;
      tmDot.setAttribute('cx', px.toFixed(1));
      tmDot.setAttribute('cy', py.toFixed(1));
      tmDot.setAttribute('stroke', hex);
    }

    // Drive compass
    _COMPASS.setDegree(deg, tier);
    _CHARGE.setLevel(tier, 0, 0, deg);
    setCompassDate(formatDate(nearestPt.date));
    setCompassMode(isTodayDate(nearestPt.date) ? 'today' : 'date');
  }

  function dtmAnimate(timestamp) {
    if (!dtmPlaying) return;
    if (!dtmAnimate.lastTime) dtmAnimate.lastTime = timestamp;

    const dt = (timestamp - dtmAnimate.lastTime) / 1000;
    dtmAnimate.lastTime = timestamp;
    const max = dailyChartPoints.length - 1;

    dtmPosition += TM_BASE_SPEED * TM_SPEEDS[dtmSpeedIdx] * dtmDirection * dt;

    if (dtmDirection === 1 && dtmPosition >= max) { dtmPosition = max; dtmStopPlayback(); }
    if (dtmDirection === -1 && dtmPosition <= 0) { dtmPosition = 0; dtmStopPlayback(); }

    updateDailyTimeMachine(dtmPosition);
    if (dtmPlaying) dtmAnimFrame = requestAnimationFrame(dtmAnimate);
  }

  function dtmStartPlayback(dir) {
    saveCompassState();
    dtmDirection = dir;
    const max = dailyChartPoints.length - 1;
    if (dir === 1 && dtmPosition >= max) dtmPosition = 0;
    if (dir === -1 && dtmPosition <= 0) dtmPosition = max;

    dtmPlaying = true;
    dtmAnimate.lastTime = null;

    const needle = document.getElementById('compass-needle');
    if (needle) needle.classList.add('no-transition');

    const playBtn = document.getElementById('daily-timemachine-play');
    const playIcon = document.getElementById('daily-timemachine-play-icon');
    const revBtn = document.getElementById('daily-timemachine-rev');
    const fwdBtn = document.getElementById('daily-timemachine-fwd');
    if (playBtn) playBtn.classList.add('active');
    if (dir === 1 && fwdBtn) fwdBtn.classList.add('active');
    if (dir === -1 && revBtn) revBtn.classList.add('active');
    if (playIcon) playIcon.innerHTML = '<rect fill="currentColor" x="6" y="4" width="4" height="16"/><rect fill="currentColor" x="14" y="4" width="4" height="16"/>';

    dtmAnimFrame = requestAnimationFrame(dtmAnimate);
  }

  function dtmStopPlayback() {
    dtmPlaying = false;
    if (dtmAnimFrame) cancelAnimationFrame(dtmAnimFrame);

    const needle = document.getElementById('compass-needle');
    if (needle) needle.classList.remove('no-transition');

    ['daily-timemachine-play', 'daily-timemachine-rev', 'daily-timemachine-fwd'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.remove('active');
    });
    const playIcon = document.getElementById('daily-timemachine-play-icon');
    if (playIcon) playIcon.innerHTML = '<path fill="currentColor" d="M8 5v14l11-7z"/>';
  }

  function initDailyTimeMachineControls(container) {
    const pts = dailyChartPoints;
    const max = pts.length - 1;
    if (max < 0) return;
    const last = pts[max];

    const tmArea = container.querySelector('.timemachine-controls');
    if (!tmArea) return;

    tmArea.innerHTML = `
      <div class="timemachine-wrap">
        <input type="range" class="timemachine-slider" id="daily-timemachine-slider" min="0" max="${max}" value="${max}" step="1" aria-label="Daily time machine slider" aria-valuetext="${formatDate(last.date)}">
      </div>
      <div class="timemachine-playback">
        <button class="timemachine-play-btn" id="daily-timemachine-rev" title="Play backward" aria-label="Play backward">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M11 18V6l-8.5 6 8.5 6zm.5-6l8.5 6V6l-8.5 6z"/></svg>
        </button>
        <button class="timemachine-play-btn" id="daily-timemachine-play" title="Play forward" aria-label="Play forward">
          <svg viewBox="0 0 24 24" width="14" height="14" id="daily-timemachine-play-icon" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
        </button>
        <button class="timemachine-play-btn" id="daily-timemachine-fwd" title="Play forward fast" aria-label="Play forward fast">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M4 18l8.5-6L4 6v12zm9-12v12l8.5-6L13 6z"/></svg>
        </button>
        <div class="timemachine-progress"><div class="timemachine-progress-fill" id="daily-timemachine-progress" style="width:100%"></div></div>
        <button class="timemachine-speed-btn" id="daily-timemachine-speed" aria-label="Playback speed">1x</button>
        <button class="timemachine-reset" id="daily-timemachine-reset" aria-label="Reset time machine" style="display:none;">Reset</button>
      </div>
    `;

    const slider = document.getElementById('daily-timemachine-slider');
    document.getElementById('daily-timemachine-play').addEventListener('click', () => {
      if (dtmPlaying) { dtmStopPlayback(); return; }
      dtmStartPlayback(1);
    });
    document.getElementById('daily-timemachine-rev').addEventListener('click', () => {
      if (dtmPlaying && dtmDirection === -1) { dtmStopPlayback(); return; }
      dtmStopPlayback();
      dtmStartPlayback(-1);
    });
    document.getElementById('daily-timemachine-fwd').addEventListener('click', () => {
      if (dtmPlaying && dtmDirection === 1) { dtmStopPlayback(); return; }
      dtmStopPlayback();
      dtmStartPlayback(1);
    });
    document.getElementById('daily-timemachine-speed').addEventListener('click', () => {
      dtmSpeedIdx = (dtmSpeedIdx + 1) % TM_SPEEDS.length;
      document.getElementById('daily-timemachine-speed').textContent = TM_SPEEDS[dtmSpeedIdx] + 'x';
    });

    // Slider scrub
    slider.addEventListener('mousedown', () => {
      const needle = document.getElementById('compass-needle');
      if (needle) needle.classList.add('no-transition');
    });
    slider.addEventListener('touchstart', () => {
      const needle = document.getElementById('compass-needle');
      if (needle) needle.classList.add('no-transition');
    });
    const endScrub = () => {
      const needle = document.getElementById('compass-needle');
      if (needle && !dtmPlaying) needle.classList.remove('no-transition');
    };
    slider.addEventListener('mouseup', endScrub);
    slider.addEventListener('touchend', endScrub);
    slider.addEventListener('input', () => {
      saveCompassState();
      dtmStopPlayback();
      dtmPosition = parseInt(slider.value);
      updateDailyTimeMachine(dtmPosition);
    });
    // Sync ether card on slider release so the API isn't hit on every tick.
    slider.addEventListener('change', () => {
      const pt = dailyChartPoints[Math.round(dtmPosition)];
      if (pt && pt.date && typeof EtherArtChart !== 'undefined') {
        EtherArtChart.render({ mode: 'date', date: pt.date });
      }
    });

    // Reset — restoreCompassState last so the live daily compass wins over
    // updateDailyTimeMachine's position-derived needle update.
    document.getElementById('daily-timemachine-reset').addEventListener('click', () => {
      dtmStopPlayback();
      dtmPosition = max;
      updateDailyTimeMachine(dtmPosition);
      restoreCompassState();
      document.getElementById('daily-timemachine-reset').style.display = 'none';
      if (typeof EtherArtChart !== 'undefined') {
        EtherArtChart.render();
      }
    });
  }
  function renderTrajectoryChart(data, container) {
    if (!data.length) return;
    chartData = data;
    // Fit to the CURRENTLY-ZOOMED window (`data` is the filtered year range),
    // not the all-time set, so the axis top/bottom tighten to max+PAD / min-PAD
    // of the selected segment. A lopsided era (e.g. one sitting entirely below
    // zero) fills the plot instead of hugging one edge under a symmetric axis.
    const dom = resolveDomain(data);
    // Pulse each y-axis bound label only when ITS value changed on this re-fit
    // (top, bottom, or both), so the number change is legible even though the
    // trajectory line barely shifts. Per-edge; skip the first render.
    const hiChanged = lastTrajHi !== null && lastTrajHi !== dom.hi;
    const loChanged = lastTrajLo !== null && lastTrajLo !== dom.lo;
    lastTrajHi = dom.hi; lastTrajLo = dom.lo;

    const H = 120;
    // Same as the daily chart: when the panel is expanded the chart box is much
    // wider but height-locked, so match the viewBox width to the box's pixel
    // aspect to scale horizontally only (no xy stretch). See renderDailyChart.
    let W = 320;
    const lockArea = container.querySelector('.traj-chart-area');
    if (lockArea && container.closest('.traj-chart-locked')) {
      const pxW = lockArea.clientWidth, pxH = lockArea.clientHeight;
      if (pxW > 0 && pxH > 0) W = Math.max(320, Math.round(H * pxW / pxH));
    }
    const padL = 30, padR = 16, padT = 10, padB = 22;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const maxIdx = data.length - 1;

    // Detect if the last year is the current calendar year (YTD)
    const currentCalYear = new Date().getFullYear();
    const hasYTD = data[maxIdx].year === currentCalYear && maxIdx > 0;
    chartHasYTD = hasYTD;

    chartPoints = data.map((d, i) => ({
      x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
      y: chargeDegreeToY(d.compass_degree, padT, chartH, dom),
      degree: d.compass_degree,
      year: d.year,
      color: d.charge_level,
      isYTD: d.year === currentCalYear,
    }));

    // Anchor the vertical color gradient to fixed charge values (+100..-100),
    // UNCLAMPED, so the tier colors stay correct even when the visible band is
    // off-center: green sits at 0 and red at the deep negatives regardless of
    // where the window lands. (In the old symmetric axis these anchors fell on
    // the plot edges, so this is a no-op there and a fix for asymmetric bands.)
    const gy = (c) => padT + ((dom.hi - c) / (dom.hi - dom.lo)) * chartH;
    const gyTop = gy(100).toFixed(1), gyBot = gy(-100).toFixed(1);

    // Build line paths — split into solid (completed years) and dashed (YTD segment)
    const solidPoints = hasYTD ? chartPoints.slice(0, -1) : chartPoints;
    const solidPath = solidPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const fullLinePath = chartPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const areaPath = fullLinePath + ` L ${chartPoints[maxIdx].x.toFixed(1)} ${padT + chartH} L ${chartPoints[0].x.toFixed(1)} ${padT + chartH} Z`;

    let ytdDashPath = '';
    if (hasYTD) {
      const prev = chartPoints[maxIdx - 1];
      const last = chartPoints[maxIdx];
      ytdDashPath = `M ${prev.x.toFixed(1)} ${prev.y.toFixed(1)} L ${last.x.toFixed(1)} ${last.y.toFixed(1)}`;
    }

    let svg = `<svg class="trajectory-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Charge trajectory chart">`;
    svg += `<defs>
      <linearGradient id="traj-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${gyTop}" x2="0" y2="${gyBot}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" />
        <stop offset="25%" stop-color="${COLOR_HEX.blue}" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" />
        <stop offset="75%" stop-color="${COLOR_HEX.orange}" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" />
      </linearGradient>
      <linearGradient id="traj-area-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${gyTop}" x2="0" y2="${gyBot}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" stop-opacity="0.2" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" stop-opacity="0.05" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" stop-opacity="0.2" />
      </linearGradient>
      <clipPath id="traj-clip"><rect id="traj-clip-rect" x="0" y="0" width="${W}" height="${H}" /></clipPath>
    </defs>`;

    // Grid lines
    // Full-height transparent strip over the y-axis gutter -> the whole axis is
    // the scale toggle's tap target (the labels alone are <17px, and "0" is ~5px
    // wide, far below a touch target). Rendered before the labels so the text's
    // own :hover still fires when pointed directly.
    svg += `<rect class="traj-y-hit" x="0" y="0" width="${padL}" height="${H}" fill="transparent" />`;
    chargeGridRows(dom).forEach(({ charge, label }) => {
      const y = padT + chargeToFrac(charge, dom) * chartH;
      svg += `<line class="trajectory-grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" />`;
      const pulse = (charge === dom.hi && hiChanged) || (charge === dom.lo && loChanged);
      if (label) svg += `<text class="trajectory-y-label${pulse ? ' traj-y-pulse' : ''}" x="${padL - 4}" y="${y + 3}"><title>${scaleMode === 'fit' ? 'Click to show the full +/-100 range' : 'Click to auto-fit the range'}</title>${label}</text>`;
    });

    // Clipped line + area
    svg += `<g clip-path="url(#traj-clip)">`;
    svg += `<path class="trajectory-area" d="${areaPath}" fill="url(#traj-area-grad)" />`;
    svg += `<path class="trajectory-line" d="${solidPath}" stroke="url(#traj-grad)" />`;
    if (hasYTD) {
      svg += `<path class="trajectory-line trajectory-line-ytd" d="${ytdDashPath}" stroke="url(#traj-grad)" stroke-dasharray="4 3" opacity="0.6" />`;
    }
    svg += `</g>`;

    // X-axis labels
    const yearSpan = data[maxIdx].year - data[0].year;
    const labelInterval = yearSpan > 40 ? 10 : yearSpan > 15 ? 5 : yearSpan > 8 ? 2 : 1;
    const labelYears = new Set();
    const startDecade = Math.ceil(data[0].year / labelInterval) * labelInterval;
    for (let yr = startDecade; yr <= data[maxIdx].year; yr += labelInterval) labelYears.add(yr);
    labelYears.add(data[0].year);
    labelYears.add(data[maxIdx].year);

    chartPoints.forEach(p => {
      if (labelYears.has(p.year)) {
        const labelText = p.isYTD ? 'YTD' : `'${String(p.year).slice(2)}`;
        svg += `<text class="trajectory-label${p.isYTD ? ' trajectory-label-ytd' : ''}" x="${p.x.toFixed(1)}" y="${H - 4}" text-anchor="middle">${labelText}</text>`;
        svg += `<line x1="${p.x.toFixed(1)}" y1="${padT + chartH}" x2="${p.x.toFixed(1)}" y2="${padT + chartH + 4}" stroke="var(--rc-text-dim)" stroke-width="0.5" opacity="0.5" />`;
      }
    });

    // YTD open-ring dot (static, pulsing)
    if (hasYTD) {
      const ytdPt = chartPoints[maxIdx];
      const ytdHex = COLOR_HEX[ytdPt.color] || '#888';
      svg += `<circle class="trajectory-ytd-ring" cx="${ytdPt.x.toFixed(1)}" cy="${ytdPt.y.toFixed(1)}" r="4" fill="none" stroke="${ytdHex}" stroke-width="1.5" opacity="0.7" />`;
      svg += `<circle class="trajectory-ytd-pulse" cx="${ytdPt.x.toFixed(1)}" cy="${ytdPt.y.toFixed(1)}" r="4" fill="none" stroke="${ytdHex}" stroke-width="1" />`;
    }

    // Moving dot (time machine position indicator)
    const lastPt = chartPoints[maxIdx];
    svg += `<circle id="traj-timemachine-dot" class="trajectory-dot" cx="${lastPt.x.toFixed(1)}" cy="${lastPt.y.toFixed(1)}" fill="var(--rc-bg-dark)" stroke="${COLOR_HEX[lastPt.color] || '#888'}" />`;

    // Hover elements
    svg += `<line id="traj-hover-line" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" class="traj-hover-line" style="display:none" />`;
    svg += `<circle id="traj-hover-dot" cx="0" cy="0" class="traj-hover-dot" style="display:none" />`;
    svg += `<rect x="${padL}" y="${padT}" width="${chartW}" height="${chartH}" fill="transparent" class="traj-hover-area" />`;
    svg += '</svg>';

    const chartEl = container.querySelector('.traj-chart-area');
    chartEl.innerHTML = `<div class="traj-wrap">${svg}<div class="traj-tooltip" id="traj-tooltip"></div></div>`;

    // Hover interaction
    const wrap = chartEl.querySelector('.traj-wrap');
    const svgEl = chartEl.querySelector('.trajectory-svg');

    function showHoverAt(clientX) {
      if (tmPlaying) return;
      const hoverLine = document.getElementById('traj-hover-line');
      const hoverDot = document.getElementById('traj-hover-dot');
      const tooltip = document.getElementById('traj-tooltip');
      if (!hoverLine) return;

      const rect = svgEl.getBoundingClientRect();
      const relX = (clientX - rect.left) / rect.width;
      const svgX = relX * W;

      let nearest = 0, minDist = Infinity;
      for (let i = 0; i <= maxIdx; i++) {
        const dist = Math.abs(chartPoints[i].x - svgX);
        if (dist < minDist) { minDist = dist; nearest = i; }
      }

      const p = chartPoints[nearest];
      const d = chartData[nearest];
      const hex = COLOR_HEX[p.color] || '#888';

      hoverLine.setAttribute('x1', p.x.toFixed(1));
      hoverLine.setAttribute('x2', p.x.toFixed(1));
      hoverLine.style.display = '';
      hoverDot.setAttribute('cx', p.x.toFixed(1));
      hoverDot.setAttribute('cy', p.y.toFixed(1));
      hoverDot.setAttribute('stroke', hex);
      hoverDot.style.display = '';

      const yearLabel = p.isYTD ? `${d.year} YTD` : String(d.year);
      const songsMeta = p.isYTD ? `${d.chart_song_count} songs \u00B7 updated daily` : `${d.chart_song_count} charting songs`;
      const wrapRect = wrap.getBoundingClientRect();
      const pixelX = clientX - wrapRect.left;
      const wrapW = wrapRect.width;
      tooltip.style.left = pixelX + 'px';
      tooltip.style.transform = pixelX > wrapW * 0.7 ? 'translateX(-100%)' : pixelX < wrapW * 0.3 ? 'translateX(0)' : 'translateX(-50%)';
      tooltip.innerHTML = `<strong>${yearLabel}</strong> <span style="color:${hex}">${degreeToScore(p.degree)}</span> ${CHARGE_LABELS[p.color]}<br><span class="traj-tooltip-sub">${songsMeta}</span>`;
      tooltip.style.display = 'block';
    }
    function hideHover() {
      const hoverLine = document.getElementById('traj-hover-line');
      const hoverDot = document.getElementById('traj-hover-dot');
      const tooltip = document.getElementById('traj-tooltip');
      if (hoverLine) hoverLine.style.display = 'none';
      if (hoverDot) hoverDot.style.display = 'none';
      if (tooltip) tooltip.style.display = 'none';
    }

    wrap.addEventListener('mousemove', (e) => {
      // Only show the tooltip inside the plot box -- not over the y-label
      // gutters or the margins left/right of the data.
      const r = svgEl.getBoundingClientRect();
      const inX = e.clientX >= r.left + (padL / W) * r.width && e.clientX <= r.left + ((W - padR) / W) * r.width;
      const inY = e.clientY >= r.top + (padT / H) * r.height && e.clientY <= r.top + ((padT + chartH) / H) * r.height;
      if (!inX || !inY) { hideHover(); return; }
      showHoverAt(e.clientX);
    });
    wrap.addEventListener('mouseleave', hideHover);

    // Touch: single-finger drag on the chart body scrubs the tooltip across the
    // points (read values), no tap-lift-tap. stopPropagation keeps the body's
    // pan handler (.traj-chart-area) from firing -- pan/zoom stays on the overview
    // mini-map + two-finger pinch. A tap (no move -> no preventDefault) still
    // falls through to the click handler to set the compass.
    let scrubHideTO = null, scrubbing = false;
    // Only start a scrub when the finger lands on the PLOT area, not the x-axis
    // label row (months/years) below it -- so the labels stay scroll-safe.
    const inPlot = (clientY) => {
      const rct = svgEl.getBoundingClientRect();
      const top = rct.top + (padT / H) * rct.height;
      const bot = rct.top + ((padT + chartH) / H) * rct.height;
      return clientY >= top && clientY <= bot;
    };
    wrap.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1 || !inPlot(e.touches[0].clientY)) { scrubbing = false; return; }
      scrubbing = true;
      e.stopPropagation();
      clearTimeout(scrubHideTO);
      showHoverAt(e.touches[0].clientX);
    }, { passive: true });
    wrap.addEventListener('touchmove', (e) => {
      if (!scrubbing || e.touches.length !== 1) return;
      e.stopPropagation();
      e.preventDefault();
      showHoverAt(e.touches[0].clientX);
    }, { passive: false });
    const endScrub = () => { if (!scrubbing) return; scrubbing = false; clearTimeout(scrubHideTO); scrubHideTO = setTimeout(hideHover, 1500); };
    wrap.addEventListener('touchend', endScrub);
    wrap.addEventListener('touchcancel', endScrub);

    // Click: move compass + charge bar to clicked year, load songs
    wrap.addEventListener('click', (e) => {
      const rect = svgEl.getBoundingClientRect();
      const relX = (e.clientX - rect.left) / rect.width;
      const svgX = relX * W;

      let nearest = 0, minDist = Infinity;
      for (let i = 0; i <= maxIdx; i++) {
        const dist = Math.abs(chartPoints[i].x - svgX);
        if (dist < minDist) { minDist = dist; nearest = i; }
      }

      const p = chartPoints[nearest];
      const d = chartData[nearest];
      setCompassDate(p.isYTD ? `${d.year} YTD` : String(d.year));
      setCompassMode('year', { year: d.year, isYTD: p.isYTD });
      _COMPASS.setDegree(p.degree, p.color);
      _CHARGE.setLevel(p.color, 0, 0, p.degree);

      _onYearSelect(d.year, p.degree, p.color);
      _syncCalendar(d.year);
      if (typeof EtherArtChart !== 'undefined') {
        EtherArtChart.render({ mode: 'year', year: d.year });
      }

      const dot = document.getElementById('traj-hover-dot');
      if (dot) { dot.classList.add('click-pulse'); setTimeout(() => dot.classList.remove('click-pulse'), 100); }
      pinchLine(chartPoints, nearest, svgEl.querySelector('.trajectory-line'), svgEl.querySelector('.trajectory-area'), padT, chartH, chartHasYTD);
    });

    // Set initial TM position to end
    tmPosition = maxIdx;
  }

  // --- Time Machine (drives trajectory clip + compass) ---
  function updateTimeMachine(pos) {
    const max = chartData.length - 1;
    if (max < 0) return;

    const i = Math.floor(pos);
    const frac = pos - i;
    const a = chartData[Math.min(i, max)];
    const b = chartData[Math.min(i + 1, max)];

    const deg = a.compass_degree + (b.compass_degree - a.compass_degree) * frac;
    const tier = degreeToTier(deg);
    const hex = COLOR_HEX[tier] || '#888';
    const nearest = chartData[Math.round(pos)];

    // Update info display
    const slider = document.getElementById('timemachine-slider');
    const progressFill = document.getElementById('timemachine-progress');
    const resetBtn = document.getElementById('timemachine-reset');

    const nearestPt = chartPoints[Math.round(Math.min(pos, max))];
    const isYTD = nearestPt && nearestPt.isYTD;
    if (progressFill) progressFill.style.width = (pos / max * 100) + '%';
    if (slider) {
      slider.value = Math.round(pos);
      slider.setAttribute('aria-valuetext', `${nearest.year}, ${CHARGE_LABELS[tier]}`);
    }
    if (resetBtn) resetBtn.style.display = '';

    // Clip trajectory chart to current position
    const clipRect = document.getElementById('traj-clip-rect');
    if (clipRect && chartPoints.length) {
      // Interpolate x position
      const px = chartPoints[Math.min(i, max)].x + (chartPoints[Math.min(i + 1, max)].x - chartPoints[Math.min(i, max)].x) * frac;
      clipRect.setAttribute('width', px + 2);
    }

    // Move the dot to current position
    const tmDot = document.getElementById('traj-timemachine-dot');
    if (tmDot && chartPoints.length) {
      const px = chartPoints[Math.min(i, max)].x + (chartPoints[Math.min(i + 1, max)].x - chartPoints[Math.min(i, max)].x) * frac;
      const py = chartPoints[Math.min(i, max)].y + (chartPoints[Math.min(i + 1, max)].y - chartPoints[Math.min(i, max)].y) * frac;
      tmDot.setAttribute('cx', px.toFixed(1));
      tmDot.setAttribute('cy', py.toFixed(1));
      tmDot.setAttribute('stroke', hex);
    }

    // Drive compass + charge + date
    _COMPASS.setDegree(deg, tier);
    _CHARGE.setLevel(tier, 0, 0, deg);
    setCompassDate(isYTD ? `${nearest.year} YTD` : String(nearest.year));
    setCompassMode('year', { year: nearest.year, isYTD });
  }

  function tmAnimate(timestamp) {
    if (!tmPlaying) return;
    if (!tmAnimate.lastTime) tmAnimate.lastTime = timestamp;

    const dt = (timestamp - tmAnimate.lastTime) / 1000;
    tmAnimate.lastTime = timestamp;
    const max = chartData.length - 1;

    tmPosition += TM_BASE_SPEED * TM_SPEEDS[tmSpeedIdx] * tmDirection * dt;

    if (tmDirection === 1 && tmPosition >= max) { tmPosition = max; tmStopPlayback(); }
    if (tmDirection === -1 && tmPosition <= 0) { tmPosition = 0; tmStopPlayback(); }

    updateTimeMachine(tmPosition);
    if (tmPlaying) tmAnimFrame = requestAnimationFrame(tmAnimate);
  }

  function tmStartPlayback(dir) {
    saveCompassState();

    tmDirection = dir;
    const max = chartData.length - 1;
    if (dir === 1 && tmPosition >= max) tmPosition = 0;
    if (dir === -1 && tmPosition <= 0) tmPosition = max;

    tmPlaying = true;
    tmAnimate.lastTime = null;

    const needle = document.getElementById('compass-needle');
    if (needle) needle.classList.add('no-transition');

    const playBtn = document.getElementById('timemachine-play');
    const playIcon = document.getElementById('timemachine-play-icon');
    const revBtn = document.getElementById('timemachine-rev');
    const fwdBtn = document.getElementById('timemachine-fwd');
    if (playBtn) playBtn.classList.add('active');
    if (dir === 1 && fwdBtn) fwdBtn.classList.add('active');
    if (dir === -1 && revBtn) revBtn.classList.add('active');
    if (playIcon) playIcon.innerHTML = '<rect fill="currentColor" x="6" y="4" width="4" height="16"/><rect fill="currentColor" x="14" y="4" width="4" height="16"/>';

    tmAnimFrame = requestAnimationFrame(tmAnimate);
  }

  function tmStopPlayback() {
    tmPlaying = false;
    if (tmAnimFrame) cancelAnimationFrame(tmAnimFrame);

    const needle = document.getElementById('compass-needle');
    if (needle) needle.classList.remove('no-transition');

    ['timemachine-play', 'timemachine-rev', 'timemachine-fwd'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.remove('active');
    });
    const playIcon = document.getElementById('timemachine-play-icon');
    if (playIcon) playIcon.innerHTML = '<path fill="currentColor" d="M8 5v14l11-7z"/>';

  }

  function initTimeMachineControls(container) {
    const max = chartData.length - 1;
    const last = chartData[max];

    const tmArea = container.querySelector('.timemachine-controls');
    if (!tmArea) return;

    tmArea.innerHTML = `
      <div class="timemachine-wrap">
        <input type="range" class="timemachine-slider" id="timemachine-slider" min="0" max="${max}" value="${max}" step="1" aria-label="Time machine year slider" aria-valuetext="${last.year}">
      </div>
      <div class="timemachine-playback">
        <button class="timemachine-play-btn" id="timemachine-rev" title="Play backward" aria-label="Play backward">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M11 18V6l-8.5 6 8.5 6zm.5-6l8.5 6V6l-8.5 6z"/></svg>
        </button>
        <button class="timemachine-play-btn" id="timemachine-play" title="Play forward" aria-label="Play forward">
          <svg viewBox="0 0 24 24" width="14" height="14" id="timemachine-play-icon" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
        </button>
        <button class="timemachine-play-btn" id="timemachine-fwd" title="Play forward fast" aria-label="Play forward fast">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M4 18l8.5-6L4 6v12zm9-12v12l8.5-6L13 6z"/></svg>
        </button>
        <div class="timemachine-progress"><div class="timemachine-progress-fill" id="timemachine-progress" style="width:100%"></div></div>
        <button class="timemachine-speed-btn" id="timemachine-speed" aria-label="Playback speed">1x</button>
        <button class="timemachine-reset" id="timemachine-reset" aria-label="Reset time machine" style="display:none;">Reset</button>
      </div>
    `;

    // Wire up controls
    const slider = document.getElementById('timemachine-slider');
    document.getElementById('timemachine-play').addEventListener('click', () => {
      if (tmPlaying) { tmStopPlayback(); return; }
      tmStartPlayback(1);
    });
    document.getElementById('timemachine-rev').addEventListener('click', () => {
      if (tmPlaying && tmDirection === -1) { tmStopPlayback(); return; }
      tmStopPlayback();
      tmStartPlayback(-1);
    });
    document.getElementById('timemachine-fwd').addEventListener('click', () => {
      if (tmPlaying && tmDirection === 1) { tmStopPlayback(); return; }
      tmStopPlayback();
      tmStartPlayback(1);
    });
    document.getElementById('timemachine-speed').addEventListener('click', () => {
      tmSpeedIdx = (tmSpeedIdx + 1) % TM_SPEEDS.length;
      document.getElementById('timemachine-speed').textContent = TM_SPEEDS[tmSpeedIdx] + 'x';
    });

    // Slider scrub
    slider.addEventListener('mousedown', () => {
      const needle = document.getElementById('compass-needle');
      if (needle) needle.classList.add('no-transition');
    });
    slider.addEventListener('touchstart', () => {
      const needle = document.getElementById('compass-needle');
      if (needle) needle.classList.add('no-transition');
    });
    const endScrub = () => {
      const needle = document.getElementById('compass-needle');
      if (needle && !tmPlaying) needle.classList.remove('no-transition');
    };
    slider.addEventListener('mouseup', endScrub);
    slider.addEventListener('touchend', endScrub);
    slider.addEventListener('input', () => {
      saveCompassState();
      tmStopPlayback();
      tmPosition = parseInt(slider.value);
      updateTimeMachine(tmPosition);
    });
    // Sync ether card on slider release so the API isn't hit on every tick.
    slider.addEventListener('change', () => {
      const yr = chartData[Math.round(tmPosition)];
      if (yr && typeof EtherArtChart !== 'undefined') {
        EtherArtChart.render({ mode: 'year', year: yr.year });
      }
    });

    // Reset — restoreCompassState last so the saved (live) compass wins over
    // updateTimeMachine's position-derived needle update.
    document.getElementById('timemachine-reset').addEventListener('click', () => {
      tmStopPlayback();
      tmPosition = max;
      updateTimeMachine(tmPosition);
      restoreCompassState();
      document.getElementById('timemachine-reset').style.display = 'none';
      if (typeof EtherArtChart !== 'undefined') {
        EtherArtChart.render();
      }
    });

  }

  function matchedPreset() {
    if (!allYearData.length) return null;
    const firstYear = allYearData[0].year;
    const lastYear = allYearData[allYearData.length - 1].year;
    if (zoomStartYear === firstYear && zoomEndYear === lastYear) return 'all';
    if (zoomEndYear !== lastYear) return null;
    const span = lastYear - zoomStartYear + 1;
    if (span === 30) return '30';
    if (span === 20) return '20';
    if (span === 10) return '10';
    return null;
  }

  function snapToPreset(zoom) {
    const firstYear = allYearData[0].year;
    const lastYear = allYearData[allYearData.length - 1].year;
    if (zoom === 'all') zoomStartYear = firstYear;
    else if (zoom === '30') zoomStartYear = Math.max(firstYear, lastYear - 29);
    else if (zoom === '20') zoomStartYear = Math.max(firstYear, lastYear - 19);
    else if (zoom === '10') zoomStartYear = Math.max(firstYear, lastYear - 9);
    zoomEndYear = lastYear;
  }

  function updateZoomWindowLabel(container) {
    const lbl = container.querySelector('.traj-zoom-window');
    if (lbl) lbl.textContent = `${zoomStartYear} – ${zoomEndYear}`;
    const active = matchedPreset();
    container.querySelectorAll('.traj-zoom-presets .traj-zoom-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.zoom === active);
    });
    updateOverviewViewport(container);
  }

  // --- Ableton-style overview locator ---
  // Static mini-trajectory of the FULL year range with a draggable viewport
  // brace on top showing the currently-zoomed window. Drag the body to pan;
  // drag either edge to zoom; click empty space to jump the viewport.
  function renderOverview(container) {
    const overviewEl = container.querySelector('.traj-overview');
    if (!overviewEl || !allYearData.length) return;
    const W = 320, H = 28;
    const padT = 4, padB = 4;
    const chartH = H - padT - padB;
    const data = allYearData;
    const dom = resolveDomain(data);
    const maxIdx = data.length - 1;
    const pts = data.map((d, i) => ({
      x: maxIdx > 0 ? (i / maxIdx) * W : W / 2,
      y: chargeDegreeToY(d.compass_degree, padT, chartH, dom),
    }));
    const linePath = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    overviewEl.innerHTML = `
      <svg class="traj-overview-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
        <path class="traj-overview-line" d="${linePath}" fill="none" stroke-width="1"/>
      </svg>
      <div class="traj-overview-viewport" data-handle="pan">
        <div class="traj-overview-handle traj-overview-handle--left" data-handle="left" aria-label="Drag to set start year"></div>
        <div class="traj-overview-handle traj-overview-handle--right" data-handle="right" aria-label="Drag to set end year"></div>
        <div class="traj-overview-grip" aria-hidden="true"></div>
      </div>`;
  }

  function updateOverviewViewport(container) {
    const overviewEl = container.querySelector('.traj-overview');
    if (!overviewEl || !allYearData.length) return;
    const vp = overviewEl.querySelector('.traj-overview-viewport');
    if (!vp) return;
    const firstYear = allYearData[0].year;
    const lastYear = allYearData[allYearData.length - 1].year;
    const totalSpan = Math.max(1, lastYear - firstYear);
    const leftPct = ((zoomStartYear - firstYear) / totalSpan) * 100;
    const widthPct = Math.max(2, ((zoomEndYear - zoomStartYear) / totalSpan) * 100);
    vp.style.left = leftPct + '%';
    vp.style.width = widthPct + '%';
  }

  function initOverviewControls(container) {
    const overviewEl = container.querySelector('.traj-overview');
    if (!overviewEl) return;

    let drag = null;
    let rafPending = false;
    const scheduleRender = () => {
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        applyZoomChartOnly(container);
      });
    };

    const overviewRect = () => overviewEl.getBoundingClientRect();
    const yearAtX = (clientX) => {
      const rect = overviewRect();
      const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const firstYear = allYearData[0].year;
      const lastYear = allYearData[allYearData.length - 1].year;
      return Math.round(firstYear + pct * (lastYear - firstYear));
    };

    const start = (handle, clientX) => {
      drag = {
        handle,
        startX: clientX,
        startZoom: { s: zoomStartYear, e: zoomEndYear },
        width: overviewRect().width || 1,
      };
      overviewEl.classList.add('is-active');
    };

    const move = (clientX) => {
      if (!drag) return;
      const firstYear = allYearData[0].year;
      const lastYear = allYearData[allYearData.length - 1].year;
      const totalSpan = Math.max(1, lastYear - firstYear);
      const pxPerYear = drag.width / totalSpan;
      const yearDelta = Math.round((clientX - drag.startX) / pxPerYear);

      let newStart = drag.startZoom.s;
      let newEnd = drag.startZoom.e;

      if (drag.handle === 'pan') {
        const span = newEnd - newStart;
        newStart += yearDelta;
        newEnd = newStart + span;
        if (newStart < firstYear) { newStart = firstYear; newEnd = newStart + span; }
        if (newEnd > lastYear) { newEnd = lastYear; newStart = newEnd - span; }
      } else if (drag.handle === 'left') {
        newStart = Math.max(firstYear, Math.min(newEnd - 1, newStart + yearDelta));
      } else if (drag.handle === 'right') {
        newEnd = Math.min(lastYear, Math.max(newStart + 1, newEnd + yearDelta));
      }

      if (newStart !== zoomStartYear || newEnd !== zoomEndYear) {
        zoomStartYear = newStart;
        zoomEndYear = newEnd;
        scheduleRender();
      }
    };

    const end = () => {
      const wasDrag = !!drag;
      drag = null;
      overviewEl.classList.remove('is-active');
      if (wasDrag) {
        tmStopPlayback();
        initTimeMachineControls(container);
      }
    };

    overviewEl.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      const el = e.target.closest('[data-handle]');
      if (el) {
        start(el.dataset.handle, e.clientX);
      } else {
        // Empty overview area: jump viewport center to click position, keep span.
        const targetCenter = yearAtX(e.clientX);
        const firstYear = allYearData[0].year;
        const lastYear = allYearData[allYearData.length - 1].year;
        const span = zoomEndYear - zoomStartYear;
        let ns = targetCenter - Math.floor(span / 2);
        let ne = ns + span;
        if (ns < firstYear) { ns = firstYear; ne = ns + span; }
        if (ne > lastYear) { ne = lastYear; ns = ne - span; }
        zoomStartYear = ns;
        zoomEndYear = ne;
        applyZoom(container);
      }
      e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => {
      if (drag) move(e.clientX);
    });
    window.addEventListener('mouseup', () => {
      if (drag) end();
    });

    overviewEl.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      const tgt = document.elementFromPoint(t.clientX, t.clientY);
      const el = tgt && tgt.closest('[data-handle]');
      if (el) start(el.dataset.handle, t.clientX);
    }, { passive: true });
    overviewEl.addEventListener('touchmove', (e) => {
      if (!drag || e.touches.length !== 1) return;
      move(e.touches[0].clientX);
      e.preventDefault();
    }, { passive: false });
    overviewEl.addEventListener('touchend', () => { if (drag) end(); });
    overviewEl.addEventListener('touchcancel', () => { if (drag) end(); });
  }

  // Re-renders the SVG only — used during pan/zoom drag so we don't tear
  // down the time-machine slider on every tick.
  function applyZoomChartOnly(container) {
    const filtered = allYearData.filter(d => d.year >= zoomStartYear && d.year <= zoomEndYear);
    if (!filtered.length) return;
    renderTrajectoryChart(filtered, container);
    updateZoomWindowLabel(container);
  }

  function applyZoom(container) {
    const filtered = allYearData.filter(d => d.year >= zoomStartYear && d.year <= zoomEndYear);
    if (!filtered.length) return;
    tmStopPlayback();
    renderTrajectoryChart(filtered, container);
    initTimeMachineControls(container);
    updateZoomWindowLabel(container);
  }

  // Drag the chart itself: horizontal pans the year window, vertical
  // zooms (up = in, down = out). Threshold of 4px distinguishes a click
  // from a drag so the existing year-select click handler still works.
  function initChartPanZoom(container) {
    const chartArea = container.querySelector('.traj-chart-area');
    if (!chartArea) return;

    // A pan must START inside the actual plot box (the transparent
    // .traj-hover-area rect), not the surrounding axis-label gutters/margins.
    const insidePlot = (clientX, clientY) => {
      const r = chartArea.querySelector('.traj-hover-area');
      if (!r) return true;
      const b = r.getBoundingClientRect();
      return clientX >= b.left && clientX <= b.right && clientY >= b.top && clientY <= b.bottom;
    };

    let drag = null;
    let rafPending = false;
    const scheduleRender = () => {
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        applyZoomChartOnly(container);
      });
    };

    const start = (clientX, clientY) => {
      drag = {
        x: clientX, y: clientY,
        startZoom: { s: zoomStartYear, e: zoomEndYear },
        width: chartArea.getBoundingClientRect().width || 1,
        moved: false,
      };
      chartArea.classList.add('traj-dragging');
    };

    const move = (clientX, clientY) => {
      if (!drag) return;
      const dx = clientX - drag.x;
      const dy = clientY - drag.y;
      if (!drag.moved && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) {
        drag.moved = true;
      }
      if (!drag.moved) return;

      const firstYear = allYearData[0].year;
      const lastYear = allYearData[allYearData.length - 1].year;
      const initSpan = drag.startZoom.e - drag.startZoom.s + 1;
      const pxPerYear = drag.width / initSpan;

      // Drag right → window shifts left to reveal earlier years.
      const yearShift = -dx / pxPerYear;
      // Drag up (negative dy) zooms in. 200px = 2x zoom step.
      const zoomFactor = Math.pow(2, dy / 200);
      const targetSpan = Math.max(1, Math.round(initSpan * zoomFactor));

      const center = (drag.startZoom.s + drag.startZoom.e) / 2 + yearShift;
      let newStart = Math.round(center - targetSpan / 2);
      let newEnd = newStart + targetSpan - 1;

      // Clamp to overall available range, preserving span where possible.
      if (newStart < firstYear) {
        newStart = firstYear;
        newEnd = Math.min(lastYear, newStart + targetSpan - 1);
      }
      if (newEnd > lastYear) {
        newEnd = lastYear;
        newStart = Math.max(firstYear, newEnd - targetSpan + 1);
      }
      newStart = Math.max(firstYear, newStart);
      newEnd = Math.min(lastYear, newEnd);

      if (newStart !== zoomStartYear || newEnd !== zoomEndYear) {
        zoomStartYear = newStart;
        zoomEndYear = newEnd;
        scheduleRender();
      }
    };

    const end = () => {
      if (!drag) return;
      const wasDrag = drag.moved;
      drag = null;
      chartArea.classList.remove('traj-dragging');
      if (wasDrag) {
        // Suppress the click that follows mouseup so we don't select a year.
        chartArea.addEventListener('click', (e) => {
          e.stopPropagation();
          e.preventDefault();
        }, { once: true, capture: true });
        // Re-attach the time-machine slider against the final zoom window.
        tmStopPlayback();
        initTimeMachineControls(container);
      }
    };

    chartArea.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (!insidePlot(e.clientX, e.clientY)) return;
      start(e.clientX, e.clientY);
      e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => {
      if (drag) move(e.clientX, e.clientY);
    });
    window.addEventListener('mouseup', () => {
      if (drag) end();
    });

    // --- Pinch zoom (two fingers) ---
    // Anchor the year under the pinch midpoint so pinch-out zooms toward
    // that point. 8px distance-change threshold filters jitter so a
    // resting two-finger touch doesn't drift the zoom.
    let pinch = null;
    const distance = (t0, t1) => Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
    const midX = (t0, t1) => (t0.clientX + t1.clientX) / 2;

    const startPinch = (t0, t1) => {
      const rect = chartArea.getBoundingClientRect();
      const mx = midX(t0, t1);
      const pctX = Math.max(0, Math.min(1, (mx - rect.left) / rect.width));
      const span = zoomEndYear - zoomStartYear + 1;
      pinch = {
        d0: distance(t0, t1) || 1,
        startMidX: mx,
        anchorYear: zoomStartYear + pctX * (span - 1),
        pctX,
        startSpan: span,
        rectWidth: rect.width || 1,
      };
      // Pinch wins over any in-progress single-finger drag.
      if (drag) {
        drag = null;
        chartArea.classList.remove('traj-dragging');
      }
    };

    const movePinch = (t0, t1) => {
      if (!pinch) return;
      const d = distance(t0, t1);
      if (Math.abs(d - pinch.d0) < 8) return;
      const ratio = Math.max(0.05, Math.min(20, d / pinch.d0));
      const firstYear = allYearData[0].year;
      const lastYear = allYearData[allYearData.length - 1].year;
      const newSpan = Math.max(1, Math.round(pinch.startSpan / ratio));
      // Anchor year stays at pctX of the new span (map-style zoom).
      let newStart = Math.round(pinch.anchorYear - pinch.pctX * (newSpan - 1));
      // Centroid drift during pinch = pan.
      const mx = midX(t0, t1);
      const pxPerYearAtStart = pinch.rectWidth / pinch.startSpan;
      const yearShift = Math.round(-(mx - pinch.startMidX) / pxPerYearAtStart);
      newStart += yearShift;
      let newEnd = newStart + newSpan - 1;
      if (newStart < firstYear) { newStart = firstYear; newEnd = Math.min(lastYear, newStart + newSpan - 1); }
      if (newEnd > lastYear) { newEnd = lastYear; newStart = Math.max(firstYear, newEnd - newSpan + 1); }
      newStart = Math.max(firstYear, newStart);
      newEnd = Math.min(lastYear, newEnd);
      if (newStart !== zoomStartYear || newEnd !== zoomEndYear) {
        zoomStartYear = newStart;
        zoomEndYear = newEnd;
        scheduleRender();
      }
    };

    const endPinch = () => {
      if (!pinch) return;
      pinch = null;
      tmStopPlayback();
      initTimeMachineControls(container);
    };

    chartArea.addEventListener('touchstart', (e) => {
      if (e.touches.length === 1) {
        const t = e.touches[0];
        if (!insidePlot(t.clientX, t.clientY)) return;
        start(t.clientX, t.clientY);
      } else if (e.touches.length === 2) {
        startPinch(e.touches[0], e.touches[1]);
      }
    }, { passive: true });
    chartArea.addEventListener('touchmove', (e) => {
      if (pinch && e.touches.length === 2) {
        movePinch(e.touches[0], e.touches[1]);
        e.preventDefault();
        return;
      }
      if (drag && e.touches.length === 1) {
        const t = e.touches[0];
        // Touch single-finger: pan only. Pass start Y so dy is 0 — vertical
        // drag is left to the page (touch-action: pan-y), so the user can
        // still scroll past the chart with a vertical swipe.
        move(t.clientX, drag.y);
        if (drag.moved) e.preventDefault();
      }
    }, { passive: false });
    chartArea.addEventListener('touchend', (e) => {
      if (pinch && e.touches.length < 2) endPinch();
      if (drag && e.touches.length === 0) end();
    });
    chartArea.addEventListener('touchcancel', () => {
      if (pinch) endPinch();
      if (drag) end();
    });
  }

  async function loadTrajectory() {
    const container = document.getElementById('trajectory-container');
    if (!container) return;

    container.innerHTML = `
      <div class="trajectory-loading" role="status" aria-label="Loading historical trajectory">
        <svg class="rc-loader" viewBox="0 0 64 64" aria-hidden="true">
          <defs>
            <linearGradient id="rc-loader-grad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stop-color="#9933ff"/>
              <stop offset="25%" stop-color="#3388ff"/>
              <stop offset="50%" stop-color="#33cc55"/>
              <stop offset="75%" stop-color="#ffbb33"/>
              <stop offset="100%" stop-color="#ff3333"/>
            </linearGradient>
          </defs>
          <circle class="rc-loader-track" cx="32" cy="32" r="26" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="3"/>
          <circle class="rc-loader-arc" cx="32" cy="32" r="26" fill="none" stroke="url(#rc-loader-grad)" stroke-width="3" stroke-linecap="round" stroke-dasharray="60 200"/>
          <line class="rc-loader-needle" x1="32" y1="32" x2="32" y2="12" stroke="#eeeef4" stroke-width="2" stroke-linecap="round" transform-origin="32 32"/>
          <circle cx="32" cy="32" r="3" fill="#00d4aa"/>
        </svg>
        <div class="rc-loader-label">Loading historical</div>
        <div class="rc-loader-sub">tuning the compass…</div>
      </div>`;

    try {
      const yearData = await _loadHistorical();

      if (!yearData.length) {
        container.innerHTML = '<p style="color:var(--rc-text-dim);font-size:0.8rem;">No historical data</p>';
        return;
      }

      allYearData = yearData;
      const firstYear = allYearData[0].year;
      const lastYear = allYearData[allYearData.length - 1].year;
      zoomStartYear = firstYear;
      zoomEndYear = lastYear;

      container.innerHTML = `
        <div class="traj-zoom-bar">
          <span class="traj-zoom-window" aria-live="polite">${firstYear} – ${lastYear}</span>
          <span class="traj-source-tag" title="The Historical Charge Index reads the Billboard Hot 100 record; it does not follow a chart toggle">Billboard Hot 100</span>
          <div class="traj-zoom-presets">
            <button class="traj-zoom-btn active" data-zoom="all">All</button>
            <button class="traj-zoom-btn" data-zoom="30">30Y</button>
            <button class="traj-zoom-btn" data-zoom="20">20Y</button>
            <button class="traj-zoom-btn" data-zoom="10">10Y</button>
          </div>
          <div class="traj-overview" role="slider" aria-label="Year range locator: drag the box to pan, drag the edges to zoom" tabindex="-1"></div>
        </div>
        <div class="traj-chart-area"></div>
        <button class="traj-tm-toggle" type="button" aria-expanded="false" aria-controls="traj-tm-drawer">
          <svg class="traj-tm-icon" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
            <circle cx="12" cy="12" r="9.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
            <line class="traj-tm-clock-min" x1="12" y1="12" x2="12" y2="6.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line class="traj-tm-clock-hr" x1="12" y1="12" x2="15.5" y2="13.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <circle cx="12" cy="12" r="1" fill="currentColor"/>
          </svg>
          <span class="traj-tm-label">Time Machine</span>
          <svg class="traj-tm-chevron" viewBox="0 0 24 24" width="10" height="10" aria-hidden="true">
            <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <div class="traj-tm-drawer" id="traj-tm-drawer" inert>
          <div class="traj-tm-drawer-inner">
            <div class="timemachine-controls"></div>
          </div>
        </div>
      `;

      container.querySelectorAll('.traj-zoom-presets .traj-zoom-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          snapToPreset(btn.dataset.zoom);
          applyZoom(container);
        });
      });

      const tmToggle = container.querySelector('.traj-tm-toggle');
      tmToggle.addEventListener('click', () => {
        tmDrawerOpen = !tmDrawerOpen;
        applyTmDrawerState();
      });
      applyTmDrawerState();

      renderOverview(container);
      applyZoom(container);
      initChartPanZoom(container);
      initOverviewControls(container);
    } catch (err) {
      container.innerHTML = '<p style="color:var(--rc-text-dim);font-size:0.8rem;">Could not load trajectory</p>';
    }
  }

    // --- Era tab switching (scoped to this panel) ---
    panelEl.querySelectorAll('.era-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        if (tab.disabled) return;
        // Lock panel height to prevent layout collapse during tab swap.
        panelEl.style.minHeight = panelEl.offsetHeight + 'px';

        panelEl.querySelectorAll('.era-tab').forEach(t => t.classList.remove('active'));
        panelEl.querySelectorAll('.era-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        const target = tab.dataset.era;
        panelEl.querySelector('#era-' + target)?.classList.add('active');

        const tabTitle = tab.querySelector('.era-tab-title');
        if (tabTitle) announce(`Switched to ${tabTitle.textContent}.`);

        if (target === 'daily' && !dailyChartLoaded) {
          loadDailyChart().then(() => { panelEl.style.minHeight = ''; });
        } else {
          requestAnimationFrame(() => { panelEl.style.minHeight = ''; });
        }
      });
    });

    // --- Scale toggle: the charge-axis labels (+N / 0 / -N) ARE the toggle;
    // click any of them (or the full-height gutter hit-strip) to flip auto-fit
    // <-> the full +/-100 domain. Delegated off the stable panel; redraws both. ---
    panelEl.addEventListener('click', (e) => {
      const hit = e.target.closest && e.target.closest('.trajectory-y-label, .traj-y-hit');
      if (!hit) return;
      scaleMode = scaleMode === 'fit' ? 'full' : 'fit';
      rerenderChartsForScale();
    });

    // --- Expand feature: trajectory-expand.js fires this after it changes the
    // panel width, so the visible chart re-renders at the new width (recomputed
    // viewBox = undistorted) instead of being CSS-stretched. ---
    window.addEventListener('rc:trajectory-resized', () => {
      const locked = panelEl.classList.contains('traj-chart-locked');
      if (locked) {
        const histActive = panelEl.querySelector('#era-historical')?.classList.contains('active');
        if (histActive) { if (histContainer && chartData.length) applyZoomChartOnly(histContainer); }
        else { if (dailyContainer && dailyChartLoaded) applyDailyZoomChartOnly(dailyContainer); }
      } else {
        if (dailyContainer && dailyChartLoaded) applyDailyZoomChartOnly(dailyContainer);
        if (histContainer && chartData.length) applyZoomChartOnly(histContainer);
      }
    });

    // --- Kick off data loads ---
    loadDailyChart();
    if (_loadHistorical) loadTrajectory();

    return {
      panel: panelEl,
      reloadDaily: function () { dailyChartLoaded = false; return loadDailyChart(); },
    };
  }

  window.DailyChargePanel = {
    mount: function (panelEl, opts) {
      if (!panelEl) return null;
      return createPanel(panelEl, opts || {});
    }
  };
})();
