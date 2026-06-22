/* Sentinel Auditor leaderboard -- public once the program is live.
   Dark-gated via /api/sentinel/config; the leaderboard read itself 503s while
   dark, so we check config first and show a closed state. */

(function () {
  const mount = () => document.getElementById('sn-board-mount');

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  async function init() {
    let cfg;
    try { cfg = await window.API.get('/api/sentinel/config'); } catch (_) { cfg = null; }
    if (!cfg || !cfg.enabled) {
      mount().innerHTML = '<p>The Sentinel Auditor Team is not open yet.</p>';
      return;
    }
    let rows = [];
    try {
      const data = await window.API.get('/api/sentinel/leaderboard');
      rows = (data && data.leaderboard) || [];
    } catch (_) {
      mount().innerHTML = '<p>Could not load the leaderboard right now.</p>';
      return;
    }
    if (!rows.length) {
      mount().innerHTML = '<p class="sn-apply-note">No confirmed findings yet. Be the first.</p>';
      return;
    }
    mount().innerHTML =
      '<table class="sn-board"><thead><tr>'
      + '<th class="sn-rank">#</th><th>Auditor</th><th>Tier</th>'
      + '<th>Findings</th><th>Points</th></tr></thead><tbody>'
      + rows.map((r) =>
          '<tr><td class="sn-rank">' + r.rank + '</td>'
          + '<td>@' + esc(r.handle) + '</td>'
          + '<td>' + esc(r.tier) + '</td>'
          + '<td>' + r.accepted_count + '</td>'
          + '<td class="sn-pts">' + r.points + '</td></tr>'
        ).join('')
      + '</tbody></table>';
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
