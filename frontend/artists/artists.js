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
    if (!input) return;

    let debounceTimer = null;

    input.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      const q = input.value.trim();
      if (q.length < 2) {
        resultsContainer.innerHTML = '';
        emptyState.hidden = true;
        initialState.hidden = q.length > 0;
        return;
      }
      debounceTimer = setTimeout(() => runSearch(q), 300);
    });

    async function runSearch(q) {
      initialState.hidden = true;
      try {
        const [artistData, songData] = await Promise.all([
          ArtistsAPI.searchArtists(q, 10),
          ArtistsAPI.searchSongs(q, 10),
        ]);

        const artists = artistData.results || [];
        const songs = songData.results || [];

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
        console.error('Search error:', err);
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

    // Kick off all four requests in parallel — each section renders as it returns.
    loadSummary(slug);
    loadTrajectory(slug);
    loadTopSongs(slug, 0);
    loadReleases(slug, 0);

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
      const el = document.getElementById('trajectory-chart');
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
     Mirrors the homepage aggregate trajectory chart (js/app.js
     renderTrajectoryChart) so styling + scale match: small viewBox,
     preserveAspectRatio="none", reuses .trajectory-svg / -line / -area /
     -grid-line / -y-label / -label / -dot classes from main.css.
  */

  function renderTrajectoryChart(trajectory) {
    const container = document.getElementById('trajectory-chart');
    if (!container) return;

    const data = (trajectory || []).filter(r => r.charge_value != null);
    if (data.length === 0) {
      container.innerHTML = '<p class="chart-empty">No classified releases to chart.</p>';
      return;
    }

    const W = 320, H = 120;
    const padL = 30, padR = 16, padT = 10, padB = 22;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const maxIdx = data.length - 1;

    // charge_value -100..+100  →  compass_degree 180..0   (y maps 0..chartH)
    const points = data.map((r, i) => {
      const degree = 90 - (r.charge_value * 0.9);
      return {
        x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
        y: padT + (degree / 180) * chartH,
        color: r.rubric_color,
        release: r,
      };
    });

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

    // Area + line
    svg += `<path class="trajectory-area" d="${areaPath}" fill="url(#artist-traj-area-grad)" />`;
    svg += `<path class="trajectory-line" d="${linePath}" stroke="url(#artist-traj-grad)" />`;

    // X-axis labels — first year, last year, optional midpoint
    const yearOf = (r) =>
      r.release_year || (r.release_date ? parseInt(r.release_date.slice(0, 4), 10) : null);
    const firstYear = yearOf(data[0]);
    const lastYear = yearOf(data[maxIdx]);
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

    // Per-release dots
    for (const p of points) {
      const hex = COLOR_HEX[p.color] || '#888';
      svg += `<circle class="trajectory-dot" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" fill="var(--rc-bg-dark)" stroke="${hex}" />`;
    }

    svg += '</svg>';
    container.innerHTML = svg;
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
