/* === Main App Logic === */

function announce(msg) {
  const el = document.getElementById('sr-announce');
  if (!el) return;
  el.textContent = '';
  requestAnimationFrame(() => { el.textContent = msg; });
}

// Crossfade: fade out element, swap content, fade in
function crossfade(el, newHtml, callback) {
  el.style.transition = 'opacity 0.15s ease';
  void el.offsetHeight;          // force reflow so browser registers the transition
  el.style.opacity = '0';
  setTimeout(() => {
    el.innerHTML = newHtml;
    if (callback) callback();
    void el.offsetHeight;        // force reflow before fade-in
    el.style.opacity = '1';
  }, 160);
}

const App = (() => {
  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };

  const CHARGE_LABELS = {
    violet: 'Ascended',
    blue: 'Elevated',
    green: 'Decent',
    orange: 'Degraded',
    red: 'Corrupted',
  };

  // --- Initialize ---
  async function init() {
    Compass.render('compass-container');
    Charge.render('charge-container');
    Contamination.render('contam-container');
    initNav();
    initCalcOverlay();
    initEraTabs();
    initCalendarPicker();
    await loadCurrent();
    loadTrajectory();
    loadGhostTrail();
    loadLibrary();
  }

  function initCalcOverlay() {
    const btn = document.getElementById('calc-overlay-toggle');
    const overlay = document.getElementById('calc-overlay');
    if (!btn || !overlay) return;

    btn.addEventListener('click', () => {
      const open = overlay.classList.toggle('open');
      btn.classList.toggle('active', open);
    });
  }

  // --- Load Current Reading ---
  async function loadCurrent() {
    try {
      const data = await API.getCompassCurrent();

      // Set compass
      const degree = data.has_reading ? data.compass_degree : data.historical_degree;
      const charge = data.has_reading ? data.charge_level : data.historical_charge;
      // Small delay for needle animation effect
      setTimeout(() => {
        Compass.setDegree(degree, charge);
      }, 300);

      // Set charge bar
      const redCount = data.songs.filter(s => s.rubric_color === 'red').length;
      Charge.setLevel(charge, redCount, data.songs.length);

      // Set contamination
      Contamination.setCount(data.contamination_count, data.songs.length || 10);

      // Set compass date (SVG element inside compass, fallback to HTML div)
      const dateSvg = document.getElementById('compass-date-svg');
      const dateHtml = document.getElementById('compass-date');
      const dateText = data.has_reading ? formatDate(data.date) : 'Historical Aggregate';
      if (dateSvg) dateSvg.textContent = dateText;
      if (dateHtml) dateHtml.textContent = dateText;

      // Render right panel
      renderReading(data);

      // Render weekly album reading if present
      renderAlbumReading(data);

    } catch (err) {
      console.error('Failed to load compass data:', err);
      document.getElementById('reading-content').innerHTML =
        '<div class="error-msg">Could not load compass data. Is the API running?</div>';
    }
  }

  function renderReading(data) {
    const container = document.getElementById('reading-content');
    if (!container) return;

    // Remove year view button if present
    const yearBtn = document.getElementById('year-view-btn');
    if (yearBtn) yearBtn.remove();

    // Restore panel header for daily readings
    const header = document.querySelector('#reading-panel .card-header');
    const desc = document.querySelector('#reading-panel .card-desc');
    if (header) header.textContent = 'Daily Top 20 Songs';
    if (desc) desc.textContent = "Today's most-heard songs, individually charged.";

    // Sync calendar picker to this reading's date
    if (data.has_reading && data.date) {
      const [y, m, d] = data.date.split('-');
      syncCalendar(parseInt(y), m, d);
    }

    if (!data.has_reading) {
      container.innerHTML = `
        <div class="no-reading">
          <p>No daily reading yet.</p>
          <p>The compass is showing the historical aggregate of 650+ Billboard #1 songs analyzed from 1960-2024.</p>
        </div>
      `;
      return;
    }

    let html = '';
    // Daily charge summary with date tab
    const dailyCharge = CHARGE_LABELS[data.charge_level] || data.charge_level;
    const dailyHex = COLOR_HEX[data.charge_level] || '#888';
    html += `<div class="reading-charge-group">
      <div class="reading-date" style="background:${dailyHex}">${formatDate(data.date)}</div>
      <div class="reading-charge-row">
        <div class="reading-charge-bar" style="background:${dailyHex}">
          <div class="reading-charge-inner">
            <span class="reading-charge-score">${degreeToScore(data.compass_degree)}</span>
            <span class="reading-charge-label">${dailyCharge}</span>
            <span class="reading-charge-meta">${data.songs.length} songs &middot; ${data.contamination_count} contaminated</span>
          </div>
        </div>
        ${calendarToggleBtnHtml()}
      </div>
    </div>`;

    if (data.editorial_summary) {
      html += `<div class="reading-editorial">${escapeHtml(data.editorial_summary)}</div>`;
    }
    html += '<ul class="song-list">';
    const sortedSongs = [...data.songs].sort((a, b) => a.position - b.position);
    sortedSongs.forEach(song => {
      const hasMEI = song.message_analysis || song.expression_analysis || song.intention_analysis;
      const hasSummary = song.charge_summary || song.contamination_note;
      const hasTooltip = hasMEI || hasSummary;
      let tooltipHtml = '';
      if (hasTooltip) {
        const songHex = COLOR_HEX[song.rubric_color] || '#888';
        const songLabel = CHARGE_LABELS[song.rubric_color] || song.rubric_color;
        const songScore = song.charge_value != null ? (song.charge_value > 0 ? '+' + song.charge_value : String(song.charge_value)) : '';

        // Charge header — inline styled (no CSS classes)
        let lines = `<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.35rem;padding-bottom:0.3rem;border-bottom:1px solid rgba(0,0,0,0.08)"><span style="width:3px;height:14px;border-radius:1.5px;flex-shrink:0;background:${songHex}"></span><span style="font-family:var(--rc-font-mono);font-size:0.7rem;font-weight:700;letter-spacing:0.02em;color:${songHex}">${songScore} ${songLabel}</span></div>`;

        // Summary line
        if (song.charge_summary) lines += `<div style="font-size:0.72rem;color:rgba(20,20,30,0.65);font-style:italic;line-height:1.4;margin-bottom:0.3rem;padding-bottom:0.25rem;border-bottom:1px solid rgba(0,0,0,0.06)">${escapeHtml(song.charge_summary)}</div>`;

        if (song.message_analysis) lines += `<div class="mei-line"><strong>M</strong> ${escapeHtml(song.message_analysis)}</div>`;
        if (song.expression_analysis) lines += `<div class="mei-line"><strong>E</strong> ${escapeHtml(song.expression_analysis)}</div>`;
        if (song.intention_analysis) lines += `<div class="mei-line"><strong>I</strong> ${escapeHtml(song.intention_analysis)}</div>`;
        if (song.contaminated && song.contamination_note) lines += `<div class="mei-line mei-contam">&#x2622; ${escapeHtml(song.contamination_note)}</div>`;
        const disputeParams = new URLSearchParams({ title: song.title, artist: song.artist, color: song.rubric_color, pos: song.position });
        if (song.message_analysis) disputeParams.set('ma', song.message_analysis);
        if (song.expression_analysis) disputeParams.set('ea', song.expression_analysis);
        if (song.intention_analysis) disputeParams.set('ia', song.intention_analysis);
        if (song.charge_summary) disputeParams.set('cs', song.charge_summary);
        lines += `<div class="mei-dispute"><a href="/misread-submission.html?${disputeParams.toString()}">Did we get it wrong?</a></div>`;
        tooltipHtml = `<div class="song-tooltip">${lines}</div>`;
      }
      html += `
        <li class="song-item${hasTooltip ? ' has-tooltip' : ''}">
          <span class="song-pos">${song.position}</span>
          <span class="song-dot ${song.rubric_color}"></span>
          <div class="song-info">
            <div class="song-title">${escapeHtml(song.title)}</div>
            <div class="song-artist">${escapeHtml(song.artist)}</div>
          </div>
          <div class="song-actions">
            ${song.contaminated ? '<span class="song-contam" aria-hidden="true">&#x2622;</span>' : ''}
            ${hasTooltip ? `<button class="song-comment-btn" title="Read analysis" aria-label="Analysis of ${escapeHtml(song.title)}">&#x1F4AC;</button>` : ''}
          </div>
          ${tooltipHtml}
        </li>
      `;
    });
    html += '</ul>';

    crossfade(container, html, () => {
      initSongTooltips(container);
      wireCalendarToggle(container);
    });

    // Announce for screen readers
    if (data.has_reading) {
      const tier = CHARGE_LABELS[data.charge_level] || data.charge_level;
      announce(`Reading loaded for ${formatDate(data.date)}. Charge: ${tier}. ${data.songs.length} songs.`);
    }
  }

  function initSongTooltips(container) {
    container.querySelectorAll('.song-comment-btn').forEach(btn => {
      btn.setAttribute('aria-expanded', 'false');
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const item = btn.closest('.song-item');
        const wasActive = item.classList.contains('active');
        container.querySelectorAll('.song-item.active').forEach(el => {
          el.classList.remove('active');
          const b = el.querySelector('.song-comment-btn');
          if (b) b.setAttribute('aria-expanded', 'false');
        });
        if (!wasActive) {
          item.classList.add('active');
          btn.setAttribute('aria-expanded', 'true');
        }
      });
    });

    // Dismiss on click outside
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.song-comment-btn')) {
        container.querySelectorAll('.song-item.active').forEach(el => {
          el.classList.remove('active');
          const b = el.querySelector('.song-comment-btn');
          if (b) b.setAttribute('aria-expanded', 'false');
        });
      }
    });
  }

  function renderAlbumReading(data) {
    const panel = document.getElementById('album-reading-panel');
    const container = document.getElementById('album-reading-content');
    if (!panel || !container) return;

    // Album panel is under development — show placeholder with overlay
    panel.style.display = '';
    const readingPanel = document.getElementById('reading-panel');
    if (readingPanel) readingPanel.style.gridColumn = '';

    container.innerHTML = `
      <div style="position:relative;min-height:200px;">
        <ul class="song-list" style="opacity:0.12;pointer-events:none;">
          ${Array.from({length: 10}, (_, i) => `
            <li class="song-item">
              <span class="song-pos">${i + 1}</span>
              <span class="song-dot orange"></span>
              <div class="song-info">
                <div class="song-title" style="background:var(--rc-border);color:transparent;border-radius:3px;width:${60 + Math.random() * 30}%;">&nbsp;</div>
                <div class="song-artist" style="background:var(--rc-border);color:transparent;border-radius:3px;width:${40 + Math.random() * 20}%;margin-top:4px;">&nbsp;</div>
              </div>
            </li>
          `).join('')}
        </ul>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:0.5rem;">
          <span style="font-size:1.4rem;opacity:0.4;" aria-hidden="true">&#x1F6A7;</span>
          <span style="font-family:var(--rc-font-mono);font-size:0.82rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--rc-text-dim);">Under Development</span>
        </div>
      </div>
    `;
  }

  // --- Ghost Trail (past 30 days on compass) ---
  async function loadGhostTrail() {
    try {
      const data = await API.getHistory(1, 30);
      if (data.items && data.items.length) {
        Compass.setGhostTrail(data.items);
      }
    } catch (err) {
      // Silent fail — ghost trail is decorative
    }
  }

  // --- Calendar Picker ---
  let rolodexDatesCache = {};  // { year: ["2026-01-15", ...] }

  // --- Trajectory Chart (year-by-year with zoom + Time Machine) ---
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

  function renderTrajectoryChart(data, container) {
    if (!data.length) return;
    chartData = data;

    const W = 320, H = 120;
    const padL = 30, padR = 10, padT = 10, padB = 22;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const maxIdx = data.length - 1;

    // Detect if the last year is the current calendar year (YTD)
    const currentCalYear = new Date().getFullYear();
    const hasYTD = data[maxIdx].year === currentCalYear && maxIdx > 0;
    chartHasYTD = hasYTD;

    chartPoints = data.map((d, i) => ({
      x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
      y: padT + (d.compass_degree / 180) * chartH,
      degree: d.compass_degree,
      year: d.year,
      color: d.charge_level,
      isYTD: d.year === currentCalYear,
    }));

    // Build line paths — split into solid (completed years) and dashed (YTD segment)
    const solidPoints = hasYTD ? chartPoints.slice(0, -1) : chartPoints;
    const solidPath = solidPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const fullLinePath = chartPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const areaPath = fullLinePath + ` L ${chartPoints[maxIdx].x.toFixed(1)} ${padT + chartH} L ${chartPoints[0].x.toFixed(1)} ${padT + chartH} Z`;

    let ytdDashPath = '';
    if (hasYTD) {
      const prev = chartPoints[maxIdx - 1];
      const last = chartPoints[maxIdx];
      ytdDashPath = `M ${prev.x.toFixed(1)} ${prev.y.toFixed(1)} L ${last.x.toFixed(1)} ${last.y.toFixed(1)}`;
    }

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
    if (hasYTD) {
      svg += `<path class="trajectory-line trajectory-line-ytd" d="${ytdDashPath}" stroke="url(#traj-grad)" stroke-dasharray="4 3" opacity="0.6" />`;
    }
    svg += `</g>`;

    // X-axis labels
    const yearSpan = data[maxIdx].year - data[0].year;
    const labelInterval = yearSpan > 40 ? 10 : yearSpan > 15 ? 5 : yearSpan > 8 ? 2 : 1;
    const labelYears = new Set();
    const startDecade = Math.ceil(data[0].year / labelInterval) * labelInterval;
    for (let yr = startDecade; yr <= data[maxIdx].year; yr += labelInterval) labelYears.add(yr);
    labelYears.add(data[0].year);
    labelYears.add(data[maxIdx].year);

    chartPoints.forEach(p => {
      if (labelYears.has(p.year)) {
        const labelText = p.isYTD ? 'YTD' : `'${String(p.year).slice(2)}`;
        svg += `<text class="trajectory-label${p.isYTD ? ' trajectory-label-ytd' : ''}" x="${p.x.toFixed(1)}" y="${H - 4}" text-anchor="middle">${labelText}</text>`;
        svg += `<line x1="${p.x.toFixed(1)}" y1="${padT + chartH}" x2="${p.x.toFixed(1)}" y2="${padT + chartH + 4}" stroke="var(--rc-text-dim)" stroke-width="0.5" opacity="0.5" />`;
      }
    });

    // YTD open-ring dot (static, pulsing)
    if (hasYTD) {
      const ytdPt = chartPoints[maxIdx];
      const ytdHex = COLOR_HEX[ytdPt.color] || '#888';
      svg += `<circle class="trajectory-ytd-ring" cx="${ytdPt.x.toFixed(1)}" cy="${ytdPt.y.toFixed(1)}" r="4" fill="none" stroke="${ytdHex}" stroke-width="1.5" opacity="0.7" />`;
      svg += `<circle class="trajectory-ytd-pulse" cx="${ytdPt.x.toFixed(1)}" cy="${ytdPt.y.toFixed(1)}" r="4" fill="none" stroke="${ytdHex}" stroke-width="1" />`;
    }

    // Moving dot (time machine position indicator)
    const lastPt = chartPoints[maxIdx];
    svg += `<circle id="traj-tm-dot" class="trajectory-dot" cx="${lastPt.x.toFixed(1)}" cy="${lastPt.y.toFixed(1)}" fill="var(--rc-bg-dark)" stroke="${COLOR_HEX[lastPt.color] || '#888'}" />`;

    // Hover elements
    svg += `<line id="traj-hover-line" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" class="traj-hover-line" style="display:none" />`;
    svg += `<circle id="traj-hover-dot" cx="0" cy="0" class="traj-hover-dot" style="display:none" />`;
    svg += `<rect x="${padL}" y="${padT}" width="${chartW}" height="${chartH}" fill="transparent" class="traj-hover-area" />`;
    svg += '</svg>';

    const chartEl = container.querySelector('.traj-chart-area');
    chartEl.innerHTML = `<div class="traj-wrap">${svg}<div class="traj-tooltip" id="traj-tooltip"></div></div>`;

    // Hover interaction
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

      const yearLabel = p.isYTD ? `${d.year} YTD` : String(d.year);
      const songsMeta = p.isYTD ? `${d.chart_song_count} songs \u00B7 updated daily` : `${d.chart_song_count} charting songs`;
      const wrapRect = wrap.getBoundingClientRect();
      const pixelX = e.clientX - wrapRect.left;
      const wrapW = wrapRect.width;
      tooltip.style.left = pixelX + 'px';
      tooltip.style.transform = pixelX > wrapW * 0.7 ? 'translateX(-100%)' : pixelX < wrapW * 0.3 ? 'translateX(0)' : 'translateX(-50%)';
      tooltip.innerHTML = `<strong>${yearLabel}</strong> <span style="color:${hex}">${degreeToScore(p.degree)}</span> ${CHARGE_LABELS[p.color]}<br><span class="traj-tooltip-sub">${songsMeta}</span>`;
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

    // Click: move compass + charge bar to clicked year, load songs
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
      setCompassDate(p.isYTD ? `${d.year} YTD` : String(d.year));
      Compass.setDegree(p.degree, p.color);
      Charge.setLevel(p.color, 0, 0);

      loadYearSongs(d.year, p.degree, p.color);
      syncCalendar(d.year);

      const dot = document.getElementById('traj-hover-dot');
      if (dot) { dot.classList.add('click-pulse'); setTimeout(() => dot.classList.remove('click-pulse'), 100); }
      pinchLine(chartPoints, nearest, svgEl.querySelector('.trajectory-line'), svgEl.querySelector('.trajectory-area'), padT, chartH, chartHasYTD);
    });

    // Set initial TM position to end
    tmPosition = maxIdx;
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

    // Update info display
    const yearEl = document.getElementById('tm-year');
    const scoreEl = document.getElementById('tm-score');
    const tierEl = document.getElementById('tm-tier');
    const countEl = document.getElementById('tm-count');
    const slider = document.getElementById('tm-slider');
    const progressFill = document.getElementById('tm-progress');
    const resetBtn = document.getElementById('tm-reset');

    const nearestPt = chartPoints[Math.round(Math.min(pos, max))];
    const isYTD = nearestPt && nearestPt.isYTD;
    if (yearEl) yearEl.textContent = isYTD ? `${nearest.year} YTD` : nearest.year;
    if (scoreEl) { scoreEl.textContent = degreeToScore(deg); scoreEl.style.color = hex; }
    if (tierEl) tierEl.textContent = CHARGE_LABELS[tier];
    if (countEl) countEl.textContent = isYTD ? `${nearest.chart_song_count} songs \u00B7 updated daily` : `${nearest.chart_song_count} charting songs`;
    if (progressFill) progressFill.style.width = (pos / max * 100) + '%';
    if (slider) {
      slider.value = Math.round(pos);
      slider.setAttribute('aria-valuetext', `${nearest.year}, ${CHARGE_LABELS[tier]}`);
    }
    if (resetBtn) resetBtn.style.display = '';

    // Clip trajectory chart to current position
    const clipRect = document.getElementById('traj-clip-rect');
    if (clipRect && chartPoints.length) {
      // Interpolate x position
      const px = chartPoints[Math.min(i, max)].x + (chartPoints[Math.min(i + 1, max)].x - chartPoints[Math.min(i, max)].x) * frac;
      clipRect.setAttribute('width', px + 2);
    }

    // Move the dot to current position
    const tmDot = document.getElementById('traj-tm-dot');
    if (tmDot && chartPoints.length) {
      const px = chartPoints[Math.min(i, max)].x + (chartPoints[Math.min(i + 1, max)].x - chartPoints[Math.min(i, max)].x) * frac;
      const py = chartPoints[Math.min(i, max)].y + (chartPoints[Math.min(i + 1, max)].y - chartPoints[Math.min(i, max)].y) * frac;
      tmDot.setAttribute('cx', px.toFixed(1));
      tmDot.setAttribute('cy', py.toFixed(1));
      tmDot.setAttribute('stroke', hex);
    }

    // Drive compass + charge + date
    Compass.setDegree(deg, tier);
    Charge.setLevel(tier, 0, 0);
    setCompassDate(isYTD ? `${nearest.year} YTD` : String(nearest.year));
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

    const playBtn = document.getElementById('tm-play');
    const playIcon = document.getElementById('tm-play-icon');
    const revBtn = document.getElementById('tm-rev');
    const fwdBtn = document.getElementById('tm-fwd');
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

    ['tm-play', 'tm-rev', 'tm-fwd'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.remove('active');
    });
    const playIcon = document.getElementById('tm-play-icon');
    if (playIcon) playIcon.innerHTML = '<path fill="currentColor" d="M8 5v14l11-7z"/>';

  }

  function initTimeMachineControls(container) {
    const max = chartData.length - 1;
    const last = chartData[max];

    const tmArea = container.querySelector('.tm-controls');
    if (!tmArea) return;

    tmArea.innerHTML = `
      <div class="calc-slider-wrap">
        <input type="range" class="calc-slider" id="tm-slider" min="0" max="${max}" value="${max}" step="1" aria-label="Time machine year slider" aria-valuetext="${last.year}">
      </div>
      <div class="calc-slider-info">
        <span class="calc-decade" id="tm-year">${last.year}</span>
        <span class="calc-score" id="tm-score" style="color:${COLOR_HEX[last.charge_level] || '#888'}">${degreeToScore(last.compass_degree)}</span>
        <span class="calc-tier" id="tm-tier">${CHARGE_LABELS[last.charge_level]}</span>
        <span class="calc-song-count" id="tm-count">${last.chart_song_count} charting songs</span>
      </div>
      <div class="calc-playback">
        <button class="calc-play-btn" id="tm-rev" title="Play backward" aria-label="Play backward">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M11 18V6l-8.5 6 8.5 6zm.5-6l8.5 6V6l-8.5 6z"/></svg>
        </button>
        <button class="calc-play-btn" id="tm-play" title="Play forward" aria-label="Play forward">
          <svg viewBox="0 0 24 24" width="14" height="14" id="tm-play-icon" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
        </button>
        <button class="calc-play-btn" id="tm-fwd" title="Play forward fast" aria-label="Play forward fast">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M4 18l8.5-6L4 6v12zm9-12v12l8.5-6L13 6z"/></svg>
        </button>
        <div class="calc-progress"><div class="calc-progress-fill" id="tm-progress" style="width:100%"></div></div>
        <button class="calc-speed-btn" id="tm-speed" aria-label="Playback speed">1x</button>
        <button class="calc-reset" id="tm-reset" aria-label="Reset time machine" style="display:none;">Reset</button>
      </div>
    `;

    // Wire up controls
    const slider = document.getElementById('tm-slider');
    document.getElementById('tm-play').addEventListener('click', () => {
      if (tmPlaying) { tmStopPlayback(); return; }
      tmStartPlayback(1);
    });
    document.getElementById('tm-rev').addEventListener('click', () => {
      if (tmPlaying && tmDirection === -1) { tmStopPlayback(); return; }
      tmStopPlayback();
      tmStartPlayback(-1);
    });
    document.getElementById('tm-fwd').addEventListener('click', () => {
      if (tmPlaying && tmDirection === 1) { tmStopPlayback(); return; }
      tmStopPlayback();
      tmStartPlayback(1);
    });
    document.getElementById('tm-speed').addEventListener('click', () => {
      tmSpeedIdx = (tmSpeedIdx + 1) % TM_SPEEDS.length;
      document.getElementById('tm-speed').textContent = TM_SPEEDS[tmSpeedIdx] + 'x';
    });

    // Slider scrub
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

    // Reset
    document.getElementById('tm-reset').addEventListener('click', () => {
      tmStopPlayback();
      restoreCompassState();
      tmPosition = max;
      updateTimeMachine(tmPosition);
      document.getElementById('tm-reset').style.display = 'none';
    });

  }

  function applyZoom(container) {
    const lastYear = allYearData[allYearData.length - 1].year;
    let filtered;
    switch (currentZoom) {
      case '10': filtered = allYearData.filter(d => d.year > lastYear - 10); break;
      case '20': filtered = allYearData.filter(d => d.year > lastYear - 20); break;
      case '30': filtered = allYearData.filter(d => d.year > lastYear - 30); break;
      default: filtered = allYearData;
    }
    tmStopPlayback();
    renderTrajectoryChart(filtered, container);
    initTimeMachineControls(container);

    container.querySelectorAll('.traj-zoom-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.zoom === currentZoom);
    });
  }

  async function loadTrajectory() {
    const container = document.getElementById('trajectory-container');
    if (!container) return;

    try {
      const [decadeData, yearData] = await Promise.all([API.getDrift(), API.getDriftYears()]);

      if (!yearData.length) {
        container.innerHTML = '<p style="color:var(--rc-text-dim);font-size:0.8rem;">No historical data</p>';
        return;
      }

      allYearData = yearData;

      container.innerHTML = `
        <div class="traj-zoom-bar">
          <button class="traj-zoom-btn active" data-zoom="all">All</button>
          <button class="traj-zoom-btn" data-zoom="30">30Y</button>
          <button class="traj-zoom-btn" data-zoom="20">20Y</button>
          <button class="traj-zoom-btn" data-zoom="10">10Y</button>
        </div>
        <div class="traj-chart-area"></div>
        <div class="tm-controls"></div>
      `;

      container.querySelectorAll('.traj-zoom-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          currentZoom = btn.dataset.zoom;
          applyZoom(container);
        });
      });

      applyZoom(container);
      renderCalculator(decadeData);
    } catch (err) {
      container.innerHTML = '<p style="color:var(--rc-text-dim);font-size:0.8rem;">Could not load trajectory</p>';
    }
  }

  // --- Compass State Save/Restore ---
  let savedDegree = null;
  let savedCharge = null;
  let savedDateText = null;

  function saveCompassState() {
    if (savedDegree !== null) return; // already saved
    const scoreText = document.getElementById('compass-score')?.textContent;
    const chargeText = document.getElementById('compass-charge-text')?.textContent;
    const dateEl = document.getElementById('compass-date-svg');
    if (scoreText && chargeText) {
      for (const [color, label] of Object.entries(CHARGE_LABELS)) {
        if (label.toUpperCase() === chargeText) { savedCharge = color; break; }
      }
      savedDegree = 90 - (parseInt(scoreText) * 90 / 100);
    }
    if (dateEl) savedDateText = dateEl.textContent;
  }

  function restoreCompassState() {
    if (savedDegree !== null) {
      Compass.setDegree(savedDegree, savedCharge);
      Charge.setLevel(savedCharge, 0, 0);
    }
    if (savedDateText) {
      const dateEl = document.getElementById('compass-date-svg');
      if (dateEl) dateEl.textContent = savedDateText;
    }
    savedDegree = null;
    savedCharge = null;
    savedDateText = null;
  }

  function setCompassDate(text) {
    const dateEl = document.getElementById('compass-date-svg');
    if (dateEl) dateEl.textContent = text;
  }

  // --- Era Panel Tabs ---
  let dailyChartLoaded = false;
  let dailyChartData = [];
  let dailyChartPoints = [];
  let dailyCurrentZoom = 'year';
  let dtmPlaying = false;
  let dtmAnimFrame = null;
  let dtmPosition = 0;
  let dtmDirection = 1;
  let dtmSpeedIdx = 1;

  function initEraTabs() {
    document.querySelectorAll('.era-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        // Lock trajectory-panel height to prevent layout collapse during tab swap
        const eraPanel = document.getElementById('trajectory-panel');
        eraPanel.style.minHeight = eraPanel.offsetHeight + 'px';

        document.querySelectorAll('.era-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.era-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        const target = tab.dataset.era;
        document.getElementById('era-' + target)?.classList.add('active');

        const tabTitle = tab.querySelector('.era-tab-title');
        if (tabTitle) announce(`Switched to ${tabTitle.textContent}.`);

        if (target === 'daily' && !dailyChartLoaded) {
          loadDailyChart().then(() => { eraPanel.style.minHeight = ''; });
        } else {
          // Content already rendered — release after layout settles
          requestAnimationFrame(() => { eraPanel.style.minHeight = ''; });
        }
      });
    });
  }

  async function loadDailyChart() {
    const container = document.getElementById('daily-chart-container');
    if (!container) return;

    container.innerHTML = '<div class="loading" role="status">Loading daily chart...</div>';

    try {
      const data = await API.getDailyChart();
      dailyChartLoaded = true;

      if (!data.length) {
        container.innerHTML = '<div class="daily-empty">No daily readings yet.</div>';
        return;
      }

      dailyChartData = data;

      container.innerHTML = `
        <div class="traj-zoom-bar">
          <button class="traj-zoom-btn active" data-zoom="year">Year</button>
          <button class="traj-zoom-btn" data-zoom="q">Q</button>
          <button class="traj-zoom-btn" data-zoom="m">M</button>
          <button class="traj-zoom-btn" data-zoom="w">W</button>
        </div>
        <div class="traj-chart-area"></div>
        <div class="tm-controls"></div>
      `;

      container.querySelectorAll('.traj-zoom-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          dailyCurrentZoom = btn.dataset.zoom;
          applyDailyZoom(container);
        });
      });

      applyDailyZoom(container);
    } catch (err) {
      container.innerHTML = '<p style="color:var(--rc-text-dim);font-size:0.8rem;">Could not load daily chart</p>';
    }
  }

  function applyDailyZoom(container) {
    const now = new Date();
    let filtered;
    switch (dailyCurrentZoom) {
      case 'w': {
        const cutoff = new Date(now);
        cutoff.setDate(cutoff.getDate() - 7);
        filtered = dailyChartData.filter(d => new Date(d.date) >= cutoff);
        break;
      }
      case 'm': {
        const cutoff = new Date(now);
        cutoff.setDate(cutoff.getDate() - 30);
        filtered = dailyChartData.filter(d => new Date(d.date) >= cutoff);
        break;
      }
      case 'q': {
        const cutoff = new Date(now);
        cutoff.setDate(cutoff.getDate() - 90);
        filtered = dailyChartData.filter(d => new Date(d.date) >= cutoff);
        break;
      }
      default:
        filtered = dailyChartData;
    }

    if (!filtered.length) filtered = dailyChartData;

    dtmStopPlayback();
    renderDailyChart(filtered, container);
    initDailyTimeMachineControls(container);

    container.querySelectorAll('.traj-zoom-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.zoom === dailyCurrentZoom);
    });
  }

  function renderDailyChart(data, container) {
    if (!data.length) return;

    const W = 320, H = 120;
    const padL = 30, padR = 10, padT = 10, padB = 22;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const maxIdx = data.length - 1;

    dailyChartPoints = data.map((d, i) => ({
      x: padL + (maxIdx > 0 ? (i / maxIdx) * chartW : chartW / 2),
      y: padT + (d.compass_degree / 180) * chartH,
      degree: d.compass_degree,
      date: d.date,
      color: d.charge_level,
    }));

    const linePath = dailyChartPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const areaPath = linePath + ` L ${dailyChartPoints[maxIdx].x.toFixed(1)} ${padT + chartH} L ${dailyChartPoints[0].x.toFixed(1)} ${padT + chartH} Z`;

    let svg = `<svg class="trajectory-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Charge trajectory chart">`;
    svg += `<defs>
      <linearGradient id="daily-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" />
        <stop offset="25%" stop-color="${COLOR_HEX.blue}" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" />
        <stop offset="75%" stop-color="${COLOR_HEX.orange}" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" />
      </linearGradient>
      <linearGradient id="daily-area-grad" gradientUnits="userSpaceOnUse" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}">
        <stop offset="0%" stop-color="${COLOR_HEX.violet}" stop-opacity="0.2" />
        <stop offset="50%" stop-color="${COLOR_HEX.green}" stop-opacity="0.05" />
        <stop offset="100%" stop-color="${COLOR_HEX.red}" stop-opacity="0.2" />
      </linearGradient>
      <clipPath id="daily-clip"><rect id="daily-clip-rect" x="0" y="0" width="${W}" height="${H}" /></clipPath>
    </defs>`;

    // Grid lines
    [{ deg: 0, label: '+100' }, { deg: 45, label: '' }, { deg: 90, label: '0' }, { deg: 135, label: '' }, { deg: 180, label: '-100' }].forEach(({ deg, label }) => {
      const y = padT + (deg / 180) * chartH;
      svg += `<line class="trajectory-grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" />`;
      if (label) svg += `<text class="trajectory-y-label" x="${padL - 4}" y="${y + 3}">${label}</text>`;
    });

    // Clipped line + area
    svg += `<g clip-path="url(#daily-clip)">`;
    svg += `<path class="trajectory-area" d="${areaPath}" fill="url(#daily-area-grad)" />`;
    svg += `<path class="trajectory-line" d="${linePath}" stroke="url(#daily-grad)" />`;
    svg += `</g>`;

    // X-axis labels — month abbreviations or day numbers depending on data span
    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const daySpan = maxIdx > 0 ? (new Date(data[maxIdx].date) - new Date(data[0].date)) / 86400000 : 0;

    if (daySpan > 60) {
      // Show month labels
      const shownMonths = new Set();
      dailyChartPoints.forEach((p, i) => {
        const d = new Date(data[i].date + 'T00:00:00');
        const m = d.getMonth();
        if (!shownMonths.has(m)) {
          shownMonths.add(m);
          svg += `<text class="trajectory-label" x="${p.x.toFixed(1)}" y="${H - 4}" text-anchor="middle">${MONTHS[m]}</text>`;
        }
      });
    } else {
      // Show select day labels
      const labelInterval = Math.max(1, Math.ceil(data.length / 8));
      dailyChartPoints.forEach((p, i) => {
        if (i % labelInterval === 0 || i === maxIdx) {
          const d = new Date(data[i].date + 'T00:00:00');
          const anchor = i === 0 ? 'start' : i === maxIdx ? 'end' : 'middle';
          svg += `<text class="trajectory-label" x="${p.x.toFixed(1)}" y="${H - 4}" text-anchor="${anchor}">${String(d.getMonth()+1).padStart(2,'0')}/${String(d.getDate()).padStart(2,'0')}/${String(d.getFullYear()).slice(2)}</text>`;
        }
      });
    }

    // Moving dot
    const lastPt = dailyChartPoints[maxIdx];
    svg += `<circle id="daily-tm-dot" class="trajectory-dot" cx="${lastPt.x.toFixed(1)}" cy="${lastPt.y.toFixed(1)}" fill="var(--rc-bg-dark)" stroke="${COLOR_HEX[lastPt.color] || '#888'}" />`;

    // Hover elements
    svg += `<line id="daily-hover-line" x1="0" y1="${padT}" x2="0" y2="${padT + chartH}" class="traj-hover-line" style="display:none" />`;
    svg += `<circle id="daily-hover-dot" cx="0" cy="0" class="traj-hover-dot" style="display:none" />`;
    svg += `<rect x="${padL}" y="${padT}" width="${chartW}" height="${chartH}" fill="transparent" class="traj-hover-area" />`;
    svg += '</svg>';

    const chartEl = container.querySelector('.traj-chart-area');
    chartEl.innerHTML = `<div class="traj-wrap">${svg}<div class="traj-tooltip" id="daily-tooltip"></div></div>`;

    // Hover interaction
    const wrap = chartEl.querySelector('.traj-wrap');
    const svgEl = chartEl.querySelector('.trajectory-svg');

    wrap.addEventListener('touchstart', () => {
      const t = document.getElementById('daily-tooltip');
      if (t) t.style.display = 'none';
    }, { passive: true });

    wrap.addEventListener('mousemove', (e) => {
      if (dtmPlaying) return;
      const hoverLine = document.getElementById('daily-hover-line');
      const hoverDot = document.getElementById('daily-hover-dot');
      const tooltip = document.getElementById('daily-tooltip');
      if (!hoverLine) return;

      const rect = svgEl.getBoundingClientRect();
      const relX = (e.clientX - rect.left) / rect.width;
      const svgX = relX * W;

      let nearest = 0, minDist = Infinity;
      for (let i = 0; i <= maxIdx; i++) {
        const dist = Math.abs(dailyChartPoints[i].x - svgX);
        if (dist < minDist) { minDist = dist; nearest = i; }
      }

      const p = dailyChartPoints[nearest];
      const d = data[nearest];
      const hex = COLOR_HEX[p.color] || '#888';

      hoverLine.setAttribute('x1', p.x.toFixed(1));
      hoverLine.setAttribute('x2', p.x.toFixed(1));
      hoverLine.style.display = '';
      hoverDot.setAttribute('cx', p.x.toFixed(1));
      hoverDot.setAttribute('cy', p.y.toFixed(1));
      hoverDot.setAttribute('stroke', hex);
      hoverDot.style.display = '';

      const fdate = formatDate(d.date);
      const wrapRect = wrap.getBoundingClientRect();
      const pixelX = e.clientX - wrapRect.left;
      const wrapW = wrapRect.width;
      tooltip.style.left = pixelX + 'px';
      tooltip.style.transform = pixelX > wrapW * 0.7 ? 'translateX(-100%)' : pixelX < wrapW * 0.3 ? 'translateX(0)' : 'translateX(-50%)';
      tooltip.innerHTML = `<strong>${fdate}</strong><br><span style="color:${hex}">${degreeToScore(p.degree)}</span> ${CHARGE_LABELS[p.color]}`;
      tooltip.style.display = 'block';
    });

    wrap.addEventListener('mouseleave', () => {
      const hoverLine = document.getElementById('daily-hover-line');
      const hoverDot = document.getElementById('daily-hover-dot');
      const tooltip = document.getElementById('daily-tooltip');
      if (hoverLine) hoverLine.style.display = 'none';
      if (hoverDot) hoverDot.style.display = 'none';
      if (tooltip) tooltip.style.display = 'none';
    });

    // Click: move compass + needle + charge bar, load full reading
    wrap.addEventListener('click', (e) => {
      const rect = svgEl.getBoundingClientRect();
      const relX = (e.clientX - rect.left) / rect.width;
      const svgX = relX * W;

      let nearest = 0, minDist = Infinity;
      for (let i = 0; i <= maxIdx; i++) {
        const dist = Math.abs(dailyChartPoints[i].x - svgX);
        if (dist < minDist) { minDist = dist; nearest = i; }
      }

      const p = dailyChartPoints[nearest];
      const d = data[nearest];
      setCompassDate(formatDate(d.date));
      Compass.setDegree(p.degree, p.color);
      Charge.setLevel(p.color, 0, 0);
      viewArchiveReading(d.date);

      const dot = document.getElementById('daily-hover-dot');
      if (dot) { dot.classList.add('click-pulse'); setTimeout(() => dot.classList.remove('click-pulse'), 100); }
      pinchLine(dailyChartPoints, nearest, svgEl.querySelector('.trajectory-line'), svgEl.querySelector('.trajectory-area'), padT, chartH, false);
    });

    dtmPosition = maxIdx;
  }

  // --- Daily Time Machine ---
  function updateDailyTimeMachine(pos) {
    const pts = dailyChartPoints;
    const ptMax = pts.length - 1;
    if (ptMax < 0) return;

    const i = Math.floor(Math.min(pos, ptMax));
    const frac = pos - i;
    const a = pts[Math.min(i, ptMax)];
    const b = pts[Math.min(i + 1, ptMax)];

    const deg = a.degree + (b.degree - a.degree) * frac;
    const tier = degreeToTier(deg);
    const hex = COLOR_HEX[tier] || '#888';
    const nearestPt = pts[Math.round(Math.min(pos, ptMax))];

    const dateEl = document.getElementById('dtm-date');
    const scoreEl = document.getElementById('dtm-score');
    const tierEl = document.getElementById('dtm-tier');
    const slider = document.getElementById('dtm-slider');
    const progressFill = document.getElementById('dtm-progress');
    const resetBtn = document.getElementById('dtm-reset');

    if (dateEl) dateEl.textContent = formatDate(nearestPt.date);
    if (scoreEl) { scoreEl.textContent = degreeToScore(deg); scoreEl.style.color = hex; }
    if (tierEl) tierEl.textContent = CHARGE_LABELS[tier];
    if (progressFill) progressFill.style.width = (pos / ptMax * 100) + '%';
    if (slider) {
      slider.value = Math.round(pos);
      slider.setAttribute('aria-valuetext', `${formatDate(nearestPt.date)}, ${CHARGE_LABELS[tier]}`);
    }
    if (resetBtn) resetBtn.style.display = '';

    // Clip chart
    const clipRect = document.getElementById('daily-clip-rect');
    if (clipRect && pts.length) {
      const px = a.x + (b.x - a.x) * frac;
      clipRect.setAttribute('width', px + 2);
    }

    // Move dot
    const tmDot = document.getElementById('daily-tm-dot');
    if (tmDot && pts.length) {
      const px = a.x + (b.x - a.x) * frac;
      const py = a.y + (b.y - a.y) * frac;
      tmDot.setAttribute('cx', px.toFixed(1));
      tmDot.setAttribute('cy', py.toFixed(1));
      tmDot.setAttribute('stroke', hex);
    }

    // Drive compass
    Compass.setDegree(deg, tier);
    Charge.setLevel(tier, 0, 0);
    setCompassDate(formatDate(nearestPt.date));
  }

  function dtmAnimate(timestamp) {
    if (!dtmPlaying) return;
    if (!dtmAnimate.lastTime) dtmAnimate.lastTime = timestamp;

    const dt = (timestamp - dtmAnimate.lastTime) / 1000;
    dtmAnimate.lastTime = timestamp;
    const max = dailyChartPoints.length - 1;

    dtmPosition += TM_BASE_SPEED * TM_SPEEDS[dtmSpeedIdx] * dtmDirection * dt;

    if (dtmDirection === 1 && dtmPosition >= max) { dtmPosition = max; dtmStopPlayback(); }
    if (dtmDirection === -1 && dtmPosition <= 0) { dtmPosition = 0; dtmStopPlayback(); }

    updateDailyTimeMachine(dtmPosition);
    if (dtmPlaying) dtmAnimFrame = requestAnimationFrame(dtmAnimate);
  }

  function dtmStartPlayback(dir) {
    saveCompassState();
    dtmDirection = dir;
    const max = dailyChartPoints.length - 1;
    if (dir === 1 && dtmPosition >= max) dtmPosition = 0;
    if (dir === -1 && dtmPosition <= 0) dtmPosition = max;

    dtmPlaying = true;
    dtmAnimate.lastTime = null;

    const needle = document.getElementById('compass-needle');
    if (needle) needle.classList.add('no-transition');

    const playBtn = document.getElementById('dtm-play');
    const playIcon = document.getElementById('dtm-play-icon');
    const revBtn = document.getElementById('dtm-rev');
    const fwdBtn = document.getElementById('dtm-fwd');
    if (playBtn) playBtn.classList.add('active');
    if (dir === 1 && fwdBtn) fwdBtn.classList.add('active');
    if (dir === -1 && revBtn) revBtn.classList.add('active');
    if (playIcon) playIcon.innerHTML = '<rect fill="currentColor" x="6" y="4" width="4" height="16"/><rect fill="currentColor" x="14" y="4" width="4" height="16"/>';

    dtmAnimFrame = requestAnimationFrame(dtmAnimate);
  }

  function dtmStopPlayback() {
    dtmPlaying = false;
    if (dtmAnimFrame) cancelAnimationFrame(dtmAnimFrame);

    const needle = document.getElementById('compass-needle');
    if (needle) needle.classList.remove('no-transition');

    ['dtm-play', 'dtm-rev', 'dtm-fwd'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.remove('active');
    });
    const playIcon = document.getElementById('dtm-play-icon');
    if (playIcon) playIcon.innerHTML = '<path fill="currentColor" d="M8 5v14l11-7z"/>';
  }

  function initDailyTimeMachineControls(container) {
    const pts = dailyChartPoints;
    const max = pts.length - 1;
    if (max < 0) return;
    const last = pts[max];

    const tmArea = container.querySelector('.tm-controls');
    if (!tmArea) return;

    tmArea.innerHTML = `
      <div class="calc-slider-wrap">
        <input type="range" class="calc-slider" id="dtm-slider" min="0" max="${max}" value="${max}" step="1" aria-label="Daily time machine slider" aria-valuetext="${formatDate(last.date)}">
      </div>
      <div class="calc-slider-info">
        <span class="calc-decade" id="dtm-date">${formatDate(last.date)}</span>
        <span class="calc-score" id="dtm-score" style="color:${COLOR_HEX[last.color] || '#888'}">${degreeToScore(last.degree)}</span>
        <span class="calc-tier" id="dtm-tier">${CHARGE_LABELS[last.color]}</span>
      </div>
      <div class="calc-playback">
        <button class="calc-play-btn" id="dtm-rev" title="Play backward" aria-label="Play backward">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M11 18V6l-8.5 6 8.5 6zm.5-6l8.5 6V6l-8.5 6z"/></svg>
        </button>
        <button class="calc-play-btn" id="dtm-play" title="Play forward" aria-label="Play forward">
          <svg viewBox="0 0 24 24" width="14" height="14" id="dtm-play-icon" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
        </button>
        <button class="calc-play-btn" id="dtm-fwd" title="Play forward fast" aria-label="Play forward fast">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M4 18l8.5-6L4 6v12zm9-12v12l8.5-6L13 6z"/></svg>
        </button>
        <div class="calc-progress"><div class="calc-progress-fill" id="dtm-progress" style="width:100%"></div></div>
        <button class="calc-speed-btn" id="dtm-speed" aria-label="Playback speed">1x</button>
        <button class="calc-reset" id="dtm-reset" aria-label="Reset time machine" style="display:none;">Reset</button>
      </div>
    `;

    const slider = document.getElementById('dtm-slider');
    document.getElementById('dtm-play').addEventListener('click', () => {
      if (dtmPlaying) { dtmStopPlayback(); return; }
      dtmStartPlayback(1);
    });
    document.getElementById('dtm-rev').addEventListener('click', () => {
      if (dtmPlaying && dtmDirection === -1) { dtmStopPlayback(); return; }
      dtmStopPlayback();
      dtmStartPlayback(-1);
    });
    document.getElementById('dtm-fwd').addEventListener('click', () => {
      if (dtmPlaying && dtmDirection === 1) { dtmStopPlayback(); return; }
      dtmStopPlayback();
      dtmStartPlayback(1);
    });
    document.getElementById('dtm-speed').addEventListener('click', () => {
      dtmSpeedIdx = (dtmSpeedIdx + 1) % TM_SPEEDS.length;
      document.getElementById('dtm-speed').textContent = TM_SPEEDS[dtmSpeedIdx] + 'x';
    });

    // Slider scrub
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
      if (needle && !dtmPlaying) needle.classList.remove('no-transition');
    };
    slider.addEventListener('mouseup', endScrub);
    slider.addEventListener('touchend', endScrub);
    slider.addEventListener('input', () => {
      saveCompassState();
      dtmStopPlayback();
      dtmPosition = parseInt(slider.value);
      updateDailyTimeMachine(dtmPosition);
    });

    // Reset
    document.getElementById('dtm-reset').addEventListener('click', () => {
      dtmStopPlayback();
      restoreCompassState();
      dtmPosition = max;
      updateDailyTimeMachine(dtmPosition);
      document.getElementById('dtm-reset').style.display = 'none';
    });
  }

  function renderCalculator(driftData) {
    const container = document.getElementById('era-calculator');
    if (!container || !driftData || !driftData.length) return;

    const currentDecade = driftData[driftData.length - 1];

    container.innerHTML = `
      <div class="era-calc">
        <div class="calc-header-row">
          <div class="calc-tabs">
            <button class="calc-tab active" data-calc="mix">What If</button>
            <button class="calc-tab calc-tab-locked" data-calc="reverse" disabled>Reverse <span class="calc-tab-soon">Soon</span></button>
            <button class="calc-tab calc-tab-locked" data-calc="playlist" disabled>Playlist <span class="calc-tab-soon">Soon</span></button>
          </div>
          <button class="calc-close-btn" id="calc-close" title="Close calculator" aria-label="Close calculator">
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
          </button>
        </div>
        <div class="calc-panel active" id="calc-mix"></div>
        <div class="calc-panel" id="calc-reverse"></div>
        <div class="calc-panel" id="calc-playlist"></div>
      </div>
    `;

    // Close button
    document.getElementById('calc-close').addEventListener('click', () => {
      const overlay = document.getElementById('calc-overlay');
      const toggleBtn = document.getElementById('calc-overlay-toggle');
      if (overlay) overlay.classList.remove('open');
      if (toggleBtn) {
        toggleBtn.classList.remove('active');
        toggleBtn.querySelector('span').textContent = 'Calculator';
      }
    });

    // Tab switching
    container.querySelectorAll('.calc-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        container.querySelectorAll('.calc-tab').forEach(t => t.classList.remove('active'));
        container.querySelectorAll('.calc-panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('calc-' + tab.dataset.calc).classList.add('active');
      });
    });

    renderMixCalc();
    renderReverseCalc(currentDecade);
    renderPlaylistCalc();
  }

  function renderMixCalc() {
    const panel = document.getElementById('calc-mix');
    if (!panel) return;

    const COLOR_DEGREES = { violet: 0, blue: 45, green: 90, orange: 135, red: 180 };
    const tiers = [
      { key: 'violet', label: 'Ascended', initial: 0 },
      { key: 'blue', label: 'Elevated', initial: 0 },
      { key: 'green', label: 'Decent', initial: 0 },
      { key: 'orange', label: 'Degraded', initial: 0 },
      { key: 'red', label: 'Corrupted', initial: 0 },
    ];

    let rows = '';
    tiers.forEach(t => {
      const hex = COLOR_HEX[t.key];
      rows += `
        <div class="wi-row">
          <span class="wi-dot" style="background:${hex}"></span>
          <span class="wi-label">${t.label}</span>
          <div class="wi-stepper">
            <button class="wi-btn" data-tier="${t.key}" data-dir="-1">-</button>
            <span class="wi-count" id="wi-${t.key}">${t.initial}</span>
            <button class="wi-btn" data-tier="${t.key}" data-dir="1">+</button>
          </div>
        </div>
      `;
    });

    panel.innerHTML = `
      <p class="calc-context">Build a hypothetical top 10. How does the day's charge shift?</p>
      <div class="wi-grid">${rows}</div>
      <div class="wi-total" id="wi-total">10 of 10 songs</div>
      <div class="calc-mix-result" id="mix-result"></div>
    `;

    const counts = {};
    tiers.forEach(t => { counts[t.key] = t.initial; });

    function getTotal() {
      return Object.values(counts).reduce((a, b) => a + b, 0);
    }

    function compute() {
      const total = getTotal();
      const totalEl = document.getElementById('wi-total');
      totalEl.textContent = `${total} of 10 songs`;
      totalEl.classList.toggle('wi-total-warn', total !== 10);

      const result = document.getElementById('mix-result');

      if (total !== 10) {
        result.innerHTML = `<span class="calc-tier" style="color:var(--rc-text-dim)">${total < 10 ? `Add ${10 - total} more` : `Remove ${total - 10}`} to see result</span>`;
        return;
      }

      // Build position-weighted average: best tiers fill top positions
      let pos = 1;
      let totalWeight = 0, weightedSum = 0;
      tiers.forEach(t => {
        const deg = COLOR_DEGREES[t.key];
        for (let i = 0; i < counts[t.key]; i++) {
          const w = 11 - pos; // pos 1 = weight 10
          totalWeight += w;
          weightedSum += w * deg;
          pos++;
        }
      });

      const avgDeg = weightedSum / totalWeight;
      const score = Math.round((90 - avgDeg) * 100 / 90);
      const tier = degreeToTier(avgDeg);
      const hex = COLOR_HEX[tier] || '#888';

      result.innerHTML = `
        <span class="calc-score" style="color:${hex}">${score > 0 ? '+' : ''}${score}</span>
        <span class="calc-tier" style="color:${hex}">${CHARGE_LABELS[tier]}</span>
      `;
    }

    // Stepper button clicks
    panel.querySelectorAll('.wi-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const tier = btn.dataset.tier;
        const dir = parseInt(btn.dataset.dir);
        const newVal = counts[tier] + dir;
        if (newVal < 0 || newVal > 10) return;
        counts[tier] = newVal;
        document.getElementById('wi-' + tier).textContent = newVal;
        compute();
      });
    });

    compute();
  }

  function renderReverseCalc(currentDecade) {
    const panel = document.getElementById('calc-reverse');
    if (!panel) return;

    const songCount = currentDecade.chart_song_count;
    const curDeg = currentDecade.compass_degree;

    panel.innerHTML = `
      <p class="calc-context">The ${currentDecade.decade} sits at <strong>${degreeToScore(curDeg)}</strong> (${CHARGE_LABELS[currentDecade.charge_level]}) across ${songCount} charting songs.</p>
      <div class="calc-mix-row">
        <div class="calc-mix-group">
          <label>Target</label>
          <select class="era-calc-select" id="rev-target">
            <option value="0">Ascended (+100)</option>
            <option value="45">Elevated (+50)</option>
            <option value="90" selected>Decent (0)</option>
            <option value="135">Degraded (-50)</option>
            <option value="180">Corrupted (-100)</option>
          </select>
        </div>
        <div class="calc-mix-group">
          <label>If every top 10 was</label>
          <select class="era-calc-select" id="rev-charge">
            <option value="0">Ascended (+100)</option>
            <option value="45" selected>Elevated (+50)</option>
            <option value="90">Decent (0)</option>
            <option value="135">Degraded (-50)</option>
            <option value="180">Corrupted (-100)</option>
          </select>
        </div>
      </div>
      <div class="era-calc-result" id="rev-result"></div>
    `;

    const targetSel = document.getElementById('rev-target');
    const chargeSel = document.getElementById('rev-charge');
    const resultEl = document.getElementById('rev-result');

    const compute = () => {
      const targetDeg = parseFloat(targetSel.value);
      const newDeg = parseFloat(chargeSel.value);
      const targetScore = Math.round((90 - targetDeg) * 100 / 90);
      const chargeScore = Math.round((90 - newDeg) * 100 / 90);

      // Already at target?
      if (Math.abs(curDeg - targetDeg) < 1) {
        resultEl.innerHTML = `The era is already at ${targetScore > 0 ? '+' + targetScore : targetScore}.`;
        return;
      }

      // Charge doesn't help
      if (Math.abs(newDeg - targetDeg) < 0.1) {
        resultEl.innerHTML = `${chargeScore > 0 ? '+' + chargeScore : chargeScore} songs hold the line — they don't move toward the target.`;
        return;
      }

      if ((curDeg > targetDeg && newDeg >= curDeg) || (curDeg < targetDeg && newDeg <= curDeg)) {
        resultEl.innerHTML = `That charge pushes the era further from the target.`;
        return;
      }

      // days = N * |curDeg - targetDeg| / (10 * |newDeg - targetDeg| ... corrected)
      // Each day adds 10 songs. Need total songs S such that:
      // (songCount * curDeg + S * newDeg) / (songCount + S) = targetDeg
      // S = songCount * (curDeg - targetDeg) / (targetDeg - newDeg)
      const songsNeeded = songCount * Math.abs(curDeg - targetDeg) / Math.abs(targetDeg - newDeg);
      const days = Math.ceil(songsNeeded / 10);
      const months = (days / 30).toFixed(1);
      const years = (days / 365).toFixed(1);

      let timeStr = `<strong>${days} days</strong>`;
      if (days > 60) timeStr += ` (${months} months)`;
      if (days > 365) timeStr = `<strong>${years} years</strong> (${days} days)`;

      resultEl.innerHTML = `${timeStr} of all-${chargeScore > 0 ? '+' + chargeScore : chargeScore} top 10s to bring the era to ${targetScore > 0 ? '+' + targetScore : targetScore}.`;
    };

    targetSel.addEventListener('change', compute);
    chargeSel.addEventListener('change', compute);
    compute();
  }

  function renderPlaylistCalc() {
    const panel = document.getElementById('calc-playlist');
    if (!panel) return;

    panel.innerHTML = `
      <p class="calc-context">Paste your playlist and see its consciousness charge.</p>
      <label for="playlist-input" class="sr-only">Paste songs, one per line</label>
      <textarea class="calc-textarea" id="playlist-input" placeholder="Paste songs, one per line&#10;&#10;e.g.&#10;Bohemian Rhapsody - Queen&#10;Blinding Lights - The Weeknd&#10;HUMBLE. - Kendrick Lamar" rows="6"></textarea>
      <div class="calc-playlist-footer">
        <span class="calc-line-count" id="playlist-count">0 songs</span>
        <button class="calc-btn calc-btn-disabled" id="playlist-analyze">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 2a4 4 0 0 1 4 4c0 1.5-.8 2.8-2 3.5V11h3l4 8H3l4-8h3V9.5A4 4 0 0 1 12 2z"/></svg>
          Analyze
        </button>
      </div>
      <div class="calc-playlist-result" id="playlist-result"></div>
    `;

    const textarea = document.getElementById('playlist-input');
    const countEl = document.getElementById('playlist-count');
    const analyzeBtn = document.getElementById('playlist-analyze');

    textarea.addEventListener('input', () => {
      const lines = textarea.value.split('\n').filter(l => l.trim().length > 0);
      const n = lines.length;
      countEl.textContent = n === 1 ? '1 song' : `${n} songs`;
    });

    analyzeBtn.addEventListener('click', () => {
      const lines = textarea.value.split('\n').filter(l => l.trim().length > 0);
      const resultEl = document.getElementById('playlist-result');
      if (lines.length === 0) {
        resultEl.innerHTML = '<span style="color:var(--rc-text-dim)">Paste some songs first.</span>';
        return;
      }
      resultEl.innerHTML = '<span style="color:var(--rc-accent)">AI-powered playlist analysis is coming soon. Stay tuned.</span>';
    });
  }

  // --- Calendar Picker (drill-up/drill-down) ---
  // Views: 'day' | 'month' | 'year' | 'decade'
  let calView = 'day';
  let calYear = null;
  let calMonth = null;  // 0-indexed
  let calDecadeStart = null;
  let calSelectedDate = null;
  const CAL_MONTHS_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const CAL_MONTHS_FULL = ['January','February','March','April','May','June','July','August','September','October','November','December'];

  function calendarToggleBtnHtml() {
    return `<button class="cal-toggle-btn" title="Open calendar picker" aria-label="Open calendar picker">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
      </svg>
    </button>`;
  }

  function initCalendarPicker() {
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.cal-picker') && !e.target.closest('.cal-toggle-btn')) {
        const cal = document.getElementById('cal-picker');
        if (cal) cal.remove();
      }
    });
  }

  function openCalendar(anchorBtn) {
    const oldCal = document.getElementById('cal-picker');
    if (oldCal) oldCal.remove();

    const group = anchorBtn.closest('.reading-charge-group');
    if (!group) return;

    const cal = document.createElement('div');
    cal.id = 'cal-picker';
    cal.className = 'cal-picker';
    cal.setAttribute('role', 'dialog');
    cal.setAttribute('aria-label', 'Date picker');
    group.appendChild(cal);

    // On narrow screens, anchor left instead of right to stay in viewport
    const groupRect = group.getBoundingClientRect();
    if (groupRect.width < 310) {
      cal.style.right = 'auto';
      cal.style.left = '0';
    }

    const now = new Date();
    if (!calYear) {
      calYear = now.getFullYear();
      calMonth = now.getMonth();
    }
    calDecadeStart = Math.floor(calYear / 10) * 10;
    calView = 'day';

    renderCalendar();
  }

  function calYearRange() {
    const min = allYearData.length ? allYearData[0].year : 1960;
    const max = allYearData.length ? allYearData[allYearData.length - 1].year : new Date().getFullYear();
    return { min, max };
  }

  function calYearHasData(yr) {
    return allYearData.some(d => d.year === yr);
  }

  async function calFetchDates(year) {
    if (year <= 2025) return new Set();
    if (!rolodexDatesCache[year]) {
      try {
        const resp = await API.getYearDates(year);
        rolodexDatesCache[year] = resp.dates;
      } catch (err) {
        rolodexDatesCache[year] = [];
      }
    }
    return new Set(rolodexDatesCache[year]);
  }

  async function renderCalendar() {
    const cal = document.getElementById('cal-picker');
    if (!cal) return;

    const { min: minYear, max: maxYear } = calYearRange();
    let html = '';

    if (calView === 'day') {
      html = await renderCalDay(minYear, maxYear);
    } else if (calView === 'month') {
      html = renderCalMonth(minYear, maxYear);
    } else if (calView === 'year') {
      html = renderCalYear(minYear, maxYear);
    } else if (calView === 'decade') {
      html = renderCalDecade();
    }

    cal.innerHTML = html;
    wireCalEvents(cal);
  }

  async function renderCalDay(minYear, maxYear) {
    const availableDates = await calFetchDates(calYear);
    const canPrev = !(calYear <= minYear && calMonth === 0);
    const canNext = !(calYear >= maxYear && calMonth === 11);

    let html = `<div class="cal-header">
      <button class="cal-nav" data-action="prev-month" aria-label="Previous month"${canPrev ? '' : ' disabled'}>&lsaquo;</button>
      <button class="cal-title" data-action="zoom-out">${CAL_MONTHS_FULL[calMonth]} ${calYear}</button>
      <button class="cal-nav" data-action="next-month" aria-label="Next month"${canNext ? '' : ' disabled'}>&rsaquo;</button>
    </div>`;

    if (calYear > 2025) {
      html += '<div class="cal-weekdays">';
      ['Su','Mo','Tu','We','Th','Fr','Sa'].forEach(d => { html += `<span>${d}</span>`; });
      html += '</div><div class="cal-grid cal-grid-days">';

      const firstDay = new Date(calYear, calMonth, 1).getDay();
      const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();

      for (let i = 0; i < firstDay; i++) html += '<span class="cal-cell cal-empty"></span>';
      for (let d = 1; d <= daysInMonth; d++) {
        const dateStr = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const has = availableDates.has(dateStr);
        const sel = dateStr === calSelectedDate;
        const cls = ['cal-cell'];
        if (has) cls.push('cal-has-data');
        if (sel) cls.push('cal-selected');
        html += `<span class="${cls.join(' ')}" data-action="pick-day" data-date="${dateStr}" role="button" tabindex="0" aria-label="${dateStr}">${d}</span>`;
      }
      html += '</div>';
    } else {
      html += `<div class="cal-hist-msg">Year-level aggregate only.<br>No daily readings before 2026.</div>`;
    }
    return html;
  }

  function renderCalMonth(minYear, maxYear) {
    const canPrev = calYear > minYear;
    const canNext = calYear < maxYear;

    let html = `<div class="cal-header">
      <button class="cal-nav" data-action="prev-year" aria-label="Previous year"${canPrev ? '' : ' disabled'}>&lsaquo;</button>
      <button class="cal-title" data-action="zoom-out">${calYear}</button>
      <button class="cal-nav" data-action="next-year" aria-label="Next year"${canNext ? '' : ' disabled'}>&rsaquo;</button>
    </div>`;

    html += '<div class="cal-grid cal-grid-months">';
    for (let m = 0; m < 12; m++) {
      const sel = m === calMonth && calView === 'month';
      html += `<span class="cal-cell cal-has-data${sel ? ' cal-selected' : ''}" data-action="pick-month" data-month="${m}" role="button" tabindex="0" aria-label="${CAL_MONTHS_FULL[m]}">${CAL_MONTHS_SHORT[m]}</span>`;
    }
    html += '</div>';
    return html;
  }

  function renderCalYear(minYear, maxYear) {
    calDecadeStart = Math.floor(calYear / 10) * 10;
    const rangeEnd = calDecadeStart + 9;
    const canPrev = calDecadeStart > Math.floor(minYear / 10) * 10;
    const canNext = calDecadeStart < Math.floor(maxYear / 10) * 10;

    let html = `<div class="cal-header">
      <button class="cal-nav" data-action="prev-decade" aria-label="Previous decade"${canPrev ? '' : ' disabled'}>&lsaquo;</button>
      <button class="cal-title" data-action="zoom-out">${calDecadeStart} &ndash; ${rangeEnd}</button>
      <button class="cal-nav" data-action="next-decade" aria-label="Next decade"${canNext ? '' : ' disabled'}>&rsaquo;</button>
    </div>`;

    html += '<div class="cal-grid cal-grid-years">';
    for (let y = calDecadeStart; y <= rangeEnd; y++) {
      const has = calYearHasData(y);
      const sel = y === calYear;
      const cls = ['cal-cell'];
      if (has) cls.push('cal-has-data');
      if (sel) cls.push('cal-selected');
      html += `<span class="${cls.join(' ')}" data-action="pick-year" data-year="${y}" role="button" tabindex="${has ? '0' : '-1'}">${y}</span>`;
    }
    html += '</div>';
    return html;
  }

  function renderCalDecade() {
    const { min: minYear, max: maxYear } = calYearRange();
    const firstDecade = Math.floor(minYear / 10) * 10;
    const lastDecade = Math.floor(maxYear / 10) * 10;

    let html = `<div class="cal-header">
      <span class="cal-title cal-title-top">${firstDecade}s &ndash; ${lastDecade}s</span>
    </div>`;

    html += '<div class="cal-grid cal-grid-decades">';
    for (let d = firstDecade; d <= lastDecade; d += 10) {
      const sel = d === calDecadeStart;
      const cls = ['cal-cell', 'cal-has-data'];
      if (sel) cls.push('cal-selected');
      html += `<span class="${cls.join(' ')}" data-action="pick-decade" data-decade="${d}" role="button" tabindex="0">${d}s</span>`;
    }
    html += '</div>';
    return html;
  }

  function calHandleAction(el) {
    const action = el.dataset.action;
    switch (action) {
      case 'zoom-out':
        if (calView === 'day') { calView = 'month'; announce(`Picking month for ${calYear}.`); }
        else if (calView === 'month') { calView = 'year'; announce('Picking year.'); }
        else if (calView === 'year') { calView = 'decade'; announce('Picking decade.'); }
        renderCalendar();
        break;
      case 'prev-month':
        calMonth--;
        if (calMonth < 0) { calMonth = 11; calYear--; }
        renderCalendar();
        break;
      case 'next-month':
        calMonth++;
        if (calMonth > 11) { calMonth = 0; calYear++; }
        renderCalendar();
        break;
      case 'prev-year':
        calYear--;
        renderCalendar();
        break;
      case 'next-year':
        calYear++;
        renderCalendar();
        break;
      case 'prev-decade':
        calDecadeStart -= 10;
        calYear = calDecadeStart;
        renderCalendar();
        break;
      case 'next-decade':
        calDecadeStart += 10;
        calYear = calDecadeStart;
        renderCalendar();
        break;
      case 'pick-day':
        calSelectedDate = el.dataset.date;
        if (el.classList.contains('cal-has-data')) {
          viewArchiveReading(el.dataset.date);
          announce(`Loading reading for ${el.dataset.date}.`);
        }
        renderCalendar();
        break;
      case 'pick-month':
        calMonth = parseInt(el.dataset.month);
        calView = 'day';
        announce(`${CAL_MONTHS_FULL[calMonth]} ${calYear}.`);
        renderCalendar();
        break;
      case 'pick-year':
        calYear = parseInt(el.dataset.year);
        calDecadeStart = Math.floor(calYear / 10) * 10;
        calView = 'month';
        announce(`Picking month for ${calYear}.`);
        onCalendarYearSelect(calYear);
        renderCalendar();
        break;
      case 'pick-decade':
        calDecadeStart = parseInt(el.dataset.decade);
        calYear = calDecadeStart;
        calView = 'year';
        announce(`${calDecadeStart}s decade.`);
        renderCalendar();
        break;
    }
  }

  function wireCalEvents(cal) {
    // Click handlers
    cal.querySelectorAll('[data-action]').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        calHandleAction(el);
      });
    });

    // Enter/Space on span cells
    cal.querySelectorAll('.cal-cell[tabindex]').forEach(el => {
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          calHandleAction(el);
        }
      });
    });

    // Arrow key navigation within grid
    cal.querySelectorAll('.cal-grid').forEach(grid => {
      grid.addEventListener('keydown', (e) => {
        if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
        e.preventDefault();
        const cells = [...grid.querySelectorAll('.cal-cell[tabindex]')];
        const idx = cells.indexOf(document.activeElement);
        if (idx === -1) return;
        const cols = grid.classList.contains('cal-grid-days') ? 7
          : grid.classList.contains('cal-grid-months') ? 4
          : grid.classList.contains('cal-grid-years') ? 5 : 3;
        let next = idx;
        if (e.key === 'ArrowRight') next = Math.min(idx + 1, cells.length - 1);
        else if (e.key === 'ArrowLeft') next = Math.max(idx - 1, 0);
        else if (e.key === 'ArrowDown') next = Math.min(idx + cols, cells.length - 1);
        else if (e.key === 'ArrowUp') next = Math.max(idx - cols, 0);
        cells[next].focus();
      });
    });

    // Escape to close
    cal.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        cal.remove();
      }
    });
  }

  function onCalendarYearSelect(year) {
    const yd = allYearData.find(d => d.year === year);
    if (yd) {
      loadYearSongs(year, yd.compass_degree, yd.charge_level);
      Compass.setDegree(yd.compass_degree, yd.charge_level);
      Charge.setLevel(yd.charge_level, 0, 0);
      setCompassDate(String(year));
    }
  }

  function syncCalendar(year, month, day) {
    calYear = year || calYear;
    if (month) {
      calMonth = parseInt(month) - 1;
      if (day) calSelectedDate = `${year}-${month}-${day}`;
      else calSelectedDate = null;
    } else {
      calSelectedDate = null;
    }
    if (calYear) calDecadeStart = Math.floor(calYear / 10) * 10;
  }

  function wireCalendarToggle(container) {
    const btn = container.querySelector('.cal-toggle-btn');
    if (!btn) return;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const existing = document.getElementById('cal-picker');
      if (existing) {
        existing.remove();
      } else {
        openCalendar(btn);
      }
    });
  }

  // --- Year Songs Loading + Rendering ---
  let yearSongsState = { year: null, songs: [], total: 0, loaded: 0 };

  async function loadYearSongs(year, degree, chargeLevel) {
    yearSongsState = { year, songs: [], total: 0, loaded: 0 };

    // Show "View songs" button under compass instead of auto-scrolling
    showYearViewButton(year, degree, chargeLevel);

    // Update reading panel header
    const header = document.querySelector('#reading-panel .card-header');
    const desc = document.querySelector('#reading-panel .card-desc');
    const isCurrentYear = year === new Date().getFullYear();
    if (year > 2025) {
      if (header) header.textContent = isCurrentYear ? `${year} — Year to Date` : `${year} — Charting Songs`;
      if (desc) desc.textContent = isCurrentYear ? 'Live aggregate, updated daily. Weighted by chart position and days on chart.' : 'Frequency-weighted by chart position and days on chart.';
    } else {
      if (header) header.textContent = `${year} — Billboard Top Songs`;
      if (desc) desc.textContent = 'Position-weighted aggregate of the year\u2019s biggest hits.';
    }

    // Load songs into reading panel (no scroll)
    const container = document.getElementById('reading-content');
    if (!container) return;
    // Keep old content visible while fetching — crossfade swaps it when ready
    container.style.opacity = '0.5';
    container.style.transition = 'opacity 0.15s ease';
    announce(`Loading songs for ${year}.`);

    try {
      const data = await API.getYearSongs(year, 0, 20);
      yearSongsState.songs = data.songs;
      yearSongsState.total = data.total;
      yearSongsState.loaded = data.songs.length;

      renderYearSongs(year, degree, chargeLevel, container);
    } catch (err) {
      console.error('Failed to load year songs:', err);
      container.style.opacity = '1';
      container.innerHTML = '<div class="error-msg">Could not load songs for this year.</div>';
    }
  }

  function showYearViewButton(year, degree, chargeLevel) {
    // Remove existing button if any
    const existing = document.getElementById('year-view-btn');
    if (existing) existing.remove();

    const isYTD = year === new Date().getFullYear();
    const tier = chargeLevel || degreeToTier(degree);
    const hex = COLOR_HEX[tier] || '#888';
    const label = isYTD ? `View ${year} YTD Songs` : `View ${year} Songs`;

    const btn = document.createElement('button');
    btn.id = 'year-view-btn';
    btn.className = 'year-view-btn';
    btn.innerHTML = `<span>${label}</span><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`;
    btn.style.borderColor = hex;
    btn.style.color = hex;

    btn.addEventListener('click', () => {
      const panel = document.getElementById('reading-panel');
      if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    // Append inside compass panel, below the compass
    const compassPanel = document.getElementById('compass-panel');
    if (compassPanel) {
      compassPanel.appendChild(btn);
    }
  }

  function renderYearSongs(year, degree, chargeLevel, container) {
    const { songs, total, loaded } = yearSongsState;
    const tier = chargeLevel || degreeToTier(degree);
    const hex = COLOR_HEX[tier] || '#888';
    const label = CHARGE_LABELS[tier] || tier;
    const contamCount = songs.filter(s => s.contaminated).length;
    const isLive = year > 2025;

    let html = '';
    // Year charge header
    html += `<div class="reading-charge-group">
      <div class="reading-date" style="background:${hex}">${year}</div>
      <div class="reading-charge-row">
        <div class="reading-charge-bar" style="background:${hex}">
          <div class="reading-charge-inner">
            <span class="reading-charge-score">${degreeToScore(degree)}</span>
            <span class="reading-charge-label">${label}</span>
            <span class="reading-charge-meta">${total} songs${contamCount ? ' \u00B7 ' + contamCount + ' contaminated' : ''}</span>
          </div>
        </div>
        ${calendarToggleBtnHtml()}
      </div>
    </div>`;

    html += '<ul class="song-list">';
    songs.forEach((song, i) => {
      const pos = song.position || (i + 1);
      const hasMEI = song.message_analysis || song.expression_analysis || song.intention_analysis;
      const hasSummary = song.charge_summary || song.contamination_note;
      const hasTooltip = hasMEI || hasSummary;
      let tooltipHtml = '';
      if (hasTooltip) {
        const songHex = COLOR_HEX[song.rubric_color] || '#888';
        const songLabel = CHARGE_LABELS[song.rubric_color] || song.rubric_color;
        const songScore = song.charge_value != null ? (song.charge_value > 0 ? '+' + song.charge_value : String(song.charge_value)) : '';
        let lines = `<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.35rem;padding-bottom:0.3rem;border-bottom:1px solid rgba(0,0,0,0.08)"><span style="width:3px;height:14px;border-radius:1.5px;flex-shrink:0;background:${songHex}"></span><span style="font-family:var(--rc-font-mono);font-size:0.7rem;font-weight:700;letter-spacing:0.02em;color:${songHex}">${songScore} ${songLabel}</span></div>`;
        if (song.charge_summary) lines += `<div style="font-size:0.72rem;color:rgba(20,20,30,0.65);font-style:italic;line-height:1.4;margin-bottom:0.3rem;padding-bottom:0.25rem;border-bottom:1px solid rgba(0,0,0,0.06)">${escapeHtml(song.charge_summary)}</div>`;
        if (song.message_analysis) lines += `<div class="mei-line"><strong>M</strong> ${escapeHtml(song.message_analysis)}</div>`;
        if (song.expression_analysis) lines += `<div class="mei-line"><strong>E</strong> ${escapeHtml(song.expression_analysis)}</div>`;
        if (song.intention_analysis) lines += `<div class="mei-line"><strong>I</strong> ${escapeHtml(song.intention_analysis)}</div>`;
        if (song.contaminated && song.contamination_note) lines += `<div class="mei-line mei-contam">&#x2622; ${escapeHtml(song.contamination_note)}</div>`;
        tooltipHtml = `<div class="song-tooltip">${lines}</div>`;
      }
      html += `
        <li class="song-item${hasTooltip ? ' has-tooltip' : ''}">
          <span class="song-pos">${pos}</span>
          <span class="song-dot ${song.rubric_color}"></span>
          <div class="song-info">
            <div class="song-title">${escapeHtml(song.title)}</div>
            <div class="song-artist">${escapeHtml(song.artist)}${isLive && song.days_on_chart > 1 ? ` <span class="song-days">${song.days_on_chart}d</span>` : ''}</div>
          </div>
          <div class="song-actions">
            ${song.contaminated ? '<span class="song-contam" aria-hidden="true">&#x2622;</span>' : ''}
            ${hasTooltip ? `<button class="song-comment-btn" title="Read analysis" aria-label="Analysis of ${escapeHtml(song.title)}">&#x1F4AC;</button>` : ''}
          </div>
          ${tooltipHtml}
        </li>
      `;
    });
    html += '</ul>';

    // Load More / View Full Year button
    if (loaded < total) {
      const remaining = total - loaded;
      if (loaded < 100) {
        html += `<button class="year-load-more" id="year-load-more">Load More (${remaining} remaining)</button>`;
      } else {
        html += `<button class="year-load-more" id="year-view-full">View Full Year (${total} songs)</button>`;
      }
    }

    crossfade(container, html, () => {
      initSongTooltips(container);
      wireCalendarToggle(container);

      // Wire load more
      const loadMoreBtn = document.getElementById('year-load-more');
      if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', () => loadMoreYearSongs(year, degree, chargeLevel));
      }

      // Wire full year overlay
      const viewFullBtn = document.getElementById('year-view-full');
      if (viewFullBtn) {
        viewFullBtn.addEventListener('click', () => openYearOverlay(year, degree, chargeLevel));
      }
    });

    // Announce for screen readers
    const tierLabel = CHARGE_LABELS[tier] || tier;
    announce(`${year} songs loaded. ${total} songs. Charge: ${tierLabel}.`);
  }

  async function loadMoreYearSongs(year, degree, chargeLevel) {
    const btn = document.getElementById('year-load-more');
    if (btn) { btn.textContent = 'Loading...'; btn.disabled = true; }

    try {
      const data = await API.getYearSongs(year, yearSongsState.loaded, 20);
      yearSongsState.songs = yearSongsState.songs.concat(data.songs);
      yearSongsState.loaded = yearSongsState.songs.length;
      yearSongsState.total = data.total;

      const container = document.getElementById('reading-content');
      if (container) renderYearSongs(year, degree, chargeLevel, container);
    } catch (err) {
      if (btn) { btn.textContent = 'Failed — try again'; btn.disabled = false; }
    }
  }

  async function openYearOverlay(year, degree, chargeLevel) {
    let overlay = document.getElementById('year-songs-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'year-songs-overlay';
      overlay.className = 'year-overlay';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      document.body.appendChild(overlay);
    }

    const tier = chargeLevel || degreeToTier(degree);
    const hex = COLOR_HEX[tier] || '#888';
    const label = CHARGE_LABELS[tier] || tier;

    overlay.setAttribute('aria-label', `${year} songs — ${label}`);
    overlay.innerHTML = `
      <div class="year-overlay-header">
        <div class="year-overlay-title">
          <span class="year-overlay-year">${year}</span>
          <span class="year-overlay-score" style="color:${hex}">${degreeToScore(degree)} ${label}</span>
        </div>
        <button class="year-overlay-close" id="year-overlay-close" aria-label="Close overlay">&times;</button>
      </div>
      <div class="year-overlay-body"><div class="loading" role="status">Loading all songs...</div></div>
    `;
    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';

    document.getElementById('year-overlay-close').addEventListener('click', closeYearOverlay);

    try {
      const data = await API.getYearSongs(year, 0, 999);
      const songs = data.songs;
      const isLive = year > 2025;
      const contamCount = songs.filter(s => s.contaminated).length;

      // Tier breakdown bar
      const colorCounts = {};
      songs.forEach(s => { colorCounts[s.rubric_color] = (colorCounts[s.rubric_color] || 0) + 1; });
      let barHtml = '';
      ['violet', 'blue', 'green', 'orange', 'red'].forEach(color => {
        const count = colorCounts[color] || 0;
        if (count === 0) return;
        const pct = (count / songs.length) * 100;
        barHtml += `<div class="decade-seg" style="width:${pct.toFixed(1)}%;background:${COLOR_HEX[color]}" title="${count} ${CHARGE_LABELS[color]}"></div>`;
      });

      let tableHtml = '<table class="year-overlay-table"><thead><tr>';
      tableHtml += '<th>#</th><th></th><th>Title</th><th>Artist</th>';
      if (isLive) tableHtml += '<th>Days</th>';
      tableHtml += '<th>Charge</th>';
      tableHtml += '</tr></thead><tbody>';

      songs.forEach((s, i) => {
        const pos = s.position || (i + 1);
        const cv = s.charge_value != null ? (s.charge_value > 0 ? '+' + s.charge_value : s.charge_value) : '';
        tableHtml += `<tr>
          <td class="yo-pos">${pos}</td>
          <td><span class="song-dot ${s.rubric_color}"></span></td>
          <td class="yo-title">${escapeHtml(s.title)}</td>
          <td class="yo-artist">${escapeHtml(s.artist)}</td>
          ${isLive ? `<td class="yo-days">${s.days_on_chart}</td>` : ''}
          <td class="yo-charge">${cv}</td>
        </tr>`;
      });
      tableHtml += '</tbody></table>';

      const body = overlay.querySelector('.year-overlay-body');
      body.innerHTML = `
        <div class="year-overlay-meta">${songs.length} songs${contamCount ? ' \u00B7 ' + contamCount + ' contaminated' : ''}</div>
        <div class="decade-bar" style="margin-bottom:1rem;">${barHtml}</div>
        ${tableHtml}
      `;
    } catch (err) {
      overlay.querySelector('.year-overlay-body').innerHTML = '<div class="error-msg">Failed to load songs.</div>';
    }
  }

  function closeYearOverlay() {
    const overlay = document.getElementById('year-songs-overlay');
    if (overlay) {
      overlay.classList.remove('open');
      document.body.style.overflow = '';
    }
  }

  // --- Chart click pinch effect ---
  function pinchLine(points, nearestIdx, lineEl, areaEl, padT, chartH, ytdSplit) {
    const radius = 20; // SVG units (~25px)
    const strength = 0.35;
    const target = points[nearestIdx];
    const pinched = points.map(p => {
      const dx = Math.abs(p.x - target.x);
      if (dx > radius) return p;
      const factor = strength * (1 - dx / radius);
      return { ...p, x: p.x + (target.x - p.x) * factor };
    });

    const buildLine = pts => pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');

    // If YTD split, solid line only goes up to second-to-last point
    const solidPinched = ytdSplit ? pinched.slice(0, -1) : pinched;
    const pinchedPath = buildLine(solidPinched);
    const fullPinched = pinched;
    const last = fullPinched[fullPinched.length - 1];
    const first = fullPinched[0];
    const pinchedArea = buildLine(fullPinched) + ` L ${last.x.toFixed(1)} ${padT + chartH} L ${first.x.toFixed(1)} ${padT + chartH} Z`;

    if (lineEl) lineEl.setAttribute('d', pinchedPath);
    if (areaEl) areaEl.setAttribute('d', pinchedArea);

    // Restore
    const solidPoints = ytdSplit ? points.slice(0, -1) : points;
    const origPath = buildLine(solidPoints);
    const origArea = buildLine(points) + ` L ${points[points.length - 1].x.toFixed(1)} ${padT + chartH} L ${points[0].x.toFixed(1)} ${padT + chartH} Z`;
    setTimeout(() => {
      if (lineEl) lineEl.setAttribute('d', origPath);
      if (areaEl) areaEl.setAttribute('d', origArea);
    }, 100);
  }

  function degreeToTier(deg) {
    if (deg <= 22.5) return 'violet';
    if (deg <= 67.5) return 'blue';
    if (deg <= 112.5) return 'green';
    if (deg <= 157.5) return 'orange';
    return 'red';
  }

  // --- Elevated Library ---
  async function loadLibrary() {
    const container = document.getElementById('library-content');
    if (!container) return;

    try {
      const collections = await API.getLibrary();

      if (!collections.length) {
        container.innerHTML = '<p style="color:var(--rc-text-dim);text-align:center;">No collections yet.</p>';
        return;
      }

      let html = '<div class="elevated-collections">';
      collections.forEach(col => {
        html += `<div class="elevated-collection">`;
        html += `<div class="elevated-collection-header">`;
        html += `<h3 class="elevated-collection-name">${escapeHtml(col.name)}</h3>`;
        if (col.description) {
          html += `<p class="elevated-collection-desc">${escapeHtml(col.description)}</p>`;
        }
        html += `</div>`;

        const INITIAL_SHOW = 20;
        if (col.items.length) {
          const visibleItems = col.items.slice(0, INITIAL_SHOW);
          const hasMore = col.items.length > INITIAL_SHOW;
          html += '<ul class="elevated-items">';
          visibleItems.forEach(item => {
            html += `<li class="elevated-item">`;
            if (item.cover_art_url) {
              html += `<img class="elevated-cover" src="${escapeHtml(item.cover_art_url)}" alt="" loading="lazy">`;
            }
            html += `<span class="song-dot ${item.rubric_color}"></span>`;
            html += `<div class="elevated-item-info">`;
            html += `<span class="elevated-item-title">${escapeHtml(item.title)}</span>`;
            html += `<span class="elevated-item-artist">${escapeHtml(item.artist)}</span>`;
            if (item.editorial_note) {
              html += `<span class="elevated-item-note">${escapeHtml(item.editorial_note)}</span>`;
            }
            html += `</div>`;
            if (item.external_url) {
              html += `<a class="elevated-link" href="${escapeHtml(item.external_url)}" target="_blank" rel="noopener" title="Listen" aria-label="Listen to ${escapeHtml(item.title)}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
              </a>`;
            }
            const sourceLabel = item.source === 'recommendation' ? '' : item.source === 'album_deep_dive' ? 'album' : '';
            if (sourceLabel) {
              html += `<span class="elevated-item-source">${sourceLabel}</span>`;
            }
            html += `</li>`;
          });
          html += '</ul>';
          if (hasMore) {
            html += `<button class="elevated-show-more" data-collection="${escapeHtml(col.name)}" data-shown="${INITIAL_SHOW}">${col.items.length - INITIAL_SHOW} more</button>`;
          }
        } else {
          html += '<p style="color:var(--rc-text-dim);font-size:0.82rem;padding:1rem 0;">No items in this collection yet.</p>';
        }

        html += `</div>`;
      });
      html += '</div>';

      container.innerHTML = html;

      // Wire "show more" buttons
      container.querySelectorAll('.elevated-show-more').forEach(btn => {
        btn.addEventListener('click', () => {
          const colName = btn.dataset.collection;
          const col = collections.find(c => c.name === colName);
          if (!col) return;
          const shown = parseInt(btn.dataset.shown);
          const next = shown + 20;
          const newItems = col.items.slice(shown, next);
          const list = btn.previousElementSibling;
          newItems.forEach(item => {
            const li = document.createElement('li');
            li.className = 'elevated-item';
            let inner = '';
            if (item.cover_art_url) inner += `<img class="elevated-cover" src="${escapeHtml(item.cover_art_url)}" alt="" loading="lazy">`;
            inner += `<span class="song-dot ${item.rubric_color}"></span>`;
            inner += `<div class="elevated-item-info"><span class="elevated-item-title">${escapeHtml(item.title)}</span><span class="elevated-item-artist">${escapeHtml(item.artist)}</span></div>`;
            li.innerHTML = inner;
            list.appendChild(li);
          });
          btn.dataset.shown = next;
          const remaining = col.items.length - next;
          if (remaining <= 0) btn.remove();
          else btn.textContent = `${remaining} more`;
        });
      });
    } catch (err) {
      container.innerHTML = '<p style="color:var(--rc-text-dim);text-align:center;">Could not load library.</p>';
    }
  }

  // --- Secondary Nav ---
  function initNav() {
    document.querySelectorAll('.nav-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const section = tab.dataset.section;
        setActiveSection(section);
      });
    });

    // Check hash on load, default to history — but don't set hash if none present
    const hash = window.location.hash.slice(1);
    if (hash) {
      setActiveSection(hash);
    } else {
      setActiveSection('history', false);
    }
  }

  function setActiveSection(section, updateHash = true) {
    document.querySelectorAll('.nav-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.section === section);
    });
    document.querySelectorAll('.section-content').forEach(s => {
      s.classList.toggle('active', s.id === `section-${section}`);
    });
    if (updateHash) window.location.hash = section;

    const sectionNames = { history: 'Historical Overview', methodology: 'Methodology' };
    if (sectionNames[section]) announce(`${sectionNames[section]} section.`);

    // Lazy-load section data
    if (section === 'history') loadDrift();
  }

  // --- Drift (Historical Overview) ---
  let driftLoaded = false;
  async function loadDrift() {
    if (driftLoaded) return;
    const container = document.getElementById('drift-content');
    if (!container) return;

    try {
      const data = await API.getDrift();
      driftLoaded = true;

      const COLOR_ORDER = ['violet', 'blue', 'green', 'orange', 'red'];

      let html = '<div class="decade-cards">';
      data.forEach(d => {
        const score = degreeToScore(d.compass_degree);
        const hex = COLOR_HEX[d.charge_level] || '#888';
        const tierLabel = CHARGE_LABELS[d.charge_level] || d.charge_level;

        // Stacked color bar segments
        let barHtml = '';
        COLOR_ORDER.forEach(color => {
          const count = d.color_counts[color] || 0;
          if (count === 0) return;
          const pct = (count / d.chart_song_count) * 100;
          barHtml += `<div class="decade-seg" style="width:${pct.toFixed(1)}%;background:${COLOR_HEX[color]}" title="${count} ${CHARGE_LABELS[color] || color}"></div>`;
        });

        // Color breakdown text
        let breakdownParts = [];
        COLOR_ORDER.forEach(color => {
          const count = d.color_counts[color] || 0;
          if (count > 0) {
            breakdownParts.push(`<span style="color:${COLOR_HEX[color]}">${count}</span> ${CHARGE_LABELS[color] || color}`);
          }
        });

        html += `
          <div class="decade-card">
            <div class="decade-header">
              <span class="decade-name">${d.decade}</span>
              <span class="decade-score" style="color:${hex}">${score}</span>
              <span class="decade-tier" style="color:${hex}">${tierLabel}</span>
            </div>
            <div class="decade-bar">${barHtml}</div>
            <div class="decade-breakdown">${breakdownParts.join('<span class="decade-sep">/</span>')}</div>
            <div class="decade-meta">${d.chart_song_count} charting songs analyzed${d.total_song_count > d.chart_song_count ? `<br><span class="decade-meta-total">${d.total_song_count} total songs</span>` : ''}</div>
          </div>
        `;
      });
      html += '</div>';

      container.innerHTML = html;
    } catch (err) {
      container.innerHTML = '<div class="error-msg">Could not load drift data.</div>';
    }
  }

  // --- Albums ---
  let albumsLoaded = false;
  async function loadAlbums() {
    if (albumsLoaded) return;
    const container = document.getElementById('albums-content');
    if (!container) return;

    try {
      const albums = await API.getAlbums();
      albumsLoaded = true;

      if (albums.length === 0) {
        container.innerHTML = '<p style="color:var(--rc-text-dim);">No album deep dives yet.</p>';
        return;
      }

      let html = '<div id="album-list-view" class="album-grid">';
      albums.forEach(a => {
        const colorDot = a.overall_color ? `<span class="song-dot ${a.overall_color}" style="display:inline-block;margin-right:0.4rem;"></span>` : '';
        html += `
          <div class="album-card" onclick="App.showAlbum('${a.slug}')">
            <div class="album-card-title">${colorDot}${escapeHtml(a.title)}</div>
            <div class="album-card-artist">${escapeHtml(a.artist)}</div>
            ${a.release_year ? `<div class="album-card-year">${a.release_year}</div>` : ''}
          </div>
        `;
      });
      html += '</div>';
      html += '<div id="album-detail-view" class="album-detail"></div>';

      container.innerHTML = html;
    } catch (err) {
      container.innerHTML = '<div class="error-msg">Could not load albums.</div>';
    }
  }

  async function showAlbum(slug) {
    const listView = document.getElementById('album-list-view');
    const detailView = document.getElementById('album-detail-view');
    if (!listView || !detailView) return;

    try {
      const album = await API.getAlbum(slug);
      listView.style.display = 'none';
      detailView.classList.add('active');

      let html = `<button class="album-back" onclick="App.backToAlbums()">&larr; Back to albums</button>`;
      html += `<h3 style="color:var(--rc-text-bright);margin-bottom:0.2rem;">${escapeHtml(album.title)}</h3>`;
      html += `<p style="color:var(--rc-text-dim);margin-bottom:1rem;">${escapeHtml(album.artist)} ${album.release_year ? `(${album.release_year})` : ''}</p>`;

      if (album.summary) {
        html += `<p style="font-size:0.9rem;margin-bottom:1.5rem;line-height:1.6;">${escapeHtml(album.summary)}</p>`;
      }

      html += '<ul class="track-list">';
      album.tracks.forEach(t => {
        html += `
          <li class="track-item">
            <span class="track-num">${t.track_number}</span>
            <span class="song-dot ${t.charge_color || 'orange'}"></span>
            <span>${escapeHtml(t.name)}</span>
            ${t.assessment ? `<span class="track-assessment">${escapeHtml(t.assessment)}</span>` : ''}
          </li>
        `;
      });
      html += '</ul>';

      detailView.innerHTML = html;
    } catch (err) {
      detailView.innerHTML = '<div class="error-msg">Could not load album.</div>';
    }
  }

  function backToAlbums() {
    const listView = document.getElementById('album-list-view');
    const detailView = document.getElementById('album-detail-view');
    if (listView) listView.style.display = '';
    if (detailView) {
      detailView.classList.remove('active');
      detailView.innerHTML = '';
    }
  }

  // --- Archive ---
  let archivePage = 1;
  let archiveLoaded = false;
  async function loadArchive() {
    if (archiveLoaded) return;
    const container = document.getElementById('archive-content');
    if (!container) return;

    try {
      const data = await API.getHistory(archivePage);
      archiveLoaded = true;

      if (data.items.length === 0) {
        container.innerHTML = '<p style="color:var(--rc-text-dim);">No archived readings yet.</p>';
        return;
      }

      let html = '<ul class="archive-list">';
      data.items.forEach(r => {
        html += `
          <li class="archive-item" onclick="App.viewArchiveReading('${r.date}')">
            <span class="archive-date">${formatDate(r.date)}</span>
            <div class="archive-meta">
              <span class="archive-degree" style="color:${COLOR_HEX[r.charge_level] || '#888'}">${degreeToScore(r.compass_degree)}</span>
              <span class="archive-charge">${CHARGE_LABELS[r.charge_level] || r.charge_level}</span>
            </div>
          </li>
        `;
      });
      html += '</ul>';

      if (data.pages > 1) {
        html += '<div style="text-align:center;margin-top:1rem;">';
        for (let p = 1; p <= data.pages; p++) {
          const active = p === archivePage ? 'color:var(--rc-accent);font-weight:bold;' : 'color:var(--rc-text-dim);';
          html += `<button onclick="App.loadArchivePage(${p})" style="background:none;border:none;padding:0.5rem;cursor:pointer;font-family:var(--rc-font-mono);${active}">${p}</button>`;
        }
        html += '</div>';
      }

      container.innerHTML = html;
    } catch (err) {
      container.innerHTML = '<div class="error-msg">Could not load archive.</div>';
    }
  }

  async function loadArchivePage(page) {
    archivePage = page;
    archiveLoaded = false;
    await loadArchive();
  }

  async function viewArchiveReading(date) {
    try {
      const reading = await API.getReading(date);
      // Update compass + panels with this reading
      Compass.setDegree(reading.compass_degree, reading.charge_level);
      const redCount = reading.songs.filter(s => s.rubric_color === 'red').length;
      Charge.setLevel(reading.charge_level, redCount, reading.songs.length);
      Contamination.setCount(reading.contamination_count, reading.songs.length);
      renderReading({ has_reading: true, ...reading });

      // Show "View Songs" button under compass
      showDailyViewButton(date, reading.compass_degree, reading.charge_level);
    } catch (err) {
      console.error('Failed to load reading:', err);
    }
  }

  function showDailyViewButton(date, degree, chargeLevel) {
    // Remove existing button if any
    const existing = document.getElementById('year-view-btn');
    if (existing) existing.remove();

    const tier = chargeLevel || degreeToTier(degree);
    const hex = COLOR_HEX[tier] || '#888';
    const label = `View ${formatDate(date)} Songs`;

    const btn = document.createElement('button');
    btn.id = 'year-view-btn';
    btn.className = 'year-view-btn';
    btn.innerHTML = `<span>${label}</span><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`;
    btn.style.borderColor = hex;
    btn.style.color = hex;

    btn.addEventListener('click', () => {
      const panel = document.getElementById('reading-panel');
      if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    const compassPanel = document.getElementById('compass-panel');
    if (compassPanel) compassPanel.appendChild(btn);
  }

  // --- Helpers ---
  function degreeToScore(degree) {
    const s = Math.round((90 - degree) * 100 / 90);
    return (s > 0 ? '+' : '') + s;
  }

  function formatDate(dateStr) {
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  }

  function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // --- Public ---
  return {
    init,
    showAlbum,
    backToAlbums,
    loadArchivePage,
    viewArchiveReading,
  };
})();

// Boot
document.addEventListener('DOMContentLoaded', App.init);
