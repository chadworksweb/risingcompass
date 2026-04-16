/* === Artists / Songs API Client === */

const ArtistsAPI = (() => {
  const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const BASE = isLocal
    ? `http://${window.location.hostname}:8000`
    : 'https://api.risingcompass.net';

  const API_KEY = isLocal
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  async function get(path) {
    const headers = {};
    if (API_KEY) headers['X-Api-Key'] = API_KEY;
    const resp = await fetch(`${BASE}${path}`, { headers });
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
  }

  return {
    searchArtists: (q, limit = 20) => get(`/api/artists/search?q=${encodeURIComponent(q)}&limit=${limit}`),
    getArtist: (slug) => get(`/api/artists/${slug}`),
    getArtistSongs: (slug, releaseId, offset = 0, limit = 20) => {
      let path = `/api/artists/${slug}/songs?offset=${offset}&limit=${limit}`;
      if (releaseId != null) path += `&release_id=${releaseId}`;
      return get(path);
    },
    searchSongs: (q, limit = 20) => get(`/api/songs?q=${encodeURIComponent(q)}&limit=${limit}`),
    getSong: (slug) => get(`/api/songs/${slug}`),
  };
})();
