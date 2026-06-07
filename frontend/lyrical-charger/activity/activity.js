/* === Charger Activity — three public feeds ===
 *
 * New Additions / Recently Calibrated / Most Calibrated, all from
 * /api/charger-activity/*. First paint is one /overview call; the Most column's
 * window toggle re-queries the most-run feed.
 */

(() => {
  'use strict';

  const OVERVIEW = 10;      // rows per column (feeds are capped at 10 each)

  const cols = {
    'new-additions': { el: document.getElementById('ca-new'),    metric: 'first_calibrated_at' },
    'recent':        { el: document.getElementById('ca-recent'), metric: 'last_calibrated_at' },
    'most-run':      { el: document.getElementById('ca-most'),   metric: 'run_count' },
  };
  let mostWindow = 'all';

  function escapeHtml(str) {
    if (str == null) return '';
    const d = document.createElement('div');
    d.textContent = String(str);
    return d.innerHTML;
  }

  // "3 hours ago", "2 days ago", "Mar 4" — compact relative for feed timestamps.
  function relTime(iso) {
    if (!iso) return '';
    const then = new Date(iso);
    if (isNaN(then)) return '';
    const secs = Math.floor((Date.now() - then.getTime()) / 1000);
    if (secs < 60) return 'just now';
    const mins = Math.floor(secs / 60);
    if (mins < 60) return mins + (mins === 1 ? ' min ago' : ' mins ago');
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + (hrs === 1 ? ' hour ago' : ' hours ago');
    const days = Math.floor(hrs / 24);
    if (days < 30) return days + (days === 1 ? ' day ago' : ' days ago');
    return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function metricHtml(feed, item) {
    if (feed === 'most-run') {
      const n = item.run_count || 0;
      return `<span class="ca-metric">${n.toLocaleString()} ${n === 1 ? 'charge' : 'charges'}</span>`;
    }
    const iso = item[cols[feed].metric];
    const verb = feed === 'new-additions' ? 'Added' : 'Calibrated';
    return `<span class="ca-metric" title="${escapeHtml(iso || '')}">${verb} ${escapeHtml(relTime(iso))}</span>`;
  }

  function cardHtml(feed, item, rank) {
    const tier = item.rubric_color || 'green';     // .atr-dot fallback parity
    const cv = item.charge_value;
    const charge = cv == null ? '' : (cv > 0 ? '+' : '') + cv;
    const href = item.slug ? `/songs/${encodeURIComponent(item.slug)}` : null;

    const hasRank = feed === 'most-run';
    const rankHtml = hasRank ? `<span class="ca-rank">${rank}</span>` : '';
    // Tier dot, contam "!" badge, dogma scroll badge — same markup as the
    // album-track-result row (charger.js).
    const dot = `<span class="ca-dot" style="background: var(--rc-${escapeHtml(tier)})"></span>`;
    // Contamination = the homepage radioactive icon (.contam-icon, main.css).
    const contam = item.contaminated
      ? '<span class="contam-icon" title="Contaminated">&#x2622;</span>' : '';
    const dogma = item.dogma_referenced
      ? '<span class="ca-dogma" title="Dogma referenced">&#x1F4DC;</span>' : '';
    const chargeHtml = charge !== ''
      ? `<span class="ca-charge color-${escapeHtml(tier)}">${escapeHtml(charge)}</span>` : '';

    const inner = `
      <span class="ca-line${hasRank ? ' has-rank' : ''}">
        ${rankHtml}${dot}
        <span class="ca-song">
          <span class="ca-song-title" title="${escapeHtml(item.title || '')}">${escapeHtml(item.title || 'Untitled')}</span>
          ${contam}${dogma}
        </span>
        ${chargeHtml}
      </span>
      <span class="ca-sub">
        <span class="ca-artist" title="${escapeHtml(item.artist || '')}">${escapeHtml(item.artist || 'Unknown')}</span>
        ${metricHtml(feed, item)}
      </span>`;
    return href
      ? `<li class="ca-card"><a class="ca-card-link" href="${href}">${inner}</a></li>`
      : `<li class="ca-card"><span class="ca-card-link">${inner}</span></li>`;
  }

  function renderInto(feed, items) {
    cols[feed].el.innerHTML = items.length
      ? items.map((it, i) => cardHtml(feed, it, i + 1)).join('')
      : '<li class="ca-empty">Nothing here yet. Be the first &mdash; <a href="/lyrical-charger/">run a song through the Lyrical Charger</a>.</li>';
  }

  function errInto(feed, msg) {
    cols[feed].el.innerHTML = `<li class="ca-empty ca-err">${escapeHtml(msg)}</li>`;
  }

  // --- Most-Calibrated window toggle ---
  async function reloadMost(win) {
    mostWindow = win;
    cols['most-run'].el.innerHTML = '<li class="ca-loading">Loading&hellip;</li>';
    let data;
    try {
      data = await API.getChargerActivityFeed('most-run', { window: win, limit: OVERVIEW, offset: 0 });
    } catch (err) {
      return errInto('most-run', 'Could not load this feed.');
    }
    renderInto('most-run', data.items || []);
  }

  function wireControls() {
    document.querySelectorAll('.ca-toggle-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.classList.contains('ca-toggle-active')) return;
        document.querySelectorAll('.ca-toggle-btn').forEach((b) => b.classList.remove('ca-toggle-active'));
        btn.classList.add('ca-toggle-active');
        reloadMost(btn.dataset.window);
      });
    });
  }

  // --- Initial paint: one /overview call ---
  async function init() {
    wireControls();
    let data;
    try {
      data = await API.getChargerActivityOverview(OVERVIEW);
    } catch (err) {
      ['new-additions', 'recent', 'most-run'].forEach((f) => errInto(f, 'Could not load activity.'));
      return;
    }
    renderInto('new-additions', data.new_additions || []);
    renderInto('recent', data.recent || []);
    renderInto('most-run', data.most_run || []);
  }

  init();
})();
