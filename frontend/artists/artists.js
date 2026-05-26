/* === Artists & Songs — Page Logic === */

(() => {
  'use strict';

  const CHARGE_LABELS = {
    violet: 'Ascended', blue: 'Elevated', green: 'Decent',
    orange: 'Degraded', red: 'Corrupted',
  };
  const COLOR_HEX = {
    violet: '#aa54ff', blue: '#3388ff', green: '#33cc55',
    orange: '#ffbb33', red: '#ff3333',
  };

  function escapeHtml(str) {
    if (str == null) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function announce(msg) {
    const el = document.getElementById('sr-announce');
    if (el) el.textContent = msg;
  }

  function chargeDisplay(v) {
    if (v == null) return '';
    return (v > 0 ? '+' : '') + v;
  }

  /* ========== SEARCH PAGE ========== */

  function initSearchPage() {
    const input = document.getElementById('search-input');
    const resultsContainer = document.getElementById('results-container');
    const emptyState = document.getElementById('empty-state');
    const initialState = document.getElementById('initial-state');
    const searchingState = document.getElementById('searching-state');
    if (!input) return;

    let debounceTimer = null;
    let currentController = null;
    let requestSeq = 0;

    input.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      const q = input.value.trim();
      if (q.length < 2) {
        if (currentController) currentController.abort();
        resultsContainer.innerHTML = '';
        emptyState.hidden = true;
        if (searchingState) searchingState.hidden = true;
        initialState.hidden = q.length > 0;
        return;
      }
      debounceTimer = setTimeout(() => runSearch(q), 300);
    });

    // Prefill + run from ?q= (the header search "See all results" link lands here).
    const initialQ = (new URLSearchParams(window.location.search).get('q') || '').trim();
    if (initialQ) {
      input.value = initialQ;
      runSearch(initialQ);
    }

    async function runSearch(q) {
      if (currentController) currentController.abort();
      const controller = new AbortController();
      currentController = controller;
      const mySeq = ++requestSeq;

      initialState.hidden = true;
      emptyState.hidden = true;
      if (searchingState) searchingState.hidden = false;
      announce('Searching');

      try {
        const [artistData, songData] = await Promise.all([
          ArtistsAPI.searchArtists(q, 10, controller.signal),
          ArtistsAPI.searchSongs(q, 10, controller.signal),
        ]);

        if (mySeq !== requestSeq) return;

        const artists = artistData.results || [];
        const songs = songData.results || [];

        if (searchingState) searchingState.hidden = true;

        if (artists.length === 0 && songs.length === 0) {
          resultsContainer.innerHTML = '';
          emptyState.hidden = false;
          announce('No results found');
          return;
        }

        emptyState.hidden = true;
        let html = '';

        if (artists.length > 0) {
          html += '<div class="results-section"><h2 class="results-heading">Artists</h2><ul class="results-list">';
          for (const a of artists) {
            if (a.indexed && a.slug) {
              html += `<li class="result-item result-artist">
                <a href="/artists/${encodeURIComponent(a.slug)}">
                  <span class="result-name">${escapeHtml(a.name)}</span>
                  <span class="result-meta">${a.calibrated_song_count} song${a.calibrated_song_count !== 1 ? 's' : ''} calibrated</span>
                </a>
              </li>`;
            } else {
              html += `<li class="result-item result-artist result-unindexed">
                <div class="result-unindexed-content">
                  <span class="result-name">${escapeHtml(a.name)}</span>
                  <span class="result-meta">${a.calibrated_song_count} song${a.calibrated_song_count !== 1 ? 's' : ''} calibrated — trajectory not yet built</span>
                  <span class="result-cta">Submit more songs via <a href="/lyrical-charger/">Lyrical Charger</a></span>
                </div>
              </li>`;
            }
          }
          html += '</ul></div>';
        }

        if (songs.length > 0) {
          html += '<div class="results-section"><h2 class="results-heading">Songs</h2><ul class="results-list">';
          for (const s of songs) {
            const dotColor = COLOR_HEX[s.rubric_color] || '#999';
            html += `<li class="result-item result-song">
              <a href="/songs/${encodeURIComponent(s.slug)}">
                <span class="result-dot" style="background:${dotColor}"></span>
                <span class="result-name">${escapeHtml(s.title)}</span>
                <span class="result-artist-name">${escapeHtml(s.artist)}</span>
                <span class="result-tier" style="color:${dotColor}">${escapeHtml(s.tier_label)}</span>
              </a>
            </li>`;
          }
          html += '</ul></div>';
        }

        resultsContainer.innerHTML = html;
        announce(`${artists.length} artist${artists.length !== 1 ? 's' : ''}, ${songs.length} song${songs.length !== 1 ? 's' : ''} found`);
      } catch (err) {
        if (err && (err.name === 'AbortError' || controller.signal.aborted)) return;
        if (mySeq !== requestSeq) return;
        console.error('Search error:', err);
        if (searchingState) searchingState.hidden = true;
        resultsContainer.innerHTML = '<p class="error-message">Search failed. Try again.</p>';
      }
    }
  }

  /* ========== ARTIST PAGE ========== */

  const TOP_SONGS_PAGE_SIZE = 20;
  const TOP_SONGS_MAX = 100;
  const RELEASES_PAGE_SIZE = 10;

  const artistPageState = {
    slug: null,
    topSongsOffset: 0,
    topSongsTotal: 0,
    releasesOffset: 0,
    releasesTotal: 0,
    summary: null,
    trajectory: null,
  };

  function getArtistSlugFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = params.get('slug');
    if (fromQuery) return fromQuery;
    const m = window.location.pathname.match(/^\/artists\/([^/]+)\/?$/);
    if (m && m[1] !== 'artist.html' && m[1] !== 'index.html') {
      return decodeURIComponent(m[1]);
    }
    return null;
  }

  function initArtistPage() {
    const slug = getArtistSlugFromUrl();
    if (!slug) return;
    artistPageState.slug = slug;

    // Kick off all requests in parallel — each section renders as it returns.
    loadSummary(slug);
    loadTrajectory(slug);
    loadTopSongs(slug, 0);
    loadReleases(slug, 0);
    loadUnreleased(slug);

    const moreBtn = document.getElementById('top-songs-more');
    if (moreBtn) {
      moreBtn.addEventListener('click', () => {
        loadTopSongs(artistPageState.slug, artistPageState.topSongsOffset);
      });
    }
    const releasesMoreBtn = document.getElementById('releases-more');
    if (releasesMoreBtn) {
      releasesMoreBtn.addEventListener('click', () => {
        loadReleases(artistPageState.slug, artistPageState.releasesOffset);
      });
    }
  }

  async function loadSummary(slug) {
    let data;
    try {
      data = await ArtistsAPI.getArtistSummary(slug);
    } catch (err) {
      console.error('Failed to fetch summary:', err);
      document.getElementById('artist-name').textContent = 'Artist not found';
      const loader = document.getElementById('artist-compass-loader');
      if (loader) loader.hidden = true;
      return;
    }

    artistPageState.summary = data;

    // Swap the catalog-charge panel from its loader to the real content
    // before we try to render into the compass / charge / breakdown hosts.
    const loader = document.getElementById('artist-compass-loader');
    const body = document.getElementById('artist-compass-body');
    if (loader) loader.hidden = true;
    if (body) body.hidden = false;

    try {
      renderHeader(data);
    } catch (err) { console.error('renderHeader failed:', err); }
    try {
      renderCatalogCompass(data.stats);
    } catch (err) { console.error('renderCatalogCompass failed:', err); }
    try {
      renderBreakdown(data.stats);
    } catch (err) { console.error('renderBreakdown failed:', err); }
    // Lobby comments anchored on the artist row. Needs the integer id
    // returned by the summary endpoint (added 2026-05-19).
    try {
      if (data && data.id && typeof Comments !== 'undefined') {
        const cmtEl = document.getElementById('artist-comments');
        if (cmtEl) {
          cmtEl.hidden = false;
          Comments.mount(cmtEl, {
            targetType: 'artist',
            targetSource: null,
            targetId: data.id,
          });
        }
      }
    } catch (err) { console.error('Comments mount failed:', err); }

    try {
      maybeInjectJsonLd();
    } catch (err) { console.error('maybeInjectJsonLd failed:', err); }

    if (data.stats && data.stats.total_calibrated_songs === 0) {
      const zero = document.getElementById('zero-state');
      if (zero) zero.hidden = false;
    }
  }

  async function loadTrajectory(slug) {
    const container = document.getElementById('artist-trajectory-chart');
    if (!container) return;
    try {
      const data = await ArtistsAPI.getArtistTrajectory(slug);
      const points = data.points || [];

      // Translate release-shape payload into the yearly-aggregate shape the
      // harvested homepage chart expects (year / compass_degree / charge_level
      // / chart_song_count) — plus a few release-specific fields (_title,
      // _type) used by the adapted tooltip and click handlers.
      const trajRows = points
        .filter(r => r.charge_value != null)
        .map(r => {
          const year = r.release_year
            || (r.release_date ? parseInt(r.release_date.slice(0, 4), 10) : 0);
          return {
            year,
            compass_degree: 90 - (r.charge_value * 0.9),
            charge_level: r.rubric_color,
            chart_song_count: r.calibrated_count || r.track_count || 0,
            _title: r.title,
            _type: r.release_type,
            _date: r.release_date,
            _charge: r.charge_value,
          };
        });

      artistPageState.trajectory = points;
      allYearData = trajRows;

      if (!trajRows.length) {
        const area = container.querySelector('.traj-chart-area') || container;
        area.innerHTML = '<p class="chart-empty">No calibrated releases to chart.</p>';
        maybeInjectJsonLd();
        return;
      }

      // Mirror the homepage loadTrajectory's DOM scaffold (zoom bar + chart
      // area + TM controls). Harvest point: this is exactly what app.js
      // writes into #trajectory-container after fetching drift-years.
      const yearSpan = trajRows[trajRows.length - 1].year - trajRows[0].year;
      const zoomButtons = [];
      zoomButtons.push({ zoom: 'all', label: 'All' });
      if (yearSpan > 10) zoomButtons.push({ zoom: '10', label: '10Y' });
      if (yearSpan > 5) zoomButtons.push({ zoom: '5', label: '5Y' });

      container.innerHTML = `
        <div class="traj-zoom-bar">
          ${zoomButtons.map(b => `<button class="traj-zoom-btn${b.zoom === 'all' ? ' active' : ''}" data-zoom="${b.zoom}">${b.label}</button>`).join('')}
        </div>
        <div class="traj-chart-area"></div>
        <div class="timemachine-controls"></div>
      `;

      container.querySelectorAll('.traj-zoom-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          currentZoom = btn.dataset.zoom;
          applyZoom(container);
        });
      });

      applyZoom(container);
      maybeInjectJsonLd();
    } catch (err) {
      console.error('Failed to load trajectory:', err);
      const area = container.querySelector('.traj-chart-area') || container;
      area.innerHTML = '<p class="chart-empty">Couldn\'t load trajectory.</p>';
    }
  }

  async function loadTopSongs(slug, offset) {
    const list = document.getElementById('top-songs-list');
    const moreBtn = document.getElementById('top-songs-more');
    try {
      const data = await ArtistsAPI.getArtistTopSongs(slug, offset, TOP_SONGS_PAGE_SIZE);
      artistPageState.topSongsTotal = data.total;
      artistPageState.topSongsOffset = offset + data.items.length;

      const itemsHtml = data.items.map((s, i) => renderTopSongRow(s, offset + i + 1)).join('');

      if (offset === 0) {
        if (data.items.length === 0) {
          list.innerHTML = '<li class="empty-row">No calibrated songs yet.</li>';
        } else {
          list.innerHTML = itemsHtml;
        }
      } else {
        list.insertAdjacentHTML('beforeend', itemsHtml);
      }

      if (moreBtn) {
        const shown = artistPageState.topSongsOffset;
        const remaining = Math.min(artistPageState.topSongsTotal, TOP_SONGS_MAX) - shown;
        if (remaining > 0) {
          moreBtn.textContent = `View ${Math.min(TOP_SONGS_PAGE_SIZE, remaining)} more`;
          moreBtn.hidden = false;
        } else {
          moreBtn.hidden = true;
        }
      }
    } catch (err) {
      console.error('Failed to load top songs:', err);
      if (offset === 0) list.innerHTML = '<li class="error-message">Couldn\'t load top songs.</li>';
    }
  }

  async function loadUnreleased(slug) {
    const section = document.getElementById('artist-unreleased-section');
    const list = document.getElementById('unreleased-list');
    if (!section || !list) return;
    try {
      const data = await ArtistsAPI.getArtistReleases(slug, 0, 100, 'desc', 'unreleased');
      if (!data.items || data.items.length === 0) {
        section.hidden = true;
        return;
      }
      list.innerHTML = data.items.map(renderReleaseRow).join('');
      section.hidden = false;
    } catch (err) {
      console.error('Failed to load unreleased:', err);
      section.hidden = true;
    }
  }

  async function loadReleases(slug, offset) {
    const list = document.getElementById('releases-list');
    const moreBtn = document.getElementById('releases-more');
    try {
      const data = await ArtistsAPI.getArtistReleases(slug, offset, RELEASES_PAGE_SIZE, 'desc');
      artistPageState.releasesTotal = data.total;
      artistPageState.releasesOffset = offset + data.items.length;

      const itemsHtml = data.items.map(renderReleaseRow).join('');

      if (offset === 0) {
        if (data.items.length === 0) {
          list.innerHTML = '<li class="empty-row">No releases indexed yet.</li>';
        } else {
          list.innerHTML = itemsHtml;
        }
      } else {
        list.insertAdjacentHTML('beforeend', itemsHtml);
      }

      if (moreBtn) {
        const remaining = artistPageState.releasesTotal - artistPageState.releasesOffset;
        if (remaining > 0) {
          moreBtn.textContent = `View ${Math.min(RELEASES_PAGE_SIZE, remaining)} more`;
          moreBtn.hidden = false;
        } else {
          moreBtn.hidden = true;
        }
      }
    } catch (err) {
      console.error('Failed to load releases:', err);
      if (offset === 0) list.innerHTML = '<li class="error-message">Couldn\'t load releases.</li>';
    }
  }

  /* ---------- renderers ---------- */

  function renderHeader(data) {
    document.getElementById('artist-name').textContent = data.name;
    // GEO framing: the page answers "what are {artist}'s songs about?".
    const question = `What are ${data.name}'s songs about?`;
    const questionEl = document.getElementById('artist-question');
    if (questionEl) questionEl.textContent = question;
    document.getElementById('page-title').textContent =
      `${question} — The Rising Compass`;
    const stats = data.stats || {};
    const chargeLabel = stats.catalog_charge != null
      ? `${chargeDisplay(stats.catalog_charge)} (${stats.catalog_tier_label || 'Uncalibrated'})`
      : 'N/A';
    const desc = `This page answers what ${data.name}'s songs are about — the meaning behind their lyrics, calibrated by The Rising Compass. ${stats.total_calibrated_songs} songs calibrated; catalog charge ${chargeLabel}.`;
    document.getElementById('meta-description').content = desc;
    document.getElementById('og-title').content = `${question} — The Rising Compass`;
    document.getElementById('og-description').content = desc;
    announce(`Loaded trajectory for ${data.name}`);
  }

  function renderCatalogCompass(stats) {
    // The Compass component expects a container id. Render once, then drive it.
    if (typeof Compass !== 'undefined') {
      Compass.render('artist-compass-container');
      if (stats.catalog_degree != null && stats.catalog_tier) {
        Compass.setDegree(stats.catalog_degree, stats.catalog_tier);
      }
    }
    if (typeof Charge !== 'undefined') {
      Charge.render('artist-charge-container');
      if (stats.catalog_tier && stats.catalog_degree != null) {
        // Charge.setLevel(color, redCount, totalSongs, degree)
        Charge.setLevel(stats.catalog_tier, 0, stats.total_calibrated_songs || 0, stats.catalog_degree);
      }
    }
  }

  function renderBreakdown(stats) {
    const container = document.getElementById('artist-breakdown');
    if (!container) return;

    const totalReleases = stats.total_releases || 0;
    const totalSongs = stats.total_calibrated_songs || 0;

    let pillsHtml = '';
    for (const [color, count] of Object.entries(stats.tier_breakdown || {})) {
      if (count > 0) {
        pillsHtml += `<span class="tier-pill" style="background:${COLOR_HEX[color]}20;color:${COLOR_HEX[color]}">${CHARGE_LABELS[color]} ${count}</span>`;
      }
    }

    container.innerHTML = `
      <div class="artist-breakdown-row">
        <div class="artist-breakdown-stat">
          <span class="stat-value">${totalReleases}</span>
          <span class="stat-label">Release${totalReleases !== 1 ? 's' : ''}</span>
        </div>
        <div class="artist-breakdown-stat">
          <span class="stat-value">${totalSongs}</span>
          <span class="stat-label">Song${totalSongs !== 1 ? 's' : ''} Calibrated</span>
        </div>
      </div>
      ${pillsHtml ? `<div class="tier-pills">${pillsHtml}</div>` : ''}
    `;
  }

  function renderTopSongRow(s, rank) {
    const color = COLOR_HEX[s.rubric_color] || '#999';
    const charge = chargeDisplay(s.charge_value);
    const titleHtml = escapeHtml(s.title);
    const titleNode = s.slug
      ? `<a class="song-title-link" href="/songs/${encodeURIComponent(s.slug)}">${titleHtml}</a>`
      : `<span class="song-title">${titleHtml}</span>`;
    return `
      <li class="song-item artist-top-song-item">
        <span class="top-song-rank">${rank}</span>
        <span class="song-dot" style="background:${color}"></span>
        <div class="song-info">
          ${titleNode}
        </div>
        <span class="song-charge" style="color:${color}">${charge}</span>
      </li>
    `;
  }

  function renderReleaseRow(r) {
    const color = COLOR_HEX[r.rubric_color] || '#999';
    const charge = chargeDisplay(r.charge_value);
    const typeLabel = r.release_type === 'album' ? 'Album'
      : r.release_type === 'ep' ? 'EP' : 'Single';
    const dateDisplay = r.release_date || (r.release_year ? String(r.release_year) : '');
    return `
      <li class="release-compact-item">
        <span class="release-dot" style="background:${color}"></span>
        <div class="release-compact-main">
          <span class="release-compact-title">${escapeHtml(r.title)}</span>
          <span class="release-compact-meta">
            <span class="release-type">${typeLabel}</span>
            ${dateDisplay ? `<span class="release-date">${dateDisplay}</span>` : ''}
          </span>
        </div>
        <span class="release-compact-charge" style="color:${color}" aria-label="${escapeHtml(r.tier_label || '')}">
          ${charge || '·'}
        </span>
      </li>
    `;
  }

  /* ========== TRAJECTORY CHART + TIME MACHINE ==========
     Harvested from js/app.js (homepage historical chart). Same viewBox, same
     gradient stops, same clip-path reveal, same hover/click, same YTD
     scaffolding (held inert — release trajectories don't have a YTD year),
     same zoom bar, same time-machine controls + state machine.

     Adaptations (only where data shape or context differs):
       - `data[i].year` is the release's year; multiple rows can share a
         year, so x-axis labels render once per year (first occurrence).
       - `data[i]._title / _type / _date / _charge` carry the release-
         specific fields used by the tooltip and click-pinch.
       - Click doesn't call loadYearSongs / syncCalendar (no such concepts
         on the artist page); it still pinches the line and drives the
         Catalog Compass + Charge widgets to that release.
       - saveCompassState / restoreCompassState snapshot the artist's
         catalog aggregate (summary.stats) instead of the homepage's
         compass-score text.
       - applyZoom filters by year-window against the current last year.
  */

  let allYearData = [];
  let currentZoom = 'all';
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

  function degreeToScore(degree) {
    const s = Math.round((90 - degree) * 100 / 90);
    return (s > 0 ? '+' : '') + s;
  }

  function degreeToTier(deg) {
    if (deg <= 22.5) return 'violet';
    if (deg <= 67.5) return 'blue';
    if (deg <= 112.5) return 'green';
    if (deg <= 157.5) return 'orange';
    return 'red';
  }

  function setCompassDate(text) {
    const dateEl = document.getElementById('compass-date-svg');
    if (dateEl) dateEl.textContent = text;
  }

  function renderTrajectoryChart(data, container) {
    if (!data.length) return;
    chartData = data;

    const W = 320, H = 120;
    const padL = 30, padR = 16, padT = 10, padB = 22;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const maxIdx = data.length - 1;

    // YTD scaffolding preserved but inert on the artist page — releases
    // don't have a YTD concept. Flag is false so the dashed segment, ring,
    // and pulsing-ring are skipped.
    const hasYTD = false;
    chartHasYTD = hasYTD;

    chartPoints = data.map((d, i) => ({
      x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
      y: padT + (d.compass_degree / 180) * chartH,
      degree: d.compass_degree,
      year: d.year,
      color: d.charge_level,
      isYTD: false,
    }));

    const solidPoints = hasYTD ? chartPoints.slice(0, -1) : chartPoints;
    const solidPath = solidPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const fullLinePath = chartPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const areaPath = fullLinePath + ` L ${chartPoints[maxIdx].x.toFixed(1)} ${padT + chartH} L ${chartPoints[0].x.toFixed(1)} ${padT + chartH} Z`;

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
    [{ deg: 0, label: '+100' }, { deg: 45, label: '' }, { deg: 90, label: '0' }, { deg: 135, label: '' }, { deg: 180, label: '-100' }].forEach(({ deg, label }) => {
      const y = padT + (deg / 180) * chartH;
      svg += `<line class="trajectory-grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" />`;
      if (label) svg += `<text class="trajectory-y-label" x="${padL - 4}" y="${y + 3}">${label}</text>`;
    });

    // Clipped line + area
    svg += `<g clip-path="url(#traj-clip)">`;
    svg += `<path class="trajectory-area" d="${areaPath}" fill="url(#traj-area-grad)" />`;
    svg += `<path class="trajectory-line" d="${solidPath}" stroke="url(#traj-grad)" />`;
    svg += `</g>`;

    // X-axis labels (per year; first occurrence only when multiple releases share a year)
    const yearSpan = data[maxIdx].year - data[0].year;
    const labelInterval = yearSpan > 40 ? 10 : yearSpan > 15 ? 5 : yearSpan > 8 ? 2 : 1;
    const labelYears = new Set();
    const startDecade = Math.ceil(data[0].year / labelInterval) * labelInterval;
    for (let yr = startDecade; yr <= data[maxIdx].year; yr += labelInterval) labelYears.add(yr);
    labelYears.add(data[0].year);
    labelYears.add(data[maxIdx].year);

    const seenYears = new Set();
    chartPoints.forEach(p => {
      if (labelYears.has(p.year) && !seenYears.has(p.year)) {
        seenYears.add(p.year);
        const labelText = `'${String(p.year).slice(2)}`;
        svg += `<text class="trajectory-label" x="${p.x.toFixed(1)}" y="${H - 4}" text-anchor="middle">${labelText}</text>`;
        svg += `<line x1="${p.x.toFixed(1)}" y1="${padT + chartH}" x2="${p.x.toFixed(1)}" y2="${padT + chartH + 4}" stroke="var(--rc-text-dim)" stroke-width="0.5" opacity="0.5" />`;
      }
    });

    // Time machine moving dot
    const lastPt = chartPoints[maxIdx];
    svg += `<circle id="traj-timemachine-dot" class="trajectory-dot" cx="${lastPt.x.toFixed(1)}" cy="${lastPt.y.toFixed(1)}" fill="var(--rc-bg-dark)" stroke="${COLOR_HEX[lastPt.color] || '#888'}" />`;

    // Hover elements
    svg += `<line id="traj-hover-line" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" class="traj-hover-line" style="display:none" />`;
    svg += `<circle id="traj-hover-dot" cx="0" cy="0" class="traj-hover-dot" style="display:none" />`;
    svg += `<rect x="${padL}" y="${padT}" width="${chartW}" height="${chartH}" fill="transparent" class="traj-hover-area" />`;
    svg += '</svg>';

    const chartEl = container.querySelector('.traj-chart-area');
    chartEl.innerHTML = `<div class="traj-wrap">${svg}<div class="traj-tooltip" id="traj-tooltip"></div></div>`;

    const wrap = chartEl.querySelector('.traj-wrap');
    const svgEl = chartEl.querySelector('.trajectory-svg');

    // Hide stale tooltip on new touch so it doesn't flash at old position
    wrap.addEventListener('touchstart', () => {
      const t = document.getElementById('traj-tooltip');
      if (t) t.style.display = 'none';
    }, { passive: true });

    wrap.addEventListener('mousemove', (e) => {
      if (tmPlaying) return;
      const hoverLine = document.getElementById('traj-hover-line');
      const hoverDot = document.getElementById('traj-hover-dot');
      const tooltip = document.getElementById('traj-tooltip');
      if (!hoverLine) return;

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
      const hex = COLOR_HEX[p.color] || '#888';

      hoverLine.setAttribute('x1', p.x.toFixed(1));
      hoverLine.setAttribute('x2', p.x.toFixed(1));
      hoverLine.style.display = '';
      hoverDot.setAttribute('cx', p.x.toFixed(1));
      hoverDot.setAttribute('cy', p.y.toFixed(1));
      hoverDot.setAttribute('stroke', hex);
      hoverDot.style.display = '';

      const typeLabel = d._type === 'album' ? 'Album' : d._type === 'ep' ? 'EP' : 'Single';
      const tracksMeta = `${d.chart_song_count} track${d.chart_song_count === 1 ? '' : 's'}`;
      const dateLabel = d._date || String(d.year);
      const wrapRect = wrap.getBoundingClientRect();
      const pixelX = e.clientX - wrapRect.left;
      const wrapW = wrapRect.width;
      tooltip.style.left = pixelX + 'px';
      tooltip.style.transform = pixelX > wrapW * 0.7 ? 'translateX(-100%)' : pixelX < wrapW * 0.3 ? 'translateX(0)' : 'translateX(-50%)';
      tooltip.innerHTML = `<strong>${escapeHtml(d._title)}</strong> <span style="color:${hex}">${degreeToScore(p.degree)}</span> ${CHARGE_LABELS[p.color]}<br><span class="traj-tooltip-sub">${typeLabel} · ${dateLabel} · ${tracksMeta}</span>`;
      tooltip.style.display = 'block';
    });

    wrap.addEventListener('mouseleave', () => {
      const hoverLine = document.getElementById('traj-hover-line');
      const hoverDot = document.getElementById('traj-hover-dot');
      const tooltip = document.getElementById('traj-tooltip');
      if (hoverLine) hoverLine.style.display = 'none';
      if (hoverDot) hoverDot.style.display = 'none';
      if (tooltip) tooltip.style.display = 'none';
    });

    // Click: move compass + charge bar to clicked release
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
      saveCompassState();
      setCompassDate(d._date || String(d.year));
      if (typeof Compass !== 'undefined') Compass.setDegree(p.degree, p.color);
      if (typeof Charge !== 'undefined') Charge.setLevel(p.color, 0, 0, p.degree);

      tmStopPlayback();
      tmPosition = nearest;
      updateTimeMachine(tmPosition);

      const dot = document.getElementById('traj-hover-dot');
      if (dot) { dot.classList.add('click-pulse'); setTimeout(() => dot.classList.remove('click-pulse'), 100); }
      pinchLine(chartPoints, nearest, svgEl.querySelector('.trajectory-line'), svgEl.querySelector('.trajectory-area'), padT, chartH, chartHasYTD);
    });

    tmPosition = maxIdx;
  }

  function pinchLine(points, nearestIdx, lineEl, areaEl, padT, chartH, ytdSplit) {
    const radius = 20;
    const strength = 0.35;
    const target = points[nearestIdx];
    const pinched = points.map(p => {
      const dx = Math.abs(p.x - target.x);
      if (dx > radius) return p;
      const factor = strength * (1 - dx / radius);
      return { ...p, x: p.x + (target.x - p.x) * factor };
    });

    const buildLine = pts => pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const solidPinched = ytdSplit ? pinched.slice(0, -1) : pinched;
    const pinchedPath = buildLine(solidPinched);
    const fullPinched = pinched;
    const last = fullPinched[fullPinched.length - 1];
    const first = fullPinched[0];
    const pinchedArea = buildLine(fullPinched) + ` L ${last.x.toFixed(1)} ${padT + chartH} L ${first.x.toFixed(1)} ${padT + chartH} Z`;

    if (lineEl) lineEl.setAttribute('d', pinchedPath);
    if (areaEl) areaEl.setAttribute('d', pinchedArea);

    const solidPoints = ytdSplit ? points.slice(0, -1) : points;
    const origPath = buildLine(solidPoints);
    const origArea = buildLine(points) + ` L ${points[points.length - 1].x.toFixed(1)} ${padT + chartH} L ${points[0].x.toFixed(1)} ${padT + chartH} Z`;
    setTimeout(() => {
      if (lineEl) lineEl.setAttribute('d', origPath);
      if (areaEl) areaEl.setAttribute('d', origArea);
    }, 100);
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

    const slider = document.getElementById('timemachine-slider');
    const progressFill = document.getElementById('timemachine-progress');
    const resetBtn = document.getElementById('timemachine-reset');

    if (progressFill) progressFill.style.width = (max === 0 ? 100 : (pos / max * 100)) + '%';
    if (slider) {
      slider.value = Math.round(pos);
      slider.setAttribute('aria-valuetext', `${nearest._title}, ${CHARGE_LABELS[tier]}`);
    }
    if (resetBtn) resetBtn.style.display = '';

    // Clip trajectory chart to current position
    const clipRect = document.getElementById('traj-clip-rect');
    if (clipRect && chartPoints.length) {
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
    if (typeof Compass !== 'undefined') Compass.setDegree(deg, tier);
    if (typeof Charge !== 'undefined') Charge.setLevel(tier, 0, 0, deg);
    setCompassDate(nearest._date || String(nearest.year));
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
        <input type="range" class="timemachine-slider" id="timemachine-slider" min="0" max="${max}" value="${max}" step="1" aria-label="Time machine release slider" aria-valuetext="${escapeHtml(last._title)}">
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

    // Reset — restoreCompassState last so the saved artist compass wins over
    // updateTimeMachine's position-derived needle update.
    document.getElementById('timemachine-reset').addEventListener('click', () => {
      tmStopPlayback();
      tmPosition = max;
      updateTimeMachine(tmPosition);
      restoreCompassState();
      document.getElementById('timemachine-reset').style.display = 'none';
    });
  }

  function applyZoom(container) {
    const lastYear = allYearData[allYearData.length - 1].year;
    let filtered;
    switch (currentZoom) {
      case '5':  filtered = allYearData.filter(d => d.year > lastYear - 5);  break;
      case '10': filtered = allYearData.filter(d => d.year > lastYear - 10); break;
      case '20': filtered = allYearData.filter(d => d.year > lastYear - 20); break;
      case '30': filtered = allYearData.filter(d => d.year > lastYear - 30); break;
      default:   filtered = allYearData;
    }
    tmStopPlayback();
    renderTrajectoryChart(filtered, container);
    initTimeMachineControls(container);

    container.querySelectorAll('.traj-zoom-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.zoom === currentZoom);
    });
  }

  // --- Compass state save/restore (artist-page baseline is the catalog
  //     aggregate from summary.stats, not a homepage score element). ---
  let savedDegree = null;
  let savedCharge = null;
  let savedDateText = null;

  function saveCompassState() {
    if (savedDegree !== null) return;
    const s = artistPageState.summary && artistPageState.summary.stats;
    if (s && s.catalog_degree != null && s.catalog_tier) {
      savedDegree = s.catalog_degree;
      savedCharge = s.catalog_tier;
    }
    const dateEl = document.getElementById('compass-date-svg');
    if (dateEl) savedDateText = dateEl.textContent;
  }

  function restoreCompassState() {
    if (savedDegree !== null) {
      if (typeof Compass !== 'undefined') Compass.setDegree(savedDegree, savedCharge);
      if (typeof Charge !== 'undefined') {
        const s = artistPageState.summary && artistPageState.summary.stats;
        const songs = (s && s.total_calibrated_songs) || 0;
        Charge.setLevel(savedCharge, 0, songs, savedDegree);
      }
    }
    if (savedDateText !== null) {
      const dateEl = document.getElementById('compass-date-svg');
      if (dateEl) dateEl.textContent = savedDateText;
    }
    savedDegree = null;
    savedCharge = null;
    savedDateText = null;
  }


  /* ========== JSON-LD ========== */

  function maybeInjectJsonLd() {
    // We need both summary and trajectory to paint the full payload.
    const { summary, trajectory } = artistPageState;
    if (!summary || !trajectory) return;

    const el = document.getElementById('json-ld');
    if (!el) return;

    const releases = (trajectory || []).map(r => ({
      '@type': 'MusicAlbum',
      name: r.title,
      albumReleaseType: r.release_type === 'album' ? 'AlbumRelease'
        : r.release_type === 'ep' ? 'EPRelease' : 'SingleRelease',
      datePublished: r.release_date || (r.release_year ? String(r.release_year) : undefined),
      additionalProperty: r.charge_value != null ? [
        { '@type': 'PropertyValue', propertyID: 'RisingCompassTier', name: 'Rising Compass Calibration', value: r.tier_label },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassCharge', name: 'Rising Compass Charge Value', value: r.charge_value, minValue: -100, maxValue: 100 },
      ] : undefined,
    }));

    const jsonLd = {
      '@context': [
        'https://schema.org',
        { rc: 'https://risingcompass.net/schema/' },
      ],
      '@type': 'MusicGroup',
      name: summary.name,
      url: `https://risingcompass.net/artists/${encodeURIComponent(summary.slug)}`,
      additionalProperty: [
        { '@type': 'PropertyValue', propertyID: 'RisingCompassCatalogCharge', name: 'Catalog Charge', value: summary.stats.catalog_charge, minValue: -100, maxValue: 100 },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassCatalogTier', name: 'Catalog Calibration', value: summary.stats.catalog_tier_label },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassCalibratedSongs', name: 'Total Calibrated Songs', value: summary.stats.total_calibrated_songs },
      ],
      album: releases,
    };

    el.textContent = JSON.stringify(jsonLd);
  }

  /* ========== INIT ========== */

  if (document.getElementById('search-input')) {
    initSearchPage();
  }
  if (getArtistSlugFromUrl()) {
    initArtistPage();
  }
})();
