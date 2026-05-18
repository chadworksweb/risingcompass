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

  function topicChipHtml(topic) {
    if (!topic) return '';
    const t = String(topic).replace(/-/g, ' ');
    return `<span class="ether-chip">${escapeHtml(t)}</span>`;
  }

  function rowHtml(item) {
    const tierHex = COLOR_HEX[item.rubric_color] || 'transparent';
    const tickStyle = `border-left:9px solid ${tierHex};`;

    const songHref = item.song_slug ? `/songs/${encodeURIComponent(item.song_slug)}` : null;

    if (!item.deadpan_line) {
      // Pre-tagger row — show the title, dim style, "untagged" hint.
      const titleHtml = songHref
        ? `<a href="${songHref}" class="ether-title-link">${escapeHtml(item.title)}</a>`
        : `<span class="ether-title-link">${escapeHtml(item.title)}</span>`;
      return `
        <li class="ether-row ether-row--untagged" style="${tickStyle}">
          <span class="ether-pos">${item.position}</span>
          <div class="ether-text">
            <div class="ether-deadpan">${titleHtml}</div>
            <div class="ether-meta">${artistHtml(item.artist, item.artist_slug, 'ether-meta-artist')} <span class="ether-untagged-pill">untagged</span></div>
          </div>
        </li>`;
    }

    const titleHtml = songHref
      ? `<a href="${songHref}" class="ether-title-link">${escapeHtml(item.title)}</a>`
      : escapeHtml(item.title);

    return `
      <li class="ether-row" style="${tickStyle}">
        <span class="ether-pos">${item.position}</span>
        <div class="ether-text">
          <div class="ether-deadpan">${escapeHtml(item.deadpan_line)}</div>
          <div class="ether-meta">
            <span class="ether-meta-title">${titleHtml}</span>
            <span class="ether-meta-sep">·</span>
            ${artistHtml(item.artist, item.artist_slug, 'ether-meta-artist')}
            ${item.dominant_topic ? `<span class="ether-meta-sep">·</span>${topicChipHtml(item.dominant_topic)}` : ''}
          </div>
        </div>
      </li>`;
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
      // Adapt EtherYearOut.top_20_deadpan into the row shape (no rubric_color).
      const items = (data.top_20_deadpan || []).map(x => ({
        position: x.position,
        title: x.title,
        artist: x.artist,
        artist_slug: x.artist_slug,
        song_slug: x.song_slug,
        deadpan_line: x.deadpan_line,
        dominant_topic: x.dominant_topic,
        rubric_color: null,
      }));
      return { items, label: String(opts.year) };
    }
    const data = await API.getEtherToday();
    return { items: data.items || [], label: data.date ? formatDate(data.date) : '' };
  }

  async function render(opts) {
    const container = document.getElementById('ether-art-chart-content');
    if (!container) return;

    const mode = opts && opts.mode ? opts.mode : 'today';

    try {
      const { items, label } = await fetchByMode(opts || {});

      if (mode === 'date' && label) {
        setDesc(`${label} — the same top 20 named through the ether, with topic tags.`);
      } else if (mode === 'year' && label) {
        setDesc(`${label} — the year's top 20 named through the ether.`);
      } else {
        setDesc(DEFAULT_DESC);
      }

      if (!items.length) {
        container.innerHTML = `<div class="ether-empty">No reading available for ${escapeHtml(label) || 'this period'}.</div>`;
        return;
      }

      container.innerHTML = `<ol class="ether-list">${items.map(rowHtml).join('')}</ol>`;
    } catch (err) {
      console.error('Failed to load ether art chart:', err);
      if (mode === 'date') {
        container.innerHTML = `<div class="ether-empty">No ether reading for that date.</div>`;
      } else {
        container.innerHTML = `<div class="ether-empty">Could not load the ether-art chart.</div>`;
      }
    }
  }

  return { render };
})();
