/* The section index: every THEME, with the topics filed under it.
 * Themes are the parent tier, so they lead. */
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

  // Charge to spectrum colour, the same ramp the compass uses, so a topic's
  // chip carries its reading rather than a decorative accent.
  const SPECTRUM = ['#aa54ff', '#3388ff', '#33cc55', '#ffbb33', '#ff3333'];
  function chargeColor(v) {
    if (v == null) return '#888';
    const t = Math.max(0, Math.min(1, (100 - v) / 200));
    const seg = t * (SPECTRUM.length - 1);
    const i = Math.min(SPECTRUM.length - 2, Math.floor(seg));
    const f = seg - i;
    const hex = (h) => [1, 3, 5].map(k => parseInt(h.substr(k, 2), 16));
    const a = hex(SPECTRUM[i]), b = hex(SPECTRUM[i + 1]);
    const mix = a.map((c, k) => Math.round(c + (b[k] - c) * f));
    return `rgb(${mix.join(',')})`;
  }

  const rowsTotal = (data) =>
    (data.themes || []).reduce((n, t) => n + (t.topics || []).length, 0);

  async function init() {
    const host = document.getElementById('theme-list');
    if (!host) return;
    let data;
    try {
      data = await API.get('/api/topics');
    } catch (err) {
      console.warn('Topics index unavailable:', err);
      host.innerHTML = '<p class="direct-answer">The topic index is unavailable right now.</p>';
      return;
    }

    const intro = document.getElementById('index-intro');
    if (intro && data.library && data.library.songs) {
      intro.textContent = `The compass sorts what songs are about into `
        + `${data.themes.length} themes, holding ${rowsTotal(data)} topics across `
        + `${data.library.songs.toLocaleString()} calibrated songs. Each theme reads its own way.`;
    }

    host.innerHTML = (data.themes || []).filter(t => t.topics.length).map(theme => `
      <section class="song-section song-section--full song-break">
        <h2><a class="topic-theme-link" href="/themes/${encodeURIComponent(theme.slug)}">${esc(theme.label)}</a></h2>
        <p class="topic-theme-count">${theme.topics.length} topics, ${theme.songs} song credits.</p>
        <ul class="topic-index-grid">
          ${theme.topics.map(t => `
            <li class="topic-index-card">
              <a class="topic-index-link" href="/topics/${encodeURIComponent(t.slug)}">
                <span class="topic-index-name">${esc(t.label)}</span>
                <span class="topic-index-meta">
                  <span class="topic-index-charge" style="color:${chargeColor(t.avg_charge)}">${signed(t.avg_charge)} avg</span>
                  <span class="topic-index-songs">${t.songs} songs</span>
                </span>
              </a>
            </li>`).join('')}
        </ul>
      </section>`).join('');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
