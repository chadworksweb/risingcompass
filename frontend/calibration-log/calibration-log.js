/* === Calibration Log — feed renderer === */

(() => {
  const PAGE_SIZE = 20;
  const feedEl = document.getElementById('cl-feed');
  const paginationEl = document.querySelector('.cl-pagination');
  const loadMoreBtn = document.getElementById('cl-load-more');
  const viewAllWrap = document.querySelector('.cl-view-all-wrap');
  const filterBtns = document.querySelectorAll('.cl-filter');

  // Homepage shows the compact row list capped at PAGE_SIZE with a "view all"
  // link out to the standalone page. The standalone page keeps the original
  // detail-card view with Load more pagination.
  const isHomepage = !!document.getElementById('section-calibration-log');

  let activeType = '';
  let offset = 0;
  let total = 0;
  let loading = false;

  const TIER_LABELS = {
    violet: 'Ascended',
    blue: 'Elevated',
    green: 'Decent',
    orange: 'Degraded',
    red: 'Corrupted',
  };

  const EVENT_TYPE_LABELS = {
    pre_publish_correction: 'Pre-publish',
    recalibration: 'Recalibration',
  };

  const EVENT_TYPE_LABELS_FULL = {
    pre_publish_correction: 'Pre-publish correction',
    recalibration: 'Recalibration',
  };

  const PIPELINE_LABELS = {
    manual: 'Manual',
    rubric_update: 'Rubric update',
    satirical_flag: 'Satirical flag',
    vibe_gap: 'Vibe gap',
    consensus_drift: 'Consensus drift',
  };

  const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function fmtDate(iso) {
    if (!iso) return { day: '—', year: '' };
    const d = new Date(iso);
    if (isNaN(d)) return { day: '—', year: '' };
    // Render in the viewer's local timezone (public UI = user-based time).
    return {
      day: `${MONTH_SHORT[d.getMonth()]} ${d.getDate()}`,
      year: d.getFullYear(),
    };
  }

  function formatCharge(val) {
    if (val === null || val === undefined) return '—';
    return val > 0 ? `+${val}` : String(val);
  }

  function sideMarkup(side) {
    if (!side) return '';
    const tier = side.rubric_color;
    const tierLabel = TIER_LABELS[tier] || '';
    const charge = formatCharge(side.charge_value);
    const contam = side.contaminated
      ? '<span class="cl-diff-contam">Contaminated</span>'
      : '';
    const dogma = side.dogma_referenced
      ? '<span class="cl-diff-dogma">Dogma</span>'
      : '';
    const tierDot = tier ? `<span class="cl-diff-tier cl-diff-tier-${tier}"></span>` : '';
    return `
      <span class="cl-diff-side">
        ${tierDot}
        <span>${tierLabel}</span>
        <span class="cl-diff-charge">${charge}</span>
        ${contam}
        ${dogma}
      </span>
    `;
  }

  // An entry "changed the score" only if tier or charge actually moved. Rubric
  // passes that re-affirm a song (e.g. dogma-framework tightening that leaves the
  // number alone) carry an identical before/after and shouldn't show a score.
  function scoreUnchanged(entry) {
    const b = entry.before, a = entry.after;
    if (!b || !a) return false;
    return b.rubric_color === a.rubric_color && b.charge_value === a.charge_value;
  }

  function diffMarkup(entry) {
    if (scoreUnchanged(entry)) return '';
    const hasBefore = entry.before && (entry.before.rubric_color || entry.before.charge_value !== null);
    const hasAfter = entry.after && (entry.after.rubric_color || entry.after.charge_value !== null);
    if (!hasBefore && !hasAfter) return '';
    return `
      <div class="cl-diff">
        ${sideMarkup(entry.before)}
        <span class="cl-diff-arrow">→</span>
        ${sideMarkup(entry.after)}
      </div>
    `;
  }

  function compactSide(side) {
    if (!side) return '';
    const tier = side.rubric_color;
    const tierDot = tier ? `<span class="cl-row-dot cl-row-dot-${tier}"></span>` : '';
    const charge = formatCharge(side.charge_value);
    return `<span class="cl-row-side">${tierDot}<span class="cl-row-charge">${charge}</span></span>`;
  }

  function compactDiff(entry) {
    // Keep an empty grid cell (the summary is a 5-col grid) so the caret stays
    // right-aligned, but show no score when nothing moved.
    if (scoreUnchanged(entry)) return '<span class="cl-row-diff-empty"></span>';
    const hasBefore = entry.before && (entry.before.rubric_color || entry.before.charge_value !== null);
    const hasAfter = entry.after && (entry.after.rubric_color || entry.after.charge_value !== null);
    if (!hasBefore && !hasAfter) return '<span class="cl-row-diff-empty">&mdash;</span>';
    return `
      <span class="cl-row-diff">
        ${compactSide(entry.before)}
        <span class="cl-row-diff-arrow">&rarr;</span>
        ${compactSide(entry.after)}
      </span>
    `;
  }

  function tagsMarkup(tags) {
    if (!tags) return '';
    const parts = tags.split(',').map(t => t.trim()).filter(Boolean);
    if (!parts.length) return '';
    return `<div class="cl-tags">${parts.map(t => `<span class="cl-tag">${escapeHtml(t)}</span>`).join('')}</div>`;
  }

  function rubricChangeMarkup(note) {
    if (!note) return '';
    return `
      <div class="cl-rubric-change">
        <span class="cl-rubric-change-label">Rubric change</span>
        ${escapeHtml(note)}
      </div>
    `;
  }

  function rationaleMarkup(entry) {
    const prose = entry.human_rationale || entry.public_summary || '';
    if (!prose) return '';
    return `<div class="cl-rationale">${escapeHtml(prose)}</div>`;
  }

  function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function anchorTitleHtml(entry, { withArtist = true } = {}) {
    const anchor = entry.song_anchor;
    if (!anchor) return escapeHtml(entry.title);
    const titleLink = anchor.slug
      ? `<a href="/songs/${encodeURIComponent(anchor.slug)}" onclick="event.stopPropagation();">${escapeHtml(anchor.title)}</a>`
      : escapeHtml(anchor.title);
    if (!withArtist) return titleLink;
    const artistLink = anchor.artist_slug
      ? `<a class="cl-anchor-artist" href="/artists/${encodeURIComponent(anchor.artist_slug)}" onclick="event.stopPropagation();">${escapeHtml(anchor.artist)}</a>`
      : escapeHtml(anchor.artist || '');
    return `${titleLink}<span class="cl-row-artist"> &mdash; ${artistLink}</span>`;
  }

  function entryMarkupCard(entry) {
    const { day, year } = fmtDate(entry.occurred_at);
    const typeLabel = EVENT_TYPE_LABELS_FULL[entry.event_type] || entry.event_type;
    const anchor = entry.song_anchor;
    const artistFrag = anchor
      ? (anchor.artist_slug
          ? `<a class="cl-anchor-artist artist-link" href="/artists/${encodeURIComponent(anchor.artist_slug)}" onclick="event.stopPropagation();">${escapeHtml(anchor.artist)}</a>`
          : escapeHtml(anchor.artist))
      : '';
    const titleHtml = anchor && anchor.slug
      ? `<a href="/songs/${encodeURIComponent(anchor.slug)}">${escapeHtml(anchor.title)}</a> <span style="color:var(--rc-text-dim);font-weight:400">&mdash; ${artistFrag}</span>`
      : escapeHtml(entry.title);

    const pipelineBadge = entry.pipeline
      ? `<span class="cl-badge">${escapeHtml(PIPELINE_LABELS[entry.pipeline] || entry.pipeline)}</span>`
      : '';
    const lensBadge = entry.lens && entry.lens !== 'standard'
      ? `<span class="cl-badge">Lens: ${escapeHtml(entry.lens)}</span>`
      : '';

    return `
      <article class="cl-entry">
        <div class="cl-entry-date">
          <span class="cl-entry-date-day">${day}</span>
          ${year}
        </div>
        <div class="cl-entry-body">
          <div class="cl-entry-head">
            <span class="cl-badge cl-badge-type-${entry.event_type}">${escapeHtml(typeLabel)}</span>
            ${pipelineBadge}
            ${lensBadge}
          </div>
          <h2 class="cl-entry-title">${titleHtml}</h2>
          ${diffMarkup(entry)}
          ${rubricChangeMarkup(entry.rubric_change_note)}
          ${rationaleMarkup(entry)}
          ${tagsMarkup(entry.tags)}
        </div>
      </article>
    `;
  }

  function entryMarkupCompact(entry) {
    const { day } = fmtDate(entry.occurred_at);
    const typeLabel = EVENT_TYPE_LABELS[entry.event_type] || entry.event_type;
    const pipelineBadge = entry.pipeline
      ? `<span class="cl-badge">${escapeHtml(PIPELINE_LABELS[entry.pipeline] || entry.pipeline)}</span>`
      : '';
    const lensBadge = entry.lens && entry.lens !== 'standard'
      ? `<span class="cl-badge">Lens: ${escapeHtml(entry.lens)}</span>`
      : '';
    const hasDetails = !!(entry.rubric_change_note || entry.human_rationale || entry.public_summary || entry.tags || pipelineBadge || lensBadge);

    const detailsBody = hasDetails ? `
      <div class="cl-row-details">
        <div class="cl-row-details-inner">
          ${(pipelineBadge || lensBadge) ? `<div class="cl-row-details-badges">${pipelineBadge}${lensBadge}</div>` : ''}
          ${rubricChangeMarkup(entry.rubric_change_note)}
          ${rationaleMarkup(entry)}
          ${tagsMarkup(entry.tags)}
        </div>
      </div>
    ` : '';

    return `
      <div class="cl-row" data-has-details="${hasDetails ? '1' : '0'}" data-expanded="false">
        <div class="cl-row-summary" role="button" tabindex="0" aria-expanded="false"${hasDetails ? '' : ' data-no-expand="1"'}>
          <span class="cl-row-date">${day}</span>
          <span class="cl-row-type cl-row-type-${entry.event_type}">${escapeHtml(typeLabel)}</span>
          <span class="cl-row-title">${anchorTitleHtml(entry)}</span>
          ${compactDiff(entry)}
          <span class="cl-row-caret" aria-hidden="true">${hasDetails ? '▾' : ''}</span>
        </div>
        ${detailsBody}
      </div>
    `;
  }

  const renderEntry = isHomepage ? entryMarkupCompact : entryMarkupCard;

  async function loadPage(reset = false) {
    if (loading) return;
    loading = true;
    if (reset) {
      offset = 0;
      feedEl.innerHTML = '<div class="cl-loading">Loading the log...</div>';
    }
    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(offset),
      });
      if (activeType) params.set('event_type', activeType);
      const data = await API.get(`/api/calibration-log?${params.toString()}`);
      total = data.total;
      if (reset) feedEl.innerHTML = '';
      if (data.items.length === 0 && offset === 0) {
        feedEl.innerHTML = '<div class="cl-empty">No entries in the log yet.</div>';
      } else {
        feedEl.insertAdjacentHTML('beforeend', data.items.map(renderEntry).join(''));
      }
      offset += data.items.length;

      if (isHomepage) {
        // Homepage caps at PAGE_SIZE and links out to the full log.
        if (viewAllWrap) viewAllWrap.hidden = data.items.length === 0;
      } else if (paginationEl) {
        paginationEl.hidden = offset >= total;
      }
    } catch (err) {
      console.error(err);
      feedEl.innerHTML = '<div class="cl-empty">Could not load the log. Try again in a moment.</div>';
    } finally {
      loading = false;
    }
  }

  function toggleRow(summary) {
    if (summary.dataset.noExpand === '1') return;
    const row = summary.closest('.cl-row');
    if (!row) return;
    const expanded = summary.getAttribute('aria-expanded') === 'true';
    summary.setAttribute('aria-expanded', String(!expanded));
    row.dataset.expanded = String(!expanded);
  }

  if (isHomepage) {
    feedEl.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;
      const summary = e.target.closest('.cl-row-summary');
      if (!summary) return;
      toggleRow(summary);
    });
    feedEl.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const summary = e.target.closest('.cl-row-summary');
      if (!summary) return;
      e.preventDefault();
      toggleRow(summary);
    });
  }

  filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      filterBtns.forEach(b => b.classList.remove('cl-filter-active'));
      btn.classList.add('cl-filter-active');
      activeType = btn.dataset.type || '';
      loadPage(true);
    });
  });

  if (loadMoreBtn) {
    loadMoreBtn.addEventListener('click', () => loadPage(false));
  }

  loadPage(true);
})();
