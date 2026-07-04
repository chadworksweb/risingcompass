/* === Main App Logic === */

function announce(msg) {
  const el = document.getElementById('sr-announce');
  if (!el) return;
  el.textContent = '';
  requestAnimationFrame(() => { el.textContent = msg; });
}

// Crossfade: fade out element, swap content, fade in
function crossfade(el, newHtml, callback) {
  el.style.transition = 'opacity 0.15s ease';
  void el.offsetHeight;          // force reflow so browser registers the transition
  el.style.opacity = '0';
  setTimeout(() => {
    el.innerHTML = newHtml;
    if (callback) callback();
    void el.offsetHeight;        // force reflow before fade-in
    el.style.opacity = '1';
  }, 160);
}

const App = (() => {
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

  // Daily-chart anomaly markers, loaded from the admin-managed API and keyed by
  // reading date (YYYY-MM-DD). Each value is an array -- a date can carry more
  // than one marker. Major album releases flood the charts and produce the
  // visible spikes; the marker + tooltip note explains the deviation. Managed
  // in Site Admin -> System -> Chart Anomalies.
  let CHART_ANOMALIES = {};

  async function loadChartAnomalies() {
    try {
      const rows = await API.getChartAnomalies();
      const map = {};
      (rows || []).forEach(r => {
        if (!r.date) return;
        (map[r.date] = map[r.date] || []).push(r);
      });
      CHART_ANOMALIES = map;
    } catch (e) {
      CHART_ANOMALIES = {};  // chart still renders without markers
    }
  }

  function anomalyLabel(a) {
    if (a.anomaly_type === 'album_release') {
      const named = [a.artist, a.album].filter(Boolean).join(' - ');
      return named || a.note || 'Album release';
    }
    return a.note || 'Chart anomaly';
  }

  // --- Initialize ---
  async function init() {
    Compass.render('compass-container');
    Contamination.render('contam-container');
    initNav();
    initEraTabs();
    initCalendarPicker();
    initScaleToggle();
    // The trajectory Expand toggle (js/trajectory-expand.js) fires this after it
    // changes the panel width, so the daily chart re-renders at the new width
    // (recomputed viewBox = undistorted) instead of being stretched by CSS.
    window.addEventListener('rc:trajectory-resized', () => {
      const daily = document.getElementById('daily-chart-container');
      const hist = document.getElementById('trajectory-container');
      const locked = document.getElementById('trajectory-panel')?.classList.contains('traj-chart-locked');
      if (locked) {
        // Expanded / mid-animation: re-render only the visible tab (cheap per frame).
        const histActive = document.getElementById('era-historical')?.classList.contains('active');
        if (histActive) { if (hist && chartData.length) applyZoomChartOnly(hist); }
        else { if (daily && dailyChartLoaded) applyDailyZoomChartOnly(daily); }
      } else {
        // Collapsed: reset BOTH charts to the default width so the hidden tab
        // doesn't keep a stale expanded viewBox (would render squished next view).
        if (daily && dailyChartLoaded) applyDailyZoomChartOnly(daily);
        if (hist && chartData.length) applyZoomChartOnly(hist);
      }
    });
    await loadCurrent();
    loadDailyChart();
    loadTrajectory();
    loadGhostTrail();
  }

  // --- Load Current Reading ---
  async function loadCurrent() {
    try {
      const data = await API.getCompassCurrent();

      // Set compass
      const degree = data.has_reading ? data.compass_degree : data.historical_degree;
      const charge = data.has_reading ? data.charge_level : data.historical_charge;
      // Small delay for needle animation effect
      setTimeout(() => {
        Compass.setDegree(degree, charge);
      }, 300);

      // Set charge bar
      const redCount = data.songs.filter(s => s.rubric_color === 'red').length;
      Charge.setLevel(charge, redCount, data.songs.length, degree);

      // Set contamination
      Contamination.setCount(data.contamination_count, data.songs.length || 10);

      // Set compass date (SVG element inside compass, fallback to HTML div)
      const dateSvg = document.getElementById('compass-date-svg');
      const dateHtml = document.getElementById('compass-date');
      const dateText = data.has_reading ? formatDate(data.date) : 'Historical Reading';
      if (dateSvg) dateSvg.textContent = dateText;
      if (dateHtml) dateHtml.textContent = dateText;

      // Capture today's ISO date so later sources can tell whether they're
      // re-rendering today's reading vs. an archived one.
      currentTodayDate = data.has_reading ? data.date : null;
      setCompassMode(data.has_reading ? 'today' : 'historical');

      // Render right panel
      renderReading(data);

      // Render weekly album reading if present
      renderAlbumReading(data);

      // Render the Ether Art Chart card (independent fetch — its own endpoint)
      if (typeof EtherArtChart !== 'undefined') {
        EtherArtChart.render();
      }

      // iTunes Download Chart panel — live daily snapshot. The RSS feed supplies
      // the song list; lyrics are supplied manually (secondary-chart SOP), so the
      // panel fills in as the chart's songs get calibrated. Stays hidden until
      // the first snapshot is fed (404 = no run yet).
      renderItunesPanel();

    } catch (err) {
      console.error('Failed to load compass data:', err);
      document.getElementById('reading-content').innerHTML =
        '<div class="error-msg">Could not load compass data. Is the API running?</div>';
    }
  }

  // Locked-chart placeholder used by the album panel while it's paused (no
  // Musixmatch wiring → manual lyrics supply isn't sustainable). Renders a
  // blurred 10-row song-list under an "Under Development" overlay. Container
  // CSS supplies the 2-col grid.
  function renderLockedChartPanel(panelId, contentId) {
    const panel = document.getElementById(panelId);
    const container = document.getElementById(contentId);
    if (!panel || !container) return;
    panel.style.display = '';
    const readingPanel = document.getElementById('reading-panel');
    if (readingPanel) readingPanel.style.gridColumn = '';
    container.innerHTML = `
      <div style="position:relative;min-height:200px;">
        <ul class="song-list" style="opacity:0.12;pointer-events:none;">
          ${Array.from({length: 10}, (_, i) => `
            <li class="song-item">
              <span class="song-pos">${i + 1}</span>
              <span class="song-dot orange"></span>
              <div class="song-info">
                <div class="song-title" style="background:var(--rc-border);color:transparent;border-radius:3px;width:${60 + Math.random() * 30}%;">&nbsp;</div>
                <div class="song-artist" style="background:var(--rc-border);color:transparent;border-radius:3px;width:${40 + Math.random() * 20}%;margin-top:4px;">&nbsp;</div>
              </div>
            </li>
          `).join('')}
        </ul>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:0.5rem;">
          <span style="font-size:1.4rem;opacity:0.4;" aria-hidden="true">&#x1F6A7;</span>
          <span style="font-family:var(--rc-font-mono);font-size:0.82rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--rc-text-dim);">Under Development</span>
        </div>
      </div>
    `;
  }

  // New Music Friday — live snapshot row (the secondary-chart slot), laid out
  // exactly like the Daily reading row: the chart (left) + its Ether Art Chart
  // deadpan/topic lens (right), both fed by one snapshot fetch
  // (/api/compass/chart/new-music-friday/current). Hidden entirely until a
  // snapshot is published (the endpoint 404s otherwise), so the pair only
  // appears once populated. Uncalibrated rows render neutral / "untagged" until
  // lyrics are supplied. (Element ids stay itunes-* — this slot previously held
  // the iTunes Download Chart; only the source + labels swapped to NMF.)
  async function renderItunesPanel() {
    const panel = document.getElementById('itunes-reading-panel');
    const container = document.getElementById('itunes-reading-content');
    const etherPanel = document.getElementById('itunes-ether-panel');
    const etherContainer = document.getElementById('itunes-ether-content');
    if (!panel || !container) return;
    const hideBoth = () => { panel.style.display = 'none'; if (etherPanel) etherPanel.style.display = 'none'; };

    let data;
    try {
      data = await API.getChartSnapshot('new-music-friday');
    } catch (err) {
      hideBoth();  // no snapshot fed yet — leave the pair out
      return;
    }

    const songs = (data.songs || []).slice().sort((a, b) => a.position - b.position);
    if (!songs.length) { hideBoth(); return; }
    panel.style.display = '';
    if (etherPanel) etherPanel.style.display = '';

    const header = panel.querySelector('.card-header');
    if (header) header.textContent = 'New Music Friday';
    const desc = panel.querySelector('.card-desc');
    if (desc) desc.textContent = `New Music Friday. Spotify's weekly new-release playlist for the US, read through the same compass. Updated ${formatDate(data.date)}.`;

    // The iTunes snapshot now carries its own aggregate (compass_degree /
    // charge_level / contamination_count / editorial), so the panel renders the
    // full canon shell -- charge group + editorial + list -- just like the daily
    // reading, instead of a bare list. No calendar toggle (charts have none).
    const reading = {
      date: data.date,
      degree: data.compass_degree,
      charge: data.charge_level,
      contaminationCount: data.contamination_count,
      editorial: data.editorial,
      songs: songs,
    };
    crossfade(container, ChartShell.buildLeft(reading), () => ChartShell.wireTooltips(container));

    // Right panel: the same songs through the deadpan + topic lens.
    if (etherPanel && etherContainer) {
      crossfade(etherContainer, ChartShell.etherListHtml(songs));
    }
  }

  function renderReading(data) {
    const container = document.getElementById('reading-content');
    if (!container) return;

    // Remove the compass CTA row if present
    removeCompassCta();

    // Restore panel header for daily readings
    const header = document.querySelector('#reading-panel .card-header');
    const desc = document.querySelector('#reading-panel .card-desc');
    if (header) header.textContent = 'Spotify (US)';
    if (desc) desc.textContent = "Spotify Top 50 — USA. Today's most-heard songs, individually charged.";

    // Sync calendar picker to this reading's date
    if (data.has_reading && data.date) {
      const [y, m, d] = data.date.split('-');
      syncCalendar(parseInt(y), m, d);
    }

    if (!data.has_reading) {
      container.innerHTML = `
        <div class="no-reading">
          <p>No daily reading yet.</p>
          <p>The compass is showing the historical reading across 650+ Billboard #1 songs analyzed from 1960-2024.</p>
        </div>
      `;
      return;
    }

    // Canon chart shell (left card body). The charge group carries the calendar
    // toggle in its row; the song-list/editorial/instrumental templates are the
    // same ones the iTunes panel and the standalone /charts/* pages render.
    const reading = {
      date: data.date,
      degree: data.compass_degree,
      charge: data.charge_level,
      contaminationCount: data.contamination_count,
      editorial: data.editorial_summary,
      songs: data.songs,
    };
    const html = ChartShell.chargeGroupHtml(reading, calendarToggleBtnHtml())
      + ChartShell.editorialHtml(reading.editorial)
      + ChartShell.songListHtml(reading.songs)
      + ChartShell.instrumentalNoteHtml(reading.songs);

    crossfade(container, html, () => {
      ChartShell.wireTooltips(container);
      wireCalendarToggle(container);
    });

    // Announce for screen readers
    if (data.has_reading) {
      const tier = CHARGE_LABELS[data.charge_level] || data.charge_level;
      announce(`Reading loaded for ${formatDate(data.date)}. Charge: ${tier}. ${data.songs.length} songs.`);
    }
  }

  function initSongTooltips(container) {
    container.querySelectorAll('.song-comment-btn').forEach(btn => {
      btn.setAttribute('aria-expanded', 'false');
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const item = btn.closest('.song-item');
        const wasActive = item.classList.contains('active');
        container.querySelectorAll('.song-item.active').forEach(el => {
          el.classList.remove('active');
          const b = el.querySelector('.song-comment-btn');
          if (b) b.setAttribute('aria-expanded', 'false');
        });
        if (!wasActive) {
          item.classList.add('active');
          btn.setAttribute('aria-expanded', 'true');
        }
      });
    });

    // Dismiss on click outside
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.song-comment-btn')) {
        container.querySelectorAll('.song-item.active').forEach(el => {
          el.classList.remove('active');
          const b = el.querySelector('.song-comment-btn');
          if (b) b.setAttribute('aria-expanded', 'false');
        });
      }
    });
  }

  function renderAlbumReading(/* data */) {
    renderLockedChartPanel('album-reading-panel', 'album-reading-content');
  }

  // --- Ghost Trail (past 30 days on compass) ---
  async function loadGhostTrail() {
    try {
      const data = await API.getHistory(1, 30);
      if (data.items && data.items.length) {
        Compass.setGhostTrail(data.items);
      }
    } catch (err) {
      // Silent fail — ghost trail is decorative
    }
  }

  // --- Calendar Picker ---
  let rolodexDatesCache = {};  // { year: ["2026-01-15", ...] }
  let rolodexDegreeCache = {};  // { year: { "2026-01-15": 42.5, ... } } -- per-day compass_degree for calendar coloring

  // --- Trajectory Chart (year-by-year with zoom + Time Machine) ---
  let allYearData = [];
  // Zoom is now a free year range; presets snap the handles to common spans.
  let zoomStartYear = null;
  let zoomEndYear = null;
  // Shared TM-drawer open state, so toggling on one chart persists to the other.
  let tmDrawerOpen = false;
  let chartPoints = [];
  let chartData = [];
  let chartHasYTD = false;
  let tmPlaying = false;
  let tmAnimFrame = null;
  let tmPosition = 0;
  let tmDirection = 1;
  const TM_SPEEDS = [0.5, 1, 2, 4];
  let tmSpeedIdx = 1;
  const TM_BASE_SPEED = 1.5;

  // --- Charge-axis scale ---------------------------------------------------
  // The trajectory charts span +scale .. -scale vertically, where `scale` is
  // resolved per chart from its own data. Two modes, flipped by the Scale toggle
  // in the panel header:
  //   'fit'  (default) -- auto-fit: the axis reaches SCALE_FIT_PAD beyond the
  //          furthest charge in either direction, so swings fill the plot instead
  //          of reading flat in the full +/-100 domain. Floored at SCALE_FIT_MIN
  //          and capped at 100.
  //   'full' -- the literal +/-100 domain (identical to the legacy
  //          compass_degree / 180 mapping, so it is a no-op regression-wise).
  const SCALE_FIT_PAD = 5;   // units of headroom past the furthest reach
  const SCALE_FIT_MIN = 10;  // never zoom tighter than +/-10 (avoid noise drama)
  let scaleMode = 'fit';
  // Last scale each chart rendered with, so a re-fit can pulse the axis labels.
  let lastTrajScale = null;
  let lastDailyScale = null;

  // compass_degree (0 = +100, 90 = 0, 180 = -100) -> charge value (+100 .. -100).
  function degreeToCharge(degree) {
    return (90 - degree) / 0.9;
  }
  // Resolve the axis half-range for a dataset under the current mode. Callers
  // pass whichever slice the axis should fit: the historical chart passes its
  // current zoom window (so the axis tightens to the selected segment), while
  // the full-range overview locator passes allYearData (stable all-time shape).
  function resolveScale(data) {
    if (scaleMode === 'full' || !data || !data.length) return 100;
    let maxAbs = 0;
    for (const d of data) {
      const a = Math.abs(degreeToCharge(d.compass_degree));
      if (a > maxAbs) maxAbs = a;
    }
    return Math.min(100, Math.max(SCALE_FIT_MIN, Math.ceil(maxAbs) + SCALE_FIT_PAD));
  }

  // charge value -> fractional plot position [0,1] under `scale`, clamped so
  // out-of-window charges pin to the top/bottom edge.
  function chargeToFrac(charge, scale) {
    return Math.max(0, Math.min(1, (scale - charge) / (2 * scale)));
  }
  function chargeDegreeToY(degree, padT, chartH, scale) {
    return padT + chargeToFrac(degreeToCharge(degree), scale) * chartH;
  }
  // Grid rows for `scale`: ends + center labeled, quarters unlabeled.
  function chargeGridRows(scale) {
    return [
      { charge: scale, label: '+' + scale },
      { charge: scale / 2, label: '' },
      { charge: 0, label: '0' },
      { charge: -scale / 2, label: '' },
      { charge: -scale, label: '-' + scale },
    ];
  }

  // Redraw both trajectory charts (main + mini-overview, both tabs) at the
  // current scale, preserving each tab's zoom window and viewport position.
  function rerenderChartsForScale() {
    const daily = document.getElementById('daily-chart-container');
    const hist = document.getElementById('trajectory-container');
    if (hist && chartData.length) {
      applyZoomChartOnly(hist);
      renderOverview(hist);
      updateOverviewViewport(hist);
    }
    if (daily && dailyChartLoaded) {
      applyDailyZoomChartOnly(daily);
      renderDailyOverview(daily);
      updateDailyOverviewViewport(daily);
    }
  }

  // The charge-axis labels (+N / 0 / -N) ARE the scale toggle: click any of them
  // to flip between auto-fit and the full +/-100 domain. The labels are
  // re-rendered on every draw, so the click is delegated off the stable panel.
  function initScaleToggle() {
    const panel = document.getElementById('trajectory-panel');
    if (!panel) return;
    panel.addEventListener('click', (e) => {
      const hit = e.target.closest && e.target.closest('.trajectory-y-label, .traj-y-hit');
      if (!hit) return;
      scaleMode = scaleMode === 'fit' ? 'full' : 'fit';
      rerenderChartsForScale();
    });
  }

  function renderTrajectoryChart(data, container) {
    if (!data.length) return;
    chartData = data;
    // Fit to the CURRENTLY-ZOOMED window (`data` is the filtered year range),
    // not the all-time set, so the axis tightens to +/-SCALE_FIT_PAD of the
    // highest/lowest charge in the selected segment. Zooming into a 30/20/10-year
    // span re-scales the axis to that span instead of staying pinned to all-time.
    const scale = resolveScale(data);
    // Pulse the y-axis labels only on the render where the scale actually
    // changed (a zoom that re-fit the axis), so the number change is legible
    // even though the trajectory line barely shifts. Skip the first render.
    const scaleChanged = lastTrajScale !== null && lastTrajScale !== scale;
    lastTrajScale = scale;

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
      y: chargeDegreeToY(d.compass_degree, padT, chartH, scale),
      degree: d.compass_degree,
      year: d.year,
      color: d.charge_level,
      isYTD: d.year === currentCalYear,
    }));

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
      <linearGradient id="traj-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" />
        <stop offset="25%" stop-color="${COLOR_HEX.blue}" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" />
        <stop offset="75%" stop-color="${COLOR_HEX.orange}" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" />
      </linearGradient>
      <linearGradient id="traj-area-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}">
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
    chargeGridRows(scale).forEach(({ charge, label }) => {
      const y = padT + chargeToFrac(charge, scale) * chartH;
      svg += `<line class="trajectory-grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" />`;
      if (label) svg += `<text class="trajectory-y-label${scaleChanged ? ' traj-y-pulse' : ''}" x="${padL - 4}" y="${y + 3}"><title>${scaleMode === 'fit' ? 'Click to show the full +/-100 range' : 'Click to auto-fit the range'}</title>${label}</text>`;
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
      Compass.setDegree(p.degree, p.color);
      Charge.setLevel(p.color, 0, 0, p.degree);

      loadYearSongs(d.year, p.degree, p.color);
      syncCalendar(d.year);
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
    Compass.setDegree(deg, tier);
    Charge.setLevel(tier, 0, 0, deg);
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
    const scale = resolveScale(data);
    const maxIdx = data.length - 1;
    const pts = data.map((d, i) => ({
      x: maxIdx > 0 ? (i / maxIdx) * W : W / 2,
      y: chargeDegreeToY(d.compass_degree, padT, chartH, scale),
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
      const [decadeData, yearData] = await Promise.all([API.getDrift(), API.getDriftYears()]);

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

  // --- Compass State Save/Restore ---
  let savedDegree = null;
  let savedCharge = null;
  let savedDateText = null;
  // Tracks the date string the homepage's "today" view rendered with —
  // used so any source that drives the compass (calendar, daily chart,
  // historical chart) can decide whether to dim the "Today's Charge"
  // header or swap it to a past/year/historical label.
  let currentTodayDate = null;

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
      Compass.setDegree(savedDegree, savedCharge);
      Charge.setLevel(savedCharge, 0, 0, savedDegree);
    }
    if (savedDateText) {
      const dateEl = document.getElementById('compass-date-svg');
      if (dateEl) dateEl.textContent = savedDateText;
    }
    savedDegree = null;
    savedCharge = null;
    savedDateText = null;
  }

  function setCompassDate(text) {
    const dateEl = document.getElementById('compass-date-svg');
    if (dateEl) dateEl.textContent = text;
  }

  // Apply the shared tmDrawerOpen flag to every TM drawer + toggle in the
  // document. Both era tabs render their own copies, but they share state.
  function applyTmDrawerState() {
    document.querySelectorAll('.traj-tm-drawer').forEach((drawer) => {
      drawer.classList.toggle('is-open', tmDrawerOpen);
      if (tmDrawerOpen) drawer.removeAttribute('inert');
      else drawer.setAttribute('inert', '');
    });
    document.querySelectorAll('.traj-tm-toggle').forEach((toggle) => {
      toggle.setAttribute('aria-expanded', String(tmDrawerOpen));
    });
  }

  // Empty state for the Daily Top 20 panel — fires when the calendar picks
  // a year/day that has no coverage, so the panel stops sitting on stale
  // content from the previous selection. Mirrors the ether card's notice.
  function renderReadingEmpty(kind, label) {
    removeCompassCta();
    const header = document.querySelector('#reading-panel .card-header');
    const desc = document.querySelector('#reading-panel .card-desc');
    const container = document.getElementById('reading-content');
    if (header) header.textContent = kind === 'year'
      ? `${label} — No Data`
      : `${label} — No Reading`;
    if (desc) desc.textContent = kind === 'year'
      ? "No Billboard or daily-reading data on the compass for this year."
      : "No daily reading archived for this date.";
    if (container) {
      container.innerHTML = `<div class="no-reading"><p>No data available for ${escapeHtml(label)}.</p></div>`;
    }
  }

  // Header above the compass. Modes mirror the data the compass is currently
  // showing — 'today' is the only un-dimmed state. Other modes get a faded
  // pill class so the user can tell at a glance that they're not looking at
  // today's reading.
  function setCompassMode(mode, opts) {
    opts = opts || {};
    const panel = document.getElementById('compass-panel');
    const header = panel && panel.querySelector('.card-header');
    if (!header) return;
    let text = "Today's Charge";
    let faded = false;
    if (mode === 'date') {
      text = 'Past Charge';
      faded = true;
    } else if (mode === 'year') {
      const year = opts.year;
      text = opts.isYTD ? `${year} YTD Charge` : `${year} Charge`;
      faded = true;
    } else if (mode === 'historical') {
      text = 'Historical Reading';
      faded = true;
    } else if (mode === 'nodata') {
      text = 'No Data';
      faded = true;
    }
    header.textContent = text;
    header.classList.toggle('compass-header-faded', faded);
    if (panel) panel.classList.toggle('is-no-data', mode === 'nodata');
  }

  // Resolve whether an iso date string represents what loadCurrent rendered
  // as "today". Falls back to today's local date if loadCurrent never
  // captured one (e.g. compass shown before the first reading lands).
  function isTodayDate(isoDate) {
    if (!isoDate) return false;
    if (currentTodayDate) return isoDate === currentTodayDate;
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    return isoDate === `${y}-${m}-${d}`;
  }

  // --- Era Panel Tabs ---
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

  function initEraTabs() {
    document.querySelectorAll('.era-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        // Lock trajectory-panel height to prevent layout collapse during tab swap
        const eraPanel = document.getElementById('trajectory-panel');
        eraPanel.style.minHeight = eraPanel.offsetHeight + 'px';

        document.querySelectorAll('.era-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.era-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        const target = tab.dataset.era;
        document.getElementById('era-' + target)?.classList.add('active');

        const tabTitle = tab.querySelector('.era-tab-title');
        if (tabTitle) announce(`Switched to ${tabTitle.textContent}.`);

        if (target === 'daily' && !dailyChartLoaded) {
          loadDailyChart().then(() => { eraPanel.style.minHeight = ''; });
        } else {
          // Content already rendered — release after layout settles
          requestAnimationFrame(() => { eraPanel.style.minHeight = ''; });
        }
      });
    });
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
      const [data] = await Promise.all([API.getDailyChart(), loadChartAnomalies()]);
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
    const scale = resolveScale(dailyChartData);
    const maxIdx = dailyChartData.length - 1;
    const pts = dailyChartData.map((d, i) => ({
      x: maxIdx > 0 ? (i / maxIdx) * W : W / 2,
      y: chargeDegreeToY(d.compass_degree, padT, chartH, scale),
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
    // Fit to the FULL daily series (dailyChartData), not the zoom window, so the
    // axis stays put while panning/zooming/time-machine.
    const scale = resolveScale(dailyChartData.length ? dailyChartData : data);
    // Same axis-change cue as the historical chart: pulse the labels when the
    // scale flips (here it changes on the fit/full toggle, not on zoom).
    const scaleChanged = lastDailyScale !== null && lastDailyScale !== scale;
    lastDailyScale = scale;

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
      y: chargeDegreeToY(d.compass_degree, padT, chartH, scale),
      degree: d.compass_degree,
      date: d.date,
      color: d.charge_level,
      originalDegree: (typeof d._originalDegree === 'number') ? d._originalDegree : d.compass_degree,
    }));

    const linePath = dailyChartPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const areaPath = linePath + ` L ${dailyChartPoints[maxIdx].x.toFixed(1)} ${padT + chartH} L ${dailyChartPoints[0].x.toFixed(1)} ${padT + chartH} Z`;

    let svg = `<svg class="trajectory-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Charge trajectory chart">`;
    svg += `<defs>
      <linearGradient id="daily-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" />
        <stop offset="25%" stop-color="${COLOR_HEX.blue}" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" />
        <stop offset="75%" stop-color="${COLOR_HEX.orange}" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" />
      </linearGradient>
      <linearGradient id="daily-area-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}">
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
    chargeGridRows(scale).forEach(({ charge, label }) => {
      const y = padT + chargeToFrac(charge, scale) * chartH;
      svg += `<line class="trajectory-grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" />`;
      if (label) svg += `<text class="trajectory-y-label${scaleChanged ? ' traj-y-pulse' : ''}" x="${padL - 4}" y="${y + 3}"><title>${scaleMode === 'fit' ? 'Click to show the full +/-100 range' : 'Click to auto-fit the range'}</title>${label}</text>`;
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
      Compass.setDegree(driveDeg, p.color);
      Charge.setLevel(p.color, 0, 0, driveDeg);
      viewArchiveReading(d.date);

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
    Compass.setDegree(deg, tier);
    Charge.setLevel(tier, 0, 0, deg);
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

  // --- Calendar Picker (drill-up/drill-down) ---
  // Views: 'day' | 'month' | 'year' | 'decade'
  let calView = 'day';
  let calYear = null;
  let calMonth = null;  // 0-indexed
  let calDecadeStart = null;
  let calSelectedDate = null;
  const CAL_MONTHS_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const CAL_MONTHS_FULL = ['January','February','March','April','May','June','July','August','September','October','November','December'];

  function calendarToggleBtnHtml() {
    return `<button class="cal-toggle-btn" title="Open calendar picker" aria-label="Open calendar picker">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
      </svg>
    </button>`;
  }

  function initCalendarPicker() {
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.cal-picker') && !e.target.closest('.cal-toggle-btn')) {
        const cal = document.getElementById('cal-picker');
        if (cal) cal.remove();
      }
    });
  }

  function openCalendar(anchorBtn) {
    const oldCal = document.getElementById('cal-picker');
    if (oldCal) oldCal.remove();

    const group = anchorBtn.closest('.reading-charge-group');
    if (!group) return;

    const cal = document.createElement('div');
    cal.id = 'cal-picker';
    cal.className = 'cal-picker';
    cal.setAttribute('role', 'dialog');
    cal.setAttribute('aria-label', 'Date picker');
    group.appendChild(cal);

    // On narrow screens, anchor left instead of right to stay in viewport
    const groupRect = group.getBoundingClientRect();
    if (groupRect.width < 310) {
      cal.style.right = 'auto';
      cal.style.left = '0';
    }

    const now = new Date();
    if (!calYear) {
      calYear = now.getFullYear();
      calMonth = now.getMonth();
    }
    calDecadeStart = Math.floor(calYear / 10) * 10;
    calView = 'day';

    renderCalendar();
  }

  function calYearRange() {
    const min = allYearData.length ? allYearData[0].year : 1960;
    const max = allYearData.length ? allYearData[allYearData.length - 1].year : new Date().getFullYear();
    return { min, max };
  }

  function calYearHasData(yr) {
    return allYearData.some(d => d.year === yr);
  }

  async function calFetchDates(year) {
    if (year <= 2025) return new Set();
    if (!rolodexDatesCache[year]) {
      try {
        const resp = await API.getYearDates(year);
        rolodexDatesCache[year] = resp.dates;
        const degMap = {};
        (resp.readings || []).forEach(r => { degMap[r.date] = r.compass_degree; });
        rolodexDegreeCache[year] = degMap;
      } catch (err) {
        rolodexDatesCache[year] = [];
        rolodexDegreeCache[year] = {};
      }
    }
    return new Set(rolodexDatesCache[year]);
  }

  async function renderCalendar() {
    const cal = document.getElementById('cal-picker');
    if (!cal) return;

    const { min: minYear, max: maxYear } = calYearRange();
    let html = '';

    if (calView === 'day') {
      html = await renderCalDay(minYear, maxYear);
    } else if (calView === 'month') {
      html = renderCalMonth(minYear, maxYear);
    } else if (calView === 'year') {
      html = renderCalYear(minYear, maxYear);
    } else if (calView === 'decade') {
      html = renderCalDecade();
    }

    // Footer link out to the full-page calendar (the dial version of this picker).
    html += `<a class="cal-fullpage-link" href="/calendar/">Open full calendar &rarr;</a>`;

    cal.innerHTML = html;
    wireCalEvents(cal);
  }

  async function renderCalDay(minYear, maxYear) {
    const availableDates = await calFetchDates(calYear);
    const canPrev = !(calYear <= minYear && calMonth === 0);
    const canNext = !(calYear >= maxYear && calMonth === 11);

    let html = `<div class="cal-header">
      <button class="cal-nav" data-action="prev-month" aria-label="Previous month"${canPrev ? '' : ' disabled'}>&lsaquo;</button>
      <button class="cal-title" data-action="zoom-out">${CAL_MONTHS_FULL[calMonth]} ${calYear}</button>
      <button class="cal-nav" data-action="next-month" aria-label="Next month"${canNext ? '' : ' disabled'}>&rsaquo;</button>
    </div>`;

    if (calYear > 2025) {
      html += '<div class="cal-weekdays">';
      ['Su','Mo','Tu','We','Th','Fr','Sa'].forEach(d => { html += `<span>${d}</span>`; });
      html += '</div><div class="cal-grid cal-grid-days">';

      const firstDay = new Date(calYear, calMonth, 1).getDay();
      const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();

      const degMap = rolodexDegreeCache[calYear] || {};
      for (let i = 0; i < firstDay; i++) html += '<span class="cal-cell cal-empty"></span>';
      for (let d = 1; d <= daysInMonth; d++) {
        const dateStr = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const has = availableDates.has(dateStr);
        const sel = dateStr === calSelectedDate;
        const cls = ['cal-cell'];
        let style = '';
        if (has) {
          cls.push('cal-has-data');
          // Each day painted its own spectrum color -- same mechanic as the
          // compass-container border/glow (Charge.spectrumHexFromT(degree/180)).
          const deg = degMap[dateStr];
          if (deg != null && typeof Charge !== 'undefined') {
            style = ` style="--day-color:${Charge.spectrumHexFromT(deg / 180)}"`;
          }
        }
        if (sel) cls.push('cal-selected');
        html += `<span class="${cls.join(' ')}"${style} data-action="pick-day" data-date="${dateStr}" role="button" tabindex="0" aria-label="${dateStr}">${d}</span>`;
      }
      html += '</div>';
    } else {
      html += `<div class="cal-hist-msg">Year-level reading only.<br>No daily readings before 2026.</div>`;
    }
    return html;
  }

  function renderCalMonth(minYear, maxYear) {
    const canPrev = calYear > minYear;
    const canNext = calYear < maxYear;

    let html = `<div class="cal-header">
      <button class="cal-nav" data-action="prev-year" aria-label="Previous year"${canPrev ? '' : ' disabled'}>&lsaquo;</button>
      <button class="cal-title" data-action="zoom-out">${calYear}</button>
      <button class="cal-nav" data-action="next-year" aria-label="Next year"${canNext ? '' : ' disabled'}>&rsaquo;</button>
    </div>`;

    html += '<div class="cal-grid cal-grid-months">';
    for (let m = 0; m < 12; m++) {
      const sel = m === calMonth && calView === 'month';
      html += `<span class="cal-cell cal-has-data${sel ? ' cal-selected' : ''}" data-action="pick-month" data-month="${m}" role="button" tabindex="0" aria-label="${CAL_MONTHS_FULL[m]}">${CAL_MONTHS_SHORT[m]}</span>`;
    }
    html += '</div>';
    return html;
  }

  function renderCalYear(minYear, maxYear) {
    calDecadeStart = Math.floor(calYear / 10) * 10;
    const rangeEnd = calDecadeStart + 9;
    const canPrev = calDecadeStart > Math.floor(minYear / 10) * 10;
    const canNext = calDecadeStart < Math.floor(maxYear / 10) * 10;

    let html = `<div class="cal-header">
      <button class="cal-nav" data-action="prev-decade" aria-label="Previous decade"${canPrev ? '' : ' disabled'}>&lsaquo;</button>
      <button class="cal-title" data-action="zoom-out">${calDecadeStart} &ndash; ${rangeEnd}</button>
      <button class="cal-nav" data-action="next-decade" aria-label="Next decade"${canNext ? '' : ' disabled'}>&rsaquo;</button>
    </div>`;

    html += '<div class="cal-grid cal-grid-years">';
    for (let y = calDecadeStart; y <= rangeEnd; y++) {
      const has = calYearHasData(y);
      const sel = y === calYear;
      const cls = ['cal-cell'];
      if (has) cls.push('cal-has-data');
      if (sel) cls.push('cal-selected');
      html += `<span class="${cls.join(' ')}" data-action="pick-year" data-year="${y}" role="button" tabindex="${has ? '0' : '-1'}">${y}</span>`;
    }
    html += '</div>';
    return html;
  }

  function renderCalDecade() {
    const { min: minYear, max: maxYear } = calYearRange();
    const firstDecade = Math.floor(minYear / 10) * 10;
    const lastDecade = Math.floor(maxYear / 10) * 10;

    let html = `<div class="cal-header">
      <span class="cal-title cal-title-top">${firstDecade}s &ndash; ${lastDecade}s</span>
    </div>`;

    html += '<div class="cal-grid cal-grid-decades">';
    for (let d = firstDecade; d <= lastDecade; d += 10) {
      const sel = d === calDecadeStart;
      const cls = ['cal-cell', 'cal-has-data'];
      if (sel) cls.push('cal-selected');
      html += `<span class="${cls.join(' ')}" data-action="pick-decade" data-decade="${d}" role="button" tabindex="0">${d}s</span>`;
    }
    html += '</div>';
    return html;
  }

  function calHandleAction(el) {
    const action = el.dataset.action;
    switch (action) {
      case 'zoom-out':
        if (calView === 'day') { calView = 'month'; announce(`Picking month for ${calYear}.`); }
        else if (calView === 'month') { calView = 'year'; announce('Picking year.'); }
        else if (calView === 'year') { calView = 'decade'; announce('Picking decade.'); }
        renderCalendar();
        break;
      case 'prev-month':
        calMonth--;
        if (calMonth < 0) { calMonth = 11; calYear--; }
        renderCalendar();
        break;
      case 'next-month':
        calMonth++;
        if (calMonth > 11) { calMonth = 0; calYear++; }
        renderCalendar();
        break;
      case 'prev-year':
        calYear--;
        renderCalendar();
        break;
      case 'next-year':
        calYear++;
        renderCalendar();
        break;
      case 'prev-decade':
        calDecadeStart -= 10;
        calYear = calDecadeStart;
        renderCalendar();
        break;
      case 'next-decade':
        calDecadeStart += 10;
        calYear = calDecadeStart;
        renderCalendar();
        break;
      case 'pick-day':
        calSelectedDate = el.dataset.date;
        if (el.classList.contains('cal-has-data')) {
          viewArchiveReading(el.dataset.date);
          announce(`Loading reading for ${el.dataset.date}.`);
        } else {
          // No daily reading for this day — both panels drop their stale
          // content, the compass fades to "No Data", and the date label
          // matches the picked day.
          renderReadingEmpty('date', formatDate(el.dataset.date));
          setCompassDate(formatDate(el.dataset.date));
          setCompassMode('nodata');
          if (typeof EtherArtChart !== 'undefined') {
            EtherArtChart.render({ mode: 'date', date: el.dataset.date });
          }
        }
        renderCalendar();
        break;
      case 'pick-month':
        calMonth = parseInt(el.dataset.month);
        calView = 'day';
        announce(`${CAL_MONTHS_FULL[calMonth]} ${calYear}.`);
        renderCalendar();
        break;
      case 'pick-year':
        calYear = parseInt(el.dataset.year);
        calDecadeStart = Math.floor(calYear / 10) * 10;
        calView = 'month';
        announce(`Picking month for ${calYear}.`);
        onCalendarYearSelect(calYear);
        renderCalendar();
        break;
      case 'pick-decade':
        calDecadeStart = parseInt(el.dataset.decade);
        calYear = calDecadeStart;
        calView = 'year';
        announce(`${calDecadeStart}s decade.`);
        renderCalendar();
        break;
    }
  }

  function wireCalEvents(cal) {
    // Click handlers
    cal.querySelectorAll('[data-action]').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        calHandleAction(el);
      });
    });

    // Enter/Space on span cells
    cal.querySelectorAll('.cal-cell[tabindex]').forEach(el => {
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          calHandleAction(el);
        }
      });
    });

    // Arrow key navigation within grid
    cal.querySelectorAll('.cal-grid').forEach(grid => {
      grid.addEventListener('keydown', (e) => {
        if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
        e.preventDefault();
        const cells = [...grid.querySelectorAll('.cal-cell[tabindex]')];
        const idx = cells.indexOf(document.activeElement);
        if (idx === -1) return;
        const cols = grid.classList.contains('cal-grid-days') ? 7
          : grid.classList.contains('cal-grid-months') ? 4
          : grid.classList.contains('cal-grid-years') ? 5 : 3;
        let next = idx;
        if (e.key === 'ArrowRight') next = Math.min(idx + 1, cells.length - 1);
        else if (e.key === 'ArrowLeft') next = Math.max(idx - 1, 0);
        else if (e.key === 'ArrowDown') next = Math.min(idx + cols, cells.length - 1);
        else if (e.key === 'ArrowUp') next = Math.max(idx - cols, 0);
        cells[next].focus();
      });
    });

    // Escape to close
    cal.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        cal.remove();
      }
    });
  }

  function onCalendarYearSelect(year) {
    const yd = allYearData.find(d => d.year === year);
    if (yd) {
      loadYearSongs(year, yd.compass_degree, yd.charge_level);
      Compass.setDegree(yd.compass_degree, yd.charge_level);
      Charge.setLevel(yd.charge_level, 0, 0, yd.compass_degree);
      const isYTD = year === new Date().getFullYear();
      setCompassDate(isYTD ? `${year} YTD` : String(year));
      setCompassMode('year', { year, isYTD });
    } else {
      // No drift data for this year — daily reading panel gets the same
      // empty-state treatment the ether card already does on its own,
      // and the compass fades to an unreadable state with "No Data" header.
      renderReadingEmpty('year', String(year));
      setCompassDate(String(year));
      setCompassMode('nodata');
    }
    // Fire the ether render regardless of drift coverage — it owns its own
    // empty state ("No reading available for [year]") so a 1973 click no
    // longer leaves the card sitting on the previous selection's data.
    if (typeof EtherArtChart !== 'undefined') {
      EtherArtChart.render({ mode: 'year', year });
    }
  }

  function syncCalendar(year, month, day) {
    calYear = year || calYear;
    if (month) {
      calMonth = parseInt(month) - 1;
      if (day) calSelectedDate = `${year}-${month}-${day}`;
      else calSelectedDate = null;
    } else {
      calSelectedDate = null;
    }
    if (calYear) calDecadeStart = Math.floor(calYear / 10) * 10;
  }

  function wireCalendarToggle(container) {
    const btn = container.querySelector('.cal-toggle-btn');
    if (!btn) return;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const existing = document.getElementById('cal-picker');
      if (existing) {
        existing.remove();
      } else {
        openCalendar(btn);
      }
    });
  }

  // --- Year Songs Loading + Rendering ---
  let yearSongsState = { year: null, songs: [], total: 0, loaded: 0 };

  async function loadYearSongs(year, degree, chargeLevel) {
    yearSongsState = { year, songs: [], total: 0, loaded: 0 };

    // Show "View songs" button under compass instead of auto-scrolling
    showYearViewButton(year, degree, chargeLevel);

    // Update reading panel header
    const header = document.querySelector('#reading-panel .card-header');
    const desc = document.querySelector('#reading-panel .card-desc');
    const isCurrentYear = year === new Date().getFullYear();
    if (year > 2025) {
      if (header) header.textContent = isCurrentYear ? `${year} — Year to Date` : `${year} — Charting Songs`;
      if (desc) desc.textContent = isCurrentYear ? 'Live year-to-date reading, updated daily. Weighted by chart position and days on chart.' : 'Frequency-weighted by chart position and days on chart.';
    } else {
      if (header) header.textContent = `${year} — Billboard Top Songs`;
      if (desc) desc.textContent = 'Position-weighted reading of the year\u2019s biggest hits.';
    }

    // Load songs into reading panel (no scroll)
    const container = document.getElementById('reading-content');
    if (!container) return;
    // Keep old content visible while fetching — crossfade swaps it when ready
    container.style.opacity = '0.5';
    container.style.transition = 'opacity 0.15s ease';
    announce(`Loading songs for ${year}.`);

    try {
      const data = await API.getYearSongs(year, 0, 20);
      yearSongsState.songs = data.songs;
      yearSongsState.total = data.total;
      yearSongsState.loaded = data.songs.length;

      renderYearSongs(year, degree, chargeLevel, container);
    } catch (err) {
      console.error('Failed to load year songs:', err);
      container.style.opacity = '1';
      container.innerHTML = '<div class="error-msg">Could not load songs for this year.</div>';
    }
  }

  function showYearViewButton(year, degree, chargeLevel) {
    const isYTD = year === new Date().getFullYear();
    const tier = chargeLevel || degreeToTier(degree);
    const hex = COLOR_HEX[tier] || '#888';
    const label = isYTD ? `View ${year} YTD Songs` : `View ${year} Songs`;
    mountCompassCta(label, hex);
  }

  // Removes the compass CTA row (View Songs + Reset to Today) if present.
  function removeCompassCta() {
    const row = document.getElementById('year-view-row');
    if (row) row.remove();
    const stray = document.getElementById('year-view-btn');
    if (stray) stray.remove();
  }

  // Snap the compass back to the live "today" daily reading and clear any
  // time-travel state (historical-index slider + its reset button), then
  // re-fetch + re-render today's reading across the compass, top-20, and ether.
  function resetCompassToToday() {
    const tmReset = document.getElementById('timemachine-reset');
    if (tmReset && tmReset.style.display !== 'none') tmReset.click();
    removeCompassCta();
    loadCurrent();
  }

  // Builds the split CTA row under the compass: left half = "View ... Songs"
  // (tier-colored, scrolls to the reading panel), right half = "Reset to Today"
  // (returns the compass to the most recent daily reading).
  function mountCompassCta(label, hex) {
    removeCompassCta();

    const row = document.createElement('div');
    row.id = 'year-view-row';
    row.className = 'year-view-row';

    const viewBtn = document.createElement('button');
    viewBtn.id = 'year-view-btn';
    viewBtn.className = 'year-view-btn';
    viewBtn.innerHTML = `<span>${label}</span><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`;
    viewBtn.style.borderColor = hex;
    viewBtn.style.color = hex;
    viewBtn.addEventListener('click', () => {
      const panel = document.getElementById('reading-panel');
      if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    const resetBtn = document.createElement('button');
    resetBtn.id = 'compass-reset-today';
    resetBtn.className = 'year-view-btn year-reset-btn';
    resetBtn.innerHTML = `<span>Reset to Today</span><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>`;
    resetBtn.addEventListener('click', resetCompassToToday);

    row.appendChild(viewBtn);
    row.appendChild(resetBtn);

    const compassPanel = document.getElementById('compass-panel');
    if (compassPanel) compassPanel.appendChild(row);
  }

  function renderYearSongs(year, degree, chargeLevel, container) {
    const { songs, total, loaded } = yearSongsState;
    const tier = chargeLevel || degreeToTier(degree);
    const hex = COLOR_HEX[tier] || '#888';
    const label = CHARGE_LABELS[tier] || tier;
    const contamCount = songs.filter(s => s.contaminated).length;
    const yearInstrCount = songs.filter(s => s.instrumental).length;
    const isLive = year > 2025;

    let html = '';
    // Year charge header
    html += `<div class="reading-charge-group">
      <div class="reading-date" style="background:${hex}">${year}</div>
      <div class="reading-charge-row">
        <div class="reading-charge-bar" style="background:${hex}">
          <div class="reading-charge-inner">
            <span class="reading-charge-score">${degreeToScore(degree)}</span>
            <span class="reading-charge-label">${label}</span>
            <span class="reading-charge-meta">${total - yearInstrCount} songs${yearInstrCount ? ' + ' + yearInstrCount + ' instrumental' + (yearInstrCount > 1 ? 's' : '') : ''}${contamCount ? ' \u00B7 ' + contamCount + ' contaminated' : ''}</span>
          </div>
        </div>
        ${calendarToggleBtnHtml()}
      </div>
    </div>`;

    html += '<ul class="song-list">';
    songs.forEach((song, i) => {
      const pos = song.position || (i + 1);
      const hasMEI = song.message_analysis || song.expression_analysis || song.intention_analysis;
      const hasSummary = song.charge_summary || song.contamination_note;
      const hasTooltip = hasMEI || hasSummary;
      let tooltipHtml = '';
      if (hasTooltip) {
        const songHex = COLOR_HEX[song.rubric_color] || '#888';
        const songLabel = CHARGE_LABELS[song.rubric_color] || song.rubric_color;
        const songScore = song.charge_value != null ? (song.charge_value > 0 ? '+' + song.charge_value : String(song.charge_value)) : '';
        let lines = `<div style="background:${songHex};color:var(--rc-bg-dark);font-family:var(--rc-font-mono);font-size:0.7rem;font-weight:700;letter-spacing:0.02em;padding:0.25rem 0.55rem;margin:-0.4rem -0.55rem 0.35rem;border-radius:4px 4px 0 0">${songScore} ${songLabel}</div>`;
        if (song.charge_summary) lines += `<div style="font-size:0.72rem;color:rgba(20,20,30,0.65);font-style:italic;line-height:1.4;margin-bottom:0.3rem;padding-bottom:0.25rem;border-bottom:1px solid rgba(0,0,0,0.06)">${escapeHtml(song.charge_summary)}</div>`;
        if (song.contaminated && song.contamination_note) lines += `<div class="mei-line mei-contam">&#x2622; ${escapeHtml(song.contamination_note)}</div>`;
        if (song.dogma_referenced && song.dogma_note) lines += `<div class="mei-line mei-dogma">&#x1F4DC; ${escapeHtml(song.dogma_note)}</div>`;
        tooltipHtml = `<div class="song-tooltip">${lines}</div>`;
      }
      const instrClass = song.instrumental ? ' instrumental' : '';
      const yearSongHref = song.song_slug ? `/songs/${encodeURIComponent(song.song_slug)}` : null;
      const yearTitleHtml = yearSongHref
        ? `<a href="${yearSongHref}" class="song-title-link">${escapeHtml(song.title)}</a>`
        : escapeHtml(song.title);
      html += `
        <li class="song-item${hasTooltip ? ' has-tooltip' : ''}${instrClass}">
          <span class="song-pos">${pos}</span>
          <span class="song-dot ${song.instrumental ? '' : song.rubric_color}"></span>
          <div class="song-info">
            <div class="song-title">${yearTitleHtml}</div>
            <div class="song-artist">${artistHtml(song.artist, song.artist_slug, 'song-artist-name')}${isLive && song.days_on_chart > 1 ? ` <span class="song-days">${song.days_on_chart}d</span>` : ''}</div>
          </div>
          <div class="song-actions">
            ${song.contaminated ? '<span class="song-contam" aria-hidden="true">&#x2622;</span>' : ''}
            ${song.dogma_referenced ? '<span class="song-dogma" aria-hidden="true" title="Dogma referenced">&#x1F4DC;</span>' : ''}
            ${hasTooltip ? `<button class="song-comment-btn" title="Read analysis" aria-label="Analysis of ${escapeHtml(song.title)}">&#x1F4AC;</button>` : ''}
          </div>
          ${tooltipHtml}
        </li>
      `;
    });
    html += '</ul>';

    // Instrumental disclosure
    const instrCount = songs.filter(s => s.instrumental).length;
    if (instrCount > 0) {
      html += `<div class="instrumental-note">${instrCount} instrumental${instrCount > 1 ? 's' : ''} — does not contribute to the compass reading.</div>`;
    }

    // Load More / View Full Year button
    if (loaded < total) {
      const remaining = total - loaded;
      if (loaded < 100) {
        html += `<button class="year-load-more" id="year-load-more">Load More (${remaining} remaining)</button>`;
      } else {
        html += `<button class="year-load-more" id="year-view-full">View Full Year (${total} songs)</button>`;
      }
    }

    crossfade(container, html, () => {
      initSongTooltips(container);
      wireCalendarToggle(container);

      // Wire load more
      const loadMoreBtn = document.getElementById('year-load-more');
      if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', () => loadMoreYearSongs(year, degree, chargeLevel));
      }

      // Wire full year overlay
      const viewFullBtn = document.getElementById('year-view-full');
      if (viewFullBtn) {
        viewFullBtn.addEventListener('click', () => openYearOverlay(year, degree, chargeLevel));
      }
    });

    // Announce for screen readers
    const tierLabel = CHARGE_LABELS[tier] || tier;
    announce(`${year} songs loaded. ${total} songs. Charge: ${tierLabel}.`);
  }

  async function loadMoreYearSongs(year, degree, chargeLevel) {
    const btn = document.getElementById('year-load-more');
    if (btn) { btn.textContent = 'Loading...'; btn.disabled = true; }

    try {
      const data = await API.getYearSongs(year, yearSongsState.loaded, 20);
      yearSongsState.songs = yearSongsState.songs.concat(data.songs);
      yearSongsState.loaded = yearSongsState.songs.length;
      yearSongsState.total = data.total;

      const container = document.getElementById('reading-content');
      if (container) renderYearSongs(year, degree, chargeLevel, container);
    } catch (err) {
      if (btn) { btn.textContent = 'Failed — try again'; btn.disabled = false; }
    }
  }

  async function openYearOverlay(year, degree, chargeLevel) {
    let overlay = document.getElementById('year-songs-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'year-songs-overlay';
      overlay.className = 'year-overlay';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      document.body.appendChild(overlay);
    }

    const tier = chargeLevel || degreeToTier(degree);
    const hex = COLOR_HEX[tier] || '#888';
    const label = CHARGE_LABELS[tier] || tier;

    overlay.setAttribute('aria-label', `${year} songs — ${label}`);
    overlay.innerHTML = `
      <div class="year-overlay-header">
        <div class="year-overlay-title">
          <span class="year-overlay-year">${year}</span>
          <span class="year-overlay-score" style="color:${hex}">${degreeToScore(degree)} ${label}</span>
        </div>
        <button class="year-overlay-close" id="year-overlay-close" aria-label="Close overlay">&times;</button>
      </div>
      <div class="year-overlay-body"><div class="loading" role="status">Loading all songs...</div></div>
    `;
    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';

    document.getElementById('year-overlay-close').addEventListener('click', closeYearOverlay);

    try {
      const data = await API.getYearSongs(year, 0, 999);
      const songs = data.songs;
      const isLive = year > 2025;
      const contamCount = songs.filter(s => s.contaminated).length;

      // Tier breakdown bar (exclude instrumentals)
      const scoredSongs = songs.filter(s => !s.instrumental);
      const colorCounts = {};
      scoredSongs.forEach(s => { colorCounts[s.rubric_color] = (colorCounts[s.rubric_color] || 0) + 1; });
      let barHtml = '';
      ['violet', 'blue', 'green', 'orange', 'red'].forEach(color => {
        const count = colorCounts[color] || 0;
        if (count === 0) return;
        const pct = (count / scoredSongs.length) * 100;
        barHtml += `<div class="decade-seg" style="width:${pct.toFixed(1)}%;background:${COLOR_HEX[color]}" title="${count} ${CHARGE_LABELS[color]}"></div>`;
      });

      let tableHtml = '<table class="year-overlay-table"><thead><tr>';
      tableHtml += '<th>#</th><th></th><th>Title</th><th>Artist</th>';
      if (isLive) tableHtml += '<th>Days</th>';
      tableHtml += '<th>Charge</th>';
      tableHtml += '</tr></thead><tbody>';

      const instrCount = songs.filter(s => s.instrumental).length;

      songs.forEach((s, i) => {
        const pos = s.position || (i + 1);
        const cv = s.instrumental ? '' : (s.charge_value != null ? (s.charge_value > 0 ? '+' + s.charge_value : s.charge_value) : '');
        const instrCls = s.instrumental ? ' class="instrumental"' : '';
        tableHtml += `<tr${instrCls}>
          <td class="yo-pos">${pos}</td>
          <td><span class="song-dot ${s.instrumental ? '' : s.rubric_color}"></span></td>
          <td class="yo-title">${escapeHtml(s.title)}${s.instrumental ? ' <em class="instr-tag">(instrumental)</em>' : ''}</td>
          <td class="yo-artist">${artistHtml(s.artist, s.artist_slug, 'yo-artist-link')}</td>
          ${isLive ? `<td class="yo-days">${s.days_on_chart}</td>` : ''}
          <td class="yo-charge">${cv}</td>
        </tr>`;
      });
      tableHtml += '</tbody></table>';

      const instrNote = instrCount > 0 ? `<div class="instrumental-note">${instrCount} instrumental${instrCount > 1 ? 's' : ''} — does not contribute to the compass reading.</div>` : '';

      const body = overlay.querySelector('.year-overlay-body');
      body.innerHTML = `
        <div class="year-overlay-meta">${scoredSongs.length} songs${instrCount ? ' + ' + instrCount + ' instrumental' + (instrCount > 1 ? 's' : '') : ''}${contamCount ? ' \u00B7 ' + contamCount + ' contaminated' : ''}</div>
        <div class="decade-bar" style="margin-bottom:1rem;">${barHtml}</div>
        ${tableHtml}
        ${instrNote}
      `;
    } catch (err) {
      overlay.querySelector('.year-overlay-body').innerHTML = '<div class="error-msg">Failed to load songs.</div>';
    }
  }

  function closeYearOverlay() {
    const overlay = document.getElementById('year-songs-overlay');
    if (overlay) {
      overlay.classList.remove('open');
      document.body.style.overflow = '';
    }
  }

  // --- Chart click pinch effect ---
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

  // --- Secondary Nav ---
  function initNav() {
    document.querySelectorAll('.nav-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const section = tab.dataset.section;
        setActiveSection(section);
      });
    });

    // Check hash on load, default to history — but don't set hash if none present
    const hash = window.location.hash.slice(1);
    if (hash) {
      setActiveSection(hash);
    } else {
      setActiveSection('history', false);
    }

    // Respond to hash changes that happen in-page — e.g. clicking the
    // mini-log's "View all →" (href="#calibration-log") while already here.
    window.addEventListener('hashchange', () => {
      const h = window.location.hash.slice(1);
      if (h) setActiveSection(h, /* updateHash= */ false);
    });
  }

  function setActiveSection(section, updateHash = true) {
    document.querySelectorAll('.nav-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.section === section);
    });
    document.querySelectorAll('.section-content').forEach(s => {
      s.classList.toggle('active', s.id === `section-${section}`);
    });
    if (updateHash) window.location.hash = section;

    const sectionNames = {
      history: 'Historical Overview',
      methodology: 'Methodology',
      'calibration-log': 'Calibration Log',
    };
    if (sectionNames[section]) announce(`${sectionNames[section]} section.`);

    // Lazy-load section data
    if (section === 'history') loadDrift();
  }

  // --- Drift (Historical Overview) ---
  let driftLoaded = false;
  async function loadDrift() {
    if (driftLoaded) return;
    const container = document.getElementById('drift-content');
    if (!container) return;

    try {
      const data = await API.getDrift();
      driftLoaded = true;

      const COLOR_ORDER = ['violet', 'blue', 'green', 'orange', 'red'];

      let html = '<div class="decade-cards">';
      data.forEach(d => {
        const score = degreeToScore(d.compass_degree);
        const hex = COLOR_HEX[d.charge_level] || '#888';
        const tierLabel = CHARGE_LABELS[d.charge_level] || d.charge_level;

        // Stacked color bar segments
        let barHtml = '';
        COLOR_ORDER.forEach(color => {
          const count = d.color_counts[color] || 0;
          if (count === 0) return;
          const pct = (count / d.chart_song_count) * 100;
          barHtml += `<div class="decade-seg" style="width:${pct.toFixed(1)}%;background:${COLOR_HEX[color]}" title="${count} ${CHARGE_LABELS[color] || color}"></div>`;
        });

        // Color breakdown text
        let breakdownParts = [];
        COLOR_ORDER.forEach(color => {
          const count = d.color_counts[color] || 0;
          if (count > 0) {
            breakdownParts.push(`<span style="color:${COLOR_HEX[color]}">${count}</span> ${CHARGE_LABELS[color] || color}`);
          }
        });

        html += `
          <div class="decade-card">
            <div class="decade-header">
              <span class="decade-name">${d.decade}</span>
              <span class="decade-score" style="color:${hex}">${score}</span>
              <span class="decade-tier" style="color:${hex}">${tierLabel}</span>
            </div>
            <div class="decade-bar">${barHtml}</div>
            <div class="decade-breakdown">${breakdownParts.join('<span class="decade-sep">/</span>')}</div>
            <div class="decade-meta">${d.chart_song_count} charting songs analyzed${d.total_song_count > d.chart_song_count ? `<br><span class="decade-meta-total">${d.total_song_count} total songs</span>` : ''}</div>
          </div>
        `;
      });
      html += '</div>';

      container.innerHTML = html;
    } catch (err) {
      container.innerHTML = '<div class="error-msg">Could not load drift data.</div>';
    }
  }

  // --- Archive ---
  let archivePage = 1;
  let archiveLoaded = false;
  async function loadArchive() {
    if (archiveLoaded) return;
    const container = document.getElementById('archive-content');
    if (!container) return;

    try {
      const data = await API.getHistory(archivePage);
      archiveLoaded = true;

      if (data.items.length === 0) {
        container.innerHTML = '<p style="color:var(--rc-text-dim);">No archived readings yet.</p>';
        return;
      }

      let html = '<ul class="archive-list">';
      data.items.forEach(r => {
        html += `
          <li class="archive-item" onclick="App.viewArchiveReading('${r.date}')">
            <span class="archive-date">${formatDate(r.date)}</span>
            <div class="archive-meta">
              <span class="archive-degree" style="color:${COLOR_HEX[r.charge_level] || '#888'}">${degreeToScore(r.compass_degree)}</span>
              <span class="archive-charge">${CHARGE_LABELS[r.charge_level] || r.charge_level}</span>
            </div>
          </li>
        `;
      });
      html += '</ul>';

      if (data.pages > 1) {
        html += '<div style="text-align:center;margin-top:1rem;">';
        for (let p = 1; p <= data.pages; p++) {
          const active = p === archivePage ? 'color:var(--rc-accent);font-weight:bold;' : 'color:var(--rc-text-dim);';
          html += `<button onclick="App.loadArchivePage(${p})" style="background:none;border:none;padding:0.5rem;cursor:pointer;font-family:var(--rc-font-mono);${active}">${p}</button>`;
        }
        html += '</div>';
      }

      container.innerHTML = html;
    } catch (err) {
      container.innerHTML = '<div class="error-msg">Could not load archive.</div>';
    }
  }

  async function loadArchivePage(page) {
    archivePage = page;
    archiveLoaded = false;
    await loadArchive();
  }

  async function viewArchiveReading(date) {
    try {
      const reading = await API.getReading(date);
      // Update compass + panels with this reading
      Compass.setDegree(reading.compass_degree, reading.charge_level);
      const redCount = reading.songs.filter(s => s.rubric_color === 'red').length;
      Charge.setLevel(reading.charge_level, redCount, reading.songs.length, reading.compass_degree);
      Contamination.setCount(reading.contamination_count, reading.songs.length);
      renderReading({ has_reading: true, ...reading });

      // Date label + header swap so the compass card matches what we're showing.
      setCompassDate(formatDate(reading.date));
      setCompassMode(isTodayDate(reading.date) ? 'today' : 'date');

      // Show "View Songs" button under compass
      showDailyViewButton(date, reading.compass_degree, reading.charge_level);

      // Re-render the ether panel for the same date so the two cards stay in lockstep.
      if (typeof EtherArtChart !== 'undefined') {
        EtherArtChart.render({ mode: 'date', date });
      }
    } catch (err) {
      console.error('Failed to load reading:', err);
    }
  }

  function showDailyViewButton(date, degree, chargeLevel) {
    const tier = chargeLevel || degreeToTier(degree);
    const hex = COLOR_HEX[tier] || '#888';
    mountCompassCta(`View ${formatDate(date)} Songs`, hex);
  }

  // --- Helpers ---
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

  function artistHtml(name, slug, className) {
    const inner = escapeHtml(name);
    if (slug) {
      return `<a class="${className} artist-link" href="/artists/${encodeURIComponent(slug)}" onclick="event.stopPropagation();">${inner}</a>`;
    }
    return `<span class="${className}">${inner}</span>`;
  }

  // --- Public ---
  return {
    init,
    loadArchivePage,
    viewArchiveReading,
  };
})();

// Tier popup moved to /js/tier-popup.js. Loaded only on pages that show
// the .charge-legend-seg buttons (currently /methodology/).

// Boot
document.addEventListener('DOMContentLoaded', App.init);
