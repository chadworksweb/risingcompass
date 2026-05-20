/* Account page state machine.

   States:
     loading      -- Clerk + /api/users/me both pending
     signed-out   -- mount Clerk sign-in widget
     onboarding   -- signed in, handle is null -- show handle picker
     profile      -- signed in, handle claimed -- show profile + sign out

   Re-rendered on every Clerk auth change (sign-in, sign-out, session swap)
   via Auth.onChange.

   returnTo:
     Pages link here as `/account/?returnTo=<path>` when they want the
     user bounced back after sign-in / onboarding. We stash it in
     sessionStorage on first load so it also survives the Stripe Identity
     roundtrip (which returns to /account/ without query params).

     Sanitized: must be a same-origin path (starts with '/', not '//'),
     and must not be /account/* (no loops). */

(async () => {
  // ---------- returnTo plumbing ----------

  const RETURN_TO_KEY = 'rc_account_return_to';

  function sanitizeReturnTo(raw) {
    if (!raw || typeof raw !== 'string') return null;
    // Same-origin paths only. Reject absolute URLs, protocol-relative
    // URLs, and anything that loops back to /account/.
    if (!raw.startsWith('/') || raw.startsWith('//')) return null;
    if (raw.includes('://')) return null;
    if (raw.startsWith('/account/') || raw === '/account') return null;
    return raw;
  }

  function getReturnTo() {
    const fromQuery = new URLSearchParams(window.location.search).get('returnTo');
    const clean = sanitizeReturnTo(fromQuery);
    if (clean) return clean;
    try {
      return sanitizeReturnTo(sessionStorage.getItem(RETURN_TO_KEY));
    } catch (_) {
      return null;
    }
  }

  function stashReturnTo(rt) {
    if (!rt) return;
    try { sessionStorage.setItem(RETURN_TO_KEY, rt); } catch (_) {}
  }

  function clearReturnTo() {
    try { sessionStorage.removeItem(RETURN_TO_KEY); } catch (_) {}
  }

  // Stash the query-string returnTo on load so it survives a subsequent
  // Stripe Identity redirect that returns to /account/ with no params.
  const queryReturnTo = sanitizeReturnTo(new URLSearchParams(window.location.search).get('returnTo'));
  if (queryReturnTo) stashReturnTo(queryReturnTo);

  const el = {
    loading: document.getElementById('acct-loading'),
    signedOut: document.getElementById('acct-signed-out'),
    onboarding: document.getElementById('acct-onboarding'),
    profile: document.getElementById('acct-profile'),
    fatal: document.getElementById('acct-fatal'),
    clerkMount: document.getElementById('clerk-mount'),
    handleForm: document.getElementById('handle-form'),
    handleInput: document.getElementById('handle-input'),
    avatarInput: document.getElementById('avatar-input'),
    handleError: document.getElementById('handle-error'),
    profileHandle: document.getElementById('profile-handle'),
    profileTier: document.getElementById('profile-tier'),
    profileAvatar: document.getElementById('profile-avatar'),
    signoutBtn: document.getElementById('signout-btn'),
    onboardingSignout: document.getElementById('onboarding-signout'),
    verifyPanel: document.getElementById('acct-verify'),
    verifyBtn: document.getElementById('verify-btn'),
    verifyError: document.getElementById('verify-error'),
    verifiedBadge: document.getElementById('acct-verified-badge'),
  };

  function show(name) {
    el.loading.hidden = name !== 'loading';
    el.signedOut.hidden = name !== 'signed-out';
    el.onboarding.hidden = name !== 'onboarding';
    el.profile.hidden = name !== 'profile';
  }

  function fatal(msg) {
    el.fatal.textContent = msg;
    el.fatal.hidden = false;
    show('');
  }

  // Tracks which Clerk widget is currently mounted. null when signed in.
  // We only re-mount when the kind has to change (signin <-> signup or
  // signed-in -> signed-out). Re-mounting on every render() call destroys
  // Clerk's internal state (verify-email, forgot-password, etc).
  let widgetMode = null;

  function ensureWidget() {
    const wantsSignUp = new URLSearchParams(window.location.search).get('mode') === 'signup';
    const want = wantsSignUp ? 'signup' : 'signin';
    if (widgetMode === want) return;
    el.clerkMount.innerHTML = '';
    // Pin Clerk's redirect to /account/ so it doesn't navigate away
    // after sign-in. We handle the returnTo navigation ourselves in
    // the Auth.onChange listener below (more reliable than Clerk's
    // signInForceRedirectUrl, which gets overridden by dashboard
    // settings in some Clerk configurations).
    const opts = {
      afterSignInUrl: '/account/',
      afterSignUpUrl: '/account/',
      signInForceRedirectUrl: '/account/',
      signUpForceRedirectUrl: '/account/',
    };
    if (want === 'signup') {
      Auth.openSignUp(el.clerkMount, opts);
    } else {
      Auth.openSignIn(el.clerkMount, opts);
    }
    widgetMode = want;
  }

  function clearWidget() {
    if (widgetMode === null) return;
    el.clerkMount.innerHTML = '';
    widgetMode = null;
  }

  function renderProfile(me) {
    el.profileHandle.textContent = `@${me.handle}`;
    el.profileTier.textContent = me.tier === 'id_verified' ? 'Verified person -- Tier 2' : 'Tier 1';
    if (me.avatar_url) {
      el.profileAvatar.src = me.avatar_url;
      el.profileAvatar.hidden = false;
    } else {
      el.profileAvatar.hidden = true;
    }
    // Show the verify panel only for unverified accounts. Verified users
    // get a small badge instead.
    const isVerified = me.tier === 'id_verified';
    el.verifyPanel.hidden = isVerified;
    el.verifiedBadge.hidden = !isVerified;
    show('profile');
  }

  async function render() {
    // Don't flash 'loading' on every state change -- only on first paint.
    if (widgetMode === null && el.profile.hidden && el.onboarding.hidden) {
      show('loading');
    }
    let me;
    try {
      me = await Auth.getMe({ force: true });
    } catch (err) {
      console.error(err);
      fatal('Could not load your account. Refresh to try again.');
      return;
    }
    if (!me) {
      // Header link + localStorage are kept in sync by auth.js whenever
      // Clerk's state changes; no need to duplicate it here.
      show('signed-out');
      ensureWidget();
      return;
    }
    clearWidget();
    if (!me.onboarding_complete) {
      show('onboarding');
      el.handleInput.focus();
      return;
    }
    // Signed in + onboarded. Auto-bounce only when /account/ has nothing
    // more to offer: tier is fully verified. If tier is still 'handled'
    // we keep the user here so the Verify Identity panel is reachable;
    // the destination page (which presumably gates on verification)
    // would just send them back anyway.
    const ret = getReturnTo();
    if (ret && me.tier === 'id_verified') {
      clearReturnTo();
      window.location.replace(ret);
      return;
    }
    renderProfile(me);
  }

  el.handleForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    el.handleError.hidden = true;
    const submit = el.handleForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    submit.textContent = 'Claiming...';
    try {
      const me = await Auth.setupHandle(
        el.handleInput.value.trim(),
        el.avatarInput.value.trim() || null,
      );
      // If they came via returnTo and onboarding was the only blocker
      // (tier already id_verified, e.g. webhook fired during onboarding
      // -- rare but possible), bounce. Otherwise show the profile so
      // they can see the verify panel.
      const ret = getReturnTo();
      if (ret && me.tier === 'id_verified') {
        clearReturnTo();
        window.location.replace(ret);
        return;
      }
      renderProfile(me);
    } catch (err) {
      el.handleError.textContent = err.message || 'Could not claim handle.';
      el.handleError.hidden = false;
    } finally {
      submit.disabled = false;
      submit.textContent = 'Claim handle';
    }
  });

  el.onboardingSignout.addEventListener('click', async (ev) => {
    ev.preventDefault();
    try { await Auth.signOut(); } catch (err) { console.error(err); }
  });

  el.verifyBtn.addEventListener('click', async () => {
    el.verifyError.hidden = true;
    el.verifyBtn.disabled = true;
    el.verifyBtn.textContent = 'Starting...';
    try {
      const resp = await Auth.authedFetch('/api/users/me/verify-identity', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const msg = (typeof body.detail === 'string') ? body.detail : 'Could not start verification.';
        throw new Error(msg);
      }
      // Stripe-hosted page handles the rest. They return to /account/.
      window.location.assign(body.url);
    } catch (err) {
      el.verifyError.textContent = err.message || 'Could not start verification.';
      el.verifyError.hidden = false;
      el.verifyBtn.disabled = false;
      el.verifyBtn.textContent = 'Verify with Stripe Identity';
    }
  });

  el.signoutBtn.addEventListener('click', async () => {
    el.signoutBtn.disabled = true;
    try {
      await Auth.signOut();
    } catch (err) {
      console.error(err);
      el.signoutBtn.disabled = false;
    }
    // Auth.onChange re-renders.
  });

  try {
    await Auth.init();
  } catch (err) {
    console.error(err);
    fatal('Could not initialise sign-in. Check your network connection and refresh.');
    return;
  }

  Auth.onChange((evt) => {
    // On a fresh sign-in transition, redirect to returnTo immediately.
    // This is the canonical "I just clicked Sign in from somewhere
    // else" case and it should always bounce -- we don't gate on
    // tier here because the destination page (e.g. /motion-desk/)
    // handles its own gate panel for unverified users.
    if (evt && evt.justSignedIn) {
      const ret = getReturnTo();
      if (ret) {
        clearReturnTo();
        window.location.replace(ret);
        return;
      }
    }
    render().catch((err) => console.error(err));
  });
  render().catch((err) => console.error(err));
})();
