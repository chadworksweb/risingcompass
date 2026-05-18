/* === API Client === */

const API = (() => {
  const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const BASE = isLocal
    ? `http://${window.location.hostname}:8000`
    : 'https://api.risingcompass.net';

  const API_KEY = isLocal
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  // Turso cold connections + occasional 502/504 from nginx during container
  // restarts cause transient failures. Retry twice with short backoff before
  // giving up on GETs — 4xx won't be retried since those won't improve.
  // 20s timeout is generous for local dev where the FastAPI process talks to
  // remote Turso over WAN; production hits the same DB from a much closer
  // region and is typically <1s, well inside the budget.
  async function get(path, { attempts = 3, timeoutMs = 20000 } = {}) {
    const headers = {};
    if (API_KEY) headers['X-Api-Key'] = API_KEY;
    let lastErr;
    for (let i = 0; i < attempts; i++) {
      try {
        const resp = await fetch(`${BASE}${path}`, {
          headers,
          signal: AbortSignal.timeout(timeoutMs),
        });
        if (resp.ok) return resp.json();
        if (resp.status >= 400 && resp.status < 500) {
          throw new Error(`API error: ${resp.status}`);
        }
        lastErr = new Error(`API error: ${resp.status}`);
      } catch (err) {
        lastErr = err;
      }
      if (i < attempts - 1) {
        await new Promise((r) => setTimeout(r, 400 * (i + 1)));
      }
    }
    throw lastErr;
  }

  return {
    get,
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
    getDailyChart: (days) => get(`/api/compass/daily-chart${days ? '?days=' + days : ''}`),
    getEtherToday: () => get('/api/ether-art-chart/today'),
    getEtherDate: (date) => get(`/api/ether-art-chart/date/${date}`),
    getEtherYears: () => get('/api/ether-art-chart/years'),
    getEtherYear: (year) => get(`/api/ether-art-chart/year/${year}`),
    getChartSnapshot: (key) => get(`/api/compass/chart/${encodeURIComponent(key)}/current`),
  };
})();
