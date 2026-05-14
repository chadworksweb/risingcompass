/* === The Collective Library — public search UI === */

(() => {
  'use strict';

  const GATE_KEY = 'rc-library-preview-access';
  const GATE_PASSWORD = 'enter';

  const CHARGE_LABELS = {
    violet: 'Ascended', blue: 'Elevated', green: 'Decent',
    orange: 'Degraded', red: 'Corrupted',
  };
  const COLOR_HEX = {
    violet: '#aa54ff', blue: '#3388ff', green: '#33cc55',
    orange: '#ffbb33', red: '#ff3333',
  };
  const SOURCE_LABELS = {
    compass: 'Chart',
    library: 'Archive',
    submitted: 'Crowd',
    stream: 'Stream',
  };

  const $ = (s) => document.querySelector(s);
  const gate = $('#lib-gate');
  const gateForm = $('#lib-gate-form');
  const gateInput = $('#lib-gate-input');
  const gateError = $('#lib-gate-error');
  const header = $('#lib-header');
  const main = $('#main');
  const q = $('#lib-q');
  const tierSel = $('#lib-tier');
  const sourceSel = $('#lib-source');
  const chargeMin = $('#lib-charge-min');
  const chargeMax = $('#lib-charge-max');
  const contamSel = $('#lib-contam');
  const sortSel = $('#lib-sort');
  const meta = $('#lib-results-meta');
  const results = $('#lib-results');

  function escapeHtml(str) {
    if (str == null) return '';
    const d = document.createElement('div');
    d.textContent = String(str);
    return d.innerHTML;
  }

  function artistHtml(name, slug, className) {
    const inner = escapeHtml(name || '-');
    if (slug) {
      return `<a class="${className} artist-link" href="/artists/${encodeURIComponent(slug)}" onclick="event.stopPropagation();">${inner}</a>`;
    }
    return `<span class="${className}">${inner}</span>`;
  }

  // --- Gate ---
  function openLibrary() {
    gate.style.display = 'none';
    header.hidden = false;
    main.hidden = false;
    attachControlListeners();
    runSearch();
  }

  if (sessionStorage.getItem(GATE_KEY) === '1') {
    openLibrary();
  } else {
    gate.style.display = '';
    gateForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const v = (gateInput.value || '').trim().toLowerCase();
      if (v === GATE_PASSWORD) {
        sessionStorage.setItem(GATE_KEY, '1');
        openLibrary();
      } else {
        gateError.hidden = false;
        gateInput.select();
      }
    });
  }

  // --- Search ---
  let searchTimer = null;
  function attachControlListeners() {
    q.addEventListener('input', debounceSearch);
    [tierSel, sourceSel, contamSel, sortSel].forEach(el => el.addEventListener('change', runSearch));
    [chargeMin, chargeMax].forEach(el => el.addEventListener('change', runSearch));
  }

  function debounceSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 240);
  }

  function collectParams() {
    const [sort_by, sort_dir] = (sortSel.value || 'created_at|desc').split('|');
    const params = {
      q: q.value.trim() || undefined,
      source: sourceSel.value || undefined,
      tier: tierSel.value || undefined,
      charge_min: chargeMin.value !== '' ? chargeMin.value : undefined,
      charge_max: chargeMax.value !== '' ? chargeMax.value : undefined,
      contaminated: contamSel.value || undefined,
      sort_by, sort_dir,
      limit: 50,
    };
    return params;
  }

  async function runSearch() {
    meta.textContent = 'Searching…';
    results.innerHTML = '';
    let data;
    try {
      data = await ArtistsAPI.searchLibrary(collectParams());
    } catch (err) {
      meta.innerHTML = `<span class="lib-err">Search failed: ${escapeHtml(err.message)}</span>`;
      return;
    }

    const items = data.items || [];
    const total = data.total || 0;

    if (!items.length) {
      meta.textContent = '';
      results.innerHTML = `
        <div class="lib-empty">
          <p>No songs match.</p>
          <p class="lib-empty-cta">Help build the corpus. <a href="/lyrical-charger/">Submit lyrics via Lyrical Charger</a>.</p>
        </div>
      `;
      return;
    }

    const hidden = data.hidden_count || 0;
    const isFree = !!data.free_tier;
    const metaBits = [`<strong>${items.length.toLocaleString()}</strong> of <strong>${total.toLocaleString()}</strong> results shown`];
    if (hidden > 0 && isFree) {
      metaBits.push(`<span class="lib-paywall-hint">${hidden.toLocaleString()} more behind the wall — full access coming soon.</span>`);
    } else if (hidden > 0) {
      metaBits.push(`<span class="lib-paywall-hint">${hidden.toLocaleString()} more — refine your filters or sort.</span>`);
    }
    meta.innerHTML = metaBits.join(' &middot; ');

    results.innerHTML = items.map(renderCard).join('');
  }

  function renderCard(it) {
    const color = COLOR_HEX[it.rubric_color] || '#888';
    const tier = CHARGE_LABELS[it.rubric_color] || (it.rubric_color || 'Uncalibrated');
    const sign = it.charge_value != null ? (it.charge_value > 0 ? '+' : '') : '';
    const charge = it.charge_value != null ? `${sign}${it.charge_value}` : '—';
    const sourceLabel = SOURCE_LABELS[it.song_source] || it.song_source || '';
    const link = it.slug ? `/songs/${encodeURIComponent(it.slug)}` : null;
    const titleHtml = link
      ? `<a href="${link}" class="lib-card-title-link">${escapeHtml(it.title || '(untitled)')}</a>`
      : `<span>${escapeHtml(it.title || '(untitled)')}</span>`;
    const contamBadge = it.contaminated
      ? `<span class="lib-contam" title="${escapeHtml(it.contamination_note || 'Contamination flagged')}">CONTAM</span>`
      : '';
    return `
      <article class="lib-card" data-tier="${escapeHtml(it.rubric_color || 'none')}">
        <div class="lib-card-head">
          <div class="lib-card-identity">
            <div class="lib-card-title">${titleHtml}</div>
            <div class="lib-card-artist">${it.artist ? artistHtml(it.artist, it.artist_slug, 'lib-card-artist-link') : '&mdash;'}</div>
          </div>
          <div class="lib-card-tier" style="color:${color}">
            <span class="lib-card-tier-label">${escapeHtml(tier)}</span>
            <span class="lib-card-charge">${charge}</span>
          </div>
        </div>
        ${it.charge_summary ? `<p class="lib-card-summary">${escapeHtml(it.charge_summary)}</p>` : ''}
        <div class="lib-card-foot">
          <span class="lib-card-source">${escapeHtml(sourceLabel)}</span>
          ${contamBadge}
        </div>
      </article>
    `;
  }
})();
