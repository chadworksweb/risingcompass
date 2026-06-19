/* === Chart Shell — the canon "chart reading" element ======================
 *
 * ONE renderer for the paired chart view used everywhere on the site: the
 * homepage daily reading, the homepage iTunes panel, and every standalone
 * /charts/* page. Left card = charge group + editorial + song list; right card
 * = the same songs through the Ether Art Chart (deadpan + topic) lens.
 *
 * Before this module the same visual was hand-copied three times (app.js
 * renderReading, app.js renderItunesPanel, charts/chart.js) and had already
 * drifted (different charge bands, three ether-row copies, violet #aa54ff vs
 * #9933ff). Everything now flows through here, so a new chart is a container +
 * a data source, not a line-by-line restyle.
 *
 * Self-contained on purpose (its own escapeHtml / formatDate / color table) so
 * a standalone chart page can load it with only api.js. app.js and
 * ether-art-chart.js delegate their row/charge/ether templates to it.
 *
 * Input contract — a normalized "reading" object:
 *   {
 *     date,                // YYYY-MM-DD (string) | null
 *     degree,              // compass_degree (number) | null
 *     charge,              // charge_level color key ('violet'..'red') | null
 *     contaminationCount,  // number
 *     editorial,           // string | null
 *     songs: [ ReadingSong-shaped, see songRowHtml/etherRowHtml ]
 *   }
 * Callers adapt their endpoint into this shape (the daily reading folds its
 * two-endpoint result; charts use the single snapshot, which already returns
 * the aggregate + ether fields per row).
 */
const ChartShell = (() => {
  'use strict';

  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };

  const CHARGE_LABELS = {
    violet: 'Ascended',
    blue: 'Elevated',
    green: 'Decent',
    orange: 'Degraded',
    red: 'Corrupted',
  };

  function escapeHtml(str) {
    if (str == null || str === '') return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
  }

  // Long-form date for the charge tab, e.g. "Monday, June 9, 2026" (matches the
  // homepage daily reading's reading-date tab).
  function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  }

  // Signed compass score from a 0..180 degree (0 = +100, 90 = 0, 180 = -100).
  function degreeToScore(degree) {
    if (degree == null) return '';
    const s = Math.round((90 - degree) * 100 / 90);
    return (s > 0 ? '+' : '') + s;
  }

  function chargeValueStr(v) {
    if (v == null) return '';
    return v > 0 ? '+' + v : String(v);
  }

  function artistHtml(name, slug, className) {
    const inner = escapeHtml(name);
    if (slug) {
      return `<a class="${className} artist-link" href="/artists/${encodeURIComponent(slug)}" onclick="event.stopPropagation();">${inner}</a>`;
    }
    return `<span class="${className}">${inner}</span>`;
  }

  // Position display merges chart_position + optional A/B letter (9A / 9B read
  // as one chart slot). Used by both list templates.
  function positionDisplay(song) {
    return `${song.position}${song.position_letter || ''}`;
  }

  // --- Charge group (date tab + score/label/meta bar) ----------------------
  // trailingHtml goes inside the charge row after the bar (the homepage daily
  // reading slots its calendar-toggle button there; charts pass nothing).
  function chargeGroupHtml(reading, trailingHtml) {
    if (reading.charge == null || reading.degree == null) return '';
    const hex = COLOR_HEX[reading.charge] || '#888';
    const label = CHARGE_LABELS[reading.charge] || reading.charge;
    const songCount = (reading.songs || []).length;
    const contam = reading.contaminationCount || 0;
    return `<div class="reading-charge-group">
      <div class="reading-date" style="background:${hex}">${escapeHtml(formatDate(reading.date))}</div>
      <div class="reading-charge-row">
        <div class="reading-charge-bar" style="background:${hex}">
          <div class="reading-charge-inner">
            <span class="reading-charge-score">${degreeToScore(reading.degree)}</span>
            <span class="reading-charge-label">${escapeHtml(label)}</span>
            <span class="reading-charge-meta">${songCount} songs &middot; ${contam} contaminated</span>
          </div>
        </div>
        ${trailingHtml || ''}
      </div>
    </div>`;
  }

  function editorialHtml(text) {
    if (!text) return '';
    return `<div class="reading-editorial">${escapeHtml(text)}</div>`;
  }

  // --- Song list (left card) -----------------------------------------------
  // The union row: charge-summary tooltip always, MEI (message/expression/
  // intention) + dogma surfaced when the source carries them (daily reading),
  // omitted when it doesn't (chart snapshots). preorder + instrumental honored.
  function songRowHtml(song) {
    const hasMEI = song.message_analysis || song.expression_analysis || song.intention_analysis;
    const hasSummary = song.charge_summary || song.contamination_note;
    const hasTooltip = hasMEI || hasSummary;

    let tooltipHtml = '';
    if (hasTooltip) {
      const songHex = COLOR_HEX[song.rubric_color] || '#888';
      const songLabel = CHARGE_LABELS[song.rubric_color] || song.rubric_color || '';
      const songScore = chargeValueStr(song.charge_value);

      let lines = `<div style="background:${songHex};color:var(--rc-bg-dark);font-family:var(--rc-font-mono);font-size:0.7rem;font-weight:700;letter-spacing:0.02em;padding:0.25rem 0.55rem;margin:-0.4rem -0.55rem 0.35rem;border-radius:4px 4px 0 0">${songScore} ${escapeHtml(songLabel)}</div>`;
      if (song.charge_summary) lines += `<div style="font-size:0.72rem;color:rgba(20,20,30,0.65);font-style:italic;line-height:1.4;margin-bottom:0.3rem;padding-bottom:0.25rem;border-bottom:1px solid rgba(0,0,0,0.06)">${escapeHtml(song.charge_summary)}</div>`;
      if (song.contaminated && song.contamination_note) lines += `<div class="mei-line mei-contam">&#x2622; ${escapeHtml(song.contamination_note)}</div>`;
      if (song.dogma_referenced && song.dogma_note) lines += `<div class="mei-line mei-dogma">&#x1F4DC; ${escapeHtml(song.dogma_note)}</div>`;

      const disputeParams = new URLSearchParams({ title: song.title, artist: song.artist, color: song.rubric_color || '', pos: song.position });
      if (song.message_analysis) disputeParams.set('ma', song.message_analysis);
      if (song.expression_analysis) disputeParams.set('ea', song.expression_analysis);
      if (song.intention_analysis) disputeParams.set('ia', song.intention_analysis);
      if (song.charge_summary) disputeParams.set('cs', song.charge_summary);
      lines += `<div class="mei-dispute"><a href="/misread-submission.html?${disputeParams.toString()}">Did we get it wrong?</a></div>`;
      tooltipHtml = `<div class="song-tooltip">${lines}</div>`;
    }

    const instrClass = song.instrumental ? ' instrumental' : '';
    const preClass = song.preorder ? ' preorder' : '';
    const luClass = song.lyrics_unavailable ? ' lyrics-unavailable' : '';
    // Link to the song page only once calibrated (the page exists then): a row
    // with no rubric_color has no reading yet.
    const songHref = (song.song_slug && song.rubric_color) ? `/songs/${encodeURIComponent(song.song_slug)}` : null;
    const titleHtml = songHref
      ? `<a href="${songHref}" class="song-title-link">${escapeHtml(song.title)}</a>`
      : escapeHtml(song.title);
    const dotColor = (song.instrumental || song.preorder || song.lyrics_unavailable) ? '' : (song.rubric_color || '');
    const preBadge = song.preorder
      ? '<span class="song-preorder" title="Charting on pre-order; not yet released, no reading yet">Pre-order</span>'
      : '';
    const luBadge = song.lyrics_unavailable
      ? '<span class="song-lyrics-unavailable" title="Released, but the lyrics are not available to read — so it carries no reading">Lyrics unavailable</span>'
      : '';

    return `
      <li class="song-item${hasTooltip ? ' has-tooltip' : ''}${instrClass}${preClass}${luClass}">
        <span class="song-pos">${positionDisplay(song)}</span>
        <span class="song-dot ${dotColor}"></span>
        <div class="song-info">
          <div class="song-title">${titleHtml}</div>
          <div class="song-artist">${artistHtml(song.artist, song.artist_slug, 'song-artist-name')}</div>
        </div>
        <div class="song-actions">
          ${preBadge}
          ${luBadge}
          ${song.contaminated ? '<span class="song-contam" aria-hidden="true">&#x2622;</span>' : ''}
          ${song.dogma_referenced ? '<span class="song-dogma" aria-hidden="true" title="Dogma referenced">&#x1F4DC;</span>' : ''}
          ${hasTooltip ? `<button class="song-comment-btn" title="Read analysis" aria-label="Analysis of ${escapeHtml(song.title)}">&#x1F4AC;</button>` : ''}
        </div>
        ${tooltipHtml}
      </li>`;
  }

  function songListHtml(songs) {
    const sorted = (songs || []).slice().sort((a, b) => a.position - b.position);
    return `<ul class="song-list">${sorted.map(songRowHtml).join('')}</ul>`;
  }

  // Instrumental disclosure line (shown under the list when any row is nulled).
  function instrumentalNoteHtml(songs) {
    const n = (songs || []).filter(s => s.instrumental).length;
    if (!n) return '';
    return `<div class="instrumental-note">${n} instrumental${n > 1 ? 's' : ''} &mdash; does not contribute to the compass reading.</div>`;
  }

  // --- Ether list (right card) ---------------------------------------------
  // Forward-only: rows with no deadpan_line (pre-tagger / uncalibrated) render
  // the title dim with an "untagged" pill rather than pretending the data is
  // there. Tier accent on the left tick matches the song's row on the left card.
  function etherRowHtml(item) {
    // Null dispositions (instrumental / preorder / lyrics-unavailable) carry no
    // reading: they hold space, render grey, and never borrow a tier accent.
    // Instrumentals store a placeholder green tier so the cache/approval gate
    // treats them as "done", but that tier is NOT a reading -- only a genuinely
    // calibrated row tints its tick. Mirrors the left list, where the dot greys
    // for these via .song-item.instrumental etc.
    const nullDisposition = !!(item.instrumental || item.preorder || item.lyrics_unavailable);
    const tierHex = nullDisposition
      ? 'var(--rc-text-dim)'
      : (COLOR_HEX[item.rubric_color] || 'transparent');
    const tickStyle = `border-left:9px solid ${tierHex};`;
    const songHref = (item.song_slug && item.rubric_color && !nullDisposition) ? `/songs/${encodeURIComponent(item.song_slug)}` : null;

    if (!item.deadpan_line) {
      const nullPill = item.instrumental ? 'instrumental'
        : item.preorder ? 'pre-order'
        : item.lyrics_unavailable ? 'lyrics n/a'
        : 'untagged';
      const titleHtml = songHref
        ? `<a href="${songHref}" class="ether-title-link">${escapeHtml(item.title)}</a>`
        : `<span class="ether-title-link">${escapeHtml(item.title)}</span>`;
      return `
        <li class="ether-row ether-row--untagged${nullDisposition ? ' ether-row--null' : ''}" style="${tickStyle}">
          <span class="ether-pos">${positionDisplay(item)}</span>
          <div class="ether-text">
            <div class="ether-deadpan">${titleHtml}</div>
            <div class="ether-meta">${artistHtml(item.artist, item.artist_slug, 'ether-meta-artist')} <span class="ether-untagged-pill">${nullPill}</span></div>
          </div>
        </li>`;
    }

    const titleHtml = songHref
      ? `<a href="${songHref}" class="ether-title-link">${escapeHtml(item.title)}</a>`
      : escapeHtml(item.title);
    const topicHtml = item.dominant_topic
      ? `<span class="ether-meta-sep">&middot;</span><span class="ether-chip">${escapeHtml(String(item.dominant_topic).replace(/-/g, ' '))}</span>`
      : '';
    return `
      <li class="ether-row" style="${tickStyle}">
        <span class="ether-pos">${positionDisplay(item)}</span>
        <div class="ether-text">
          <div class="ether-deadpan">${escapeHtml(item.deadpan_line)}</div>
          <div class="ether-meta">
            <span class="ether-meta-title">${titleHtml}</span>
            <span class="ether-meta-sep">&middot;</span>
            ${artistHtml(item.artist, item.artist_slug, 'ether-meta-artist')}
            ${topicHtml}
          </div>
        </div>
      </li>`;
  }

  function etherListHtml(songs) {
    const sorted = (songs || []).slice().sort((a, b) => a.position - b.position);
    return `<ol class="ether-list">${sorted.map(etherRowHtml).join('')}</ol>`;
  }

  // --- Tooltip wiring ------------------------------------------------------
  // Click a row's comment button to expand its analysis tooltip; click another
  // or outside to dismiss. Shared so every surface behaves identically.
  function wireTooltips(container) {
    if (!container) return;
    container.querySelectorAll('.song-comment-btn').forEach(btn => {
      btn.setAttribute('aria-expanded', 'false');
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const item = btn.closest('.song-item');
        const wasActive = item.classList.contains('active');
        container.querySelectorAll('.song-item.active').forEach(el => {
          el.classList.remove('active');
          const b = el.querySelector('.song-comment-btn');
          if (b) b.setAttribute('aria-expanded', 'false');
        });
        if (!wasActive) {
          item.classList.add('active');
          btn.setAttribute('aria-expanded', 'true');
        }
      });
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.song-comment-btn')) {
        container.querySelectorAll('.song-item.active').forEach(el => {
          el.classList.remove('active');
          const b = el.querySelector('.song-comment-btn');
          if (b) b.setAttribute('aria-expanded', 'false');
        });
      }
    });
  }

  // --- High-level paired render --------------------------------------------
  // Fills the left card body (charge + editorial + list + instrumental note)
  // and the right card body (ether list) from one normalized reading. Pass the
  // content elements (not the cards). leftTrailingHtml slots into the charge
  // row (homepage calendar toggle); omit on chart pages.
  function buildLeft(reading, leftTrailingHtml) {
    return chargeGroupHtml(reading, leftTrailingHtml)
      + editorialHtml(reading.editorial)
      + songListHtml(reading.songs)
      + instrumentalNoteHtml(reading.songs);
  }

  function buildRight(reading) {
    return etherListHtml(reading.songs);
  }

  return {
    COLOR_HEX,
    CHARGE_LABELS,
    escapeHtml,
    formatDate,
    degreeToScore,
    artistHtml,
    chargeGroupHtml,
    editorialHtml,
    songRowHtml,
    songListHtml,
    instrumentalNoteHtml,
    etherRowHtml,
    etherListHtml,
    wireTooltips,
    buildLeft,
    buildRight,
  };
})();

// Make available to module-scoped IIFEs (app.js) and global pages alike.
if (typeof window !== 'undefined') window.ChartShell = ChartShell;
