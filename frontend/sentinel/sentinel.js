/* Sentinel Auditor Team -- intake page.
   Recruitment copy is always visible. The intake card adapts:
   dark -> a "notify me when it opens" waitlist form;
   live + signed-out -> sign-in;
   live + signed-in -> the application form (or a pointer to the portal). */

(function () {
  const $ = (id) => document.getElementById(id);
  const RETURN_TO = '/sentinel/';
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function only(which) {
    // show exactly one of: apply form, waitlist form, note
    $('sn-apply-form').hidden = which !== 'apply';
    $('sn-waitlist-form').hidden = which !== 'waitlist';
    $('sn-intake-note').hidden = which !== 'note';
  }
  function note(html) {
    $('sn-intake-note').innerHTML = html;
    only('note');
  }
  function signInHref() {
    return '/account/?returnTo=' + encodeURIComponent(RETURN_TO);
  }

  async function init() {
    let cfg = null;
    try { cfg = await window.API.get('/api/sentinel/config'); } catch (_) {}
    window.RCTurnstile.configure(cfg && cfg.turnstile_site_key);
    if (!cfg || !cfg.enabled) {
      only('waitlist');
      wireWaitlist();
      window.RCTurnstile.mount('sn-wl-turnstile-mount');
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
    only('apply');
    wireApply();
    window.RCTurnstile.mount('sn-turnstile-mount');
  }

  function wireApply() {
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
          turnstile_token: window.RCTurnstile.token('sn-turnstile-mount'),
        }, { auth: true });
        msg.textContent = res.already_applied
          ? 'You have already applied. Status: ' + res.status + '.'
          : 'Application received. We read every one ourselves.';
        btn.textContent = 'Submitted';
        setTimeout(() => { window.location.href = '/sentinel/portal/'; }, 1400);
      } catch (err) {
        btn.disabled = false;
        window.RCTurnstile.reset('sn-turnstile-mount');
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

  function wireWaitlist() {
    const form = $('sn-waitlist-form');
    if (!form || form.dataset.wired) return;
    form.dataset.wired = '1';
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = $('sn-wl-msg');
      const btn = $('sn-wl-submit');
      const email = $('sn-wl-email').value.trim();
      if (!EMAIL_RE.test(email)) { msg.textContent = 'Please enter a valid email.'; return; }
      btn.disabled = true;
      msg.textContent = 'Adding you...';
      try {
        const res = await window.API.post('/api/sentinel/waitlist', {
          email, hp_website: $('sn-wl-hp').value || null,
          turnstile_token: window.RCTurnstile.token('sn-wl-turnstile-mount'),
        });
        msg.textContent = res.message || 'You are on the list.';
        btn.textContent = 'On the list';
      } catch (err) {
        btn.disabled = false;
        window.RCTurnstile.reset('sn-wl-turnstile-mount');
        msg.textContent = (err && err.message) ? err.message : 'Could not add you. Try again.';
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
