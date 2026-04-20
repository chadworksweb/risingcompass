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
    getArtistSummary: (slug) => get(`/api/artists/${slug}/summary`),
    getArtistTrajectory: (slug) => get(`/api/artists/${slug}/trajectory`),
    getArtistReleases: (slug, offset = 0, limit = 10, order = 'desc') =>
      get(`/api/artists/${slug}/releases?offset=${offset}&limit=${limit}&order=${order}`),
    getArtistTopSongs: (slug, offset = 0, limit = 20) =>
      get(`/api/artists/${slug}/top-songs?offset=${offset}&limit=${limit}`),
    getArtistSongs: (slug, releaseId, offset = 0, limit = 20) => {
      let path = `/api/artists/${slug}/songs?offset=${offset}&limit=${limit}`;
      if (releaseId != null) path += `&release_id=${releaseId}`;
      return get(path);
    },
    searchSongs: (q, limit = 20) => get(`/api/songs?q=${encodeURIComponent(q)}&limit=${limit}`),
    getSong: (slug) => get(`/api/songs/${slug}`),
    getSongFlagCounts: (slug) => get(`/api/songs/${slug}/flag-counts`),
    getSongHistory: (slug) => get(`/api/songs/${slug}/history`),
    getSongCalibrationRuns: (slug) => get(`/api/songs/${slug}/calibration-runs`),
    getVibeState: (source, songId, deviceId) => {
      const q = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : '';
      return get(`/api/vibe/${source}/${songId}${q}`);
    },
    pushVibe: async (source, songId, direction, deviceId) => {
      const headers = { 'Content-Type': 'application/json' };
      if (API_KEY) headers['X-Api-Key'] = API_KEY;
      const resp = await fetch(`${BASE}/api/vibe/${source}/${songId}/push`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ direction, device_id: deviceId }),
      });
      const text = await resp.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch {}
      if (!resp.ok) {
        const detail = (data && data.detail) || `HTTP ${resp.status}`;
        const err = new Error(detail);
        err.status = resp.status;
        throw err;
      }
      return data;
    },
  };
})();
