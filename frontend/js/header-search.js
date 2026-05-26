/* === Global header search — Google-style autocomplete dropdown ===

   Lives in the shared header (partials/header.html) on every public page.
   Reuses the same API as the /search/ page (artist + song search) but renders
   results in a dropdown attached to the input rather than a separate section.
   Self-contained (own API base + key) since it loads everywhere and can't
   assume artists-api.js is present. */

(() => {
  'use strict';

  const input = document.getElementById('rcSearchInput');
  const panel = document.getElementById('rcSearchPanel');
  const wrap = document.getElementById('rcSearch');
  if (!input || !panel || !wrap) return;

  // Full-page dim scrim shown behind the dropdown. Sits below the header
  // (z-index 1000) so the search bar + results stay lit while the page dims.
  const dim = document.createElement('div');
  dim.className = 'rc-search-dim';
  document.body.appendChild(dim);

  const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const BASE = '';  // same-origin relative; dev_server proxies /api -> :8000 locally
  const API_KEY = isLocal
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  const COLOR_HEX = {
    violet: '#aa54ff', blue: '#3388ff', green: '#33cc55',
    orange: '#ffbb33', red: '#ff3333',
  };
  const PER_KIND = 6;
  const MIN_CHARS = 2;
  const DEBOUNCE_MS = 250;

  let debounceTimer = null;
  let controller = null;
  let seq = 0;
  let items = [];        // flat list of {url} for keyboard nav
  let activeIdx = -1;

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  async function getJSON(path, signal) {
    const resp = await fetch(`${BASE}${path}`, {
      headers: { 'X-Api-Key': API_KEY },
      signal,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  function openPanel() {
    panel.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    dim.classList.add('show');
  }
  function closePanel() {
    panel.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    activeIdx = -1;
    dim.classList.remove('show');
  }

  function setActive(idx) {
    const nodes = panel.querySelectorAll('.rc-search-item');
    if (!nodes.length) return;
    activeIdx = (idx + nodes.length) % nodes.length;
    nodes.forEach((n, i) => n.classList.toggle('active', i === activeIdx));
    nodes[activeIdx].scrollIntoView({ block: 'nearest' });
  }

  function render(artists, songs, q) {
    items = [];
    let html = '';

    if (artists.length) {
      html += '<div class="rc-search-group-label">Artists</div>';
      for (const a of artists) {
        const url = a.indexed && a.slug ? `/artists/${encodeURIComponent(a.slug)}` : null;
        const meta = `${a.calibrated_song_count} song${a.calibrated_song_count !== 1 ? 's' : ''}`;
        if (url) {
          items.push({ url });
          html += `<a class="rc-search-item" href="${url}" role="option">
            <span class="rc-search-name">${esc(a.name)}</span>
            <span class="rc-search-sub">${meta} calibrated</span>
          </a>`;
        } else {
          // Unindexed artist — not navigable; show as a dim, non-clickable row.
          html += `<div class="rc-search-item rc-search-item-static" role="option">
            <span class="rc-search-name">${esc(a.name)}</span>
            <span class="rc-search-sub">${meta} — trajectory not built</span>
          </div>`;
        }
      }
    }

    if (songs.length) {
      html += '<div class="rc-search-group-label">Songs</div>';
      for (const s of songs) {
        const url = `/songs/${encodeURIComponent(s.slug)}`;
        const dot = COLOR_HEX[s.rubric_color] || '#999';
        items.push({ url });
        html += `<a class="rc-search-item" href="${url}" role="option">
          <span class="rc-search-dot" style="background:${dot}"></span>
          <span class="rc-search-name">${esc(s.title)} <span style="color:var(--rc-text-dim);font-weight:400;">— ${esc(s.artist)}</span></span>
          <span class="rc-search-sub" style="color:${dot}">${esc(s.tier_label)}</span>
        </a>`;
      }
    }

    if (!html) {
      html = '<div class="rc-search-empty">No results found.</div>';
    } else {
      html += `<a class="rc-search-all" href="/search/?q=${encodeURIComponent(q)}">See all results for &ldquo;${esc(q)}&rdquo; &#8599;</a>`;
    }

    panel.innerHTML = html;
    activeIdx = -1;
    openPanel();
  }

  async function run(q) {
    if (controller) controller.abort();
    controller = new AbortController();
    const mySeq = ++seq;
    try {
      const [artistData, songData] = await Promise.all([
        getJSON(`/api/artists/search?q=${encodeURIComponent(q)}&limit=${PER_KIND}`, controller.signal),
        getJSON(`/api/songs?q=${encodeURIComponent(q)}&limit=${PER_KIND}`, controller.signal),
      ]);
      if (mySeq !== seq) return;
      render(artistData.results || [], songData.results || [], q);
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      if (mySeq !== seq) return;
      // Network/API failure: fail quietly to a hint, don't spam the header.
      panel.innerHTML = '<div class="rc-search-empty">Search is unavailable right now.</div>';
      openPanel();
    }
  }

  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (q.length < MIN_CHARS) {
      if (controller) controller.abort();
      closePanel();
      panel.innerHTML = '';
      return;
    }
    debounceTimer = setTimeout(() => run(q), DEBOUNCE_MS);
  });

  input.addEventListener('keydown', (e) => {
    if (panel.hidden) {
      if (e.key === 'ArrowDown' && input.value.trim().length >= MIN_CHARS) run(input.value.trim());
      return;
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(activeIdx + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(activeIdx - 1); }
    else if (e.key === 'Enter') {
      const nodes = panel.querySelectorAll('.rc-search-item[href]');
      if (activeIdx >= 0 && items[activeIdx]) {
        e.preventDefault();
        window.location.href = items[activeIdx].url;
      } else {
        // No active item: go to the full search page with the query.
        const q = input.value.trim();
        if (q.length >= MIN_CHARS) { e.preventDefault(); window.location.href = `/search/?q=${encodeURIComponent(q)}`; }
      }
    } else if (e.key === 'Escape') {
      closePanel();
      input.blur();
    }
  });

  input.addEventListener('focus', () => {
    if (input.value.trim().length >= MIN_CHARS && panel.innerHTML) openPanel();
  });

  // Close on outside click / focus loss.
  document.addEventListener('click', (e) => {
    if (!wrap.contains(e.target)) closePanel();
  });
})();
