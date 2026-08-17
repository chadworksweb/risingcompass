/* Sentinel Auditor portal -- signed-in auditor surface.
   Branches on GET /api/sentinel/me: not-applied / pending / rejected / revoked /
   approved. Approved auditors get the finding form + their findings list. */

(function () {
  const $ = (id) => document.getElementById(id);
  const RETURN_TO = '/sentinel/portal/';
  let scope = 'song';
  let pickedSong = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function setState(html) { $('sn-state').innerHTML = html; }

  async function init() {
    let cfg;
    try { cfg = await window.API.get('/api/sentinel/config'); } catch (_) { cfg = null; }
    window.RCTurnstile.configure(cfg && cfg.turnstile_site_key);
    if (!cfg || !cfg.enabled) {
      setState('<p>The Sentinel Auditor Team is not open right now.</p>');
      return;
    }
    try { if (window.Auth) await window.Auth.init(); } catch (_) {}
    if (!(window.Auth && window.Auth.isSignedIn && window.Auth.isSignedIn())) {
      setState('<p>You need to sign in to reach the portal. '
        + '<a href="/account/?returnTo=' + encodeURIComponent(RETURN_TO) + '">Sign in</a>.</p>');
      return;
    }

    let me = null;
    try {
      const resp = await window.Auth.authedFetch('/api/sentinel/me');
      if (resp.ok) me = await resp.json();
    } catch (_) {}
    if (!me) { setState('<p>Could not load your auditor status. Try again.</p>'); return; }

    if (!me.auditor_status) {
      setState('<p>You have not applied yet. '
        + '<a href="/sentinel/">Apply to join the team</a>.</p>');
      return;
    }
    if (me.auditor_status === 'pending') {
      setState('<p>Your application is under review. We will be in touch.</p>');
      return;
    }
    if (me.auditor_status === 'rejected') {
      setState('<p>Your application was not accepted this time.</p>');
      return;
    }
    if (me.auditor_status === 'revoked') {
      setState('<p>Your auditor access has been paused.</p>');
      return;
    }

    // approved
    setState('');
    $('sn-approved').hidden = false;
    const c = me.contribution || { filed: 0, confirmed: 0 };
    $('sn-filed').textContent = c.filed;
    $('sn-confirmed').textContent = c.confirmed;
    wireScopeToggle();
    wireSongPicker();
    wireFindingForm();
    // Render only once the finding form is actually on screen -- Turnstile
    // will not render into a hidden container.
    window.RCTurnstile.mount('sn-finding-turnstile-mount');
    loadMyFindings();
  }

  function wireScopeToggle() {
    const bar = $('sn-scope-toggle');
    bar.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-scope]');
      if (!btn) return;
      scope = btn.dataset.scope;
      bar.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      $('sn-song-field').style.display = scope === 'song' ? '' : 'none';
    });
  }

  function wireSongPicker() {
    const input = $('sn-song-search');
    const results = $('sn-song-results');
    let timer = null;
    input.addEventListener('input', () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) { results.hidden = true; return; }
      timer = setTimeout(async () => {
        try {
          const data = await window.API.get('/api/songs/search?q=' + encodeURIComponent(q) + '&limit=8');
          // The live library search returns {items:[...]}; older variants used
          // {results:[...]}. Accept either so the picker is endpoint-agnostic.
          const rows = (data && (data.items || data.results)) || [];
          if (!rows.length) { results.hidden = true; return; }
          results.innerHTML = rows.map((r) =>
            '<div class="sn-pick" data-id="' + r.id + '" data-label="'
            + esc((r.title || '') + ' - ' + (r.artist || '')) + '">'
            + esc(r.title) + ' <span style="color:#888;">- ' + esc(r.artist || '') + '</span></div>'
          ).join('');
          results.hidden = false;
        } catch (_) { results.hidden = true; }
      }, 250);
    });
    results.addEventListener('click', (e) => {
      const pick = e.target.closest('.sn-pick');
      if (!pick) return;
      pickedSong = { id: parseInt(pick.dataset.id, 10), label: pick.dataset.label };
      results.hidden = true;
      input.value = '';
      const picked = $('sn-song-picked');
      picked.hidden = false;
      picked.innerHTML = 'Selected: <strong>' + esc(pickedSong.label) + '</strong>'
        + '<button type="button" id="sn-clear-song">change</button>';
      $('sn-clear-song').addEventListener('click', () => {
        pickedSong = null; picked.hidden = true; picked.innerHTML = '';
      });
    });
  }

  function wireFindingForm() {
    const form = $('sn-finding-form');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = $('sn-finding-msg');
      const btn = $('sn-finding-submit');
      const title = $('sn-title').value.trim();
      const description = $('sn-description').value.trim();
      if (title.length < 3 || description.length < 20) {
        msg.textContent = 'Add a title and a fuller description.';
        return;
      }
      if (scope === 'song' && !pickedSong) {
        msg.textContent = 'Pick a song, or switch to a general finding.';
        return;
      }
      btn.disabled = true;
      msg.textContent = 'Submitting...';
      try {
        await window.API.post('/api/sentinel/findings', {
          scope,
          category: $('sn-category').value,
          title,
          description,
          song_id: scope === 'song' ? pickedSong.id : null,
          evidence_url: $('sn-evidence').value.trim() || null,
          proposed_severity: $('sn-severity').value,
          hp_website: $('sn-hp').value || null,
          turnstile_token: window.RCTurnstile.token('sn-finding-turnstile-mount'),
        }, { auth: true });
        msg.textContent = 'Finding submitted. Thank you.';
        form.reset();
        pickedSong = null;
        $('sn-song-picked').hidden = true;
        // Token is single use; a second finding needs a fresh one.
        window.RCTurnstile.reset('sn-finding-turnstile-mount');
        btn.disabled = false;
        loadMyFindings();
      } catch (err) {
        btn.disabled = false;
        window.RCTurnstile.reset('sn-finding-turnstile-mount');
        msg.textContent = (err && err.message) ? err.message : 'Submit failed. Try again.';
      }
    });
  }

  const STATUS_LABEL = {
    new: 'New', triaged: 'Triaged', investigating: 'Investigating',
    confirmed: 'Confirmed', fixed: 'Fixed', accepted: 'Accepted',
    rejected: 'Not accepted', duplicate: 'Duplicate', wont_fix: "Won't fix",
  };

  async function loadMyFindings() {
    let items = [];
    try {
      const resp = await window.Auth.authedFetch('/api/sentinel/findings/mine');
      if (resp.ok) items = (await resp.json()).items || [];
    } catch (_) {}
    const el = $('sn-my-findings');
    if (!items.length) { el.innerHTML = '<p class="sn-apply-note">None yet.</p>'; return; }
    el.innerHTML = items.map((f) => {
      const target = f.scope === 'song'
        ? (f.song_slug
            ? '<a href="/songs/' + esc(f.song_slug) + '" style="color:var(--rc-accent,#00d4aa);">' + esc(f.song_title || 'song') + '</a>'
            : esc(f.song_title || '(song removed)'))
        : 'general / ' + esc(f.category);
      const sev = f.accepted_severity || f.proposed_severity;
      const disp = f.disposition ? '<div class="sn-disp">Reply: ' + esc(f.disposition) + '</div>' : '';
      return '<div class="sn-finding">'
        + '<div class="sn-fhead"><div>'
        + '<span class="sn-status">' + esc(STATUS_LABEL[f.status] || f.status) + '</span> '
        + '<strong style="margin-left:.4rem;">' + esc(f.title) + '</strong></div>'
        + '<div style="color:#888;font-size:.78rem;">' + esc(sev) + '</div></div>'
        + '<div style="color:#9a9aac;font-size:.8rem;margin-top:.25rem;">' + target + '</div>'
        + disp + '</div>';
    }).join('');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
