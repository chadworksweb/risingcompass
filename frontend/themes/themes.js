/* Theme detail page: the parent of topics.
 *
 * COUNTING RULE: a song registers ONCE in a theme, however many of that
 * theme's topics it carries. So the theme's count is lower than the sum of its
 * topic counts, and that is correct rather than a discrepancy to explain away.
 * Every figure here is over distinct songs.
 */
(function () {
  'use strict';

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
    const m = window.location.pathname.match(/^\/themes\/([^/]+)\/?$/);
    if (m && m[1] !== 'theme.html') return decodeURIComponent(m[1]);
    return new URLSearchParams(window.location.search).get('slug');
  }

  const SPECTRUM = ['#aa54ff', '#3388ff', '#33cc55', '#ffbb33', '#ff3333'];
  function chargeColor(v) {
    if (v == null) return '#888';
    const t = Math.max(0, Math.min(1, (100 - v) / 200));
    const seg = t * (SPECTRUM.length - 1);
    const i = Math.min(SPECTRUM.length - 2, Math.floor(seg));
    const f = seg - i;
    const hex = (h) => [1, 3, 5].map(k => parseInt(h.substr(k, 2), 16));
    const a = hex(SPECTRUM[i]), b = hex(SPECTRUM[i + 1]);
    return `rgb(${a.map((c, k) => Math.round(c + (b[k] - c) * f)).join(',')})`;
  }

  function topicCard(t) {
    return `
      <li class="topic-index-card">
        <a class="topic-index-link" href="/topics/${encodeURIComponent(t.slug)}">
          <span class="topic-index-name">${esc(t.label)}</span>
          <span class="topic-index-meta">
            <span class="topic-index-charge" style="color:${chargeColor(t.avg_charge)}">${signed(t.avg_charge)} avg</span>
            <span class="topic-index-songs">${t.songs} songs</span>
          </span>
        </a>
      </li>`;
  }

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

  async function init() {
    const slug = slugFromPath();
    if (!slug) { document.getElementById('theme-notfound').hidden = false; return; }

    let d;
    try {
      d = await API.get(`/api/themes/${encodeURIComponent(slug)}`);
    } catch (err) {
      console.warn('Theme load failed:', err);
      document.querySelectorAll('#topic-main .song-section').forEach(s => { s.hidden = true; });
      document.getElementById('theme-notfound').hidden = false;
      return;
    }

    const st = d.stats, lib = d.library;
    document.getElementById('theme-title').textContent = d.theme.label;
    document.getElementById('crumb-theme').textContent = d.theme.label;
    document.title = `Songs about ${d.theme.label} | The Rising Compass`;

    const nT = d.topics.length;
    let finding = nT === 1
      ? `One topic sits under this theme, carried by ${st.songs.toLocaleString()} calibrated songs`
      : `${nT} topics sit under this theme, carried by ${st.songs.toLocaleString()} calibrated songs`;
    finding += `. They average ${signed(st.avg_charge)}`;
    if (st.avg_charge != null && lib.avg_charge != null) {
      const delta = st.avg_charge - lib.avg_charge;
      if (Math.abs(delta) >= 3) {
        finding += `, ${Math.abs(delta)} points ${delta > 0 ? 'above' : 'below'} the library as a whole`;
      }
    }
    finding += `, and run from ${signed(st.min_charge)} to ${signed(st.max_charge)}.`;
    document.getElementById('theme-finding').textContent = finding;

    const tiers = st.tiers || {};
    const tierBits = [
      ['Ascended', tiers.violet, '#9933ff'], ['Elevated', tiers.blue, '#3388ff'],
      ['Decent', tiers.green, '#33cc55'], ['Degraded', tiers.orange, '#ffbb33'],
      ['Corrupted', tiers.red, '#ff3333'],
    ].filter(x => x[1]);
    const contam = st.contaminated_pct > 0
      ? `${st.contaminated_pct}% carry a contamination flag, against ${lib.contaminated_pct}% across the library`
      : 'None of them carry a contamination flag';
    document.getElementById('theme-stats').innerHTML = `
      <li class="topic-stat"><span class="topic-stat-label">Contamination</span>
        <span class="topic-stat-value">${esc(contam)}.</span></li>
      <li class="topic-stat"><span class="topic-stat-label">Where they land</span>
        <span class="topic-stat-value">${tierBits.map(([label, n, hex]) =>
          `<span class="topic-tier-chip"><span class="topic-tier-dot" style="background:${hex}"></span>${n} ${esc(label)}</span>`).join('')}</span></li>`;

    document.getElementById('theme-topic-grid').innerHTML = d.topics.map(topicCard).join('');

    if ((d.also_topics || []).length) {
      document.getElementById('theme-also-grid').innerHTML = d.also_topics.map(topicCard).join('');
      document.getElementById('theme-also').hidden = false;
    }

    const ex = d.extremes || {};
    const ends = [];
    if (ex.highest) ends.push(ex.highest);
    if (ex.lowest && (!ex.highest || ex.lowest.id !== ex.highest.id)) ends.push(ex.lowest);
    if (ends.length) {
      document.getElementById('theme-extremes-grid').innerHTML = ends.map(songCard).join('');
      document.getElementById('theme-extremes').hidden = false;
    }

    // Related themes are the ones a topic actually crosses into, and the count
    // is what earns the word "related" rather than asserting it.
    const rel = d.related_themes || [];
    if (rel.length) {
      document.getElementById('theme-related-list').innerHTML = rel.map(s => `
        <li><a href="/themes/${encodeURIComponent(s.slug)}" class="topic-sibling">
          <span class="topic-sibling-name">${esc(s.label)}</span>
          <span class="topic-sibling-meta">${s.shared_topics === 1
            ? '1 topic crosses between them'
            : `${s.shared_topics} topics cross between them`}</span>
        </a></li>`).join('');
      document.getElementById('theme-related').hidden = false;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
