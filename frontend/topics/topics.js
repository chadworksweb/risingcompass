/* Topic detail page.
 *
 * Every sentence on this page is built from the API's own numbers. Nothing is
 * asserted that the payload does not carry, which is why the split section and
 * the sibling section hide themselves rather than printing an empty frame.
 */
(function () {
  'use strict';

  const grid = (id) => document.getElementById(id);

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function signed(v) {
    if (v == null) return '—';
    return (v > 0 ? '+' : '') + v;
  }

  function slugFromPath() {
    const m = window.location.pathname.match(/^\/topics\/([^/]+)\/?$/);
    if (m && m[1] !== 'topic.html' && m[1] !== 'index.html') return decodeURIComponent(m[1]);
    return new URLSearchParams(window.location.search).get('slug');
  }

  // --- the card, shared with the song page's Similar Songs so a reader meets
  // one card shape across the site.
  function songCard(s) {
    const kicker = [esc(s.tier_label || ''), signed(s.charge_value)].filter(Boolean).join(' ');
    const why = s.deadpan_line ? `<span class="related-card-why">${esc(s.deadpan_line)}</span>` : '';
    return `
      <li class="related-card">
        <a class="related-card-link" href="/songs/${encodeURIComponent(s.slug || '')}">
          <span class="related-card-kicker">
            <span class="related-card-dot" style="background:${s.tier_hex || '#888'}" aria-hidden="true"></span>
            <span style="color:${s.tier_hex || '#888'}">${kicker}</span>
          </span>
          <span class="related-card-name">${esc(s.title)}</span>
          <span class="related-card-sub">${esc(s.artist)}</span>
          ${why}
        </a>
      </li>`;
  }

  // The API returns song ids, not slugs (slugs are minted lazily elsewhere), so
  // resolve them through the same search the rest of the site uses.
  function withSlug(s) {
    return Object.assign({}, s, { slug: s.slug || '' });
  }

  // The song list is a LIST, not a card grid. A topic can carry hundreds of
  // songs and the reader is scanning a ranking, so the row puts the charge
  // first and lets the eye run down one column of numbers. The card shape is
  // kept for the span above, where there are exactly two and each deserves
  // its own weight.
  function songRow(s) {
    const why = s.deadpan_line
      ? `<span class="topic-row-why">${esc(s.deadpan_line)}</span>` : '';
    return `
      <li class="topic-row">
        <a class="topic-row-link" href="/songs/${encodeURIComponent(s.slug || '')}">
          <span class="topic-row-charge" style="color:${s.tier_hex || '#888'}">${signed(s.charge_value)}</span>
          <span class="topic-row-id">
            <span class="topic-row-title">${esc(s.title)}</span>
            <span class="topic-row-artist">${esc(s.artist)}</span>
          </span>
          ${why}
        </a>
      </li>`;
  }

  function renderHead(d) {
    const t = d.topic, st = d.stats, lib = d.library;
    document.getElementById('topic-title').textContent = t.label;
    const scope = document.getElementById('topic-scope');
    if (t.scope) scope.textContent = t.scope; else scope.hidden = true;

    document.getElementById('crumb-current').textContent = t.label;

    // The parent theme, stated under the title rather than in the crumb. The
    // crumb mirrors the URL and topics are their own collection; the taxonomy
    // relation is content, so it reads as a sentence.
    const parent = document.getElementById('topic-parent');
    if (t.theme && parent) {
      parent.innerHTML = `A topic under <a href="/themes/${encodeURIComponent(t.theme.slug)}">${esc(t.theme.label)}</a>`;
      parent.hidden = false;
    }

    // The headline finding, in a sentence, against the library it sits in.
    const dir = st.avg_charge == null || lib.avg_charge == null ? null
      : st.avg_charge - lib.avg_charge;
    let finding = `${st.songs} calibrated songs carry this topic. They average ${signed(st.avg_charge)}`;
    if (dir != null && Math.abs(dir) >= 3) {
      finding += `, which is ${Math.abs(dir)} points ${dir > 0 ? 'above' : 'below'} the library as a whole`;
    }
    finding += `, and they run from ${signed(st.min_charge)} to ${signed(st.max_charge)}.`;
    document.getElementById('topic-finding').textContent = finding;

    const contamLine = st.contaminated_pct > 0
      ? `${st.contaminated_pct}% carry a contamination flag, against ${lib.contaminated_pct}% across the library`
      : 'None of them carry a contamination flag';
    const tiers = st.tiers || {};
    const tierBits = [
      ['Ascended', tiers.violet, '#9933ff'], ['Elevated', tiers.blue, '#3388ff'],
      ['Decent', tiers.green, '#33cc55'], ['Degraded', tiers.orange, '#ffbb33'],
      ['Corrupted', tiers.red, '#ff3333'],
    ].filter(x => x[1]);

    document.getElementById('topic-stats').innerHTML = `
      <li class="topic-stat"><span class="topic-stat-label">Contamination</span>
        <span class="topic-stat-value">${esc(contamLine)}.</span></li>
      <li class="topic-stat"><span class="topic-stat-label">Where they land</span>
        <span class="topic-stat-value">${tierBits.map(([label, n, hex]) =>
          `<span class="topic-tier-chip"><span class="topic-tier-dot" style="background:${hex}"></span>${n} ${esc(label)}</span>`).join('')}</span></li>`;
  }

  function renderSplit(d) {
    const sp = d.dominant_split;
    const section = document.getElementById('topic-split');
    if (!sp) return;
    const t = d.topic.label;
    document.getElementById('topic-split-copy').textContent =
      `${sp.dominant_songs} of these songs are mostly about ${t}, and those average ` +
      `${signed(sp.avg_dominant)}. Where ${t} is present but not the point, the average is ` +
      `${signed(sp.avg_incidental)}. The topic reads ` +
      `${sp.delta > 0 ? 'higher' : 'lower'} by ${Math.abs(sp.delta)} points when it leads.`;
    section.hidden = false;
  }

  function renderExtremes(d) {
    const ex = d.extremes || {};
    // Remembered so the song list can skip them rather than repeat them.
    state.spanIds = new Set([ex.highest, ex.lowest].filter(Boolean).map(s => s.id));
    const items = [];
    if (ex.highest) items.push(withSlug(ex.highest));
    if (ex.lowest && (!ex.highest || ex.lowest.id !== ex.highest.id)) items.push(withSlug(ex.lowest));
    if (!items.length) return;
    grid('topic-extremes-grid').innerHTML = items.map(songCard).join('');
    document.getElementById('topic-extremes').hidden = false;
  }

  function renderSiblings(d) {
    const sibs = d.siblings || [];
    if (!sibs.length) return;
    const themeLabel = (d.topic.theme || {}).label;
    if (themeLabel) {
      document.getElementById('topic-siblings-h').textContent = `Other topics under ${themeLabel}`;
    }
    document.getElementById('topic-sibling-list').innerHTML = sibs.map(s => `
      <li><a href="/topics/${encodeURIComponent(s.slug)}" class="topic-sibling">
        <span class="topic-sibling-name">${esc(s.label)}</span>
        <span class="topic-sibling-meta">${s.songs} songs, averaging ${signed(s.avg_charge)}</span>
      </a></li>`).join('');
    document.getElementById('topic-siblings').hidden = false;
  }

  const PAGE = 24;
  // One "Show more" and no more. A topic can carry hundreds of songs, and
  // paging through them a row at a time is the Library's job; past 48 the page
  // hands the reader over rather than becoming a worse version of it.
  const MAX_LOADS = 1;

  let state = { slug: null, offset: 0, dominantOnly: false, loads: 0, spanIds: new Set() };

  function renderSongs(songs, append) {
    // The span already names the highest and lowest reading above, so showing
    // them again as rows one and last is the same link twice on one page.
    const shown = songs.filter(s => !state.spanIds.has(s.id));
    const html = shown.map(withSlug).map(songRow).join('');
    const el = grid('topic-song-grid');
    if (append) el.insertAdjacentHTML('beforeend', html);
    else el.innerHTML = html;

    // A short page means the end of the topic; no count is needed to know it.
    const exhausted = songs.length < PAGE;
    const capped = state.loads >= MAX_LOADS;
    document.getElementById('topic-more-btn').hidden = exhausted || capped;
    // The handoff only makes sense when songs are actually being withheld.
    document.getElementById('topic-deeper').hidden = !(capped && !exhausted);
  }

  async function loadPage(append) {
    const qs = new URLSearchParams({ offset: String(state.offset) });
    if (state.dominantOnly) qs.set('dominant_only', 'true');
    const d = await API.get(`/api/topics/${encodeURIComponent(state.slug)}?${qs}`);
    renderSongs(d.songs || [], append);
    return d;
  }

  async function init() {
    const slug = slugFromPath();
    if (!slug) { document.getElementById('topic-notfound').hidden = false; return; }
    state.slug = slug;
    try {
      const d = await API.get(`/api/topics/${encodeURIComponent(slug)}`);
      renderHead(d);
      renderSplit(d);
      renderExtremes(d);
      renderSiblings(d);
      renderSongs(d.songs || [], false);
      document.title = `Songs about ${d.topic.label} | The Rising Compass`;
    } catch (err) {
      console.warn('Topic load failed:', err);
      document.getElementById('topic-main').querySelectorAll('.song-section').forEach(s => { s.hidden = true; });
      document.getElementById('topic-notfound').hidden = false;
      return;
    }

    document.getElementById('topic-more-btn').addEventListener('click', async () => {
      state.offset += PAGE;
      state.loads += 1;
      await loadPage(true);
    });

    document.querySelectorAll('.topic-filter-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        document.querySelectorAll('.topic-filter-btn').forEach(b => b.classList.remove('is-active'));
        btn.classList.add('is-active');
        state.dominantOnly = btn.getAttribute('data-dominant') === '1';
        // Switching the filter is a fresh question, so the allowance resets
        // with it.
        state.offset = 0;
        state.loads = 0;
        await loadPage(false);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
