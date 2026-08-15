/* The topics index: all 32 in one flat list.
 *
 * Sibling to the themes index, not a child of it. Topics live at /topics/{slug}
 * rather than nested under their theme's path, so the collection gets its own
 * front door; the parent theme rides along on each row as a label instead of
 * as a path segment.
 *
 * Ranked by song count, because that is the honest ordering of a corpus: the
 * subjects pop music returns to most often, first.
 */
(function () {
  'use strict';

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  const signed = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + v);

  // The compass ramp, so a topic's average carries its reading rather than a
  // decorative accent.
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

  async function init() {
    const host = document.getElementById('topic-list');
    if (!host) return;
    let data;
    try {
      data = await API.get('/api/topics');
    } catch (err) {
      console.warn('Topics index unavailable:', err);
      host.innerHTML = '<p class="direct-answer">The topic index is unavailable right now.</p>';
      return;
    }

    // Flatten the themed payload into one ranking, keeping each topic's theme
    // as a label so the relation survives the flattening.
    const rows = [];
    (data.themes || []).forEach(theme => {
      (theme.topics || []).forEach(t => {
        rows.push(Object.assign({}, t, { theme: { slug: theme.slug, label: theme.label } }));
      });
    });
    rows.sort((a, b) => b.songs - a.songs || a.label.localeCompare(b.label));

    const intro = document.getElementById('index-intro');
    if (intro && data.library && data.library.songs) {
      intro.textContent = `All ${rows.length} topics the compass tracks, ranked by how many of `
        + `${data.library.songs} calibrated songs carry each one.`;
    }

    host.innerHTML = `
      <section class="song-section song-section--full">
        <ul class="topic-rank" id="topic-rank">
          ${rows.map(t => `
            <li class="topic-rank-row">
              <a class="topic-rank-link" href="/topics/${encodeURIComponent(t.slug)}">
                <span class="topic-rank-charge" style="color:${chargeColor(t.avg_charge)}">${signed(t.avg_charge)}</span>
                <span class="topic-rank-id">
                  <span class="topic-rank-name">${esc(t.label)}</span>
                  <span class="topic-rank-theme">${esc(t.theme.label)}</span>
                </span>
                <span class="topic-rank-songs">${t.songs} songs</span>
              </a>
            </li>`).join('')}
        </ul>
      </section>`;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
