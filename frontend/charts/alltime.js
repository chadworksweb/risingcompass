/* === All-Time chart pages (paired view) ===
   A dedicated page for an all-time board, rendered as the homepage daily-reading
   shell: the regular ranked chart on the LEFT and its Ether Art view (deadpan
   line + topic chip) on the RIGHT. Extends the homepage's 20-row pairing to 100.

   Two boards, one renderer, selected by window.RC_ALLTIME = { kind } inline:
     kind 'streams' -> /api/charts/alltime/streams  (Most Streamed of All Time, global)
     kind 'albums'  -> /api/charts/alltime/albums    (Best-Selling Albums, US / RIAA)

   Reuses the global .song-list / .song-item vocabulary (left) and the .ether-row
   vocabulary (right) from main.css -- no per-page row styling. Rows whose song
   isn't calibrated yet render with a neutral dot + the existing "untagged" ether
   pill, never faked. */

(function () {
  'use strict';

  var CFG = window.RC_ALLTIME || { kind: 'streams' };

  var BOARDS = {
    streams: {
      fetch: function () { return API.getAlltimeStreams(); },
      leftTitle: 'Most Streamed of All Time',
      leftDesc: 'Spotify global lifetime streams, top 100. Each song individually charged.',
      etherDesc: 'The same 100, named for what the lyrics really say, with the topics pulled through the ether.',
    },
    albums: {
      fetch: function () { return API.getAlltimeAlbums(); },
      leftTitle: 'Best-Selling Albums of All Time',
      leftDesc: 'RIAA certified units, USA, top 50. Each album charged across its tracks.',
      etherDesc: 'The same albums, named for what they really are, with the topics pulled through the ether.',
    },
    'stream-albums': {
      fetch: function () { return API.getAlltimeStreamAlbums(); },
      leftTitle: 'Most Streamed Albums of All Time',
      leftDesc: 'Spotify global lifetime streams, top 100 -- the streaming-era albums the sales chart misses.',
      etherDesc: 'The same 100 albums, named for what they really are, with the topics pulled through the ether.',
    },
  };

  var TOPIC = BOARDS[CFG.kind] ? CFG.kind : 'streams';
  var BOARD = BOARDS[TOPIC];

  var COLOR_HEX = {
    violet: '#aa54ff', blue: '#3388ff', green: '#33cc55',
    orange: '#ffbb33', red: '#ff3333',
  };

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function formatStreams(n) {
    if (n == null) return '';
    if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    return Number(n).toLocaleString('en-US');
  }

  // --- shared row helpers -------------------------------------------------

  function rowLink(row) {
    // The canonical detail link per board: song page for streams, release page
    // for albums. Returns null when there's nothing to link to yet.
    if (TOPIC === 'streams') {
      return (row.song_slug && row.rubric_color)
        ? '/songs/' + encodeURIComponent(row.song_slug) : null;
    }
    return (row.artist_slug && row.release_slug)
      ? '/artists/' + encodeURIComponent(row.artist_slug) + '/' + encodeURIComponent(row.release_slug) : null;
  }

  function rowTitle(row) { return TOPIC === 'streams' ? row.title : row.album_title; }

  function metricText(row) {
    // RIAA albums show certified units + year; both stream boards show streams.
    if (TOPIC === 'albums') {
      var bits = [];
      if (row.certified_units) bits.push(row.certified_units);
      else if (row.units_millions) bits.push(row.units_millions + 'M units');
      if (row.release_year) bits.push(String(row.release_year));
      return bits.join(' · ');
    }
    var s = formatStreams(row.total_streams);
    return s ? s + ' streams' : '';
  }

  // --- left (regular) card ------------------------------------------------

  function regularRow(row) {
    var link = rowLink(row);
    var title = escapeHtml(rowTitle(row));
    var titleHtml = link
      ? '<a href="' + link + '" class="song-title-link">' + title + '</a>' : title;
    var artistHtml = row.artist_slug
      ? '<a href="/artists/' + encodeURIComponent(row.artist_slug) + '" class="song-artist-name">' + escapeHtml(row.artist) + '</a>'
      : escapeHtml(row.artist || '');
    var dotCls = 'song-dot ' + (row.non_music ? '' : (row.rubric_color || ''));
    var metric = metricText(row);
    var tag = row.non_music ? '<span class="alltime-nonmusic-pill">non-music</span>' : '';
    return '<li class="song-item' + (row.non_music ? ' non-music' : '') + '">'
      + '<span class="song-pos">' + row.rank + '</span>'
      + '<span class="' + dotCls + '"></span>'
      + '<div class="song-info">'
      + '<div class="song-title">' + titleHtml + ' ' + tag + '</div>'
      + '<div class="song-artist">' + artistHtml + '</div>'
      + '</div>'
      + (metric ? '<div class="alltime-metric">' + escapeHtml(metric) + '</div>' : '')
      + '</li>';
  }

  // --- right (ether) card -------------------------------------------------

  function topicChip(topics) {
    if (!topics || !topics.length) return '';
    var t = String(topics[0]).replace(/-/g, ' ');
    return '<span class="ether-chip">' + escapeHtml(t) + '</span>';
  }

  function etherRow(row) {
    var tierHex = COLOR_HEX[row.rubric_color] || 'transparent';
    var tickStyle = 'border-left:9px solid ' + tierHex + ';';
    var link = rowLink(row);
    var title = escapeHtml(rowTitle(row));

    if (row.non_music || !row.deadpan_line) {
      // Non-music carries its own tag (nulled like an instrumental); a real
      // song with no reading yet shows the "untagged" pill.
      var pill = row.non_music
        ? '<span class="alltime-nonmusic-pill">non-music</span>'
        : '<span class="ether-untagged-pill">untagged</span>';
      var titleHtml = link
        ? '<a href="' + link + '" class="ether-title-link">' + title + '</a>'
        : '<span class="ether-title-link">' + title + '</span>';
      return '<li class="ether-row ether-row--untagged" style="' + tickStyle + '">'
        + '<span class="ether-pos">' + row.rank + '</span>'
        + '<div class="ether-text">'
        + '<div class="ether-deadpan">' + titleHtml + '</div>'
        + '<div class="ether-meta">' + escapeHtml(row.artist || '') + ' ' + pill + '</div>'
        + '</div></li>';
    }

    var metaTitle = link
      ? '<a href="' + link + '" class="ether-title-link">' + title + '</a>' : title;
    return '<li class="ether-row" style="' + tickStyle + '">'
      + '<span class="ether-pos">' + row.rank + '</span>'
      + '<div class="ether-text">'
      + '<div class="ether-deadpan">' + escapeHtml(row.deadpan_line) + '</div>'
      + '<div class="ether-meta">'
      + '<span class="ether-meta-title">' + metaTitle + '</span>'
      + '<span class="ether-meta-sep">·</span>'
      + '<span class="ether-meta-artist">' + escapeHtml(row.artist || '') + '</span>'
      + (row.topics && row.topics.length ? '<span class="ether-meta-sep">·</span>' + topicChip(row.topics) : '')
      + '</div></div></li>';
  }

  function renderEmpty(root, msg) {
    root.innerHTML = '<div class="card"><div class="card-header">' + escapeHtml(BOARD.leftTitle) + '</div>'
      + '<div class="no-reading"><p>' + escapeHtml(msg) + '</p></div></div>';
  }

  async function load() {
    var root = document.getElementById('chart-root');
    if (!root) return;
    root.innerHTML = '<div class="card"><div class="loading" role="status">Loading ' + escapeHtml(BOARD.leftTitle) + '...</div></div>';

    var data;
    try {
      data = await BOARD.fetch();
    } catch (e) {
      renderEmpty(root, "This chart hasn't been populated yet.");
      return;
    }

    var rows = (data.rows || []).slice().sort(function (a, b) { return a.rank - b.rank; });
    if (!rows.length) {
      renderEmpty(root, "This chart hasn't been populated yet. Check back soon.");
      return;
    }

    root.innerHTML =
      '<div class="alltime-grid">'
      + '<div class="card">'
      +   '<div class="card-header">' + escapeHtml(BOARD.leftTitle) + '</div>'
      +   '<p class="card-desc">' + escapeHtml(BOARD.leftDesc) + '</p>'
      +   '<ul class="song-list">' + rows.map(regularRow).join('') + '</ul>'
      + '</div>'
      + '<div class="card">'
      +   '<div class="card-header">The Ether Art Chart</div>'
      +   '<p class="card-desc">' + escapeHtml(BOARD.etherDesc) + '</p>'
      +   '<ol class="ether-list">' + rows.map(etherRow).join('') + '</ol>'
      + '</div>'
      + '</div>';
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})();
