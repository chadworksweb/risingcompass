// Demo harness for the per-song Audience Resonance section.
// Resonances come from the LIVE API (/api/audience-resonance/song/{id}), with
// the seed as a labeled fallback while the corpus is empty (see data.js). The
// song metadata still comes from the seed here because the demo songs are
// fictional; on the real song page the section mounts with the page's own song
// meta and the same live fetch (see loadSongSection in song-section.js).

import { renderSongSection } from '/audience-resonance/song-section.js';
import { fetchSongResonances, fetchSeedSongs } from '/audience-resonance/data.js';

async function boot() {
  const params = new URLSearchParams(location.search);
  const id = parseInt(params.get('id') || '2001', 10);
  const songs = await fetchSeedSongs();
  const song = songs.find((s) => s.id === id) || songs[0];
  const { resonances, isDemo } = await fetchSongResonances(id);
  renderSongSection(document.getElementById('ar-song-mount'), { song, resonances });
  console.log(`[AR] song ${id}: ${resonances.length} resonances${isDemo ? ' [demo seed]' : ' [live]'}`);
}

boot();
