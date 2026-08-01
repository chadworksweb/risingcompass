/* === SANDBOX: Homepage chart toggle ========================================
   Prototype for the homepage rows 1+2 chart toggle: one dropdown that swaps
   which daily chart feeds the whole top dashboard (dial + trajectory panel +
   reading + Ether Art Chart lens). Not linked, not shipped.

   Composition (all production modules, nothing forked):
   - Chart list:   /api/compass/chart/calendar-charts (the calendar's runtime
                   toggle list: Daily Listens first, then every Tier-2 daily
                   chart with painted data; a new chart appears on its own).
   - Reading data: 'daily' source -> /api/compass/current + ether today;
                   'chart' source -> /api/compass/chart/<key>/current
                   (the one fetch fills both row-2 cards). Same adapters as
                   charts/chart.js.
   - Trajectory:   the shared DailyChargePanel capsule, mounted ONCE with a
                   loadDaily closure that reads the current selection, then
                   reloadDaily() on every switch.
   - No-shift swap: the calendar detail panel's mechanic (hold each swapped
                   container at its current height, show the loader centered
                   inside it, release only after the new content is in). The
                   dashboard never moves during a load. */

(function () {
  'use strict';

  var S = window.ChartShell;

  var FALLBACK_CHARTS = [
    { key: 'spotify', label: 'Spotify (US)', sub: 'Spotify Top 50 - USA', source: 'daily' },
  ];
  var CHARTS = {};       // { key: {key, label, sub, source} }
  var CHART_ORDER = [];  // keys in dropdown order
  var curChart = 'spotify';
  var switching = false;
  var trajHandle = null;

  function chartCfg() { return CHARTS[curChart] || FALLBACK_CHARTS[0]; }

  function setChartList(list) {
    if (!list || !list.length) list = FALLBACK_CHARTS;
    CHARTS = {}; CHART_ORDER = [];
    list.forEach(function (c) { CHARTS[c.key] = c; CHART_ORDER.push(c.key); });
    if (!CHARTS[curChart]) curChart = CHART_ORDER[0];
  }

  // Short-form date for the card desc, e.g. "Updated June 9, 2026."
  function formatDateShort(dateStr) {
    if (!dateStr) return '';
    var d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  }

  // The same rc-loader spinner the homepage/chart panels ship. gradId must be
  // unique per instance (SVG gradient ids are document-global).
  function loaderHtml(label, sub, gradId) {
    return '<div class="trajectory-loading" role="status" aria-label="' + S.escapeHtml(label) + '">'
      + '<svg class="rc-loader" viewBox="0 0 64 64" aria-hidden="true">'
      + '<defs><linearGradient id="' + gradId + '" x1="0" y1="0" x2="1" y2="0">'
      + '<stop offset="0%" stop-color="#9933ff"/><stop offset="25%" stop-color="#3388ff"/>'
      + '<stop offset="50%" stop-color="#33cc55"/><stop offset="75%" stop-color="#ffbb33"/>'
      + '<stop offset="100%" stop-color="#ff3333"/></linearGradient></defs>'
      + '<circle class="rc-loader-track" cx="32" cy="32" r="26" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="3"/>'
      + '<circle class="rc-loader-arc" cx="32" cy="32" r="26" fill="none" stroke="url(#' + gradId + ')" stroke-width="3" stroke-linecap="round" stroke-dasharray="60 200"/>'
      + '<line class="rc-loader-needle" x1="32" y1="32" x2="32" y2="12" stroke="#eeeef4" stroke-width="2" stroke-linecap="round" transform-origin="32 32"/>'
      + '<circle cx="32" cy="32" r="3" fill="#00d4aa"/>'
      + '</svg>'
      + '<div class="rc-loader-label">' + S.escapeHtml(label) + '</div>'
      + (sub ? '<div class="rc-loader-sub">' + S.escapeHtml(sub) + '</div>' : '')
      + '</div>';
  }

  // --- No-shift swap (the calendar's held-height mechanic) -----------------
  // beginSwap freezes the container and swaps in the loader; the .is-switching
  // flex centering must come OFF before any real content renders (block
  // children lay out side by side under it and the chart measures itself
  // squished -- the ss24 bug), so real fills go: unswitch, fill, release.
  function beginSwap(el, html) {
    if (!el) return;
    holdHeight(el);
    el.classList.add('is-switching');
    if (html != null) el.innerHTML = html;
  }
  function unswitch(el) { if (el) el.classList.remove('is-switching'); }
  // Lock the height BOTH ways: min-height alone stops a shrink but a loader
  // taller than the held content still grows the box and pushes the rows
  // below (caught on the mobile audit). Fixed height + hidden overflow means
  // the container cannot move at all while the swap is in flight.
  // #daily-chart-container (capsule-owned content) only ever gets this hold,
  // never .is-switching.
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

  function setCompassDate(text) {
    var el = document.getElementById('compass-date-svg');
    if (el) el.textContent = text;
  }

  // --- Data adapters (mirror charts/chart.js) ------------------------------
  async function fetchReading(key) {
    var c = CHARTS[key] || FALLBACK_CHARTS[0];
    if (c.source === 'daily') {
      var data = await API.getCompassCurrent();
      if (!data.has_reading || !(data.songs || []).length) return null;
      var etherSongs = [];
      try { etherSongs = (await API.getEtherToday()).items || []; } catch (e) { /* left card still renders */ }
      return {
        reading: {
          date: data.date, degree: data.compass_degree, charge: data.charge_level,
          contaminationCount: data.contamination_count, editorial: data.editorial_summary, songs: data.songs,
        },
        etherSongs: etherSongs,
      };
    }
    var snap = await API.getChartSnapshot(key);
    if (!(snap.songs || []).length) return null;
    return {
      reading: {
        date: snap.date, degree: snap.compass_degree, charge: snap.charge_level,
        contaminationCount: snap.contamination_count, editorial: snap.editorial, songs: snap.songs,
      },
      etherSongs: null,  // the snapshot rows carry the ether fields themselves
    };
  }

  // --- Render --------------------------------------------------------------
  function renderPanels(res) {
    var c = chartCfg();
    var header = document.getElementById('reading-header');
    var desc = document.getElementById('reading-desc');
    if (header) header.textContent = c.label;
    if (desc) {
      desc.textContent = c.sub + (res.reading.date ? '. Updated ' + formatDateShort(res.reading.date) + '.' : '');
    }

    var rc = document.getElementById('reading-content');
    if (rc) {
      rc.innerHTML = S.buildLeft(res.reading);
      S.wireTooltips(rc);
    }
    var ec = document.getElementById('ether-art-chart-content');
    if (ec) {
      ec.innerHTML = res.etherSongs ? S.etherListHtml(res.etherSongs) : S.buildRight(res.reading);
    }
  }

  function renderDial(reading) {
    var songs = reading.songs || [];
    if (reading.degree != null) {
      Compass.setDegree(reading.degree, reading.charge);
      if (typeof Charge !== 'undefined') {
        var redCount = songs.filter(function (s) { return s.rubric_color === 'red'; }).length;
        Charge.setLevel(reading.charge, redCount, songs.length, reading.degree);
      }
    }
    if (typeof Contamination !== 'undefined') {
      Contamination.setCount(reading.contaminationCount || 0, songs.length || 20);
    }
    setCompassDate(reading.date ? S.formatDate(reading.date) : '');
  }

  function renderEmptyReading(failed) {
    var c = chartCfg();
    var header = document.getElementById('reading-header');
    var desc = document.getElementById('reading-desc');
    if (header) header.textContent = c.label;
    if (desc) desc.textContent = c.sub;
    var msg = failed
      ? 'Could not load this chart. Is the API running?'
      : "This chart hasn't published a reading yet. Check back once today's run is approved.";
    var rc = document.getElementById('reading-content');
    if (rc) rc.innerHTML = '<div class="no-reading"><p>' + S.escapeHtml(msg) + '</p></div>';
    var ec = document.getElementById('ether-art-chart-content');
    if (ec) ec.innerHTML = '<div class="no-reading"><p>No ether rows to show.</p></div>';
    setCompassDate('No reading');
  }

  // --- Switch --------------------------------------------------------------
  // initial=true skips the same-chart guard AND the trajectory reload (the
  // capsule mount just kicked off its own first load for this chart).
  async function switchChart(key, initial) {
    if (!initial && (switching || key === curChart || !CHARTS[key])) return;
    switching = true;
    curChart = key;
    updateToggleUI();

    // Keep the choice in the URL so refresh / deep links hold it (calendar parity).
    try {
      var u = new URL(window.location.href);
      u.searchParams.set('chart', key);
      window.history.replaceState({}, '', u);
    } catch (e) {}

    var c = chartCfg();
    var rc = document.getElementById('reading-content');
    var ec = document.getElementById('ether-art-chart-content');
    var dc = document.getElementById('daily-chart-container');

    beginSwap(rc, loaderHtml('Loading ' + c.label, c.sub, 'hp-grad-reading'));
    beginSwap(ec, loaderHtml('Loading the ether lens', 'deadpan + topics', 'hp-grad-ether'));
    setCompassDate('Loading\u2026');

    var trajDone = Promise.resolve();
    if (!initial && trajHandle) {
      holdHeight(dc);
      trajDone = trajHandle.reloadDaily();
    }

    var res = null, failed = false;
    try { res = await fetchReading(key); } catch (e) { failed = true; }

    unswitch(rc); unswitch(ec);
    if (res) {
      renderPanels(res);
      renderDial(res.reading);
    } else {
      renderEmptyReading(failed);
    }
    releaseHeight(rc); releaseHeight(ec);

    try { await trajDone; } catch (e) {}
    releaseHeight(dc);
    switching = false;
  }

  // --- Dropdown ------------------------------------------------------------
  function buildToggle() {
    var wrap = document.getElementById('hp-chart-switch');
    var menu = document.getElementById('hp-chart-menu');
    if (!wrap || !menu) return;
    if (CHART_ORDER.length < 2) { wrap.hidden = true; return; }
    wrap.hidden = false;
    menu.innerHTML = CHART_ORDER.map(function (key) {
      var c = CHARTS[key];
      return '<li class="hp-chart-opt" role="option" data-chart="' + S.escapeHtml(key) + '">'
        + S.escapeHtml(c.label)
        + '<span class="hp-chart-opt-sub">' + S.escapeHtml(c.sub) + '</span>'
        + '</li>';
    }).join('');
    updateToggleUI();
  }

  function updateToggleUI() {
    var lbl = document.getElementById('hp-chart-btn-label');
    if (lbl) lbl.textContent = chartCfg().label;
    document.querySelectorAll('.hp-chart-opt').forEach(function (o) {
      o.setAttribute('aria-selected', o.dataset.chart === curChart ? 'true' : 'false');
    });
  }

  function wireToggle() {
    var btn = document.getElementById('hp-chart-btn');
    var menu = document.getElementById('hp-chart-menu');
    if (!btn || !menu) return;
    function close() {
      menu.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
    }
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var opening = menu.hidden;
      menu.hidden = !opening;
      btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
    menu.addEventListener('click', function (e) {
      var opt = e.target.closest('.hp-chart-opt');
      if (!opt) return;
      close();
      switchChart(opt.dataset.chart);
    });
    document.addEventListener('click', function (e) {
      if (!e.target.closest('.hp-chart-switch')) close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') close();
    });
  }

  // --- Init ----------------------------------------------------------------
  async function init() {
    // Chart list FIRST so the capsule's initial series load already targets
    // the deep-linked chart (no wrong-chart flash, no double fetch).
    var list = null;
    try { list = await API.getCalendarCharts(); } catch (e) {}
    setChartList(list);
    try {
      var q = new URLSearchParams(window.location.search).get('chart');
      if (q && CHARTS[q]) curChart = q;
    } catch (e) {}

    Compass.render('compass-container');
    Contamination.render('contam-container');

    var trajPanel = document.getElementById('trajectory-panel');
    if (trajPanel && window.DailyChargePanel) {
      trajHandle = DailyChargePanel.mount(trajPanel, {
        // Closure reads the live selection, so one mount serves every chart;
        // switches go through trajHandle.reloadDaily().
        loadDaily: function () {
          var c = chartCfg();
          return c.source === 'daily' ? API.getDailyChart() : API.getChartDailyChart(curChart);
        },
        // The Historical Charge Index is the macro index (chart-independent),
        // so it loads once and stays put across switches (homepage behavior).
        loadHistorical: function () { return API.getDriftYears(); },
        eraTaglines: { daily: 'trailing days, day by day' },
      });
    }

    buildToggle();
    wireToggle();
    await switchChart(curChart, true);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
