/* === API Client === */

const API = (() => {
  const BASE = window.location.hostname === 'localhost'
    ? 'http://localhost:8000'
    : 'https://api.risingcompass.com';

  async function get(path) {
    const resp = await fetch(`${BASE}${path}`);
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
  }

  return {
    getCompassCurrent: () => get('/api/compass/current'),
    getHistory: (page = 1, perPage = 10) => get(`/api/compass/history?page=${page}&per_page=${perPage}`),
    getReading: (date) => get(`/api/compass/reading/${date}`),
    getDrift: () => get('/api/drift'),
    getDriftYears: () => get('/api/drift/years'),
    getAlbums: () => get('/api/albums'),
    getAlbum: (slug) => get(`/api/albums/${slug}`),
    getWeeklyAlbumsCurrent: () => get('/api/weekly-albums/current'),
    getWeeklyAlbumsHistory: (page = 1) => get(`/api/weekly-albums/history?page=${page}`),
    getWeeklyAlbumsReading: (date) => get(`/api/weekly-albums/reading/${date}`),
    getLibrary: () => get('/api/library'),
  };
})();
