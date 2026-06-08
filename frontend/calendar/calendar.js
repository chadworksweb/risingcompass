/* === Full-page Compass Calendar ===
   A large month calendar that doubles as a dial: every daily reading paints
   its day cell in that day's exact spectrum color (Charge.spectrumHexFromT,
   the same hue mechanic as the compass-container border/glow). Drill from
   days -> months -> years -> decades; pick a day to open its full reading in
   the inline detail panel. */

(function () {
  'use strict';

  var LIVE_CUTOFF = 2025; // years <= this have no daily readings (aggregate only)

  // Charts the calendar can paint. 'daily' source reads the daily-reading drift
  // endpoints; 'chart' source reads the chart-snapshot endpoints by registry
  // key. Brand names are working names ("for now") -- centralised here so a
  // rename is a single edit (label = the toggle button, sub = the source line).
  var CHARTS = {
    'daily-listens':   { label: 'Daily Listens',   sub: 'Spotify Top 50 - USA',   source: 'daily' },
    'daily-downloads': { label: 'Daily Downloads', sub: 'iTunes Downloads - USA', source: 'chart', key: 'itunes' },
  };
  var DEFAULT_CHART = 'daily-listens';
  var curChart = DEFAULT_CHART;
  function chartCfg() { return CHARTS[curChart] || CHARTS[DEFAULT_CHART]; }

  // --- Data-source adapter: same calendar, swappable chart behind it ---
  function fetchYears() {
    var c = chartCfg();
    return c.source === 'chart' ? API.getChartYears(c.key) : API.getDriftYears();
  }
  function fetchYearDates(year) {
    var c = chartCfg();
    return c.source === 'chart' ? API.getChartYearDates(c.key, year) : API.getYearDates(year);
  }
  function fetchReading(date) {
    var c = chartCfg();
    return c.source === 'chart' ? API.getChartReading(c.key, date) : API.getReading(date);
  }

  var MONTHS_FULL = ['January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];
  var MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  var CHARGE_LABEL = {
    violet: 'ASCENDED', blue: 'ELEVATED', green: 'DECENT',
    orange: 'DEGRADED', red: 'CORRUPTED', skipped: 'SKIPPED',
  };

  // --- State ---
  var view = 'day';        // 'day' | 'month' | 'year' | 'decade'
  var curYear, curMonth;   // curMonth 0-11
  var decadeStart;
  var selectedDate = null;

  // --- Caches ---
  var yearReadings = {};   // { year: { "2026-05-19": {degree, charge} } }
  var monthMeans = {};     // { year: { 4: 121.3, ... } }  mean degree per month
  var yearsList = null;    // [{year, compass_degree, charge_level}, ...]
  var yearAgg = {};        // { year: degree }

  // --- DOM ---
  var board, titleEl, prevBtn, nextBtn, detailEl, detailBody, layoutEl;

  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function scoreFromDegree(deg) {
    var s = Math.round((90 - deg) * 100 / 90);
    return (s > 0 ? '+' : '') + s;
  }
  function colorFor(deg) {
    return (typeof Charge !== 'undefined') ? Charge.spectrumHexFromT(deg / 180) : '#888';
  }
  function formatLong(dateStr) {
    var d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  }

  // --- Data ---
  async function loadYears() {
    if (yearsList) return;
    try {
      yearsList = await fetchYears();
    } catch (e) {
      yearsList = [];
    }
    yearsList.forEach(function (y) { yearAgg[y.year] = y.compass_degree; });
  }

  async function loadYearReadings(year) {
    if (year <= LIVE_CUTOFF) { yearReadings[year] = {}; monthMeans[year] = {}; return; }
    if (yearReadings[year]) return;
    var map = {};
    try {
      var resp = await fetchYearDates(year);
      (resp.readings || []).forEach(function (r) {
        map[r.date] = { degree: r.compass_degree, charge: r.charge_level };
      });
    } catch (e) { /* leave empty */ }
    yearReadings[year] = map;
    // Monthly means for the month-grid coloring.
    var sums = {}, counts = {};
    Object.keys(map).forEach(function (date) {
      var m = parseInt(date.slice(5, 7), 10) - 1;
      sums[m] = (sums[m] || 0) + map[date].degree;
      counts[m] = (counts[m] || 0) + 1;
    });
    var means = {};
    Object.keys(sums).forEach(function (m) { means[m] = sums[m] / counts[m]; });
    monthMeans[year] = means;
  }

  function yearBounds() {
    if (!yearsList || !yearsList.length) {
      var y = new Date().getFullYear();
      return { min: 2026, max: y };
    }
    var years = yearsList.map(function (d) { return d.year; });
    return { min: Math.min.apply(null, years), max: Math.max.apply(null, years) };
  }
  function yearHasData(y) {
    return yearAgg.hasOwnProperty(y);
  }

  // --- Render dispatch ---
  async function render() {
    if (view === 'day') {
      await loadYearReadings(curYear);
      renderDay();
    } else if (view === 'month') {
      await loadYearReadings(curYear);
      renderMonth();
    } else if (view === 'year') {
      renderYear();
    } else if (view === 'decade') {
      renderDecade();
    }
    updateNav();
  }

  function updateNav() {
    var b = yearBounds();
    var canPrev = true, canNext = true;
    if (view === 'day') {
      canPrev = !(curYear <= b.min && curMonth === 0);
      canNext = !(curYear >= b.max && curMonth === 11);
    } else if (view === 'month') {
      canPrev = curYear > b.min; canNext = curYear < b.max;
    } else if (view === 'year') {
      canPrev = decadeStart > Math.floor(b.min / 10) * 10;
      canNext = decadeStart < Math.floor(b.max / 10) * 10;
    } else {
      canPrev = false; canNext = false;
    }
    prevBtn.disabled = !canPrev;
    nextBtn.disabled = !canNext;
  }

  // --- Day grid (big month) ---
  function renderDay() {
    titleEl.textContent = MONTHS_FULL[curMonth] + ' ' + curYear;
    board.className = 'cal-board cal-board-days';

    if (curYear <= LIVE_CUTOFF) {
      board.innerHTML = '<div class="cal-board-msg">Year-level reading only.<br>No daily readings before 2026 -- zoom out to see the year on the dial.</div>';
      return;
    }

    var readings = yearReadings[curYear] || {};
    var firstDay = new Date(curYear, curMonth, 1).getDay();
    var daysInMonth = new Date(curYear, curMonth + 1, 0).getDate();
    var today = todayStr();

    var html = '<div class="cal-weekrow">';
    WEEKDAYS.forEach(function (d) { html += '<span class="cal-weekcell">' + d + '</span>'; });
    html += '</div><div class="cal-grid cal-grid-days">';

    for (var i = 0; i < firstDay; i++) html += '<span class="cal-day cal-day-empty"></span>';

    for (var d = 1; d <= daysInMonth; d++) {
      var dateStr = curYear + '-' + pad(curMonth + 1) + '-' + pad(d);
      var r = readings[dateStr];
      var cls = ['cal-day'];
      var style = '';
      var inner = '<span class="cal-day-num">' + d + '</span>';
      if (r) {
        cls.push('cal-day-has');
        style = ' style="--day-color:' + colorFor(r.degree) + '"';
        inner += '<span class="cal-day-score">' + scoreFromDegree(r.degree) + '</span>'
          + '<span class="cal-day-charge">' + (CHARGE_LABEL[r.charge] || r.charge.toUpperCase()) + '</span>';
      }
      if (dateStr === today) cls.push('cal-day-today');
      if (dateStr === selectedDate) cls.push('cal-day-selected');
      var attrs = r
        ? ' data-action="pick-day" data-date="' + dateStr + '" role="button" tabindex="0" aria-label="' + dateStr + '"'
        : '';
      html += '<span class="' + cls.join(' ') + '"' + style + attrs + '>' + inner + '</span>';
    }
    html += '</div>';
    board.innerHTML = html;
  }

  // --- Month grid (12 months of a year) ---
  function renderMonth() {
    titleEl.textContent = String(curYear);
    board.className = 'cal-board cal-board-months';
    var means = monthMeans[curYear] || {};
    var html = '<div class="cal-grid cal-grid-months">';
    for (var m = 0; m < 12; m++) {
      var mean = means[m];
      var cls = ['cal-tile'];
      var style = '';
      var sub = '';
      if (mean != null) {
        cls.push('cal-tile-has');
        style = ' style="--day-color:' + colorFor(mean) + '"';
        sub = '<span class="cal-tile-sub">' + scoreFromDegree(mean) + '</span>';
      }
      if (m === curMonth) cls.push('cal-tile-selected');
      html += '<span class="' + cls.join(' ') + '"' + style
        + ' data-action="pick-month" data-month="' + m + '" role="button" tabindex="0" aria-label="' + MONTHS_FULL[m] + ' ' + curYear + '">'
        + '<span class="cal-tile-label">' + MONTHS_SHORT[m] + '</span>' + sub + '</span>';
    }
    html += '</div>';
    board.innerHTML = html;
  }

  // --- Year grid (years of a decade) ---
  function renderYear() {
    decadeStart = Math.floor(curYear / 10) * 10;
    var end = decadeStart + 9;
    titleEl.textContent = decadeStart + ' - ' + end;
    board.className = 'cal-board cal-board-years';
    var html = '<div class="cal-grid cal-grid-years">';
    for (var y = decadeStart; y <= end; y++) {
      var has = yearHasData(y);
      var cls = ['cal-tile'];
      var style = '';
      var sub = '';
      if (has) {
        cls.push('cal-tile-has');
        style = ' style="--day-color:' + colorFor(yearAgg[y]) + '"';
        sub = '<span class="cal-tile-sub">' + scoreFromDegree(yearAgg[y]) + '</span>';
      }
      if (y === curYear) cls.push('cal-tile-selected');
      var attrs = has
        ? ' data-action="pick-year" data-year="' + y + '" role="button" tabindex="0"'
        : ' aria-disabled="true"';
      html += '<span class="' + cls.join(' ') + '"' + style + attrs + '>'
        + '<span class="cal-tile-label">' + y + '</span>' + sub + '</span>';
    }
    html += '</div>';
    board.innerHTML = html;
  }

  // --- Decade grid ---
  function renderDecade() {
    var b = yearBounds();
    var firstDec = Math.floor(b.min / 10) * 10;
    var lastDec = Math.floor(b.max / 10) * 10;
    titleEl.textContent = firstDec + 's - ' + lastDec + 's';
    board.className = 'cal-board cal-board-decades';
    var html = '<div class="cal-grid cal-grid-decades">';
    for (var dec = firstDec; dec <= lastDec; dec += 10) {
      var cls = ['cal-tile', 'cal-tile-has'];
      if (dec === decadeStart) cls.push('cal-tile-selected');
      html += '<span class="' + cls.join(' ') + '"'
        + ' data-action="pick-decade" data-decade="' + dec + '" role="button" tabindex="0">'
        + '<span class="cal-tile-label">' + dec + 's</span></span>';
    }
    html += '</div>';
    board.innerHTML = html;
  }

  // --- Actions ---
  function onPrev() {
    if (view === 'day') {
      curMonth--; if (curMonth < 0) { curMonth = 11; curYear--; }
    } else if (view === 'month') {
      curYear--;
    } else if (view === 'year') {
      decadeStart -= 10; curYear = decadeStart;
    }
    render();
  }
  function onNext() {
    if (view === 'day') {
      curMonth++; if (curMonth > 11) { curMonth = 0; curYear++; }
    } else if (view === 'month') {
      curYear++;
    } else if (view === 'year') {
      decadeStart += 10; curYear = decadeStart;
    }
    render();
  }
  function onZoomOut() {
    if (view === 'day') view = 'month';
    else if (view === 'month') view = 'year';
    else if (view === 'year') view = 'decade';
    render();
  }
  function onToday() {
    var d = new Date();
    curYear = d.getFullYear(); curMonth = d.getMonth();
    decadeStart = Math.floor(curYear / 10) * 10;
    view = 'day';
    render();
  }

  function handleAction(el) {
    var action = el.dataset.action;
    switch (action) {
      case 'prev': onPrev(); break;
      case 'next': onNext(); break;
      case 'zoom-out': onZoomOut(); break;
      case 'today': onToday(); break;
      case 'close-detail': closeDetail(); break;
      case 'pick-day':
        selectedDate = el.dataset.date;
        openDay(el.dataset.date);
        updateDaySelection(); // move the highlight only -- don't rebuild the board
        break;
      case 'pick-month':
        curMonth = parseInt(el.dataset.month, 10);
        view = 'day';
        render();
        break;
      case 'pick-year':
        curYear = parseInt(el.dataset.year, 10);
        decadeStart = Math.floor(curYear / 10) * 10;
        view = 'month';
        render();
        break;
      case 'pick-decade':
        decadeStart = parseInt(el.dataset.decade, 10);
        curYear = decadeStart;
        view = 'year';
        render();
        break;
    }
  }

  // --- Detail panel ---
  async function openDay(date) {
    var wasOpen = layoutEl.classList.contains('is-panel-open');
    detailEl.hidden = false;
    // Next frame so the column grow + panel fade run together from the closed state.
    requestAnimationFrame(function () { layoutEl.classList.add('is-panel-open'); });
    // Switching days with the panel already open: hold the current body height
    // so it doesn't collapse to the loader and then jump to the new reading --
    // released once the new content is in, so it resizes only once, if at all.
    if (wasOpen && detailBody.offsetHeight > 0) {
      detailBody.style.minHeight = detailBody.offsetHeight + 'px';
      detailBody.classList.add('is-switching');
    }
    detailBody.innerHTML = loaderHtml('Loading reading', formatLong(date));
    // Stacked layout (tablet/phone): the panel opens below the calendar, so
    // bring it into view -- otherwise a tap looks like nothing happened.
    if (window.matchMedia('(max-width: 820px)').matches) {
      requestAnimationFrame(function () {
        detailEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
    var reading;
    try {
      reading = await fetchReading(date);
    } catch (e) {
      detailBody.innerHTML = '<div class="cal-detail-loading">No reading found for this date.</div>';
      releaseBodyHeight();
      return;
    }
    var hex = colorFor(reading.compass_degree);
    var score = scoreFromDegree(reading.compass_degree);
    var label = CHARGE_LABEL[reading.charge_level] || reading.charge_level.toUpperCase();
    var redCount = (reading.songs || []).filter(function (s) { return s.rubric_color === 'red'; }).length;
    var total = (reading.songs || []).length;

    var html = '<div class="cal-detail-head">'
      + '<div class="cal-detail-date">' + formatLong(date) + '</div>'
      + '<div class="cal-detail-readout" style="--day-color:' + hex + '">'
      + '<span class="cal-detail-score">' + score + '</span>'
      + '<span class="cal-detail-charge">' + label + '</span>'
      + '</div>'
      + '</div>';

    var contamCls = reading.contamination_count > 0 ? 'cal-detail-contam' : 'cal-detail-contam is-clean';
    html += '<div class="' + contamCls + '">'
      + reading.contamination_count + ' of ' + total + ' top songs carry corrupted-charge lyrics'
      + '</div>';

    if (reading.editorial_summary) {
      html += '<p class="cal-detail-summary">' + escapeHtml(reading.editorial_summary) + '</p>';
    }

    if (total) {
      html += '<ul class="cal-detail-songs">';
      (reading.songs || []).slice().sort(function (a, b) {
        return (a.position || 99) - (b.position || 99);
      }).forEach(function (s) {
        var dotColor = s.rubric_color ? ('var(--rc-' + s.rubric_color + ')') : 'var(--rc-border-ui)';
        var titleHtml = s.song_slug
          ? '<a href="/songs/' + encodeURIComponent(s.song_slug) + '">' + escapeHtml(s.title) + '</a>'
          : escapeHtml(s.title);
        var artistHtml = s.artist_slug
          ? '<a href="/artists/' + encodeURIComponent(s.artist_slug) + '">' + escapeHtml(s.artist) + '</a>'
          : escapeHtml(s.artist || '');
        html += '<li class="cal-detail-song">'
          + '<span class="cal-detail-dot" style="background:' + dotColor + '"></span>'
          + '<span class="cal-detail-song-meta"><span class="cal-detail-song-title">' + titleHtml + '</span>'
          + '<span class="cal-detail-song-artist">' + artistHtml + '</span></span>'
          + '</li>';
      });
      html += '</ul>';
    }

    detailBody.innerHTML = html;
    releaseBodyHeight();
  }

  // Release the held height so the panel settles to the new reading's height.
  function releaseBodyHeight() {
    detailBody.classList.remove('is-switching');
    detailBody.style.minHeight = '';
  }

  function closeDetail() {
    // Column shrink + panel fade run concurrently (same timeline).
    layoutEl.classList.remove('is-panel-open');
    selectedDate = null;
    // Remove from the layout/a11y tree only after the exit transition finishes.
    window.setTimeout(function () {
      if (!layoutEl.classList.contains('is-panel-open')) detailEl.hidden = true;
    }, 320);
    if (view === 'day') updateDaySelection();
  }

  // Move the selected-day highlight without rebuilding the board, so picking a
  // day changes only the selection -- the calendar itself never re-animates.
  function updateDaySelection() {
    if (!board) return;
    var prev = board.querySelector('.cal-day-selected');
    if (prev) prev.classList.remove('cal-day-selected');
    if (selectedDate) {
      var cell = board.querySelector('.cal-day[data-date="' + selectedDate + '"]');
      if (cell) cell.classList.add('cal-day-selected');
    }
  }

  // Mini animated calendar loader: a grid of little day-squares that flicker
  // through the spectrum (violet -> red, left to right) -- the calendar's own
  // take on the site loader, in the same style/colors as the compass .rc-loader.
  function loaderHtml(label, sub) {
    var cols = 7, rows = 4, size = 10, gap = 3, headH = 7, headGap = 5;
    var w = cols * size + (cols - 1) * gap;
    var gridTop = headH + headGap;
    var h = gridTop + rows * size + (rows - 1) * gap;
    var svg = '<svg class="cal-loader" viewBox="0 0 ' + w + ' ' + h + '" aria-hidden="true">'
      + '<rect class="cal-loader-head" x="0" y="0" width="' + w + '" height="' + headH + '" rx="2"/>';
    var i = 0;
    for (var r = 0; r < rows; r++) {
      for (var c = 0; c < cols; c++) {
        var x = c * (size + gap);
        var y = gridTop + r * (size + gap);
        var color = colorFor((c / (cols - 1)) * 180); // spectrum across columns
        var delay = (((i * 37) % 100) / 100 * 1.4).toFixed(2); // pseudo-random twinkle
        svg += '<rect class="cal-loader-cell" x="' + x + '" y="' + y + '" width="' + size
          + '" height="' + size + '" rx="2" fill="' + color
          + '" style="animation-delay:' + delay + 's"/>';
        i++;
      }
    }
    svg += '</svg>';
    return '<div class="trajectory-loading" role="status" aria-label="' + escapeHtml(label) + '">'
      + svg
      + '<div class="rc-loader-label">' + escapeHtml(label) + '</div>'
      + (sub ? '<div class="rc-loader-sub">' + escapeHtml(sub) + '</div>' : '')
      + '</div>';
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // --- Chart switching ---
  // The per-chart caches are all keyed by year only, so switching charts means
  // wiping them and reloading from the new source. View/year/month are kept so
  // the user stays where they were on the dial.
  function resetForChart() {
    yearReadings = {}; monthMeans = {}; yearsList = null; yearAgg = {};
  }
  function updateChartUI() {
    var c = chartCfg();
    document.querySelectorAll('.cal-chart-btn').forEach(function (btn) {
      var on = btn.dataset.chart === curChart;
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    var sub = document.getElementById('cal-chart-sub');
    if (sub) sub.textContent = c.sub;
  }
  async function switchChart(key) {
    if (!CHARTS[key] || key === curChart) return;
    curChart = key;
    closeDetail();
    updateChartUI();
    resetForChart();
    // Reflect the chart in the URL so footer deep-links + refresh keep it.
    try {
      var u = new URL(window.location.href);
      u.searchParams.set('chart', key);
      window.history.replaceState({}, '', u);
    } catch (e) {}
    board.innerHTML = loaderHtml('Loading ' + chartCfg().label, chartCfg().sub);
    await loadYears();
    var b = yearBounds();
    if (curYear > b.max) curYear = b.max;
    if (curYear < b.min) curYear = b.min;
    await render();
  }

  // --- Wiring ---
  function wire() {
    document.querySelectorAll('.cal-chart-btn').forEach(function (btn) {
      btn.addEventListener('click', function () { switchChart(btn.dataset.chart); });
    });
    document.querySelectorAll('.cal-bar [data-action]').forEach(function (el) {
      el.addEventListener('click', function () { handleAction(el); });
    });
    detailEl.addEventListener('click', function (e) {
      var el = e.target.closest('[data-action]');
      if (el) handleAction(el);
    });
    // Event delegation for board cells (rebuilt on every render).
    board.addEventListener('click', function (e) {
      var el = e.target.closest('[data-action]');
      if (el) handleAction(el);
    });
    board.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var el = e.target.closest('[data-action]');
      if (el) { e.preventDefault(); handleAction(el); }
    });
  }

  async function init() {
    board = document.getElementById('cal-board');
    titleEl = document.querySelector('.cal-bar-title');
    prevBtn = document.querySelector('.cal-bar-nav[data-action="prev"]');
    nextBtn = document.querySelector('.cal-bar-nav[data-action="next"]');
    detailEl = document.getElementById('cal-detail');
    detailBody = document.getElementById('cal-detail-body');
    layoutEl = document.querySelector('.cal-page-layout');
    if (!board) return;

    // Initial chart from ?chart= (footer deep-links / refresh), else default.
    try {
      var qChart = new URLSearchParams(window.location.search).get('chart');
      if (qChart && CHARTS[qChart]) curChart = qChart;
    } catch (e) {}

    var d = new Date();
    curYear = d.getFullYear();
    curMonth = d.getMonth();
    decadeStart = Math.floor(curYear / 10) * 10;
    view = 'day';

    wire();
    updateChartUI();
    board.innerHTML = loaderHtml('Loading ' + chartCfg().label, chartCfg().sub);
    await loadYears();
    // If the calendar's "today" year has no live data yet, fall back to the
    // most recent year that does so the dial opens on something colored.
    var b = yearBounds();
    if (curYear > b.max) { curYear = b.max; }
    await render();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
