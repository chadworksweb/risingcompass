// Audience Resonance - data layer.
// Primary source is the LIVE API (/api/audience-resonance/*). The hand-authored
// seed JSON is a DEMO fallback only: it renders the display layer in
// local/staging while the corpus has no real, published resonances yet (and its
// fictional songs do not exist in the corpus, so the live endpoints return
// empty for them). Synthetic seed is never written to the shared DB.
//
// Force behavior with a query param: ?live = API only (no fallback),
// ?demo / ?seed = seed only. Default: API, falling back to seed when empty.

const SEED_URL = '/audience-resonance/seed-resonances.json';

function mode() {
  const p = new URLSearchParams(location.search);
  if (p.has('live')) return 'live';
  if (p.has('demo') || p.has('seed')) return 'seed';
  return 'auto';
}

function api() {
  return (typeof window !== 'undefined' && window.API) ? window.API : null;
}

let _seedPromise = null;
function loadSeed() {
  if (!_seedPromise) _seedPromise = fetch(SEED_URL).then((r) => r.json());
  return _seedPromise;
}

// Normalized rollup shape consumed by the ternary surfaces:
//   { song: {id, title, artist, slug, tier_label, color, charge}, n,
//     mean: {true, camouflage, adjacent} }
function rollupFromSeed(data) {
  const songs = data.songs || [];
  const resonances = data.resonances || [];
  const byId = new Map(songs.map((s) => [s.id, { song: s, n: 0, t: 0, c: 0, a: 0 }]));
  for (const r of resonances) {
    const row = byId.get(r.song_id);
    if (!row) continue;
    row.n += 1; row.t += r.true; row.c += r.camouflage; row.a += r.adjacent;
  }
  const rollups = [...byId.values()].filter((r) => r.n > 0).map((r) => ({
    song: r.song, n: r.n,
    mean: { true: r.t / r.n, camouflage: r.c / r.n, adjacent: r.a / r.n },
  }));
  return { rollups, totalResonances: resonances.length, isDemo: true };
}

function rollupFromLive(payload) {
  const rows = (payload && payload.songs) || [];
  const rollups = rows.map((s) => ({
    song: {
      id: s.song_id, title: s.title, artist: s.artist, slug: s.slug,
      tier_label: s.tier_label, color: s.color, charge: s.charge,
    },
    n: s.n,
    mean: { true: s.mean_true, camouflage: s.mean_camouflage, adjacent: s.mean_adjacent },
  }));
  const totalResonances = rollups.reduce((acc, r) => acc + r.n, 0);
  return { rollups, totalResonances, isDemo: false };
}

// Corpus map: every song with at least one published resonance, pre-rolled.
export async function fetchCorpus() {
  const m = mode();
  if (m !== 'seed') {
    try {
      const live = await api().get('/api/audience-resonance/corpus');
      const out = rollupFromLive(live);
      if (out.rollups.length > 0 || m === 'live') return out;
    } catch (err) {
      if (m === 'live') throw err;
      console.warn('[AR] live corpus failed, falling back to seed demo', err);
    }
  }
  return rollupFromSeed(await loadSeed());
}

// Per-song story set. Returns { resonances: [{id, username, story, true,
// camouflage, adjacent, flag}], mean, count, isDemo }. Live resonance rows
// already match the card shape; seed rows are filtered to the song.
export async function fetchSongResonances(songId) {
  const m = mode();
  if (m !== 'seed') {
    try {
      const live = await api().get(`/api/audience-resonance/song/${songId}`);
      const resonances = live.resonances || [];
      if (resonances.length > 0 || m === 'live') {
        return { resonances, mean: live.mean, count: live.count, isDemo: false };
      }
    } catch (err) {
      if (m === 'live') throw err;
      console.warn('[AR] live song resonances failed, falling back to seed demo', err);
    }
  }
  const data = await loadSeed();
  const resonances = (data.resonances || []).filter((r) => r.song_id === songId);
  return { resonances, count: resonances.length, isDemo: true };
}

// Seed song list (used by the wizard's offline demo song picker until the live
// song search is wired into the submission flow).
export async function fetchSeedSongs() {
  const data = await loadSeed();
  return data.songs || [];
}
