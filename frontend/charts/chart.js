/* === Standalone chart page ===
   Renders the SAME paired "chart reading" shell the homepage shows -- left card
   (charge group + editorial + song list) beside its Ether Art Chart (deadpan +
   topic) -- for one chart, full width on a dedicated page. All templating lives
   in /js/chart-shell.js (ChartShell); this file is just the page's data wiring.

   Each page sets window.RC_CHART = { source, title, sub } inline:
     source 'daily'  -> /api/compass/current        (Spotify (US); the daily
                        reading carries the aggregate; its ether view comes from
                        the separate /api/ether-art-chart/today endpoint)
     any other source is a chart-snapshot registry key (itunes | shazam |
     youtube) -> /api/compass/chart/<source>/current, whose rows carry BOTH the
                 list fields and the deadpan_line/dominant_topic ether fields, so
                 one fetch fills both cards. */

(function () {
  'use strict';

  var CFG = window.RC_CHART || { source: 'daily', title: 'Chart', sub: '' };
  var S = window.ChartShell;

  function formatDate(dateStr) {
    if (!dateStr) return '';
    var d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  }

  function renderEmpty(root, msg) {
    root.innerHTML = '<div class="card"><div class="card-header">' + S.escapeHtml(CFG.title) + '</div>'
      + '<p class="card-desc">' + S.escapeHtml(CFG.sub) + '</p>'
      + '<div class="no-reading"><p>' + S.escapeHtml(msg) + '</p></div></div>';
  }

  // Paired two-card shell, mirroring the homepage dashboard pairing.
  function renderShell(root, reading, etherSongs) {
    var leftDesc = S.escapeHtml(CFG.sub) + (reading.date ? '. Updated ' + S.escapeHtml(formatDate(reading.date)) + '.' : '');
    var rightHtml = etherSongs ? S.etherListHtml(etherSongs) : S.buildRight(reading);

    root.innerHTML =
      '<div class="chart-shell-grid">'
        + '<div class="card" id="chart-reading-panel">'
          + '<div class="card-header">' + S.escapeHtml(CFG.title) + '</div>'
          + '<p class="card-desc">' + leftDesc + '</p>'
          + '<div id="chart-reading-content">' + S.buildLeft(reading) + '</div>'
        + '</div>'
        + '<div class="card" id="chart-ether-panel">'
          + '<div class="card-header"><a href="/ether-art-chart/" class="ether-card-link">The Ether Art Chart</a></div>'
          + '<p class="card-desc">The same songs named for what the lyrics really say, plus the topics pulled through the ether.</p>'
          + '<div id="chart-ether-content">' + rightHtml + '</div>'
          + '<a href="/ether-art-chart/" class="ether-open-btn">Open The Ether Art Chart'
            + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>'
          + '</a>'
        + '</div>'
      + '</div>';

    S.wireTooltips(document.getElementById('chart-reading-content'));
  }

  // Daily-chart math, copied from the homepage (js/app.js) so the chart reads
  // identically. COLOR_HEX/CHARGE_LABELS come from ChartShell.
  var COLOR_HEX = (S && S.COLOR_HEX) || { violet: '#9933ff', blue: '#3388ff', green: '#33cc55', orange: '#ffbb33', red: '#ff3333' };
  var CHARGE_LABELS = (S && S.CHARGE_LABELS) || {};
  var SCALE_FIT_PAD = 5, SCALE_FIT_MIN = 10;
  // 'fit' = auto-fit the axis to the data; 'full' = the whole +/-100 domain.
  // The charge-axis labels ARE the toggle (click any -> flip), same as app.js.
  var scaleMode = 'fit';

  function degreeToCharge(degree) { return (90 - degree) / 0.9; }
  function degreeToScore(degree) { var v = Math.round(degreeToCharge(degree)); return (v > 0 ? '+' : '') + v; }
  function resolveScale(data) {
    if (scaleMode === 'full' || !data || !data.length) return 100;
    var maxAbs = 0;
    for (var i = 0; i < data.length; i++) { var a = Math.abs(degreeToCharge(data[i].compass_degree)); if (a > maxAbs) maxAbs = a; }
    return Math.min(100, Math.max(SCALE_FIT_MIN, Math.ceil(maxAbs) + SCALE_FIT_PAD));
  }
  function chargeToFrac(charge, scale) { return Math.max(0, Math.min(1, (scale - charge) / (2 * scale))); }
  function chargeDegreeToY(degree, padT, chartH, scale) { return padT + chargeToFrac(degreeToCharge(degree), scale) * chartH; }
  function chargeGridRows(scale) {
    return [
      { charge: scale, label: '+' + scale },
      { charge: scale / 2, label: '' },
      { charge: 0, label: '0' },
      { charge: -scale / 2, label: '' },
      { charge: -scale, label: '-' + scale },
    ];
  }
  function interpolateSkippedDegrees(data) {
    var n = data.length; if (!n) return data;
    var prev = new Array(n).fill(-1), next = new Array(n).fill(-1), last = -1, i;
    for (i = 0; i < n; i++) { prev[i] = last; if (data[i].charge_level !== 'skipped') last = i; }
    last = -1;
    for (i = n - 1; i >= 0; i--) { next[i] = last; if (data[i].charge_level !== 'skipped') last = i; }
    return data.map(function (d, idx) {
      if (d.charge_level !== 'skipped') return d;
      var p = prev[idx], x = next[idx], interp = d.compass_degree;
      if (p >= 0 && x >= 0) interp = (data[p].compass_degree + data[x].compass_degree) / 2;
      else if (p >= 0) interp = data[p].compass_degree;
      else if (x >= 0) interp = data[x].compass_degree;
      return Object.assign({}, d, { compass_degree: interp });
    });
  }

  var trajPoints = [];

  // Dial: the reused Compass gauge + contamination badge, from the aggregate.
  // (Compass/Contamination are top-level `const` modules, so they live on the
  // global lexical binding, NOT window -- guard with typeof, reference bare.)
  function renderDial(reading) {
    if (document.getElementById('compass-container') && typeof Compass !== 'undefined') {
      Compass.render('compass-container');
      setTimeout(function () { Compass.setDegree(reading.degree, reading.charge); }, 200);
    }
    if (document.getElementById('contam-container') && typeof Contamination !== 'undefined') {
      Contamination.render('contam-container');
      Contamination.setCount(reading.contaminationCount || 0, (reading.songs || []).length || 20);
    }
  }

  // Trajectory = the homepage Daily Charge chart, reproduced faithfully (tier
  // gradient line + area fill, grid, y-labels, month-boundary x-axis, end dot,
  // hover tooltip). Reuses the global .trajectory-*/.traj-* classes from main.css
  // so it reads identically. (The zoom presets + Time Machine, being app.js
  // stateful widgets, are intentionally not ported.)
  var trajState = { data: null, host: null };
  var scaleToggleWired = false;

  function renderTrajectory(series, reading) {
    var host = document.getElementById('chart-traj-container');
    if (!host) return;
    var data = (series || []).filter(function (p) { return p && p.compass_degree != null; });
    if (!data.length && reading && reading.degree != null) {
      data = [{ date: reading.date, compass_degree: reading.degree, charge_level: reading.charge }];
    }
    if (!data.length) { host.innerHTML = '<div class="daily-empty">No daily readings yet.</div>'; return; }
    trajState.data = data; trajState.host = host;
    host.innerHTML = '<div class="traj-chart-area"></div>';
    drawDailyChart(data, host);
    wireScaleToggle();
  }

  // The charge-axis labels (+N / 0 / -N) ARE the scale toggle: click any to flip
  // between auto-fit and the full +/-100 domain. Delegated off the stable panel.
  function wireScaleToggle() {
    if (scaleToggleWired) return;
    var panel = document.getElementById('trajectory-panel');
    if (!panel) return;
    scaleToggleWired = true;
    panel.addEventListener('click', function (e) {
      var hit = e.target.closest && e.target.closest('.trajectory-y-label, .traj-y-hit');
      if (!hit) return;
      scaleMode = scaleMode === 'fit' ? 'full' : 'fit';
      if (trajState.data && trajState.host) drawDailyChart(trajState.data, trajState.host);
    });
  }

  function drawDailyChart(rawData, container) {
    var data = interpolateSkippedDegrees(rawData);
    var scale = resolveScale(data);
    var W = 320, H = 120, padL = 30, padR = 16, padT = 10, padB = 22;
    var chartW = W - padL - padR, chartH = H - padT - padB;
    var maxIdx = data.length - 1;

    trajPoints = data.map(function (d, i) {
      return {
        x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
        y: chargeDegreeToY(d.compass_degree, padT, chartH, scale),
        degree: d.compass_degree, date: d.date, color: d.charge_level,
      };
    });

    var linePath = trajPoints.map(function (p, i) { return (i === 0 ? 'M' : 'L') + ' ' + p.x.toFixed(1) + ' ' + p.y.toFixed(1); }).join(' ');
    var areaPath = linePath + ' L ' + trajPoints[maxIdx].x.toFixed(1) + ' ' + (padT + chartH) + ' L ' + trajPoints[0].x.toFixed(1) + ' ' + (padT + chartH) + ' Z';

    var svg = '<svg class="trajectory-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="Charge trajectory chart">';
    svg += '<defs>'
      + '<linearGradient id="ct-grad" gradientUnits="userSpaceOnUse" x1="0" y1="' + padT + '" x2="0" y2="' + (padT + chartH) + '">'
        + '<stop offset="0%" stop-color="' + COLOR_HEX.violet + '"/><stop offset="25%" stop-color="' + COLOR_HEX.blue + '"/>'
        + '<stop offset="50%" stop-color="' + COLOR_HEX.green + '"/><stop offset="75%" stop-color="' + COLOR_HEX.orange + '"/>'
        + '<stop offset="100%" stop-color="' + COLOR_HEX.red + '"/></linearGradient>'
      + '<linearGradient id="ct-area-grad" gradientUnits="userSpaceOnUse" x1="0" y1="' + padT + '" x2="0" y2="' + (padT + chartH) + '">'
        + '<stop offset="0%" stop-color="' + COLOR_HEX.violet + '" stop-opacity="0.2"/>'
        + '<stop offset="50%" stop-color="' + COLOR_HEX.green + '" stop-opacity="0.05"/>'
        + '<stop offset="100%" stop-color="' + COLOR_HEX.red + '" stop-opacity="0.2"/></linearGradient>'
      + '<clipPath id="ct-clip"><rect x="0" y="0" width="' + W + '" height="' + H + '"/></clipPath></defs>';

    // Full-height transparent strip over the y gutter = the scale toggle's tap
    // target (the labels alone are tiny). Rendered before the labels so their
    // own hover still fires when pointed directly.
    svg += '<rect class="traj-y-hit" x="0" y="0" width="' + padL + '" height="' + H + '" fill="transparent"/>';
    var yhTitle = scaleMode === 'fit' ? 'Click to show the full +/-100 range' : 'Click to auto-fit the range';
    chargeGridRows(scale).forEach(function (row) {
      var y = padT + chargeToFrac(row.charge, scale) * chartH;
      svg += '<line class="trajectory-grid-line" x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '"/>';
      if (row.label) svg += '<text class="trajectory-y-label" x="' + (padL - 4) + '" y="' + (y + 3).toFixed(1) + '"><title>' + yhTitle + '</title>' + row.label + '</text>';
    });

    svg += '<g clip-path="url(#ct-clip)">';
    svg += '<path class="trajectory-area" d="' + areaPath + '" fill="url(#ct-area-grad)"/>';
    svg += '<path class="trajectory-line" d="' + linePath + '" stroke="url(#ct-grad)"/>';
    svg += '</g>';

    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var daySpan = maxIdx > 0 ? (new Date(data[maxIdx].date) - new Date(data[0].date)) / 86400000 : 0;
    var minLabelGap = 40, boundaries = [], prevDate = null;
    trajPoints.forEach(function (p, i) {
      var d = new Date(data[i].date + 'T00:00:00');
      if (daySpan > 90) {
        if (!prevDate || d.getMonth() !== prevDate.getMonth() || d.getFullYear() !== prevDate.getFullYear()) boundaries.push({ x: p.x, label: MONTHS[d.getMonth()] });
      } else if (daySpan > 14) {
        if (!prevDate || Math.floor((d - new Date(d.getFullYear(), 0, 1)) / 604800000) !== Math.floor((prevDate - new Date(prevDate.getFullYear(), 0, 1)) / 604800000)) boundaries.push({ x: p.x, label: MONTHS[d.getMonth()] + ' ' + d.getDate() });
      } else {
        boundaries.push({ x: p.x, label: String(d.getMonth() + 1).padStart(2, '0') + '/' + String(d.getDate()).padStart(2, '0') });
      }
      prevDate = d;
    });
    var lastPlacedX = -Infinity;
    boundaries.forEach(function (b) {
      if (b.x - lastPlacedX >= minLabelGap) {
        var anchor = b.x <= padL + 10 ? 'start' : b.x >= W - padR - 10 ? 'end' : 'middle';
        svg += '<text class="trajectory-label" x="' + b.x.toFixed(1) + '" y="' + (H - 4) + '" text-anchor="' + anchor + '">' + b.label + '</text>';
        lastPlacedX = b.x;
      }
    });

    var lastPt = trajPoints[maxIdx];
    svg += '<circle class="trajectory-dot" cx="' + lastPt.x.toFixed(1) + '" cy="' + lastPt.y.toFixed(1) + '" fill="var(--rc-bg-dark)" stroke="' + (COLOR_HEX[lastPt.color] || '#888') + '"/>';
    svg += '<line id="ct-hover-line" x1="0" y1="' + padT + '" x2="0" y2="' + (padT + chartH) + '" class="traj-hover-line" style="display:none"/>';
    svg += '<circle id="ct-hover-dot" cx="0" cy="0" class="traj-hover-dot" style="display:none"/>';
    svg += '<rect x="' + padL + '" y="' + padT + '" width="' + chartW + '" height="' + chartH + '" fill="transparent" class="traj-hover-area"/>';
    svg += '</svg>';

    var chartEl = container.querySelector('.traj-chart-area');
    chartEl.innerHTML = '<div class="traj-wrap">' + svg + '<div class="traj-tooltip" id="ct-tooltip"></div></div>';
    wireTrajHover(chartEl, data, W);
  }

  function wireTrajHover(chartEl, data, W) {
    var wrap = chartEl.querySelector('.traj-wrap');
    var svgEl = chartEl.querySelector('.trajectory-svg');
    var maxIdx = data.length - 1;
    function showHoverAt(clientX) {
      var hoverLine = document.getElementById('ct-hover-line');
      var hoverDot = document.getElementById('ct-hover-dot');
      var tooltip = document.getElementById('ct-tooltip');
      if (!hoverLine) return;
      var rect = svgEl.getBoundingClientRect();
      var svgX = ((clientX - rect.left) / rect.width) * W;
      var nearest = 0, minDist = Infinity;
      for (var i = 0; i <= maxIdx; i++) { var dist = Math.abs(trajPoints[i].x - svgX); if (dist < minDist) { minDist = dist; nearest = i; } }
      var p = trajPoints[nearest], d = data[nearest], hex = COLOR_HEX[p.color] || '#888';
      hoverLine.setAttribute('x1', p.x.toFixed(1)); hoverLine.setAttribute('x2', p.x.toFixed(1)); hoverLine.style.display = '';
      hoverDot.setAttribute('cx', p.x.toFixed(1)); hoverDot.setAttribute('cy', p.y.toFixed(1)); hoverDot.setAttribute('stroke', hex); hoverDot.style.display = '';
      var wrapRect = wrap.getBoundingClientRect();
      var pixelX = clientX - wrapRect.left, wrapW = wrapRect.width;
      tooltip.style.left = pixelX + 'px';
      tooltip.style.transform = pixelX > wrapW * 0.7 ? 'translateX(-100%)' : pixelX < wrapW * 0.3 ? 'translateX(0)' : 'translateX(-50%)';
      tooltip.innerHTML = '<strong>' + S.escapeHtml(formatDate(d.date)) + '</strong><br><span style="color:' + hex + '">' + degreeToScore(p.degree) + '</span> ' + (CHARGE_LABELS[p.color] || '');
      tooltip.style.display = 'block';
    }
    function hideHover() {
      var hoverLine = document.getElementById('ct-hover-line'), hoverDot = document.getElementById('ct-hover-dot'), tooltip = document.getElementById('ct-tooltip');
      if (hoverLine) hoverLine.style.display = 'none';
      if (hoverDot) hoverDot.style.display = 'none';
      if (tooltip) tooltip.style.display = 'none';
    }
    wrap.addEventListener('mousemove', function (e) { showHoverAt(e.clientX); });
    wrap.addEventListener('mouseleave', hideHover);
    wrap.addEventListener('touchstart', function (e) { if (e.touches[0]) showHoverAt(e.touches[0].clientX); }, { passive: true });
    wrap.addEventListener('touchmove', function (e) { if (e.touches[0]) showHoverAt(e.touches[0].clientX); }, { passive: true });
    wrap.addEventListener('touchend', hideHover);
  }

  async function load() {
    var root = document.getElementById('chart-root');
    if (!root) return;
    root.innerHTML = '<div class="card"><div class="loading" role="status">Loading ' + S.escapeHtml(CFG.title) + '...</div></div>';

    var reading = null, etherSongs = null, series = null;

    if (CFG.source === 'daily') {
      // Spotify (US) = the daily reading. List + aggregate from /compass/current;
      // its ether view from the dedicated ether endpoint; trajectory from the
      // daily-reading series.
      let data;
      try {
        data = await API.getCompassCurrent();
      } catch (e) {
        renderEmpty(root, "This chart hasn't published a reading yet. Check back once today's run is approved.");
        return;
      }
      if (!data.has_reading || !(data.songs || []).length) {
        renderEmpty(root, 'No reading published yet today.');
        return;
      }
      reading = {
        date: data.date,
        degree: data.compass_degree,
        charge: data.charge_level,
        contaminationCount: data.contamination_count,
        editorial: data.editorial_summary,
        songs: data.songs,
      };
      try { const ether = await API.getEtherToday(); etherSongs = ether.items || []; }
      catch (e) { etherSongs = []; }
      try { series = await API.getDailyChart(); } catch (e) { series = null; }
    } else {
      // Chart-snapshot source (itunes / shazam / youtube): one fetch fills the
      // reading + ether shell; the trajectory comes from the per-chart series.
      let data;
      try {
        data = await API.getChartSnapshot(CFG.source);
      } catch (e) {
        renderEmpty(root, "This chart hasn't published a reading yet. Check back once today's run is approved.");
        return;
      }
      if (!(data.songs || []).length) {
        renderEmpty(root, "This chart hasn't published a reading yet.");
        return;
      }
      reading = {
        date: data.date,
        degree: data.compass_degree,
        charge: data.charge_level,
        contaminationCount: data.contamination_count,
        editorial: data.editorial,
        songs: data.songs,
      };
      try { series = await API.getChartDailyChart(CFG.source); } catch (e) { series = null; }
    }

    renderShell(root, reading, etherSongs);
    renderDial(reading);
    renderTrajectory(series, reading);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})();
