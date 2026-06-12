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

  async function load() {
    var root = document.getElementById('chart-root');
    if (!root) return;
    root.innerHTML = '<div class="card"><div class="loading" role="status">Loading ' + S.escapeHtml(CFG.title) + '...</div></div>';

    if (CFG.source === 'daily') {
      // Spotify (US) = the daily reading. List + aggregate from /compass/current;
      // its ether view from the dedicated ether endpoint (daily-reading rows
      // don't carry deadpan/topic inline -- those live on /api/ether).
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
      const reading = {
        date: data.date,
        degree: data.compass_degree,
        charge: data.charge_level,
        contaminationCount: data.contamination_count,
        editorial: data.editorial_summary,
        songs: data.songs,
      };
      let etherSongs = null;
      try {
        const ether = await API.getEtherToday();
        etherSongs = ether.items || [];
      } catch (e) {
        etherSongs = [];  // ether card shows its own empty state below
      }
      renderShell(root, reading, etherSongs);
      return;
    }

    // Chart-snapshot source (itunes / shazam / youtube): one fetch fills both.
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
    const reading = {
      date: data.date,
      degree: data.compass_degree,
      charge: data.charge_level,
      contaminationCount: data.contamination_count,
      editorial: data.editorial,
      songs: data.songs,
    };
    renderShell(root, reading, null);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})();
