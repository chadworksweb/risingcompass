/* === Standalone chart page ===
   Renders the SAME dashboard the homepage shows, for one chart: the compass dial
   + the Daily Charge panel (the shared DailyChargePanel capsule) on top, and the
   paired reading + Ether Art Chart shell below. This file is page data-wiring
   only; the trajectory panel lives in /js/daily-charge-panel.js and the reading/
   ether templating in /js/chart-shell.js.

   Each page sets window.RC_CHART = { source, title, sub } inline:
     source 'daily'  -> /api/compass/current (Spotify (US); ether from
                        /api/ether-art-chart/today; series from /api/compass/daily-chart)
     any other source is a chart-snapshot key (itunes | shazam | youtube) ->
                        /api/compass/chart/<source>/current (+ /daily-chart series). */

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

  // Paired two-card shell (reading + Ether Art Chart), mirroring the homepage.
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

  // Trajectory: mount the shared Daily Charge capsule with this chart's series.
  function mountTrajectory(loadSeries) {
    var c = document.getElementById('chart-daily-container');
    if (c && window.DailyChargePanel) DailyChargePanel.mount(c, { loadSeries: loadSeries });
  }

  async function load() {
    var root = document.getElementById('chart-root');
    if (!root) return;
    root.innerHTML = '<div class="card"><div class="loading" role="status">Loading ' + S.escapeHtml(CFG.title) + '...</div></div>';

    var reading = null, etherSongs = null, loadSeries = null;

    if (CFG.source === 'daily') {
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
        date: data.date, degree: data.compass_degree, charge: data.charge_level,
        contaminationCount: data.contamination_count, editorial: data.editorial_summary, songs: data.songs,
      };
      try { const ether = await API.getEtherToday(); etherSongs = ether.items || []; }
      catch (e) { etherSongs = []; }
      loadSeries = function () { return API.getDailyChart(); };
    } else {
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
        date: data.date, degree: data.compass_degree, charge: data.charge_level,
        contaminationCount: data.contamination_count, editorial: data.editorial, songs: data.songs,
      };
      var src = CFG.source;
      loadSeries = function () { return API.getChartDailyChart(src); };
    }

    renderShell(root, reading, etherSongs);
    renderDial(reading);
    mountTrajectory(loadSeries);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})();
