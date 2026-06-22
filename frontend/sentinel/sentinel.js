/* Sentinel Auditor Team -- landing + apply page.
   Dark-gated: GET /api/sentinel/config decides whether anything shows. The
   apply form needs a signed-in account with a claimed handle; otherwise we send
   the visitor through the standard /account/ returnTo flow. */

(function () {
  const $ = (id) => document.getElementById(id);
  const RETURN_TO = '/sentinel/';

  function signInHref() {
    return '/account/?returnTo=' + encodeURIComponent(RETURN_TO);
  }

  function renderCta(html) {
    const el = $('sn-cta');
    if (el) el.innerHTML = html;
  }

  function showClosed() {
    $('sn-explainer').hidden = true;
    $('sn-apply').hidden = true;
    $('sn-closed').hidden = false;
    renderCta('');
  }

  async function init() {
    let cfg;
    try {
      cfg = await window.API.get('/api/sentinel/config');
    } catch (_) {
      showClosed();
      return;
    }
    if (!cfg || !cfg.enabled) {
      showClosed();
      return;
    }

    // Live. Decide the apply path based on sign-in state.
    try { if (window.Auth) await window.Auth.init(); } catch (_) {}
    const signedIn = !!(window.Auth && window.Auth.isSignedIn && window.Auth.isSignedIn());

    if (!signedIn) {
      renderCta('<a class="sn-btn" href="' + signInHref() + '">Sign in to apply</a>'
        + ' <a class="sn-btn sn-btn-ghost" href="/sentinel/leaderboard/">See the leaderboard</a>');
      return;
    }

    renderCta('<a class="sn-btn sn-btn-ghost" href="/sentinel/portal/">Your auditor portal</a>'
      + ' <a class="sn-btn sn-btn-ghost" href="/sentinel/leaderboard/">Leaderboard</a>');

    // If they already applied, send them to the portal instead of re-showing the form.
    let me = null;
    try {
      const resp = await window.Auth.authedFetch('/api/sentinel/me');
      if (resp.ok) me = await resp.json();
    } catch (_) {}

    if (me && me.auditor_status) {
      $('sn-apply').hidden = true;
      renderCta('<a class="sn-btn" href="/sentinel/portal/">Go to your portal</a>'
        + ' <a class="sn-btn sn-btn-ghost" href="/sentinel/leaderboard/">Leaderboard</a>');
      return;
    }
    if (me && me.has_handle === false) {
      $('sn-apply').hidden = true;
      renderCta('<a class="sn-btn" href="/account/">Claim a handle first</a>');
      return;
    }

    $('sn-apply').hidden = false;
    wireForm();
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
          : 'Application received. We will review it soon.';
        form.querySelector('button[type=submit]').textContent = 'Submitted';
        setTimeout(() => { window.location.href = '/sentinel/portal/'; }, 1400);
      } catch (err) {
        btn.disabled = false;
        if (err && err.status === 409) {
          msg.innerHTML = 'You need a handle first. <a href="/account/">Set one up</a>, then come back.';
        } else if (err && err.status === 503) {
          msg.textContent = 'The program just closed. Try again later.';
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
