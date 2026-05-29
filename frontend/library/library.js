/* === The Collective Library — public search UI ===
 *
 * Renders the same column set the admin DB Explorer's virtual `all_songs`
 * table renders (db_search.py list_tables() -> all_songs.columns), minus
 * PII fields. Layout mirrors admin/db.html: one horizontally-scrolling
 * <table> with single-row entries, sticky thead, tinted tier cells.
 */

(() => {
  'use strict';

  // Desktop-only — coarse pointer (touch) or narrow viewport gets the static
  // block in the HTML; we don't attach controls. CSS already hides #main and
  // the page h1 and shows .lib-mobile-block on the same query, so this just
  // stops the JS path early.
  if (window.matchMedia('(max-width: 1100px), (pointer: coarse)').matches) {
    return;
  }

  // Tier label/color maps (parity with public song-page conventions).
  const TIER_LABELS = {
    violet: 'Ascended', blue: 'Elevated', green: 'Decent',
    orange: 'Degraded', red: 'Corrupted',
  };

  // Columns — order matches admin DB Explorer's all_songs view, with the
  // synthetic `charge` cell inserted immediately after `rubric_color`
  // (db.html dbEffectiveCols). PII columns (ip_address, device_id, email,
  // confidence) are absent from the API response on the public path.
  const COLS = [
    { name: 'title',                  type: 'text', kind: 'song_link' },
    { name: 'artist',                 type: 'text', kind: 'artist_link' },
    { name: 'rubric_color',           type: 'text', kind: 'tier' },
    { name: 'charge',                 type: 'integer', synthetic: true },
    { name: 'contaminated',           type: 'boolean', kind: 'contam' },
    { name: 'charge_summary',         type: 'text', kind: 'hover_pop' },
    { name: 'effects_prose',          type: 'text', kind: 'locked' },
    { name: 'societal_effects_prose', type: 'text', kind: 'locked' },
    { name: 'year',                   type: 'integer' },
    { name: 'created_at',             type: 'datetime' },
  ];

  const $ = (s) => document.querySelector(s);
  const main = $('#main');
  const q = $('#lib-q');
  const tierSel = $('#lib-tier');
  const sourceSel = $('#lib-source');
  const chargeMin = $('#lib-charge-min');
  const chargeMax = $('#lib-charge-max');
  const contamSel = $('#lib-contam');
  const sortSel = $('#lib-sort');
  const meta = $('#lib-results-meta');
  const table = $('#lib-table');
  const empty = $('#lib-empty');

  function escapeHtml(str) {
    if (str == null) return '';
    const d = document.createElement('div');
    d.textContent = String(str);
    return d.innerHTML;
  }

  // Open by default -- the preview gate (M0 "enter" password) was removed
  // in M4 when the subscription gate moved server-side. Free users see the
  // first 20 rows + paywall hint; Plus/Pro lifts the cap.
  attachControlListeners();
  renderHeader();
  runSearch();

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

  function renderHeader() {
    const thead = table.querySelector('thead');
    thead.innerHTML = '<tr>' + COLS.map(c => `<th class="lib-th">${escapeHtml(c.name)}</th>`).join('') + '</tr>';
  }

  // Set from each search response; gates whether the locked prose columns
  // render real text (paid subscribers) or the paywall treatment (free/anon).
  let viewerIsPaid = false;

  async function runSearch() {
    meta.textContent = 'Searching…';
    table.querySelector('tbody').innerHTML = '';
    empty.hidden = true;
    let data;
    try {
      data = await ArtistsAPI.searchLibrary(collectParams());
    } catch (err) {
      meta.innerHTML = `<span class="lib-err">Search failed: ${escapeHtml(err.message)}</span>`;
      return;
    }

    viewerIsPaid = data.user_subscription_tier === 'plus' || data.user_subscription_tier === 'pro';
    const items = data.items || [];
    const total = data.total || 0;

    if (!items.length) {
      meta.textContent = '';
      empty.innerHTML = `
        <p>No songs match.</p>
        <p class="lib-empty-cta">Help build the corpus. <a href="/lyrical-charger/">Submit lyrics via Lyrical Charger</a>.</p>
      `;
      empty.hidden = false;
      return;
    }

    const hidden = data.hidden_count || 0;
    const isFree = !!data.free_tier;
    const metaBits = [`<strong>${items.length.toLocaleString()}</strong> of <strong>${total.toLocaleString()}</strong> results shown`];
    if (hidden > 0 && isFree) {
      metaBits.push(
        `<span class="lib-paywall-hint">${hidden.toLocaleString()} more behind the paywall &mdash; ` +
        `<a href="/account/" class="lib-paywall-link">subscribe to see all</a>.</span>`
      );
    } else if (hidden > 0) {
      metaBits.push(`<span class="lib-paywall-hint">${hidden.toLocaleString()} more &mdash; refine your filters or sort.</span>`);
    }
    meta.innerHTML = metaBits.join(' &middot; ');

    table.querySelector('tbody').innerHTML = items.map(renderRow).join('');
  }

  // --- Row rendering ---

  function cellHtml(col, row) {
    if (col.synthetic && col.name === 'charge') {
      const cv = row.charge_value;
      const colorCls = row.rubric_color ? `color-${escapeHtml(String(row.rubric_color))}` : '';
      const display = cv == null
        ? '<span class="lib-null">—</span>'
        : ((cv > 0 ? '+' : '') + cv);
      return `<td class="lib-td-charge ${colorCls}">${display}</td>`;
    }

    if (col.kind === 'song_link') {
      const title = row.title;
      if (title == null) return `<td class="lib-td"><span class="lib-null">—</span></td>`;
      const slug = row.slug;
      const inner = escapeHtml(String(title));
      const href = slug ? `/songs/${encodeURIComponent(slug)}` : null;
      const titleAttr = escapeHtml(String(title));
      const content = href
        ? `<a href="${href}" class="lib-link">${inner}</a>`
        : inner;
      return `<td class="lib-td" title="${titleAttr}">${content}</td>`;
    }

    if (col.kind === 'artist_link') {
      const name = row.artist;
      if (name == null) return `<td class="lib-td"><span class="lib-null">—</span></td>`;
      const inner = escapeHtml(String(name));
      const titleAttr = escapeHtml(String(name));
      const slug = row.artist_slug;
      const content = slug
        ? `<a href="/artists/${encodeURIComponent(slug)}" class="lib-link">${inner}</a>`
        : inner;
      return `<td class="lib-td" title="${titleAttr}">${content}</td>`;
    }

    if (col.kind === 'tier') {
      const v = row.rubric_color;
      if (!v) return `<td class="lib-td"><span class="lib-null">—</span></td>`;
      const colorCls = `color-${escapeHtml(String(v))}`;
      const label = TIER_LABELS[v] || v;
      return `<td class="lib-td ${colorCls}" title="${escapeHtml(String(v))}">${escapeHtml(label)}</td>`;
    }

    if (col.kind === 'contam') {
      const flagged = !!row.contaminated;
      const note = row.contamination_note;
      if (!flagged) {
        return `<td class="lib-td">no</td>`;
      }
      // Flagged with no note: just "yes". Flagged with a note: "yes" becomes
      // a hover trigger that reveals the note in the same tooltip style.
      if (note == null || String(note).trim() === '') {
        return `<td class="lib-td">yes</td>`;
      }
      return `<td class="lib-td">
        <span class="lib-hover-wrap" tabindex="0">
          <span class="lib-hover-trigger lib-hover-trigger--inline">yes</span>
          <span class="lib-hover-tip" role="tooltip">
            <span class="lib-hover-tip-label">contamination_note</span>
            <span class="lib-hover-tip-body">${escapeHtml(String(note))}</span>
          </span>
        </span>
      </td>`;
    }

    if (col.kind === 'hover_pop') {
      const v = row[col.name];
      if (v == null || String(v).trim() === '') {
        return `<td class="lib-td"><span class="lib-null">—</span></td>`;
      }
      // Pure-CSS hover tooltip: trigger pill in the cell, absolutely-positioned
      // tip revealed on :hover / :focus-within of the wrap.
      const labels = {
        charge_summary: 'summary',
        effects_prose: 'effects',
        societal_effects_prose: 'societal',
      };
      const labelText = labels[col.name] || col.name;
      return `<td class="lib-td">
        <span class="lib-hover-wrap" tabindex="0">
          <span class="lib-hover-trigger">${labelText}</span>
          <span class="lib-hover-tip" role="tooltip">
            <span class="lib-hover-tip-label">${escapeHtml(col.name)}</span>
            <span class="lib-hover-tip-body">${escapeHtml(String(v))}</span>
          </span>
        </span>
      </td>`;
    }

    if (col.kind === 'locked') {
      const labels = {
        effects_prose: 'effects',
        societal_effects_prose: 'societal',
      };
      const labelText = labels[col.name] || col.name;
      // Paid subscribers read the prose; free/anon see the paywall treatment.
      // The backend already caps free results, so this is display-only.
      if (viewerIsPaid) {
        const v = row[col.name];
        if (v == null || String(v).trim() === '') {
          return `<td class="lib-td"><span class="lib-null">—</span></td>`;
        }
        return `<td class="lib-td">
          <span class="lib-hover-wrap lib-hover-wrap--right" tabindex="0">
            <span class="lib-hover-trigger">${labelText}</span>
            <span class="lib-hover-tip" role="tooltip">
              <span class="lib-hover-tip-label">${escapeHtml(col.name)}</span>
              <span class="lib-hover-tip-body">${escapeHtml(String(v))}</span>
            </span>
          </span>
        </td>`;
      }
      // Subscription-gated: trigger shows the prose name with a locked
      // treatment; tooltip explains the paywall. Right-aligned since both
      // locked columns sit in the right half of the table.
      return `<td class="lib-td">
        <span class="lib-hover-wrap lib-hover-wrap--right" tabindex="0">
          <span class="lib-hover-trigger lib-hover-trigger--locked">${labelText}</span>
          <span class="lib-hover-tip" role="tooltip">
            <span class="lib-hover-tip-label">${escapeHtml(col.name)}</span>
            <span class="lib-hover-tip-body">Locked. Sign up for a plan to read.</span>
          </span>
        </span>
      </td>`;
    }

    const v = row[col.name];
    if (v == null) {
      const cls = col.wide ? 'lib-td lib-td-wide' : 'lib-td';
      return `<td class="${cls}"><span class="lib-null">—</span></td>`;
    }
    if (col.type === 'boolean') {
      return `<td class="lib-td">${v ? 'yes' : 'no'}</td>`;
    }
    if (col.type === 'datetime' && typeof v === 'string') {
      // Trim ISO microseconds for a single-line display; tooltip keeps the full value.
      const trimmed = v.replace('T', ' ').replace(/\.\d+$/, '').replace(/(\+\d\d:?\d\d|Z)$/, '');
      return `<td class="lib-td" title="${escapeHtml(v)}">${escapeHtml(trimmed)}</td>`;
    }
    const text = String(v);
    const cls = col.wide ? 'lib-td lib-td-wide' : 'lib-td';
    return `<td class="${cls}" title="${escapeHtml(text)}">${escapeHtml(text)}</td>`;
  }

  function renderRow(row) {
    return '<tr class="lib-tr">' + COLS.map(c => cellHtml(c, row)).join('') + '</tr>';
  }
})();
