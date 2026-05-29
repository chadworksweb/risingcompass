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
    verifyConsent: document.getElementById('verify-consent'),
    verifyError: document.getElementById('verify-error'),
    verifiedBadge: document.getElementById('acct-verified-badge'),
    verifiedName: document.getElementById('verified-name'),
    vibeActivityState: document.getElementById('vibe-activity-state'),
    vibeActivityList: document.getElementById('vibe-activity-list'),
    calibrationsState: document.getElementById('calibrations-state'),
    calibrationsList: document.getElementById('calibrations-list'),
    notifState: document.getElementById('notif-state'),
    notifList: document.getElementById('notif-list'),
    notifBadge: document.getElementById('notif-unread-badge'),
    notifMarkRead: document.getElementById('notif-markread'),
  };

  const TIER_LABELS = {
    violet: 'Ascended', blue: 'Elevated', green: 'Decent',
    orange: 'Degraded', red: 'Corrupted',
  };

  const DIR_GLYPH = { 1: '↑', 0: '=', '-1': '↓' };
  const DIR_WORD = { 1: 'pushed higher', 0: 'agreed', '-1': 'pushed lower' };

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function fmtScore(v) {
    if (v == null) return '--';
    return (v > 0 ? '+' : '') + v;
  }

  function fmtDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  async function loadVibeActivity() {
    if (!el.vibeActivityState || !el.vibeActivityList) return;
    el.vibeActivityState.hidden = false;
    el.vibeActivityState.textContent = 'Loading your activity...';
    el.vibeActivityList.hidden = true;
    el.vibeActivityList.innerHTML = '';
    let data;
    try {
      const resp = await Auth.authedFetch('/api/vibe/me/activity');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
    } catch (err) {
      console.error('vibe activity load failed', err);
      const code = ((err && err.message) || '').match(/\d{3}/);
      el.vibeActivityState.textContent = code && code[0] === '401'
        ? 'Sign in to see your voting activity.'
        : 'Could not load your activity right now.';
      return;
    }
    const items = (data && data.items) || [];
    if (!items.length) {
      el.vibeActivityState.textContent = "You haven't voted on any songs yet. Open a song and weigh in on its Audience Vibe.";
      return;
    }
    el.vibeActivityState.hidden = true;
    el.vibeActivityList.hidden = false;
    el.vibeActivityList.innerHTML = items.map((it) => {
      const title = it.song_title || 'Unknown song';
      const titleHtml = it.song_slug
        ? `<a class="account-activity-song" href="/songs/${encodeURIComponent(it.song_slug)}">${esc(title)}</a>`
        : `<span class="account-activity-song">${esc(title)}</span>`;
      const artist = it.song_artist ? `<span class="account-activity-artist">${esc(it.song_artist)}</span>` : '';
      const dir = String(it.direction);
      // "currently" links to the song's Audience Vibe section.
      const currently = it.song_slug
        ? `<a class="account-activity-currently" href="/songs/${encodeURIComponent(it.song_slug)}#section-vibe">currently</a>`
        : 'currently';
      return `
        <li class="account-activity-item">
          <span class="account-activity-dir account-activity-dir--${dir === '-1' ? 'lower' : (dir === '1' ? 'higher' : 'agree')}" aria-hidden="true">${DIR_GLYPH[dir] || '='}</span>
          <span class="account-activity-meta">
            <span class="account-activity-songline">${titleHtml}${artist}</span>
            <span class="account-activity-detail">You ${DIR_WORD[dir] || 'agreed'} in ${it.push_year} &middot; audience ${currently} ${fmtScore(it.current_value)}</span>
          </span>
          <span class="account-activity-date">${fmtDate(it.pushed_at)}</span>
        </li>`;
    }).join('');
  }

  async function loadCalibrations() {
    if (!el.calibrationsState || !el.calibrationsList) return;
    el.calibrationsState.hidden = false;
    el.calibrationsState.textContent = 'Loading your calibrations...';
    el.calibrationsList.hidden = true;
    el.calibrationsList.innerHTML = '';
    let data;
    try {
      const resp = await Auth.authedFetch('/api/users/me/calibrations');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
    } catch (err) {
      console.error('calibrations load failed', err);
      const code = ((err && err.message) || '').match(/\d{3}/);
      el.calibrationsState.textContent = code && code[0] === '401'
        ? 'Sign in to see the songs you\'ve calibrated.'
        : 'Could not load your calibrations right now.';
      return;
    }
    const items = (data && data.items) || [];
    if (!items.length) {
      el.calibrationsState.textContent = "You haven't calibrated any songs yet. Run some lyrics through Lyrical Charger while signed in and they'll show up here.";
      return;
    }
    el.calibrationsState.hidden = true;
    el.calibrationsList.hidden = false;
    el.calibrationsList.innerHTML = items.map((it) => {
      const title = it.title || 'Untitled';
      const titleHtml = it.song_slug
        ? `<a class="account-activity-song" href="/songs/${encodeURIComponent(it.song_slug)}">${esc(title)}</a>`
        : `<span class="account-activity-song">${esc(title)}</span>`;
      const artist = it.artist ? `<span class="account-activity-artist">${esc(it.artist)}</span>` : '';
      const color = it.rubric_color || '';
      const tierLabel = TIER_LABELS[color] || '';
      const charge = fmtScore(it.charge_value);
      const dot = color
        ? `<span class="account-cal-dot account-cal-dot--${esc(color)}" aria-hidden="true"></span>`
        : '';
      return `
        <li class="account-activity-item">
          ${dot}
          <span class="account-activity-meta">
            <span class="account-activity-songline">${titleHtml}${artist}</span>
            <span class="account-activity-detail">${esc(tierLabel)} &middot; ${charge}</span>
          </span>
          <span class="account-activity-date">${fmtDate(it.calibrated_at)}</span>
        </li>`;
    }).join('');
  }

  function notifText(it) {
    const who = it.actor_handle ? `@${esc(it.actor_handle)}` : 'Someone';
    const verb = it.type === 'mention' ? 'mentioned you in a comment' : 'replied to your comment';
    return `${who} ${verb}`;
  }

  async function loadNotifications() {
    if (!el.notifState || !el.notifList) return;
    el.notifState.hidden = false;
    el.notifState.textContent = 'Loading your notifications...';
    el.notifList.hidden = true;
    el.notifList.innerHTML = '';
    if (el.notifBadge) el.notifBadge.hidden = true;
    if (el.notifMarkRead) el.notifMarkRead.hidden = true;
    let data;
    try {
      const resp = await Auth.authedFetch('/api/users/me/notifications');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
    } catch (err) {
      console.error('notifications load failed', err);
      const code = ((err && err.message) || '').match(/\d{3}/);
      el.notifState.textContent = code && code[0] === '401'
        ? 'Sign in to see your notifications.'
        : 'Could not load your notifications right now.';
      return;
    }
    const items = (data && data.items) || [];
    const unread = (data && data.unread_count) || 0;
    if (el.notifBadge && unread > 0) {
      el.notifBadge.textContent = String(unread);
      el.notifBadge.hidden = false;
    }
    if (el.notifMarkRead) el.notifMarkRead.hidden = unread === 0;
    if (!items.length) {
      el.notifState.textContent = 'No notifications yet. Replies and @mentions in the Lobby show up here.';
      return;
    }
    el.notifState.hidden = true;
    el.notifList.hidden = false;
    el.notifList.innerHTML = items.map((it) => {
      const cls = it.read ? 'account-notif-item' : 'account-notif-item account-notif-item--unread';
      const headline = notifText(it);
      const headlineHtml = it.link
        ? `<a class="account-notif-headline" href="${esc(it.link)}">${headline}</a>`
        : `<span class="account-notif-headline">${headline}</span>`;
      const snippet = it.snippet
        ? `<span class="account-notif-snippet">${esc(it.snippet)}</span>` : '';
      return `
        <li class="${cls}">
          <span class="account-activity-meta">
            ${headlineHtml}
            ${snippet}
          </span>
          <span class="account-activity-date">${fmtDate(it.created_at)}</span>
        </li>`;
    }).join('');
  }

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

  // --- Pre-launch gate ---------------------------------------------------
  // Public flag (system_flags 'launch.locked', exposed at /api/launch-status).
  // When locked we fade the sign-up form + billing buttons; sign-IN stays
  // open for existing accounts. Fail CLOSED: if the status can't be read we
  // treat it as locked, matching the "not ready to launch" intent. (The
  // billing checkout endpoints enforce the same gate server-side -- this is
  // the UX layer.)
  let launchLocked = true;
  let launchMessage = '';
  async function loadLaunchStatus() {
    try {
      const resp = await fetch('/api/launch-status');
      if (!resp.ok) return;            // keep the fail-closed default
      const data = await resp.json();
      launchLocked = !!data.locked;
      launchMessage = data.message || '';
    } catch (_) { /* keep locked on error */ }
  }

  function renderSignupLocked() {
    el.clerkMount.innerHTML =
      '<div class="rc-launch-lock">' +
        '<h2 class="rc-launch-lock-title">Sign-ups opening soon</h2>' +
        '<p class="rc-launch-lock-msg">' + esc(launchMessage) + '</p>' +
        '<p class="rc-launch-lock-sub">Already have an account? ' +
          '<a href="/account/">Sign in</a>.</p>' +
      '</div>';
  }

  function lockWalletActions() {
    const actions = document.querySelector('#wallet-body .wallet-actions');
    if (!actions) return;
    actions.classList.add('wallet-actions--locked');
    actions.querySelectorAll('button[data-billing]').forEach((b) => {
      b.disabled = true;
      b.setAttribute('aria-disabled', 'true');
    });
    let note = document.getElementById('wallet-locked-note');
    if (!note) {
      note = document.createElement('p');
      note.id = 'wallet-locked-note';
      note.className = 'wallet-locked-note';
      actions.parentNode.insertBefore(note, actions);
    }
    note.textContent = launchMessage || 'Subscriptions open soon.';
  }

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
      if (launchLocked) {
        // Pre-launch: don't mount Clerk SignUp. Show the faded gate panel
        // instead so the public can't create accounts yet.
        renderSignupLocked();
      } else {
        Auth.openSignUp(el.clerkMount, opts);
      }
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
    // Show the user the legal name on file (publicly shown in the Chamber).
    if (el.verifiedName) {
      if (isVerified && me.legal_name) {
        el.verifiedName.textContent = `Shown in the Chamber as: ${me.legal_name}`;
        el.verifiedName.hidden = false;
      } else {
        el.verifiedName.hidden = true;
      }
    }
    show('profile');
    // Personal vibe voting history — fire-and-forget, never blocks the profile.
    loadVibeActivity().catch((err) => console.error(err));
    // Songs calibrated through Lyrical Charger — same fire-and-forget pattern.
    loadCalibrations().catch((err) => console.error(err));
    // Reply / @mention notifications — same fire-and-forget pattern.
    loadNotifications().catch((err) => console.error(err));
    // Wallet (M5): tier + buckets via /api/billing/me; subscribe / pack buttons.
    loadWallet().catch((err) => console.error(err));
  }

  // --- Wallet (M5) -------------------------------------------------------
  async function loadWallet() {
    const stateEl = document.getElementById('wallet-state');
    const bodyEl  = document.getElementById('wallet-body');
    if (!stateEl || !bodyEl) return;
    let snap;
    try {
      const resp = await Auth.authedFetch('/api/billing/me');
      if (!resp.ok) throw new Error(`GET /api/billing/me failed: ${resp.status}`);
      snap = await resp.json();
    } catch (err) {
      stateEl.textContent = 'Could not load wallet.';
      console.error(err);
      return;
    }
    document.getElementById('wallet-tier').textContent       = snap.tier_label || snap.tier || 'Free';
    document.getElementById('wallet-allowance').textContent  = (snap.allowance_credits || 0).toLocaleString();
    document.getElementById('wallet-purchased').textContent  = (snap.purchased_credits || 0).toLocaleString();
    document.getElementById('wallet-total').textContent      = (snap.total_credits || 0).toLocaleString();
    const renewalEl = document.getElementById('wallet-renewal');
    if (snap.period_end && snap.is_paid) {
      const d = new Date(snap.period_end);
      if (!Number.isNaN(d.getTime())) {
        renewalEl.textContent = `Allowance resets ${d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}.`;
        renewalEl.hidden = false;
      }
    }
    stateEl.hidden = true;
    bodyEl.hidden = false;
    if (launchLocked) lockWalletActions();
  }

  async function startBillingCheckout(kind, key) {
    const errEl = document.getElementById('wallet-error');
    if (errEl) { errEl.hidden = true; errEl.textContent = ''; }
    const success_url = `${window.location.origin}/account/?billing=success`;
    const cancel_url  = `${window.location.origin}/account/?billing=cancel`;
    const path = kind === 'subscribe'
      ? '/api/billing/checkout/subscription'
      : '/api/billing/checkout/pack';
    const body = kind === 'subscribe'
      ? { tier: key, success_url, cancel_url }
      : { pack: key, success_url, cancel_url };
    try {
      const resp = await Auth.authedFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`Checkout failed (${resp.status}): ${text}`);
      }
      const data = await resp.json();
      if (data.url) {
        window.location.assign(data.url);
      } else {
        throw new Error('Checkout response missing redirect URL.');
      }
    } catch (err) {
      if (errEl) {
        errEl.textContent = err.message || 'Could not start checkout.';
        errEl.hidden = false;
      } else {
        console.error(err);
      }
    }
  }

  document.addEventListener('click', (ev) => {
    const btn = ev.target instanceof Element ? ev.target.closest('[data-billing]') : null;
    if (!btn) return;
    if (launchLocked) return;   // pre-launch: buttons are faded; backend 503s anyway
    const kind = btn.dataset.billing;
    if (kind === 'subscribe' && btn.dataset.tier) {
      startBillingCheckout('subscribe', btn.dataset.tier);
    } else if (kind === 'pack' && btn.dataset.pack) {
      startBillingCheckout('pack', btn.dataset.pack);
    }
  });

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

  // The verify button stays disabled until the user ticks the public
  // legal-name consent box -- the backend also enforces this, but gating
  // the button makes the requirement obvious.
  if (el.verifyConsent) {
    el.verifyConsent.addEventListener('change', () => {
      el.verifyBtn.disabled = !el.verifyConsent.checked;
    });
  }

  el.verifyBtn.addEventListener('click', async () => {
    if (el.verifyConsent && !el.verifyConsent.checked) {
      el.verifyError.textContent = 'Please confirm the legal-name consent first.';
      el.verifyError.hidden = false;
      return;
    }
    el.verifyError.hidden = true;
    el.verifyBtn.disabled = true;
    el.verifyBtn.textContent = 'Starting...';
    try {
      const resp = await Auth.authedFetch('/api/users/me/verify-identity', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name_consent: true }),
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

  if (el.notifMarkRead) {
    el.notifMarkRead.addEventListener('click', async () => {
      el.notifMarkRead.disabled = true;
      try {
        const resp = await Auth.authedFetch('/api/users/me/notifications/mark-read', { method: 'POST' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        await loadNotifications();
      } catch (err) {
        console.error('mark-read failed', err);
      } finally {
        el.notifMarkRead.disabled = false;
      }
    });
  }

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

  // Resolve the pre-launch gate before any render so the sign-up widget is
  // never briefly mountable while locked.
  await loadLaunchStatus();

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
