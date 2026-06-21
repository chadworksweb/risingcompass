// Audience Resonance - mount the per-song section on the REAL song page.
// Decoupled from songs.js (a classic script): this ES module resolves the song
// from the URL slug via the public API and renders independently. LIVE DATA
// ONLY -- never the seed fixture, so fictional demo stories can never appear on
// a real song page. Shows the display row when there are published resonances,
// otherwise an empty-state entry point. Hidden entirely for uncalibrated songs.

import { renderSongSection } from '/audience-resonance/song-section.js';

function slugFromPath() {
  const m = location.pathname.match(/\/songs\/([^/]+)\/?$/);
  return m ? decodeURIComponent(m[1]) : null;
}
function api() { return (typeof window !== 'undefined' && window.API) ? window.API : null; }

async function boot() {
  const section = document.getElementById('section-audience-resonance');
  const mount = document.getElementById('ar-song-mount');
  const cta = document.getElementById('ar-share-cta');
  if (!section || !mount || !api()) return;

  const slug = slugFromPath();
  if (!slug) return;

  let detail;
  try {
    detail = await api().get(`/api/songs/${encodeURIComponent(slug)}`);
  } catch (_) {
    return;  // no song / not found -> leave the section hidden
  }
  // Only scored songs carry an Audience Resonance surface.
  if (!detail || detail.song_id == null || detail.charge_value == null) return;

  const song = {
    id: detail.song_id, title: detail.title, artist: detail.artist,
    tier_label: detail.tier_label, color: detail.tier_hex, charge: detail.charge_value,
  };

  // Bind the share entry point to THIS song (SCOPE: the song page entry point
  // binds to the song automatically).
  if (cta) cta.href = `/audience-resonance/submit/?slug=${encodeURIComponent(slug)}`;

  let resonances = [];
  try {
    const r = await api().get(`/api/audience-resonance/song/${song.id}`);
    resonances = r.resonances || [];
  } catch (_) { resonances = []; }

  if (resonances.length) {
    renderSongSection(mount, { song, resonances });
  } else {
    mount.innerHTML = '<p class="ar-empty">No resonances yet. Be the first to say what this song actually did to you.</p>';
  }
  section.hidden = false;
}

boot();
