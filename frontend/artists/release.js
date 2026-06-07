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

  function questionFor(t) {
    if (t === 'album') return 'What is this album about?';
    if (t === 'ep') return 'What is this EP about?';
    return 'What is this release about?';
  }

  // CAA hotlinks can 404 if a group's art is pulled; degrade to the tier glow
  // by hiding the broken image rather than showing a broken-image icon.
  function showArt(url) {
    const wrap = document.getElementById('release-art-wrap');
    const img = document.getElementById('release-art');
    if (!url || !wrap || !img) return;
    img.onerror = () => { wrap.hidden = true; };
    img.src = url;
    wrap.hidden = false;
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

    const dateStr = rel.release_date || (rel.release_year ? String(rel.release_year) : '');
    const parts = [
      `<a href="/artists/${encodeURIComponent(rel.artist.slug)}">${escapeHtml(rel.artist.name)}</a>`,
      typeLabel(rel.release_type),
    ];
    if (dateStr) parts.push(escapeHtml(dateStr));
    if (rel.track_count) parts.push(`${rel.track_count} track${rel.track_count === 1 ? '' : 's'}`);
    document.getElementById('release-meta').innerHTML = parts.join(' &middot; ');

    // Hero: compass gauge (calibrated) or a quiet uncalibrated pill.
    const compassWrap = document.getElementById('release-compass-container');
    const badge = document.getElementById('release-tier-badge');
    const hero = document.getElementById('release-hero');
    if (rel.charge_value != null && typeof Compass !== 'undefined') {
      hero.style.setProperty('--charge-color', color);
      hero.style.setProperty('--charge-glow', color + '40');
      compassWrap.hidden = false;
      Compass.render('release-compass-container');
      Compass.setDegree(90 - rel.charge_value * 0.9, color);
    } else if (rel.charge_value != null) {
      badge.hidden = false;
      badge.innerHTML = `<span class="badge-charge" style="color:${color}">${chargeDisplay(rel.charge_value)}</span>`
        + `<span class="badge-tier" style="background:${color}20;color:${color}">${escapeHtml(rel.tier_label || '')}</span>`;
    } else {
      badge.hidden = false;
      badge.innerHTML = `<span class="badge-tier badge-tier--uncalibrated">Uncalibrated</span>`;
    }

    // Cover art (front-500) in the summary head, if CAA had art.
    showArt(rel.cover_url);

    // Summary
    document.getElementById('release-question').textContent = questionFor(rel.release_type);
    const summaryEl = document.getElementById('release-summary');
    summaryEl.classList.remove('is-loading');
    if (rel.charge_summary && rel.charge_summary.trim()) {
      summaryEl.textContent = rel.charge_summary;
    } else {
      summaryEl.classList.add('release-summary--empty');
      summaryEl.textContent = "This release hasn't been given a written reading yet -- its charge is the mean of its calibrated tracks below.";
    }

    // Prose blocks (hidden unless present; mostly Album-Charger releases).
    fillProse('release-arc-section', 'release-arc', rel.arc_prose);
    fillProse('release-listener-effects-section', 'release-listener-effects', rel.listener_effects_prose);
    fillProse('release-societal-section', 'release-societal', rel.societal_effects_prose);

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
