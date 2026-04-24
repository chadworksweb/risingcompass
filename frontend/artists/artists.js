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

  // --- Trajectory time-machine shared constants ---
  const TM_SPEEDS = [1, 2, 4];
  const TM_BASE_SPEED = 1.5; // points-per-second at 1x (adjusted per page)

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
                <a href="artist.html?slug=${encodeURIComponent(a.slug)}">
                  <span class="result-name">${escapeHtml(a.name)}</span>
                  <span class="result-meta">${a.calibrated_song_count} song${a.calibrated_song_count !== 1 ? 's' : ''} classified</span>
                </a>
              </li>`;
            } else {
              html += `<li class="result-item result-artist result-unindexed">
                <div class="result-unindexed-content">
                  <span class="result-name">${escapeHtml(a.name)}</span>
                  <span class="result-meta">${a.calibrated_song_count} song${a.calibrated_song_count !== 1 ? 's' : ''} classified — trajectory not yet built</span>
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

  function initArtistPage() {
    const params = new URLSearchParams(window.location.search);
    const slug = params.get('slug');
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
      return;
    }

    artistPageState.summary = data;

    try {
      renderHeader(data);
    } catch (err) { console.error('renderHeader failed:', err); }
    try {
      renderCatalogCompass(data.stats);
    } catch (err) { console.error('renderCatalogCompass failed:', err); }
    try {
      renderBreakdown(data.stats);
    } catch (err) { console.error('renderBreakdown failed:', err); }
    try {
      maybeInjectJsonLd();
    } catch (err) { console.error('maybeInjectJsonLd failed:', err); }

    if (data.stats && data.stats.total_calibrated_songs === 0) {
      const zero = document.getElementById('zero-state');
      if (zero) zero.hidden = false;
    }
  }

  async function loadTrajectory(slug) {
    try {
      const data = await ArtistsAPI.getArtistTrajectory(slug);
      artistPageState.trajectory = data.points || [];
      renderTrajectoryChart(artistPageState.trajectory);
      maybeInjectJsonLd();
    } catch (err) {
      console.error('Failed to load trajectory:', err);
      const el = document.getElementById('artist-trajectory-chart');
      if (el) el.innerHTML = '<p class="chart-empty">Couldn\'t load trajectory.</p>';
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
          list.innerHTML = '<li class="empty-row">No classified songs yet.</li>';
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
    document.getElementById('page-title').textContent =
      `${data.name} Lyrical Charge Trajectory — The Rising Compass`;
    const stats = data.stats || {};
    const chargeLabel = stats.catalog_charge != null
      ? `${chargeDisplay(stats.catalog_charge)} (${stats.catalog_tier_label || 'Unclassified'})`
      : 'N/A';
    document.getElementById('meta-description').content =
      `Lyrical charge trajectory for ${data.name}. ${stats.total_calibrated_songs} songs classified. Catalog charge ${chargeLabel}.`;
    document.getElementById('og-title').content =
      `${data.name} — Lyrical Charge Trajectory`;
    document.getElementById('og-description').content =
      `${data.name} catalog charge: ${chargeLabel}.`;
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
          <span class="stat-label">Song${totalSongs !== 1 ? 's' : ''} Classified</span>
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

  /* ========== TRAJECTORY CHART (SVG) ==========
     Release-based trajectory, ported from the homepage aggregate chart
     (js/app.js). Features: hover line/dot/tooltip, time-machine slider +
     playback that drives the Catalog Compass / Charge widgets to each
     release's state in turn, animated reveal via a clip-path that grows
     with the time-machine position.

     Reuses the shared SVG/tooltip/time-machine CSS classes from main.css.
  */

  // Closure-scoped chart state so the time machine can read what the
  // renderer built.
  let artistChartData = [];   // the filtered trajectory rows (release objects)
  let artistChartPoints = []; // SVG-space {x, y, degree, color, title, yearLabel}
  let artistTmPosition = 0;
  let artistTmPlaying = false;
  let artistTmDirection = 1;
  let artistTmSpeedIdx = 0;
  let artistTmAnimFrame = null;
  let artistCompassBaseline = null; // { degree, tier } to restore on reset

  function degreeToTier(deg) {
    // 0-36 red, 36-72 orange, 72-108 green, 108-144 blue, 144-180 violet
    if (deg < 36) return 'red';
    if (deg < 72) return 'orange';
    if (deg < 108) return 'green';
    if (deg < 144) return 'blue';
    return 'violet';
  }

  function releaseYearOf(r) {
    if (r.release_year) return r.release_year;
    if (r.release_date) return parseInt(r.release_date.slice(0, 4), 10);
    return null;
  }

  function renderTrajectoryChart(trajectory) {
    const container = document.getElementById('artist-trajectory-chart');
    if (!container) return;
    const chartEl = container.querySelector('.traj-chart-area');
    const tmEl = container.querySelector('.timemachine-controls');
    if (!chartEl) return;

    const data = (trajectory || []).filter(r => r.charge_value != null);
    artistChartData = data;
    if (data.length === 0) {
      chartEl.innerHTML = '<p class="chart-empty">No classified releases to chart.</p>';
      if (tmEl) tmEl.innerHTML = '';
      return;
    }

    const W = 320, H = 120;
    const padL = 30, padR = 16, padT = 10, padB = 22;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const maxIdx = data.length - 1;

    // charge_value -100..+100  →  compass_degree 180..0
    artistChartPoints = data.map((r, i) => {
      const degree = 90 - (r.charge_value * 0.9);
      return {
        x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
        y: padT + (degree / 180) * chartH,
        degree,
        color: r.rubric_color,
        title: r.title,
        yearLabel: releaseYearOf(r),
        release: r,
      };
    });
    const points = artistChartPoints;

    const linePath = points.map((p, i) =>
      `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`
    ).join(' ');
    const areaPath =
      linePath +
      ` L ${points[maxIdx].x.toFixed(1)} ${padT + chartH}` +
      ` L ${points[0].x.toFixed(1)} ${padT + chartH} Z`;

    let svg = `<svg class="trajectory-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Artist charge trajectory chart">`;
    svg += `<defs>
      <linearGradient id="artist-traj-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" />
        <stop offset="25%" stop-color="${COLOR_HEX.blue}" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" />
        <stop offset="75%" stop-color="${COLOR_HEX.orange}" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" />
      </linearGradient>
      <linearGradient id="artist-traj-area-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" stop-opacity="0.2" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" stop-opacity="0.05" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" stop-opacity="0.2" />
      </linearGradient>
      <clipPath id="artist-traj-clip"><rect id="artist-traj-clip-rect" x="0" y="0" width="${W}" height="${H}" /></clipPath>
    </defs>`;

    // Grid + Y-axis labels
    [
      { deg: 0,   label: '+100' },
      { deg: 45,  label: '' },
      { deg: 90,  label: '0' },
      { deg: 135, label: '' },
      { deg: 180, label: '-100' },
    ].forEach(({ deg, label }) => {
      const y = padT + (deg / 180) * chartH;
      svg += `<line class="trajectory-grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" />`;
      if (label) svg += `<text class="trajectory-y-label" x="${padL - 4}" y="${y + 3}">${label}</text>`;
    });

    // Clipped area + line (clip-rect expands with time-machine position)
    svg += `<g clip-path="url(#artist-traj-clip)">`;
    svg += `<path class="trajectory-area" d="${areaPath}" fill="url(#artist-traj-area-grad)" />`;
    svg += `<path class="trajectory-line" d="${linePath}" stroke="url(#artist-traj-grad)" />`;
    svg += `</g>`;

    // X-axis labels — first/last year, plus optional midpoint for longer catalogs
    const firstYear = points[0].yearLabel;
    const lastYear = points[maxIdx].yearLabel;
    if (firstYear && lastYear && firstYear !== lastYear) {
      svg += `<text class="trajectory-label" x="${padL.toFixed(1)}" y="${H - 4}" text-anchor="start">'${String(firstYear).slice(2)}</text>`;
      svg += `<text class="trajectory-label" x="${(W - padR).toFixed(1)}" y="${H - 4}" text-anchor="end">'${String(lastYear).slice(2)}</text>`;
      if (lastYear - firstYear >= 8) {
        const midYear = Math.round((firstYear + lastYear) / 2);
        svg += `<text class="trajectory-label" x="${(W / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle">'${String(midYear).slice(2)}</text>`;
      }
    } else if (firstYear) {
      svg += `<text class="trajectory-label" x="${(W / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle">${firstYear}</text>`;
    }

    // Per-release dots (always visible, on top of clipped line)
    for (const p of points) {
      const hex = COLOR_HEX[p.color] || '#888';
      svg += `<circle class="trajectory-dot" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" fill="var(--rc-bg-dark)" stroke="${hex}" />`;
    }

    // Time-machine moving dot (starts at last release)
    const lastPt = points[maxIdx];
    svg += `<circle id="artist-tm-dot" class="trajectory-dot" cx="${lastPt.x.toFixed(1)}" cy="${lastPt.y.toFixed(1)}" fill="var(--rc-bg-dark)" stroke="${COLOR_HEX[lastPt.color] || '#888'}" />`;

    // Hover overlay
    svg += `<line id="artist-traj-hover-line" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" class="traj-hover-line" style="display:none" />`;
    svg += `<circle id="artist-traj-hover-dot" cx="0" cy="0" class="traj-hover-dot" style="display:none" />`;
    svg += `<rect x="${padL}" y="${padT}" width="${chartW}" height="${chartH}" fill="transparent" class="traj-hover-area" />`;
    svg += '</svg>';

    chartEl.innerHTML = `<div class="traj-wrap">${svg}<div class="traj-tooltip" id="artist-traj-tooltip"></div></div>`;

    const wrap = chartEl.querySelector('.traj-wrap');
    const svgEl = chartEl.querySelector('.trajectory-svg');

    // Hide stale tooltip on fresh touch so it doesn't flash at a previous position
    wrap.addEventListener('touchstart', () => {
      const t = document.getElementById('artist-traj-tooltip');
      if (t) t.style.display = 'none';
    }, { passive: true });

    wrap.addEventListener('mousemove', (e) => {
      if (artistTmPlaying) return;
      const hoverLine = document.getElementById('artist-traj-hover-line');
      const hoverDot = document.getElementById('artist-traj-hover-dot');
      const tooltip = document.getElementById('artist-traj-tooltip');
      if (!hoverLine) return;

      const rect = svgEl.getBoundingClientRect();
      const relX = (e.clientX - rect.left) / rect.width;
      const svgX = relX * W;

      let nearest = 0, minDist = Infinity;
      for (let i = 0; i <= maxIdx; i++) {
        const dist = Math.abs(points[i].x - svgX);
        if (dist < minDist) { minDist = dist; nearest = i; }
      }

      const p = points[nearest];
      const r = p.release;
      const hex = COLOR_HEX[p.color] || '#888';

      hoverLine.setAttribute('x1', p.x.toFixed(1));
      hoverLine.setAttribute('x2', p.x.toFixed(1));
      hoverLine.style.display = '';
      hoverDot.setAttribute('cx', p.x.toFixed(1));
      hoverDot.setAttribute('cy', p.y.toFixed(1));
      hoverDot.setAttribute('stroke', hex);
      hoverDot.style.display = '';

      const typeLabel = r.release_type === 'album' ? 'Album'
        : r.release_type === 'ep' ? 'EP' : 'Single';
      const charge = chargeDisplay(r.charge_value);
      const yearStr = p.yearLabel || '';
      const tier = CHARGE_LABELS[p.color] || '';
      const wrapRect = wrap.getBoundingClientRect();
      const pixelX = e.clientX - wrapRect.left;
      const wrapW = wrapRect.width;
      tooltip.style.left = pixelX + 'px';
      tooltip.style.transform = pixelX > wrapW * 0.7 ? 'translateX(-100%)'
        : pixelX < wrapW * 0.3 ? 'translateX(0)'
        : 'translateX(-50%)';
      tooltip.innerHTML = `<strong>${escapeHtml(p.title)}</strong> <span style="color:${hex}">${charge}</span> ${tier}<br><span class="traj-tooltip-sub">${typeLabel}${yearStr ? ' · ' + yearStr : ''}</span>`;
      tooltip.style.display = 'block';
    });

    wrap.addEventListener('mouseleave', () => {
      const hoverLine = document.getElementById('artist-traj-hover-line');
      const hoverDot = document.getElementById('artist-traj-hover-dot');
      const tooltip = document.getElementById('artist-traj-tooltip');
      if (hoverLine) hoverLine.style.display = 'none';
      if (hoverDot) hoverDot.style.display = 'none';
      if (tooltip) tooltip.style.display = 'none';
    });

    // Click: move time machine to the clicked release
    wrap.addEventListener('click', (e) => {
      const rect = svgEl.getBoundingClientRect();
      const relX = (e.clientX - rect.left) / rect.width;
      const svgX = relX * W;
      let nearest = 0, minDist = Infinity;
      for (let i = 0; i <= maxIdx; i++) {
        const dist = Math.abs(points[i].x - svgX);
        if (dist < minDist) { minDist = dist; nearest = i; }
      }
      saveArtistCompassBaseline();
      artistTmStopPlayback();
      artistTmPosition = nearest;
      updateArtistTimeMachine(nearest);
      const dot = document.getElementById('artist-traj-hover-dot');
      if (dot) { dot.classList.add('click-pulse'); setTimeout(() => dot.classList.remove('click-pulse'), 100); }
    });

    // Initialise TM position to the end
    artistTmPosition = maxIdx;

    initArtistTimeMachineControls();
  }

  /* ---------- Time Machine ---------- */

  function saveArtistCompassBaseline() {
    if (artistCompassBaseline) return; // already saved
    const s = artistPageState.summary && artistPageState.summary.stats;
    if (s && s.catalog_degree != null && s.catalog_tier) {
      artistCompassBaseline = { degree: s.catalog_degree, tier: s.catalog_tier, songs: s.total_calibrated_songs || 0 };
    }
  }

  function restoreArtistCompassBaseline() {
    if (!artistCompassBaseline) return;
    const { degree, tier, songs } = artistCompassBaseline;
    if (typeof Compass !== 'undefined') Compass.setDegree(degree, tier);
    if (typeof Charge !== 'undefined') Charge.setLevel(tier, 0, songs, degree);
  }

  function updateArtistTimeMachine(pos) {
    const max = artistChartData.length - 1;
    if (max < 0) return;

    const i = Math.floor(pos);
    const frac = pos - i;
    const a = artistChartPoints[Math.min(i, max)];
    const b = artistChartPoints[Math.min(i + 1, max)];

    const deg = a.degree + (b.degree - a.degree) * frac;
    const tier = degreeToTier(deg);
    const hex = COLOR_HEX[tier] || '#888';

    const slider = document.getElementById('artist-tm-slider');
    const progressFill = document.getElementById('artist-tm-progress');
    const resetBtn = document.getElementById('artist-tm-reset');

    if (progressFill) progressFill.style.width = (max === 0 ? 100 : (pos / max * 100)) + '%';
    if (slider) {
      slider.value = Math.round(pos);
      const nearest = artistChartData[Math.round(pos)];
      slider.setAttribute('aria-valuetext', `${nearest.title}, ${CHARGE_LABELS[tier]}`);
    }
    if (resetBtn) resetBtn.style.display = '';

    // Grow the clip rect to the current position (animated reveal effect)
    const clipRect = document.getElementById('artist-traj-clip-rect');
    if (clipRect && artistChartPoints.length) {
      const px = a.x + (b.x - a.x) * frac;
      clipRect.setAttribute('width', (px + 2).toFixed(1));
    }

    // Move the TM dot
    const tmDot = document.getElementById('artist-tm-dot');
    if (tmDot) {
      const px = a.x + (b.x - a.x) * frac;
      const py = a.y + (b.y - a.y) * frac;
      tmDot.setAttribute('cx', px.toFixed(1));
      tmDot.setAttribute('cy', py.toFixed(1));
      tmDot.setAttribute('stroke', hex);
    }

    // Drive the Catalog Compass + Charge widgets to the release-at-position
    if (typeof Compass !== 'undefined') Compass.setDegree(deg, tier);
    if (typeof Charge !== 'undefined') Charge.setLevel(tier, 0, 0, deg);
  }

  function artistTmAnimate(ts) {
    if (!artistTmPlaying) return;
    if (!artistTmAnimate.lastTime) artistTmAnimate.lastTime = ts;
    const dt = (ts - artistTmAnimate.lastTime) / 1000;
    artistTmAnimate.lastTime = ts;

    const max = artistChartData.length - 1;
    artistTmPosition += TM_BASE_SPEED * TM_SPEEDS[artistTmSpeedIdx] * artistTmDirection * dt;

    if (artistTmDirection === 1 && artistTmPosition >= max) { artistTmPosition = max; artistTmStopPlayback(); }
    if (artistTmDirection === -1 && artistTmPosition <= 0) { artistTmPosition = 0; artistTmStopPlayback(); }

    updateArtistTimeMachine(artistTmPosition);
    if (artistTmPlaying) artistTmAnimFrame = requestAnimationFrame(artistTmAnimate);
  }

  function artistTmStartPlayback(dir) {
    saveArtistCompassBaseline();
    artistTmDirection = dir;
    const max = artistChartData.length - 1;
    if (dir === 1 && artistTmPosition >= max) artistTmPosition = 0;
    if (dir === -1 && artistTmPosition <= 0) artistTmPosition = max;

    artistTmPlaying = true;
    artistTmAnimate.lastTime = null;

    const playBtn = document.getElementById('artist-tm-play');
    const playIcon = document.getElementById('artist-tm-play-icon');
    const revBtn = document.getElementById('artist-tm-rev');
    const fwdBtn = document.getElementById('artist-tm-fwd');
    if (playBtn) playBtn.classList.add('active');
    if (dir === 1 && fwdBtn) fwdBtn.classList.add('active');
    if (dir === -1 && revBtn) revBtn.classList.add('active');
    if (playIcon) playIcon.innerHTML = '<rect fill="currentColor" x="6" y="4" width="4" height="16"/><rect fill="currentColor" x="14" y="4" width="4" height="16"/>';

    artistTmAnimFrame = requestAnimationFrame(artistTmAnimate);
  }

  function artistTmStopPlayback() {
    artistTmPlaying = false;
    if (artistTmAnimFrame) cancelAnimationFrame(artistTmAnimFrame);
    ['artist-tm-play', 'artist-tm-rev', 'artist-tm-fwd'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.remove('active');
    });
    const playIcon = document.getElementById('artist-tm-play-icon');
    if (playIcon) playIcon.innerHTML = '<path fill="currentColor" d="M8 5v14l11-7z"/>';
  }

  function initArtistTimeMachineControls() {
    const max = artistChartData.length - 1;
    if (max < 1) return;
    const last = artistChartData[max];
    const container = document.getElementById('artist-trajectory-chart');
    if (!container) return;
    const tmArea = container.querySelector('.timemachine-controls');
    if (!tmArea) return;

    tmArea.innerHTML = `
      <div class="timemachine-wrap">
        <input type="range" class="timemachine-slider" id="artist-tm-slider" min="0" max="${max}" value="${max}" step="1" aria-label="Time machine release slider" aria-valuetext="${escapeHtml(last.title)}">
      </div>
      <div class="timemachine-playback">
        <button class="timemachine-play-btn" id="artist-tm-rev" title="Play backward" aria-label="Play backward">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M11 18V6l-8.5 6 8.5 6zm.5-6l8.5 6V6l-8.5 6z"/></svg>
        </button>
        <button class="timemachine-play-btn" id="artist-tm-play" title="Play forward" aria-label="Play forward">
          <svg viewBox="0 0 24 24" width="14" height="14" id="artist-tm-play-icon" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
        </button>
        <button class="timemachine-play-btn" id="artist-tm-fwd" title="Play forward fast" aria-label="Play forward fast">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M4 18l8.5-6L4 6v12zm9-12v12l8.5-6L13 6z"/></svg>
        </button>
        <div class="timemachine-progress"><div class="timemachine-progress-fill" id="artist-tm-progress" style="width:100%"></div></div>
        <button class="timemachine-speed-btn" id="artist-tm-speed" aria-label="Playback speed">1x</button>
        <button class="timemachine-reset" id="artist-tm-reset" aria-label="Reset time machine" style="display:none;">Reset</button>
      </div>
    `;

    const slider = document.getElementById('artist-tm-slider');
    document.getElementById('artist-tm-play').addEventListener('click', () => {
      if (artistTmPlaying) { artistTmStopPlayback(); return; }
      artistTmStartPlayback(1);
    });
    document.getElementById('artist-tm-rev').addEventListener('click', () => {
      if (artistTmPlaying && artistTmDirection === -1) { artistTmStopPlayback(); return; }
      artistTmStopPlayback();
      artistTmStartPlayback(-1);
    });
    document.getElementById('artist-tm-fwd').addEventListener('click', () => {
      if (artistTmPlaying && artistTmDirection === 1) { artistTmStopPlayback(); return; }
      artistTmStopPlayback();
      artistTmStartPlayback(1);
    });
    document.getElementById('artist-tm-speed').addEventListener('click', () => {
      artistTmSpeedIdx = (artistTmSpeedIdx + 1) % TM_SPEEDS.length;
      document.getElementById('artist-tm-speed').textContent = TM_SPEEDS[artistTmSpeedIdx] + 'x';
    });

    slider.addEventListener('input', () => {
      saveArtistCompassBaseline();
      artistTmStopPlayback();
      artistTmPosition = parseInt(slider.value);
      updateArtistTimeMachine(artistTmPosition);
    });

    document.getElementById('artist-tm-reset').addEventListener('click', () => {
      artistTmStopPlayback();
      artistTmPosition = max;
      // Fully reveal the clip rect + set TM dot to final position
      updateArtistTimeMachine(artistTmPosition);
      // Restore catalog aggregate on the compass/charge widgets
      restoreArtistCompassBaseline();
      document.getElementById('artist-tm-reset').style.display = 'none';
    });
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
        { '@type': 'PropertyValue', propertyID: 'RisingCompassTier', name: 'Rising Compass Classification', value: r.tier_label },
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
      url: `https://risingcompass.net/artists/artist.html?slug=${summary.slug}`,
      additionalProperty: [
        { '@type': 'PropertyValue', propertyID: 'RisingCompassCatalogCharge', name: 'Catalog Charge', value: summary.stats.catalog_charge, minValue: -100, maxValue: 100 },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassCatalogTier', name: 'Catalog Classification', value: summary.stats.catalog_tier_label },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassClassifiedSongs', name: 'Total Classified Songs', value: summary.stats.total_calibrated_songs },
      ],
      album: releases,
    };

    el.textContent = JSON.stringify(jsonLd);
  }

  /* ========== INIT ========== */

  if (document.getElementById('search-input')) {
    initSearchPage();
  }
  if (new URLSearchParams(window.location.search).get('slug')) {
    initArtistPage();
  }
})();
