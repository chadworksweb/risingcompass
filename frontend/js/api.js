/* === API Client === */

const API = (() => {
  const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const BASE = '';  // same-origin relative; dev_server proxies /api -> :8000 locally

  const API_KEY = isLocal
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  // nginx returns a brief 502/504 while the backend container restarts on
  // deploy; retry with short backoff so a page load mid-deploy recovers.
  // 4xx is not retried (won't improve). 8s timeout is ample now that the
  // backend talks to Postgres over the VPC (<1s typical) -- the old 20s
  // budget was sized for the WAN round-trip to Turso, which is gone.
  async function get(path, { attempts = 3, timeoutMs = 8000 } = {}) {
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
    getDailyChart: (days) => get(`/api/compass/daily-chart${days ? '?days=' + days : ''}`),
    getChartAnomalies: () => get('/api/compass/anomalies'),
    getEtherToday: () => get('/api/ether-art-chart/today'),
    getEtherDate: (date) => get(`/api/ether-art-chart/date/${date}`),
    getEtherYears: () => get('/api/ether-art-chart/years'),
    getEtherYear: (year) => get(`/api/ether-art-chart/year/${year}`),
    getChartSnapshot: (key) => get(`/api/compass/chart/${encodeURIComponent(key)}/current`),
    // All-time boards (top 100, refreshed monthly / annual-manual). Paired
    // left-regular + right-ether pages under /charts/.
    getAlltimeStreams: () => get('/api/charts/alltime/streams'),
    getAlltimeAlbums: () => get('/api/charts/alltime/albums'),
    getAlltimeStreamAlbums: () => get('/api/charts/alltime/stream-albums'),
    // Chart-agnostic Calendar feeds: mirror the drift endpoints for any
    // chart-snapshot chart (key = CHART_REGISTRY key, e.g. "itunes").
    // getCalendarCharts lists the charts the Calendar should offer (Daily
    // Listens + every Tier-2 daily chart that has painted data).
    getCalendarCharts: () => get('/api/compass/chart/calendar-charts'),
    getChartYears: (key) => get(`/api/compass/chart/${encodeURIComponent(key)}/years`),
    getChartYearDates: (key, year) => get(`/api/compass/chart/${encodeURIComponent(key)}/years/${year}/dates`),
    getChartReading: (key, date) => get(`/api/compass/chart/${encodeURIComponent(key)}/reading/${date}`),
    // Charger Activity feeds (public). overview = all three at once for first paint.
    getChargerActivityOverview: (limit = 12) => get(`/api/charger-activity/overview?limit=${limit}`),
    getChargerActivityFeed: (feed, { window, limit = 20, offset = 0 } = {}) => {
      const qs = new URLSearchParams({ limit, offset });
      if (window) qs.set('window', window);
      return get(`/api/charger-activity/${feed}?${qs.toString()}`);
    },
  };
})();
