/* === Release detail page ===
 * URL: /artists/<artist-slug>/<release-slug>
 * Reuses the song-page shell (song-main / song-row / song-section). The hero
 * compass renders exactly like a song's; the cover is the one new element.
 */
(function () {
  const COLOR_HEX = {
    violet: '#aa54ff', blue: '#3388ff', green: '#33cc55',
    orange: '#ffbb33', red: '#ff3333',
  };

  function escapeHtml(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function chargeDisplay(v) {
    if (v == null) return '';
    return (v > 0 ? '+' : '') + v;
  }

  function typeLabel(t) {
    return t === 'album' ? 'Album' : t === 'ep' ? 'EP' : 'Single';
  }

  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                  'August', 'September', 'October', 'November', 'December'];

  // "2026-05-01" -> "May, 01 2026".
  //
  // Parsed by hand rather than through `new Date(iso)`: that treats a bare
  // YYYY-MM-DD as UTC midnight, so every reader west of Greenwich would see the
  // day before the album came out. Day stays zero-padded.
  function formatReleaseDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    if (!m) return '';
    const month = MONTHS[Number(m[2]) - 1];
    return month ? `${month}, ${m[3]} ${m[1]}` : '';
  }

  function questionFor(t, tagline) {
    const noun = t === 'album' ? 'album' : t === 'ep' ? 'EP' : 'release';
    return tagline ? `What is the ${noun} ${tagline} about?` : `What is this ${noun} about?`;
  }

  // CAA hotlinks can 404 if a group's art is pulled; degrade to the tier glow
  // by hiding the broken image rather than showing a broken-image icon.
  //
  // Reveal on LOAD, not on src-set, for the reason spelled out in
  // songs.js::showCoverArt: the hotlink 307s to archive.org, which measured
  // seconds-to-502 on 2026-08-16, and unhiding early left an empty frame open
  // for the whole wait and shifted the layout twice. The <img> is loading="eager"
  // because a lazy image inside a hidden wrap never loads, so onload would never
  // fire.
  function showArt(url) {
    const wrap = document.getElementById('release-art-wrap');
    const img = document.getElementById('release-art');
    if (!url || !wrap || !img) return;
    img.onload = () => { wrap.hidden = false; };
    img.onerror = () => { wrap.hidden = true; };
    img.src = url;
  }

  function proseHtml(text) {
    return String(text).split(/\n{2,}/).map(p => p.trim()).filter(Boolean)
      .map(p => `<p>${escapeHtml(p).replace(/\n/g, '<br>')}</p>`).join('');
  }

  function fillProse(sectionId, proseId, text) {
    if (!text || !String(text).trim()) return;  // section stays hidden
    document.getElementById(proseId).innerHTML = proseHtml(text);
    document.getElementById(sectionId).hidden = false;
  }

  function parseSlugs() {
    const m = window.location.pathname.match(/^\/artists\/([^/]+)\/([^/]+)\/?$/);
    if (!m) return null;
    const artist = decodeURIComponent(m[1]);
    const release = decodeURIComponent(m[2]);
    if (artist === 'artist.html' || release === 'release.html') return null;
    return { artist, release };
  }

  function showNotFound(title, msg) {
    document.getElementById('main').querySelectorAll(
      '.song-row, #release-arc-section, #release-tracks-section'
    ).forEach(el => { el.hidden = true; });
    const nf = document.getElementById('release-notfound');
    document.getElementById('release-notfound-title').textContent = title;
    document.getElementById('release-notfound-msg').textContent = msg || '';
    nf.hidden = false;
  }

  function renderTrack(t) {
    const color = COLOR_HEX[t.rubric_color] || '#3a3a55';
    const calibrated = t.charge_value != null;
    const titleHtml = t.slug
      ? `<a href="/songs/${encodeURIComponent(t.slug)}">${escapeHtml(t.title)}</a>`
      : escapeHtml(t.title);
    const meta = t.track_number != null ? `Track ${t.track_number}` : '';
    return `
      <li class="release-compact-item${calibrated ? '' : ' is-uncalibrated'}">
        <span class="release-dot" style="background:${calibrated ? color : '#3a3a55'}"></span>
        <div class="release-compact-main">
          <span class="release-compact-title"${calibrated ? '' : ' style="color:var(--rc-text-dim)"'}>${titleHtml}</span>
          ${meta ? `<span class="release-compact-meta"><span>${meta}</span></span>` : ''}
        </div>
        <span class="release-compact-charge" style="color:${calibrated ? color : 'var(--rc-text-dim)'}">${calibrated ? chargeDisplay(t.charge_value) : '&middot;'}</span>
      </li>`;
  }

  function render(rel) {
    const color = COLOR_HEX[rel.rubric_color] || '#999';

    // Head / SEO
    const titleStr = `${rel.title} - ${rel.artist.name} - The Rising Compass`;
    document.title = titleStr;
    const setMeta = (id, attr, val) => {
      const el = document.getElementById(id);
      if (el) el.setAttribute(attr, val);
    };
    setMeta('page-title', 'textContent', titleStr);
    setMeta('og-title', 'content', titleStr);
    setMeta('canonical-link', 'href',
      `https://risingcompass.net/artists/${encodeURIComponent(rel.artist.slug)}/${encodeURIComponent(rel.slug)}`);

    // Hero: title + meta line
    const titleEl = document.getElementById('release-title');
    titleEl.textContent = rel.title;
    titleEl.classList.remove('is-loading');

    // Artist on its own line above the release facts. Two stacked lines rather
    // than one middot run, so the link is not competing with the type, the date
    // and the track count for the same scan.
    // A year-only release has no month or day to spell out, so it stays a year.
    const dateStr = formatReleaseDate(rel.release_date)
      || (rel.release_year ? String(rel.release_year) : '');
    const facts = [typeLabel(rel.release_type)];
    if (dateStr) facts.push(escapeHtml(dateStr));
    if (rel.track_count) facts.push(`${rel.track_count} track${rel.track_count === 1 ? '' : 's'}`);
    document.getElementById('release-meta').innerHTML =
      `<span class="release-meta-artist"><a href="/artists/${encodeURIComponent(rel.artist.slug)}" class="accent-link">${escapeHtml(rel.artist.name)}</a></span>`
      + `<span class="release-meta-facts">${facts.join(' &middot; ')}</span>`;

    // The placard, under the identity line. A fragment by contract, so it is
    // printed as written -- no trailing period added, no sentence casing.
    const dp = document.getElementById('release-deadpan');
    if (dp && rel.deadpan_line && rel.deadpan_line.trim()) {
      dp.textContent = rel.deadpan_line.trim();
      dp.hidden = false;
    }

    // Hero: compass gauge (calibrated) or a quiet uncalibrated pill.
    const compassWrap = document.getElementById('release-compass-container');
    const badge = document.getElementById('release-tier-badge');
    const hero = document.getElementById('release-hero');
    if (rel.charge_value != null && typeof Compass !== 'undefined') {
      hero.style.setProperty('--charge-color', color);
      hero.style.setProperty('--charge-glow', color + '40');
      compassWrap.hidden = false;
      Compass.render('release-compass-container');
      Compass.setDegree(90 - rel.charge_value * 0.9, rel.rubric_color || color);
    } else if (rel.charge_value != null) {
      badge.hidden = false;
      badge.innerHTML = `<span class="badge-charge" style="color:${color}">${chargeDisplay(rel.charge_value)}</span>`
        + `<span class="badge-tier" style="background:${color}20;color:${color}">${escapeHtml(rel.tier_label || '')}</span>`;
    } else {
      badge.hidden = false;
      badge.innerHTML = `<span class="badge-tier badge-tier--uncalibrated">Uncalibrated</span>`;
    }

    // Contamination + dogma as the MINI inline marks the homepage panel uses,
    // not the song hero's labelled pill.
    //
    // On contamination the album's OWN finding governs whenever it has one.
    // `contamination_count` counts flagged TRACKS, which is a different claim: a
    // release can carry a flagged track and still not be a contaminated release,
    // because the album finding is settled against the per-track findings rather
    // than against a count of them. Driving the mark off the count alone put a
    // CONTAMINATED flag on an album whose reading says it is not contaminated.
    // The count speaks only when no album reading exists.
    const setMark = (id, cls, glyph, note) => {
      const el = document.getElementById(id);
      if (!el || !note) return;
      el.innerHTML = `<span class="${cls}" aria-hidden="true">${glyph}</span>`;
      el.setAttribute('title', note);
      el.hidden = false;
    };

    const contamN = rel.contamination_count || 0;
    let contamNote = '';
    if (rel.has_reading) {
      if (rel.contaminated) {
        contamNote = rel.contamination_note || 'This release carries contaminating content.';
      }
    } else if (contamN > 0) {
      const n = rel.track_count || 0;
      contamNote = `${contamN} of ${n} track${n === 1 ? '' : 's'} on this release carries `
        + 'contaminating content. Open a track to see what was flagged.';
    }
    setMark('release-contam-mark', 'song-contam', '&#x2622;', contamNote);
    setMark('release-dogma-mark', 'song-dogma', '&#x1F4DC;', rel.dogma_referenced
      ? (rel.dogma_note
         || 'This release invokes a specific doctrinal framework. Metadata only, it does not affect the charge.')
      : '');

    // Cover art (front-500) in the summary head, if CAA had art.
    showArt(rel.cover_url);

    // SEO/GEO tagline, mirroring the song page: every H2 carries the
    // release/artist string so the page is dense with it for search and for
    // generative engines. Headings are also phrased per release type, so an EP
    // page never says album.
    const noun = rel.release_type === 'album' ? 'Album'
      : rel.release_type === 'ep' ? 'EP' : 'Release';
    const nounLower = noun === 'EP' ? 'EP' : noun.toLowerCase();
    const tagline = rel.artist && rel.artist.name
      ? `"${rel.title}" by ${rel.artist.name}`
      : `"${rel.title}"`;

    const setText = (id, txt) => {
      const el = document.getElementById(id);
      if (el) el.textContent = txt;
    };
    const setH2 = (sectionId, txt) => {
      const h2 = document.querySelector(`#${sectionId} h2`);
      if (h2) h2.textContent = txt;
    };
    setH2('release-arc-section', `How Does ${tagline} Move Across Its Tracks?`);
    setH2('release-listener-effects-section', `What Might Listening to ${tagline} Do to the Listener?`);
    setH2('release-societal-section', `What Might Listening to ${tagline} Do to a Society?`);
    setH2('release-tracks-section', `Every Track on ${tagline}`);
    // Closing footnotes. One line of links now, so each carries its whole
    // question and there is no heading or body copy left to phrase.
    setText('release-claim-link', `Are you the artist of this ${nounLower}?`);
    setText('release-about-link', `How was this ${nounLower} calibrated?`);

    // Summary
    document.getElementById('release-question').textContent = questionFor(rel.release_type, tagline);
    const summaryEl = document.getElementById('release-summary');
    summaryEl.classList.remove('is-loading');
    if (rel.charge_summary && rel.charge_summary.trim()) {
      summaryEl.innerHTML = proseHtml(rel.charge_summary);
    } else {
      summaryEl.classList.add('release-summary--empty');
      summaryEl.textContent = "This release hasn't been given a written reading yet -- its charge is the mean of its calibrated tracks below.";
    }

    // Topics the album lens settled on, linking to the same /topics/ pages the
    // song chips use. Topics only, no theme chip: the song page cut that
    // deliberately (it pointed thousands of links at nine URLs to say where a
    // reading is FILED rather than what it is about), and the theme is still
    // named on every topic page.
    const topicsEl = document.getElementById('release-topics');
    const topics = Array.isArray(rel.topics) ? rel.topics.filter(Boolean) : [];
    if (topicsEl && topics.length) {
      topicsEl.innerHTML = topics.map(t =>
        `<a class="song-topic" href="/topics/${encodeURIComponent(t)}">${escapeHtml(String(t).replace(/-/g, ' '))}</a>`
      ).join('');
      topicsEl.hidden = false;
    }

    // Prose blocks (hidden unless present; mostly Album-Charger releases).
    fillProse('release-arc-section', 'release-arc', rel.arc_prose);
    fillProse('release-listener-effects-section', 'release-listener-effects', rel.listener_effects_prose);
    fillProse('release-societal-section', 'release-societal', rel.societal_effects_prose);

    // Psyche Facts - the prescription label for the release. Every field is
    // independently optional: an album with only a purpose gets a one-field
    // panel rather than a grid of blanks, and a release with none of it keeps
    // the whole section hidden.
    const pf = rel.psyche_facts || {};
    const effects = Array.isArray(rel.effects_pl_labels) ? rel.effects_pl_labels : [];
    const setField = (id, value) => {
      const body = document.getElementById(`release-psyche-${id}`);
      const field = document.getElementById(`release-psyche-${id}-field`);
      if (!body || !field || !value) return false;
      body.textContent = value;
      field.hidden = false;
      return true;
    };
    // Onset and duration share one cell, exactly as on the label they mirror.
    const onsetDuration = [pf.onset, pf.duration].filter(Boolean).join(' / ');
    const indicated = Array.isArray(pf.indicated_for) ? pf.indicated_for.filter(Boolean) : [];

    let anyField = setField('purpose', pf.purpose);
    if (indicated.length) {
      document.getElementById('release-psyche-indicated').innerHTML =
        indicated.map(t => `<li>${escapeHtml(t)}</li>`).join('');
      document.getElementById('release-psyche-indicated-field').hidden = false;
      anyField = true;
    }
    if (setField('donotuse', pf.do_not_use_if)) anyField = true;
    if (effects.length) {
      document.getElementById('release-psyche-effects').innerHTML = effects.map(e =>
        `<li class="psyche-label__effect${e.shadow ? ' psyche-label__effect--shadow' : ''}">${escapeHtml(e.label)}</li>`
      ).join('');
      document.getElementById('release-psyche-effects-field').hidden = false;
      anyField = true;
    }
    if (setField('directions', pf.directions)) anyField = true;
    if (setField('onset', onsetDuration)) anyField = true;
    // `pf.warning` is deliberately NOT rendered -- see the note in the panel's
    // markup. It keeps arriving in the payload and keeps being authored; the
    // field is on hold pending the sitewide decision, not removed.

    if (anyField) {
      document.getElementById('release-psyche-track').textContent =
        [rel.title, rel.artist && rel.artist.name].filter(Boolean).join(' / ');
      document.getElementById('release-psyche-section').hidden = false;
    }

    // Dogma Reference section - surfaces only when the tag fired.
    if (rel.dogma_referenced) {
      const ds = document.getElementById('release-section-dogma');
      if (ds) {
        setH2('release-section-dogma', `Dogma Reference in ${tagline}`);
        document.getElementById('release-dogma-answer').textContent =
          rel.dogma_note || `${tagline} references a specific doctrinal framework.`;
        ds.hidden = false;
      }
    }

    // Tracklist
    const list = document.getElementById('release-tracks');
    if (rel.tracks && rel.tracks.length) {
      list.innerHTML = rel.tracks.map(renderTrack).join('');
    } else {
      list.innerHTML = '<li class="empty-row">No calibrated tracks on this release yet.</li>';
    }
  }

  async function init() {
    const slugs = parseSlugs();
    if (!slugs) { showNotFound('Release not found', 'This page expects an /artists/<artist>/<release> address.'); return; }
    try {
      const rel = await ArtistsAPI.getRelease(slugs.artist, slugs.release);
      render(rel);
    } catch (e) {
      const is404 = /\b404\b/.test(String(e && e.message));
      showNotFound(
        is404 ? 'Release not found' : 'Could not load this release',
        is404 ? 'We could not find that release for this artist.'
              : 'Something went wrong loading this release. Please try again.'
      );
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
