/* === Ether Art Chart — homepage card renderer ===
 *
 * Renders today's top 20 (or an arbitrary date / year) as
 * "[rank]  [deadpan line]  ·  [topic chip]" rows into the card slot
 * to the right of Daily Top 20 Songs.
 *
 * Modes:
 *   render()                          → today
 *   render({mode:'today'})            → today
 *   render({mode:'date', date:'...'}) → specific YYYY-MM-DD
 *   render({mode:'year', year:1986})  → year aggregate (top_20_deadpan)
 *
 * Forward-only state: rows that pre-date the ether tagger have
 * `deadpan_line: null` and render as the song title in dim style with
 * an "untagged" pill, so the card doesn't pretend the data is there.
 * Audit-flagged rows (`topics: []` after the agent ran) render the
 * deadpan line with no chip — that's the spec.
 *
 * Tier accents come from rubric_color so the row's left tick matches
 * the corresponding row on the Daily Top 20 card. Year mode has no
 * per-song rubric color, so the tick stays transparent there — that
 * mirrors how the year reading panel treats per-song coloring.
 */
const EtherArtChart = (() => {
  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };

  const DEFAULT_DESC = "A second view on the same top 20 — songs named for what they are, plus the topics pulled through the ether.";

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function artistHtml(name, slug, className) {
    const inner = escapeHtml(name);
    if (slug) {
      return `<a class="${className} artist-link" href="/artists/${encodeURIComponent(slug)}" onclick="event.stopPropagation();">${inner}</a>`;
    }
    return `<span class="${className}">${inner}</span>`;
  }

  // The ether row template is canon in /js/chart-shell.js so the homepage card,
  // the iTunes panel, and the standalone chart pages all render an identical
  // row. This module keeps only the fetch/mode logic around it.
  function rowHtml(item) {
    return ChartShell.etherRowHtml(item);
  }

  function setDesc(text) {
    const panel = document.getElementById('ether-art-chart-panel');
    if (!panel) return;
    const desc = panel.querySelector('.card-desc');
    if (desc) desc.textContent = text;
  }

  function formatDate(isoDate) {
    if (!isoDate) return '';
    const [y, m, d] = isoDate.split('-');
    const dt = new Date(parseInt(y), parseInt(m) - 1, parseInt(d));
    return dt.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  }

  async function fetchByMode(opts) {
    const mode = opts && opts.mode ? opts.mode : 'today';
    if (mode === 'date' && opts.date) {
      const data = await API.getEtherDate(opts.date);
      return { items: data.items || [], label: formatDate(data.date || opts.date) };
    }
    if (mode === 'year' && opts.year) {
      const data = await API.getEtherYear(opts.year);
      // Year endpoint now returns rubric_color + position_letter so the
      // row template can color the tick and render double-A-side splits
      // (e.g. 9A / 9B) under the same chart slot.
      const items = (data.top_20_deadpan || []).map(x => ({
        position: x.position,
        position_letter: x.position_letter,
        title: x.title,
        artist: x.artist,
        artist_slug: x.artist_slug,
        song_slug: x.song_slug,
        deadpan_line: x.deadpan_line,
        dominant_topic: x.dominant_topic,
        rubric_color: x.rubric_color,
      }));
      return { items, label: String(opts.year) };
    }
    const data = await API.getEtherToday();
    return { items: data.items || [], label: data.date ? formatDate(data.date) : '' };
  }

  // Up-front label for the chosen mode — used so the description and the
  // empty-state notice both name the period the user asked for, even if
  // the fetch 404s or returns nothing.
  function previewLabel(opts) {
    const mode = opts && opts.mode ? opts.mode : 'today';
    if (mode === 'date' && opts.date) return formatDate(opts.date);
    if (mode === 'year' && opts.year) return String(opts.year);
    return '';
  }

  function applyDesc(mode, label) {
    if (mode === 'date' && label) {
      setDesc(`${label} — the same top 20 named for what the lyrics really say, with topic tags.`);
    } else if (mode === 'year' && label) {
      setDesc(`${label} — the year's top 20 named for what the lyrics really say.`);
    } else {
      setDesc(DEFAULT_DESC);
    }
  }

  async function render(opts) {
    const container = document.getElementById('ether-art-chart-content');
    if (!container) return;

    const mode = opts && opts.mode ? opts.mode : 'today';
    const upfrontLabel = previewLabel(opts || {});
    applyDesc(mode, upfrontLabel);

    try {
      const { items, label } = await fetchByMode(opts || {});
      // Refresh desc with the canonical label from the server (it may
      // resolve a slightly different formatted string than the up-front
      // preview, e.g. for /today).
      applyDesc(mode, label || upfrontLabel);

      if (!items.length) {
        const shown = label || upfrontLabel;
        container.innerHTML = `<div class="ether-empty">No reading available for ${escapeHtml(shown) || 'this period'}.</div>`;
        return;
      }

      container.innerHTML = `<ol class="ether-list">${items.map(rowHtml).join('')}</ol>`;
    } catch (err) {
      console.error('Failed to load ether art chart:', err);
      const shown = upfrontLabel;
      if (mode === 'date') {
        container.innerHTML = `<div class="ether-empty">No ether reading${shown ? ' for ' + escapeHtml(shown) : ' for that date'}.</div>`;
      } else if (mode === 'year') {
        container.innerHTML = `<div class="ether-empty">No reading available for ${escapeHtml(shown) || 'this year'}.</div>`;
      } else {
        container.innerHTML = `<div class="ether-empty">Could not load the ether-art chart.</div>`;
      }
    }
  }

  return { render };
})();
