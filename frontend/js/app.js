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
      return map;  // the capsule's loadAnomalies hook consumes the returned map
    } catch (e) {
      CHART_ANOMALIES = {};  // chart still renders without markers
      return {};
    }
  }

  // Report a real load milestone to the homepage splash (index.html inline
  // script). No-op on pages without the splash or after it has revealed.
  function splashStep(name) {
    try { if (window.rcSplashStep) window.rcSplashStep(name); } catch (e) {}
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
    initCalendarPicker();
    // The whole trajectory panel (both era tabs, the shared scale toggle, the
    // Time Machine drawer, and rc:trajectory-resized handling) is owned by the
    // shared DailyChargePanel capsule -- the SAME implementation the chart pages
    // mount, so the homepage and the charts never drift. We also keep a local
    // copy of the historical year data for the calendar picker + the year-songs
    // overlay (both read allYearData); it's fetched once here and handed to the
    // capsule via loadHistorical so there's no double fetch. The host hooks route
    // the capsule's clicks/scrubs back into the homepage's own reading /
    // year-songs / calendar / compass-header logic.
    const trajPanel = document.getElementById('trajectory-panel');
    if (trajPanel && window.DailyChargePanel) {
      const histData = API.getDriftYears().then(yd => { allYearData = yd; return yd; });
      trajPanelHandle = DailyChargePanel.mount(trajPanel, {
        // The Daily tab follows the homepage chart toggle: the closure reads
        // the live selection and switchHomeChart() calls reloadDaily().
        loadDaily: () => homeChart === 'spotify' ? API.getDailyChart()
          : homeChart === 'unified' ? API.getUnifiedDailyChart()
          : API.getChartDailyChart(homeChart),
        // Anomaly markers are daily-reading facts; other charts load none
        // (returning {} clears the previous chart's markers on reload).
        loadAnomalies: () => homeChart === 'spotify' ? loadChartAnomalies() : Promise.resolve({}),
        loadHistorical: () => histData,
        // Daily-chart point clicks open the archived SPOTIFY reading; on any
        // other chart the click has no matching archive view, so ignore it.
        onDateSelect: (...args) => { if (homeChart === 'spotify') viewArchiveReading(...args); },
        onYearSelect: loadYearSongs,
        syncCalendar: syncCalendar,
        setCompassMode: setCompassMode,
        eraTaglines: { daily: 'trailing 365 days, day by day', historical: "where we've been and where we are" },
      });
    }
    const deepLinkChart = await initHomeChartToggle();
    splashStep('charts');
    await loadCurrent();
    loadGhostTrail();
    // ?chart= deep link (written by the toggle itself): honor it after the
    // first paint so the default load path stays untouched.
    if (deepLinkChart && deepLinkChart !== 'spotify') switchHomeChart(deepLinkChart);
  }

  // Dial + compass-header state for the daily reading. Split out of
  // loadCurrent so the chart toggle's switch-back-to-Spotify path renders the
  // identical dial without re-running the row-2.5 panels.
  function applyDailyDial(data) {
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
  }

  // --- Load Current Reading ---
  async function loadCurrent() {
    try {
      const data = await API.getCompassCurrent();

      applyDailyDial(data);

      // Render right panel
      renderReading(data);
      splashStep('reading');

      // Render weekly album reading if present
      renderAlbumReading(data);

      // Render the Ether Art Chart card (independent fetch — its own endpoint).
      // The splash's ether milestone fires when the render actually settles,
      // success or not.
      if (typeof EtherArtChart !== 'undefined') {
        EtherArtChart.render().then(
          () => splashStep('ether'),
          () => splashStep('ether')
        );
      } else {
        splashStep('ether');
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
      // Step the splash anyway so a failed fetch opens onto the error message
      // instead of stranding the splash until its safety net.
      splashStep('reading');
      splashStep('ether');
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
  // Cached so the chart toggle can clear it on a chart view (the trail is
  // daily-reading history) and restore it on switch-back without a refetch.
  let ghostTrailItems = [];
  async function loadGhostTrail() {
    try {
      const data = await API.getHistory(1, 30);
      if (data.items && data.items.length) {
        ghostTrailItems = data.items;
        if (homeChart === 'spotify') Compass.setGhostTrail(ghostTrailItems);
      }
    } catch (err) {
      // Silent fail — ghost trail is decorative
    }
  }

  // === Homepage chart toggle (rows 1+2) ====================================
  // One dropdown in the Today's Charge header swaps which daily chart feeds
  // the whole top dashboard: dial + trajectory Daily tab + reading card +
  // Ether Art Chart lens. Chart list comes from the Calendar's runtime
  // endpoint, so a new Tier-2 daily chart appears here on its own once its
  // first snapshot is approved. The Historical Charge Index never follows
  // the toggle (it is the Billboard-sourced macro index).
  let HOME_CHARTS = {};       // key -> {key, label, sub, source}
  let HOME_CHART_ORDER = [];
  let homeChart = 'spotify';
  let homeChartSwitching = false;
  let trajPanelHandle = null;

  // The rc-loader spinner these panels already use. gradId must be unique
  // per instance (SVG gradient ids are document-global).
  function homeLoaderHtml(label, sub, gradId) {
    return `<div class="trajectory-loading" role="status" aria-label="${escapeHtml(label)}">
      <svg class="rc-loader" viewBox="0 0 64 64" aria-hidden="true">
        <defs><linearGradient id="${gradId}" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#9933ff"/><stop offset="25%" stop-color="#3388ff"/>
          <stop offset="50%" stop-color="#33cc55"/><stop offset="75%" stop-color="#ffbb33"/>
          <stop offset="100%" stop-color="#ff3333"/></linearGradient></defs>
        <circle class="rc-loader-track" cx="32" cy="32" r="26" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="3"/>
        <circle class="rc-loader-arc" cx="32" cy="32" r="26" fill="none" stroke="url(#${gradId})" stroke-width="3" stroke-linecap="round" stroke-dasharray="60 200"/>
        <line class="rc-loader-needle" x1="32" y1="32" x2="32" y2="12" stroke="#eeeef4" stroke-width="2" stroke-linecap="round" transform-origin="32 32"/>
        <circle cx="32" cy="32" r="3" fill="#00d4aa"/>
      </svg>
      <div class="rc-loader-label">${escapeHtml(label)}</div>
      ${sub ? `<div class="rc-loader-sub">${escapeHtml(sub)}</div>` : ''}
    </div>`;
  }

  // No-shift swap: freeze the container at its current height in BOTH
  // directions (min-height alone lets a tall loader grow the box) and center
  // the loader in it; real content must never render under .is-switching
  // (its flex centering lays block children out side by side), so fills go:
  // unswitch, fill, release. #daily-chart-container is capsule-owned and
  // only ever gets the height hold.
  function holdHeight(el) {
    if (!el || !el.offsetHeight) return;
    el.style.height = el.offsetHeight + 'px';
    el.style.overflow = 'hidden';
  }
  function releaseHeight(el) {
    if (!el) return;
    el.style.height = '';
    el.style.overflow = '';
  }
  function beginSwap(el, html) {
    if (!el) return;
    holdHeight(el);
    el.classList.add('is-switching');
    if (html != null) el.innerHTML = html;
  }
  function unswitch(el) { if (el) el.classList.remove('is-switching'); }

  // Builds the dropdown from calendar-charts. Returns the ?chart= deep-link
  // key when valid (init honors it after first paint), else null. If the
  // list fetch fails or has <2 charts the toggle stays hidden and the
  // homepage behaves exactly as before.
  async function initHomeChartToggle() {
    const wrap = document.getElementById('hp-chart-switch');
    const menu = document.getElementById('hp-chart-menu');
    const btn = document.getElementById('hp-chart-btn');
    if (!wrap || !menu || !btn) return null;
    let list = null;
    try { list = await API.getCalendarCharts(); } catch (e) { /* stays hidden */ }
    if (!list || list.length < 2) return null;
    HOME_CHARTS = {}; HOME_CHART_ORDER = [];
    list.forEach(c => { HOME_CHARTS[c.key] = c; HOME_CHART_ORDER.push(c.key); });
    if (!HOME_CHARTS[homeChart]) homeChart = HOME_CHART_ORDER[0];

    menu.innerHTML = HOME_CHART_ORDER.map(key => {
      const c = HOME_CHARTS[key];
      return `<li class="hp-chart-opt" role="option" data-chart="${escapeHtml(key)}">${escapeHtml(c.label)}<span class="hp-chart-opt-sub">${escapeHtml(c.sub)}</span></li>`;
    }).join('');
    wrap.hidden = false;
    updateHomeChartUI();

    const close = () => { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const opening = menu.hidden;
      menu.hidden = !opening;
      btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
    menu.addEventListener('click', (e) => {
      const opt = e.target.closest('.hp-chart-opt');
      if (!opt) return;
      close();
      switchHomeChart(opt.dataset.chart);
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.hp-chart-switch')) close();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

    try {
      const q = new URLSearchParams(window.location.search).get('chart');
      if (q && HOME_CHARTS[q]) return q;
    } catch (e) {}
    return null;
  }

  function updateHomeChartUI() {
    const lbl = document.getElementById('hp-chart-btn-label');
    if (lbl && HOME_CHARTS[homeChart]) lbl.textContent = HOME_CHARTS[homeChart].label;
    document.querySelectorAll('.hp-chart-opt').forEach(o => {
      o.setAttribute('aria-selected', o.dataset.chart === homeChart ? 'true' : 'false');
    });
  }

  async function switchHomeChart(key) {
    if (homeChartSwitching || key === homeChart || !HOME_CHARTS[key]) return;
    homeChartSwitching = true;
    homeChart = key;
    updateHomeChartUI();
    // Reflect the chart in the URL so refresh keeps it (calendar parity).
    try {
      const u = new URL(window.location.href);
      if (key === 'spotify') u.searchParams.delete('chart');
      else u.searchParams.set('chart', key);
      window.history.replaceState({}, '', u);
    } catch (e) {}

    // Clear any time-traveled compass state (mirrors resetCompassToToday
    // minus its refetch; the switch does its own).
    const tmReset = document.getElementById('timemachine-reset');
    if (tmReset && tmReset.style.display !== 'none') tmReset.click();
    removeCompassCta();
    const calPicker = document.getElementById('cal-picker');
    if (calPicker) calPicker.remove();

    const c = HOME_CHARTS[key];
    const rc = document.getElementById('reading-content');
    const ec = document.getElementById('ether-art-chart-content');
    const dc = document.getElementById('daily-chart-container');

    beginSwap(rc, homeLoaderHtml('Loading ' + c.label, c.sub, 'hp-grad-reading'));
    beginSwap(ec, homeLoaderHtml('Loading the ether lens', 'deadpan + topics', 'hp-grad-ether'));
    setCompassDate('Loading\u2026');

    let trajDone = Promise.resolve();
    if (trajPanelHandle) {
      holdHeight(dc);
      trajDone = trajPanelHandle.reloadDaily();
    }

    if (key === 'spotify') {
      let data = null;
      try { data = await API.getCompassCurrent(); } catch (e) {}
      unswitch(rc); unswitch(ec);
      if (data) {
        applyDailyDial(data);
        renderReading(data);
        if (typeof EtherArtChart !== 'undefined') {
          try { await EtherArtChart.render(); } catch (e) {}
        }
        Compass.setGhostTrail(ghostTrailItems);
      } else {
        renderHomeChartEmpty(true);
      }
    } else {
      // The Unified Charge Chart is DERIVED, so it has no chart-snapshot row to
      // fetch. Its payload is deliberately shaped like a snapshot (date,
      // compass_degree, charge_level, contamination_count, editorial, songs) so
      // renderHomeChartReading takes it unchanged; only the fetch differs.
      let snap = null, failed = false;
      try {
        snap = key === 'unified' ? await API.getUnifiedCurrent()
                                 : await API.getChartSnapshot(key);
      } catch (e) { failed = true; }
      unswitch(rc); unswitch(ec);
      if (snap && (snap.songs || []).length) {
        renderHomeChartReading(snap);
      } else {
        renderHomeChartEmpty(failed);
      }
    }

    // crossfade swaps content ~160ms in; keep the height lock until the new
    // content is actually in the box so the release settles at most once.
    await new Promise(r => setTimeout(r, 220));
    releaseHeight(rc); releaseHeight(ec);
    try { await trajDone; } catch (e) {}
    releaseHeight(dc);
    homeChartSwitching = false;
  }

  // A Tier-2 chart snapshot through the canon shell: left card + ether lens
  // + the dial from the snapshot's stamped aggregate.
  function renderHomeChartReading(snap) {
    const c = HOME_CHARTS[homeChart] || {};
    const songs = (snap.songs || []).slice().sort((a, b) => a.position - b.position);

    const header = document.querySelector('#reading-panel .card-header');
    const desc = document.querySelector('#reading-panel .card-desc');
    if (header) header.textContent = c.label || 'Chart';
    if (desc) desc.textContent = (c.sub || '') + (snap.date ? '. Updated ' + formatDate(snap.date) + '.' : '');

    const reading = {
      date: snap.date,
      degree: snap.compass_degree,
      charge: snap.charge_level,
      contaminationCount: snap.contamination_count,
      editorial: snap.editorial,
      songs: songs,
    };
    const rc = document.getElementById('reading-content');
    crossfade(rc, ChartShell.buildLeft(reading), () => ChartShell.wireTooltips(rc));
    const ec = document.getElementById('ether-art-chart-content');
    if (ec) crossfade(ec, ChartShell.etherListHtml(songs));

    setCompassMode('today');
    Contamination.setCount(reading.contaminationCount || 0, songs.length || 20);
    if (reading.degree != null) {
      setTimeout(() => { Compass.setDegree(reading.degree, reading.charge); }, 300);
      const redCount = songs.filter(s => s.rubric_color === 'red').length;
      Charge.setLevel(reading.charge, redCount, songs.length, reading.degree);
      setCompassDate(reading.date ? formatDate(reading.date) : '');
    } else {
      setCompassDate('No aggregate yet');
    }
    // The ghost trail is daily-reading history; it comes back on switch-back.
    Compass.setGhostTrail([]);
    announce(`${c.label || 'Chart'} loaded${reading.date ? ' for ' + formatDate(reading.date) : ''}. ${songs.length} songs.`);
  }

  function renderHomeChartEmpty(failed) {
    const c = HOME_CHARTS[homeChart] || {};
    const header = document.querySelector('#reading-panel .card-header');
    const desc = document.querySelector('#reading-panel .card-desc');
    if (header && c.label) header.textContent = c.label;
    if (desc && c.sub) desc.textContent = c.sub;
    const msg = failed
      ? 'Could not load this chart right now. Try again in a moment.'
      : "This chart hasn't published a reading yet. Check back once today's run is approved.";
    const rc = document.getElementById('reading-content');
    if (rc) crossfade(rc, `<div class="no-reading"><p>${escapeHtml(msg)}</p></div>`);
    const ec = document.getElementById('ether-art-chart-content');
    if (ec) crossfade(ec, '<div class="ether-empty">No ether rows to show.</div>');
    setCompassDate('No reading');
  }
  // === /Homepage chart toggle ==============================================

  // --- Calendar Picker ---
  let rolodexDatesCache = {};  // { year: ["2026-01-15", ...] }
  let rolodexDegreeCache = {};  // { year: { "2026-01-15": 42.5, ... } } -- per-day compass_degree for calendar coloring

  // Historical year drift data (per-year compass_degree), shared by the compass
  // calendar picker + the year-songs overlay. Fetched once in init() and also
  // handed to the DailyChargePanel capsule, which renders the Historical tab.
  // (The Time-Machine compass save/restore that used to live here moved into the
  // capsule with the rest of the trajectory subsystem.)
  let allYearData = [];
  // Tracks the date string the homepage's "today" view rendered with -- used so
  // any source that drives the compass (calendar, year-songs) can decide whether
  // to dim the "Today's Charge" header or swap it to a past/year label.
  let currentTodayDate = null;

  function setCompassDate(text) {
    const dateEl = document.getElementById('compass-date-svg');
    if (dateEl) dateEl.textContent = text;
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
    // The header carries the chart dropdown beside the label, so write only
    // the label span (falling back for pages without the toggle markup).
    const headerLabel = header.querySelector('.hp-head-label');
    (headerLabel || header).textContent = text;
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
