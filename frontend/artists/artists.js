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
              <a href="/songs/song.html?slug=${encodeURIComponent(s.slug)}">
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
    try {
      const data = await ArtistsAPI.getArtistSummary(slug);
      artistPageState.summary = data;
      renderHeader(data);
      renderCatalogCompass(data.stats);
      renderBreakdown(data.stats);
      maybeInjectJsonLd();

      if (data.stats.total_calibrated_songs === 0) {
        document.getElementById('zero-state').hidden = false;
      }
    } catch (err) {
      console.error('Failed to load summary:', err);
      document.getElementById('artist-name').textContent = 'Artist not found';
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
      ? `<a class="song-title-link" href="/songs/song.html?slug=${encodeURIComponent(s.slug)}">${titleHtml}</a>`
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

  /* ========== TRAJECTORY CHART (SVG) ========== */

  function renderTrajectoryChart(trajectory) {
    const container = document.getElementById('trajectory-chart');
    if (!container) return;
    if (!trajectory || trajectory.length === 0) {
      container.innerHTML = '<p class="chart-empty">Not enough data to render trajectory.</p>';
      return;
    }

    const points = trajectory.filter(r => r.charge_value != null);
    if (points.length === 0) {
      container.innerHTML = '<p class="chart-empty">No classified releases to chart.</p>';
      return;
    }

    const W = 800, H = 300;
    const PAD = { top: 30, right: 30, bottom: 50, left: 55 };
    const plotW = W - PAD.left - PAD.right;
    const plotH = H - PAD.top - PAD.bottom;

    const xPositions = [];
    if (points.length === 1) {
      xPositions.push(plotW / 2);
    } else {
      const step = plotW / (points.length - 1);
      for (let i = 0; i < points.length; i++) xPositions.push(i * step);
    }

    function yForCharge(charge) {
      return PAD.top + plotH * (1 - (charge + 100) / 200);
    }

    const tierBands = [
      { min: 75, max: 100, color: '#aa54ff' },
      { min: 25, max: 74, color: '#3388ff' },
      { min: -24, max: 24, color: '#33cc55' },
      { min: -74, max: -25, color: '#ffbb33' },
      { min: -100, max: -75, color: '#ff3333' },
    ];

    let bandsHtml = '';
    for (const band of tierBands) {
      const y1 = yForCharge(band.max);
      const y2 = yForCharge(band.min);
      bandsHtml += `<rect x="${PAD.left}" y="${y1}" width="${plotW}" height="${y2 - y1}" fill="${band.color}" opacity="0.06"/>`;
    }

    const zeroY = yForCharge(0);
    const zeroLine = `<line x1="${PAD.left}" y1="${zeroY}" x2="${PAD.left + plotW}" y2="${zeroY}" stroke="#444" stroke-dasharray="4,4" opacity="0.5"/>`;

    let yLabels = '';
    for (const val of [100, 50, 0, -50, -100]) {
      const y = yForCharge(val);
      yLabels += `<text x="${PAD.left - 10}" y="${y + 4}" text-anchor="end" fill="#808094" font-size="11">${val > 0 ? '+' : ''}${val}</text>`;
    }

    let pathD = '';
    let dotsHtml = '';
    let labelsHtml = '';

    for (let i = 0; i < points.length; i++) {
      const x = PAD.left + xPositions[i];
      const y = yForCharge(points[i].charge_value);
      const color = COLOR_HEX[points[i].rubric_color] || '#999';
      const r = points[i].release_type === 'album' ? 7 : points[i].release_type === 'ep' ? 5.5 : 4;

      if (i === 0) pathD += `M ${x} ${y}`;
      else pathD += ` L ${x} ${y}`;

      dotsHtml += `<circle cx="${x}" cy="${y}" r="${r}" fill="${color}" stroke="#0a0a14" stroke-width="2" class="chart-dot" data-index="${i}"/>`;

      const label = points[i].release_date
        ? new Date(points[i].release_date + 'T00:00:00').getFullYear()
        : points[i].release_year || '';
      if (i === 0 || i === points.length - 1 || points.length <= 10 || i % Math.ceil(points.length / 8) === 0) {
        labelsHtml += `<text x="${x}" y="${H - 10}" text-anchor="middle" fill="#808094" font-size="11">${label}</text>`;
      }
    }

    const firstX = PAD.left + xPositions[0];
    const lastX = PAD.left + xPositions[xPositions.length - 1];
    const bottomY = PAD.top + plotH;
    const areaD = pathD + ` L ${lastX} ${bottomY} L ${firstX} ${bottomY} Z`;

    const tooltipId = 'chart-tooltip';

    container.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img"
           aria-label="Artist charge trajectory chart">
        <defs>
          <linearGradient id="area-gradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#00d4aa" stop-opacity="0.2"/>
            <stop offset="100%" stop-color="#00d4aa" stop-opacity="0"/>
          </linearGradient>
        </defs>
        ${bandsHtml}
        ${zeroLine}
        ${yLabels}
        <path d="${areaD}" fill="url(#area-gradient)"/>
        <path d="${pathD}" fill="none" stroke="#00d4aa" stroke-width="2" stroke-linejoin="round"/>
        ${dotsHtml}
        ${labelsHtml}
      </svg>
      <div id="${tooltipId}" class="chart-tooltip" hidden></div>
    `;

    const tooltip = document.getElementById(tooltipId);
    const svgEl = container.querySelector('svg');
    const dots = container.querySelectorAll('.chart-dot');

    dots.forEach(dot => {
      dot.addEventListener('mouseenter', () => {
        const idx = parseInt(dot.dataset.index);
        const p = points[idx];
        const color = COLOR_HEX[p.rubric_color] || '#999';
        const dateStr = p.release_date || (p.release_year ? String(p.release_year) : 'Unknown date');
        const charge = p.charge_value > 0 ? '+' + p.charge_value : p.charge_value;
        tooltip.innerHTML = `
          <div class="tt-title">${escapeHtml(p.title)}</div>
          <div class="tt-meta">${escapeHtml(p.release_type)} &middot; ${dateStr}</div>
          <div class="tt-charge" style="color:${color}">${charge} ${escapeHtml(p.tier_label)}</div>
        `;
        tooltip.hidden = false;

        const rect = svgEl.getBoundingClientRect();
        const cx = parseFloat(dot.getAttribute('cx'));
        const cy = parseFloat(dot.getAttribute('cy'));
        const scaleX = rect.width / W;
        const scaleY = rect.height / H;
        tooltip.style.left = (rect.left + cx * scaleX - tooltip.offsetWidth / 2) + 'px';
        tooltip.style.top = (rect.top + cy * scaleY - tooltip.offsetHeight - 12) + 'px';
      });

      dot.addEventListener('mouseleave', () => {
        tooltip.hidden = true;
      });
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
