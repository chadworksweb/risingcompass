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
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function degreeToScore(degree) {
    const s = Math.round((90 - degree) * 100 / 90);
    return (s > 0 ? '+' : '') + s;
  }

  function announce(msg) {
    const el = document.getElementById('sr-announce');
    if (el) el.textContent = msg;
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

  /* ========== ARTIST TRAJECTORY PAGE ========== */

  function initArtistPage() {
    const params = new URLSearchParams(window.location.search);
    const slug = params.get('slug');
    if (!slug) return;

    loadArtist(slug);
  }

  async function loadArtist(slug) {
    try {
      const data = await ArtistsAPI.getArtist(slug);

      // Update page title and meta
      document.getElementById('artist-name').textContent = data.name;
      document.getElementById('page-title').textContent = `${data.name} Lyrical Charge Trajectory — The Rising Compass`;
      document.getElementById('meta-description').content =
        `Lyrical charge trajectory for ${data.name}. ${data.stats.total_calibrated_songs} songs classified by The Rising Compass.`;
      document.getElementById('og-title').content = `${data.name} — Lyrical Charge Trajectory`;
      document.getElementById('og-description').content =
        `${data.name} catalog charge: ${data.stats.catalog_charge !== null ? (data.stats.catalog_charge > 0 ? '+' : '') + data.stats.catalog_charge : 'N/A'} (${data.stats.catalog_tier_label || 'Unclassified'}).`;

      if (data.stats.total_calibrated_songs === 0) {
        document.getElementById('zero-state').hidden = false;
        return;
      }

      renderStatsBar(data.stats);
      renderTrajectoryChart(data.trajectory);
      renderReleaseList(data.trajectory, slug);
      injectJsonLd(data);

      announce(`Loaded trajectory for ${data.name}`);
    } catch (err) {
      console.error('Failed to load artist:', err);
      document.getElementById('artist-name').textContent = 'Artist not found';
    }
  }

  function renderStatsBar(stats) {
    const container = document.getElementById('stats-bar');
    const tierColor = COLOR_HEX[stats.catalog_tier] || '#999';
    const chargeDisplay = stats.catalog_charge !== null
      ? (stats.catalog_charge > 0 ? '+' : '') + stats.catalog_charge
      : 'N/A';

    let breakdownHtml = '';
    for (const [color, count] of Object.entries(stats.tier_breakdown)) {
      if (count > 0) {
        breakdownHtml += `<span class="tier-pill" style="background:${COLOR_HEX[color]}20;color:${COLOR_HEX[color]}">${CHARGE_LABELS[color]} ${count}</span>`;
      }
    }

    container.innerHTML = `
      <div class="stat-card">
        <span class="stat-value">${stats.total_releases}</span>
        <span class="stat-label">Release${stats.total_releases !== 1 ? 's' : ''}</span>
      </div>
      <div class="stat-card">
        <span class="stat-value">${stats.total_calibrated_songs}</span>
        <span class="stat-label">Songs Classified</span>
      </div>
      <div class="stat-card stat-card--charge">
        <span class="stat-dot" style="background:${tierColor}"></span>
        <span class="stat-value" style="color:${tierColor}">${chargeDisplay}</span>
        <span class="stat-label">${stats.catalog_tier_label || ''}</span>
      </div>
      <div class="stat-card stat-card--breakdown">
        <div class="tier-pills">${breakdownHtml}</div>
      </div>
    `;
  }

  /* ========== TRAJECTORY CHART (SVG) ========== */

  function renderTrajectoryChart(trajectory) {
    const container = document.getElementById('trajectory-chart');
    if (!trajectory || trajectory.length === 0) {
      container.innerHTML = '<p class="chart-empty">Not enough data to render trajectory.</p>';
      return;
    }

    // Filter to releases with charge data
    const points = trajectory.filter(r => r.charge_value != null);
    if (points.length === 0) {
      container.innerHTML = '<p class="chart-empty">No classified releases to chart.</p>';
      return;
    }

    const W = 800, H = 300;
    const PAD = { top: 30, right: 30, bottom: 50, left: 55 };
    const plotW = W - PAD.left - PAD.right;
    const plotH = H - PAD.top - PAD.bottom;

    // X positioning: evenly spaced if only release_year, proportional if dates
    const xPositions = [];
    if (points.length === 1) {
      xPositions.push(plotW / 2);
    } else {
      const step = plotW / (points.length - 1);
      for (let i = 0; i < points.length; i++) {
        xPositions.push(i * step);
      }
    }

    // Y: charge -100 to +100 mapped to plotH (top = +100, bottom = -100)
    function yForCharge(charge) {
      return PAD.top + plotH * (1 - (charge + 100) / 200);
    }

    // Tier background bands
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

    // Zero line
    const zeroY = yForCharge(0);
    const zeroLine = `<line x1="${PAD.left}" y1="${zeroY}" x2="${PAD.left + plotW}" y2="${zeroY}" stroke="#444" stroke-dasharray="4,4" opacity="0.5"/>`;

    // Y axis labels
    let yLabels = '';
    for (const val of [100, 50, 0, -50, -100]) {
      const y = yForCharge(val);
      yLabels += `<text x="${PAD.left - 10}" y="${y + 4}" text-anchor="end" fill="#808094" font-size="11">${val > 0 ? '+' : ''}${val}</text>`;
    }

    // Build path and dots
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

      // Dot
      dotsHtml += `<circle cx="${x}" cy="${y}" r="${r}" fill="${color}" stroke="#0a0a14" stroke-width="2" class="chart-dot" data-index="${i}"/>`;

      // X axis label (year or date)
      const label = points[i].release_date
        ? new Date(points[i].release_date + 'T00:00:00').getFullYear()
        : points[i].release_year || '';
      if (i === 0 || i === points.length - 1 || points.length <= 10 || i % Math.ceil(points.length / 8) === 0) {
        labelsHtml += `<text x="${x}" y="${H - 10}" text-anchor="middle" fill="#808094" font-size="11">${label}</text>`;
      }
    }

    // Area fill under the line
    const firstX = PAD.left + xPositions[0];
    const lastX = PAD.left + xPositions[xPositions.length - 1];
    const bottomY = PAD.top + plotH;
    const areaD = pathD + ` L ${lastX} ${bottomY} L ${firstX} ${bottomY} Z`;

    // Tooltip (hidden, positioned on hover)
    const tooltipId = 'chart-tooltip';

    const svg = `
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

    container.innerHTML = svg;

    // Tooltip interaction
    const tooltip = document.getElementById(tooltipId);
    const svgEl = container.querySelector('svg');
    const dots = container.querySelectorAll('.chart-dot');

    dots.forEach(dot => {
      dot.addEventListener('mouseenter', (e) => {
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

        // Position tooltip
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

      // Click scrolls to release in list
      dot.addEventListener('click', () => {
        const idx = parseInt(dot.dataset.index);
        const releaseEl = document.querySelector(`[data-release-id="${points[idx].id}"]`);
        if (releaseEl) {
          releaseEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
          releaseEl.classList.add('release-highlight');
          setTimeout(() => releaseEl.classList.remove('release-highlight'), 1500);
        }
      });
    });
  }

  /* ========== RELEASE LIST ========== */

  function renderReleaseList(trajectory, artistSlug) {
    const container = document.getElementById('release-list');
    if (!trajectory || trajectory.length === 0) return;

    let html = '<h2 class="section-title">Releases</h2>';

    for (const r of trajectory) {
      const color = COLOR_HEX[r.rubric_color] || '#999';
      const chargeDisplay = r.charge_value != null
        ? (r.charge_value > 0 ? '+' : '') + r.charge_value
        : 'N/A';
      const dateDisplay = r.release_date || (r.release_year ? String(r.release_year) : '');
      const typeLabel = r.release_type === 'album' ? 'Album'
        : r.release_type === 'ep' ? 'EP' : 'Single';

      html += `
        <div class="release-card" data-release-id="${r.id}">
          <div class="release-header">
            <span class="release-dot" style="background:${color}"></span>
            <div class="release-info">
              <span class="release-title">${escapeHtml(r.title)}</span>
              <span class="release-meta">
                <span class="release-type">${typeLabel}</span>
                ${dateDisplay ? `<span class="release-date">${dateDisplay}</span>` : ''}
              </span>
            </div>
            <div class="release-charge" style="color:${color}">
              <span class="release-charge-value">${chargeDisplay}</span>
              <span class="release-charge-tier">${escapeHtml(r.tier_label)}</span>
            </div>
          </div>
          <div class="release-stats">
            <span>${r.calibrated_count}/${r.track_count} tracks classified</span>
            ${r.contamination_count > 0 ? `<span class="release-contam">${r.contamination_count} contaminated</span>` : ''}
          </div>
          <div class="release-songs" id="release-songs-${r.id}" hidden>
            <div class="release-songs-loading">Loading songs...</div>
          </div>
          <button class="release-expand" data-release-id="${r.id}" data-artist-slug="${artistSlug}" aria-expanded="false">
            Show songs
          </button>
        </div>
      `;
    }

    container.innerHTML = html;

    // Expand/collapse handlers
    container.querySelectorAll('.release-expand').forEach(btn => {
      btn.addEventListener('click', async () => {
        const rid = btn.dataset.releaseId;
        const slug = btn.dataset.artistSlug;
        const songsContainer = document.getElementById(`release-songs-${rid}`);
        const expanded = btn.getAttribute('aria-expanded') === 'true';

        if (expanded) {
          songsContainer.hidden = true;
          btn.setAttribute('aria-expanded', 'false');
          btn.textContent = 'Show songs';
        } else {
          songsContainer.hidden = false;
          btn.setAttribute('aria-expanded', 'true');
          btn.textContent = 'Hide songs';

          // Load songs if not yet loaded
          if (songsContainer.querySelector('.release-songs-loading')) {
            try {
              const data = await ArtistsAPI.getArtistSongs(slug, rid, 0, 50);
              let songsHtml = '<ul class="song-list">';
              for (const s of data.items) {
                const sColor = COLOR_HEX[s.rubric_color] || '#999';
                const sCharge = s.charge_value != null
                  ? (s.charge_value > 0 ? '+' : '') + s.charge_value
                  : '';
                songsHtml += `<li class="song-item">
                  <span class="song-dot" style="background:${sColor}"></span>
                  <div class="song-info">
                    <span class="song-title">${escapeHtml(s.title)}</span>
                  </div>
                  <span class="song-charge" style="color:${sColor}">${sCharge}</span>
                </li>`;
              }
              songsHtml += '</ul>';
              songsContainer.innerHTML = songsHtml;
            } catch (err) {
              songsContainer.innerHTML = '<p class="error-message">Failed to load songs.</p>';
            }
          }
        }
      });
    });
  }

  /* ========== JSON-LD ========== */

  function injectJsonLd(data) {
    const el = document.getElementById('json-ld');
    if (!el) return;

    const releases = (data.trajectory || []).map(r => ({
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
      name: data.name,
      url: `https://risingcompass.net/artists/artist.html?slug=${data.slug}`,
      additionalProperty: [
        { '@type': 'PropertyValue', propertyID: 'RisingCompassCatalogCharge', name: 'Catalog Charge', value: data.stats.catalog_charge, minValue: -100, maxValue: 100 },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassCatalogTier', name: 'Catalog Classification', value: data.stats.catalog_tier_label },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassClassifiedSongs', name: 'Total Classified Songs', value: data.stats.total_calibrated_songs },
      ],
      album: releases,
    };

    el.textContent = JSON.stringify(jsonLd);
  }

  /* ========== INIT ========== */

  // Detect which page we're on
  if (document.getElementById('search-input')) {
    initSearchPage();
  }
  if (new URLSearchParams(window.location.search).get('slug')) {
    initArtistPage();
  }
})();
