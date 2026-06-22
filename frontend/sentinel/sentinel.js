/* Sentinel Auditor Team -- intake page.
   The recruitment copy is always visible. The intake card adapts to the dark
   flag + sign-in state: dark -> "not open"; live+signed-out -> sign-in;
   live+signed-in -> the application form (or a pointer to the portal). */

(function () {
  const $ = (id) => document.getElementById(id);
  const RETURN_TO = '/sentinel/';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function note(html) {
    const n = $('sn-intake-note');
    if (n) { n.innerHTML = html; n.hidden = false; }
    $('sn-apply-form').hidden = true;
  }
  function showForm() {
    $('sn-intake-note').hidden = true;
    $('sn-apply-form').hidden = false;
    wireForm();
  }
  function signInHref() {
    return '/account/?returnTo=' + encodeURIComponent(RETURN_TO);
  }

  async function init() {
    let cfg = null;
    try { cfg = await window.API.get('/api/sentinel/config'); } catch (_) {}
    if (!cfg || !cfg.enabled) {
      note('<p class="sn-state-title">Intake is closed.</p>'
        + '<p class="sn-state-sub">Applications open in waves. The desk reopens soon.</p>');
      return;
    }
    try { if (window.Auth) await window.Auth.init(); } catch (_) {}
    const signedIn = !!(window.Auth && window.Auth.isSignedIn && window.Auth.isSignedIn());
    if (!signedIn) {
      note('<p class="sn-state-sub">An account keeps your findings attributed to you.</p>'
        + '<a class="sn-btn" href="' + signInHref() + '">Sign in to apply</a>');
      return;
    }
    let me = null;
    try {
      const r = await window.Auth.authedFetch('/api/sentinel/me');
      if (r.ok) me = await r.json();
    } catch (_) {}
    if (me && me.auditor_status) {
      note('<p class="sn-state-title">You have already applied.</p>'
        + '<p class="sn-state-sub">Status: ' + esc(me.auditor_status) + '.</p>'
        + '<a class="sn-btn" href="/sentinel/portal/">Open your portal</a>');
      return;
    }
    if (me && me.has_handle === false) {
      note('<p class="sn-state-sub">Claim a handle on your account first, then come back.</p>'
        + '<a class="sn-btn" href="/account/">Set up a handle</a>');
      return;
    }
    showForm();
  }

  function wireForm() {
    const form = $('sn-apply-form');
    if (!form || form.dataset.wired) return;
    form.dataset.wired = '1';
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = $('sn-apply-msg');
      const btn = $('sn-apply-submit');
      const motivation = $('sn-motivation').value.trim();
      const focus_area = $('sn-focus').value;
      if (motivation.length < 20) {
        msg.textContent = 'Tell us a little more (at least 20 characters).';
        return;
      }
      btn.disabled = true;
      msg.textContent = 'Submitting...';
      try {
        const res = await window.API.post('/api/sentinel/apply', {
          motivation, focus_area, hp_website: $('sn-hp').value || null,
        }, { auth: true });
        msg.textContent = res.already_applied
          ? 'You have already applied. Status: ' + res.status + '.'
          : 'Application received. We review every one by hand.';
        btn.textContent = 'Submitted';
        setTimeout(() => { window.location.href = '/sentinel/portal/'; }, 1400);
      } catch (err) {
        btn.disabled = false;
        if (err && err.status === 409) {
          msg.innerHTML = 'You need a handle first. <a href="/account/">Set one up</a>, then come back.';
        } else if (err && err.status === 503) {
          msg.textContent = 'The desk just closed. Try again later.';
        } else {
          msg.textContent = (err && err.message) ? err.message : 'Something went wrong. Try again.';
        }
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
