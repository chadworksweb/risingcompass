/* === Calibration Log — feed renderer === */

(() => {
  const PAGE_SIZE = 20;
  const feedEl = document.getElementById('cl-feed');
  const paginationEl = document.querySelector('.cl-pagination');
  const loadMoreBtn = document.getElementById('cl-load-more');
  const filterBtns = document.querySelectorAll('.cl-filter');

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
    return {
      day: `${MONTH_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}`,
      year: d.getUTCFullYear(),
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
    const tierDot = tier ? `<span class="cl-diff-tier cl-diff-tier-${tier}"></span>` : '';
    return `
      <span class="cl-diff-side">
        ${tierDot}
        <span>${tierLabel}</span>
        <span class="cl-diff-charge">${charge}</span>
        ${contam}
      </span>
    `;
  }

  function diffMarkup(entry) {
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

  function entryMarkup(entry) {
    const { day, year } = fmtDate(entry.occurred_at);
    const typeLabel = EVENT_TYPE_LABELS[entry.event_type] || entry.event_type;
    const anchor = entry.song_anchor;
    const titleHtml = anchor && anchor.slug
      ? `<a href="/songs/${encodeURIComponent(anchor.slug)}">${escapeHtml(anchor.title)} <span style="color:var(--rc-text-dim);font-weight:400">— ${escapeHtml(anchor.artist)}</span></a>`
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
        feedEl.insertAdjacentHTML('beforeend', data.items.map(entryMarkup).join(''));
      }
      offset += data.items.length;
      paginationEl.hidden = offset >= total;
    } catch (err) {
      console.error(err);
      feedEl.innerHTML = '<div class="cl-empty">Could not load the log. Try again in a moment.</div>';
    } finally {
      loading = false;
    }
  }

  filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      filterBtns.forEach(b => b.classList.remove('cl-filter-active'));
      btn.classList.add('cl-filter-active');
      activeType = btn.dataset.type || '';
      loadPage(true);
    });
  });

  loadMoreBtn.addEventListener('click', () => loadPage(false));

  loadPage(true);
})();
