/* === Standalone chart page ===
   A dedicated page per chart that shows the same "today" reading the homepage
   shows for it -- the charge readout (when the chart carries an aggregate) plus
   the top-N song list. Self-contained on purpose: it reuses the global
   .card / .song-list styling but not app.js's homepage monolith, so a chart
   page is a thin, independent view. "For now" it mirrors the homepage panel;
   richer per-chart views come later.

   Each page sets window.RC_CHART = { source, title, sub } inline:
     source 'daily'  -> /api/compass/current               (Spotify (US); has a charge readout)
     any other source is a chart-snapshot registry key:
     source 'itunes' | 'shazam' | 'youtube'
                     -> /api/compass/chart/<source>/current (song list only, mirroring the
                        homepage's secondary panel; no aggregate readout) */

(function () {
  'use strict';

  var CFG = window.RC_CHART || { source: 'daily', title: 'Chart', sub: '' };

  var CHARGE_LABELS = {
    violet: 'ASCENDED', blue: 'ELEVATED', green: 'DECENT',
    orange: 'DEGRADED', red: 'CORRUPTED',
  };
  var COLOR_HEX = {
    violet: '#9933ff', blue: '#3388ff', green: '#33cc55',
    orange: '#ffbb33', red: '#ff3333',
  };

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function scoreStr(deg) {
    var s = Math.round((90 - deg) * 100 / 90);
    return (s > 0 ? '+' : '') + s;
  }
  function formatDate(dateStr) {
    if (!dateStr) return '';
    var d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  }

  function songRow(song) {
    var dotCls = song.instrumental ? 'song-dot' : ('song-dot ' + (song.rubric_color || ''));
    var titleHtml = (song.song_slug && song.rubric_color)
      ? '<a href="/songs/' + encodeURIComponent(song.song_slug) + '" class="song-title-link">' + escapeHtml(song.title) + '</a>'
      : escapeHtml(song.title);
    var artistHtml = song.artist_slug
      ? '<a href="/artists/' + encodeURIComponent(song.artist_slug) + '" class="song-artist-name">' + escapeHtml(song.artist) + '</a>'
      : escapeHtml(song.artist || '');
    return '<li class="song-item' + (song.instrumental ? ' instrumental' : '') + '">'
      + '<span class="song-pos">' + song.position + '</span>'
      + '<span class="' + dotCls + '"></span>'
      + '<div class="song-info">'
      + '<div class="song-title">' + titleHtml + '</div>'
      + '<div class="song-artist">' + artistHtml + '</div>'
      + '</div>'
      + '<div class="song-actions">'
      + (song.contaminated ? '<span class="song-contam" aria-hidden="true">&#x2622;</span>' : '')
      + '</div>'
      + '</li>';
  }

  function renderReadout(charge, degree, date) {
    if (charge == null || degree == null) return '';
    var hex = COLOR_HEX[charge] || '#888';
    return '<div class="chart-readout" style="--c:' + hex + '">'
      + '<span class="chart-readout-score">' + scoreStr(degree) + '</span>'
      + '<span class="chart-readout-label">' + (CHARGE_LABELS[charge] || String(charge).toUpperCase()) + '</span>'
      + (date ? '<span class="chart-readout-date">' + escapeHtml(formatDate(date)) + '</span>' : '')
      + '</div>';
  }

  function renderEmpty(root, msg) {
    root.innerHTML = '<div class="card"><div class="card-header">' + escapeHtml(CFG.title) + '</div>'
      + '<p class="card-desc">' + escapeHtml(CFG.sub) + '</p>'
      + '<div class="no-reading"><p>' + escapeHtml(msg) + '</p></div></div>';
  }

  async function load() {
    var root = document.getElementById('chart-root');
    if (!root) return;
    root.innerHTML = '<div class="card"><div class="loading" role="status">Loading ' + escapeHtml(CFG.title) + '...</div></div>';

    var data;
    try {
      data = CFG.source === 'daily'
        ? await API.getCompassCurrent()
        : await API.getChartSnapshot(CFG.source);
    } catch (e) {
      renderEmpty(root, "This chart hasn't published a reading yet. Check back once today's run is approved.");
      return;
    }

    var songs = (data.songs || []).slice().sort(function (a, b) { return a.position - b.position; });
    if (CFG.source === 'daily' && !data.has_reading) {
      renderEmpty(root, 'No reading published yet today.');
      return;
    }
    if (!songs.length) {
      renderEmpty(root, "This chart hasn't published a reading yet.");
      return;
    }

    // Spotify (US) carries the aggregate; the chart-snapshot /current endpoints
    // (iTunes / Shazam / YouTube) do not, so those show the list only (as on the
    // homepage).
    var readout = CFG.source === 'daily'
      ? renderReadout(data.charge_level, data.compass_degree, data.date)
      : '';

    var html = readout
      + '<div class="card">'
      + '<div class="card-header">' + escapeHtml(CFG.title) + '</div>'
      + '<p class="card-desc">' + escapeHtml(CFG.sub) + (data.date ? '. Updated ' + escapeHtml(formatDate(data.date)) + '.' : '') + '</p>'
      + '<ul class="song-list">' + songs.map(songRow).join('') + '</ul>'
      + '</div>';
    root.innerHTML = html;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})();
