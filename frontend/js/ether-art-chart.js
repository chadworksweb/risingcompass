/* === Ether Art Chart — homepage card renderer ===
 *
 * Fetches /api/ether-art-chart/today and renders today's top 20 as
 * "[rank]  [deadpan line]  ·  [topic chip]" rows into the card slot
 * to the right of Daily Top 20 Songs.
 *
 * Forward-only state: rows that pre-date the ether tagger have
 * `deadpan_line: null` and render as the song title in dim style with
 * an "untagged" pill, so the card doesn't pretend the data is there.
 * Audit-flagged rows (`topics: []` after the agent ran) render the
 * deadpan line with no chip — that's the spec.
 *
 * Tier accents come from rubric_color so the row's left tick matches
 * the corresponding row on the Daily Top 20 card.
 */
const EtherArtChart = (() => {
  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function topicChipHtml(topic) {
    if (!topic) return '';
    const t = String(topic).replace(/-/g, ' ');
    return `<span class="ether-chip">${escapeHtml(t)}</span>`;
  }

  function rowHtml(item) {
    const tierHex = COLOR_HEX[item.rubric_color] || 'transparent';
    const tickStyle = `border-left:3px solid ${tierHex};`;

    const songHref = item.song_slug ? `/songs/${encodeURIComponent(item.song_slug)}/` : null;

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
            <div class="ether-meta">${escapeHtml(item.artist)} <span class="ether-untagged-pill">untagged</span></div>
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
            <span class="ether-meta-artist">${escapeHtml(item.artist)}</span>
            ${item.dominant_topic ? `<span class="ether-meta-sep">·</span>${topicChipHtml(item.dominant_topic)}` : ''}
          </div>
        </div>
      </li>`;
  }

  async function render() {
    const container = document.getElementById('ether-art-chart-content');
    if (!container) return;

    try {
      const data = await API.getEtherToday();
      if (!data || !data.items || !data.items.length) {
        container.innerHTML = `
          <div class="ether-empty">No reading available yet.</div>`;
        return;
      }

      const rows = data.items.map(rowHtml).join('');
      const taggedCount = data.items.filter((i) => i.deadpan_line).length;
      const total = data.items.length;
      const untaggedNote = taggedCount < total
        ? `<p class="ether-status">${taggedCount} of ${total} tagged · the rest will fill in as new songs come through, or the deferred backfill runs.</p>`
        : '';

      container.innerHTML = `${untaggedNote}<ol class="ether-list">${rows}</ol>`;
    } catch (err) {
      console.error('Failed to load ether art chart:', err);
      container.innerHTML = `
        <div class="ether-empty">Could not load the ether-art chart.</div>`;
    }
  }

  return { render };
})();
