/* === API Client === */

const API = (() => {
  const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const BASE = isLocal
    ? `http://${window.location.hostname}:8000`
    : 'https://api.risingcompass.net';

  const API_KEY = isLocal
    ? ''
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  async function get(path) {
    const headers = {};
    if (API_KEY) headers['X-Api-Key'] = API_KEY;
    const resp = await fetch(`${BASE}${path}`, { headers });
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
  }

  return {
    getCompassCurrent: () => get('/api/compass/current'),
    getHistory: (page = 1, perPage = 10) => get(`/api/compass/history?page=${page}&per_page=${perPage}`),
    getReading: (date) => get(`/api/compass/reading/${date}`),
    getDrift: () => get('/api/drift'),
    getDriftYears: () => get('/api/drift/years'),
    getYearSongs: (year, offset = 0, limit = 20) => get(`/api/drift/years/${year}/songs?offset=${offset}&limit=${limit}`),
    getYearDates: (year) => get(`/api/drift/years/${year}/dates`),
    getAlbums: () => get('/api/albums'),
    getAlbum: (slug) => get(`/api/albums/${slug}`),
    getWeeklyAlbumsCurrent: () => get('/api/weekly-albums/current'),
    getWeeklyAlbumsHistory: (page = 1) => get(`/api/weekly-albums/history?page=${page}`),
    getWeeklyAlbumsReading: (date) => get(`/api/weekly-albums/reading/${date}`),
    getLibrary: () => get('/api/library'),
    getDailyChart: (days) => get(`/api/compass/daily-chart${days ? '?days=' + days : ''}`),
  };
})();
