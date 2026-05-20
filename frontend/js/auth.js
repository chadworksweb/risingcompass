/* === Public Participation Tier 1 auth (Clerk + local /api/users/me) ===

   Wraps Clerk JS in a tiny singleton so the rest of the frontend never has
   to think about Clerk directly. All pages that need auth should:

     1. <script src="/js/auth.js" defer></script>
     2. await Auth.init()
     3. const me = await Auth.getMe();   // null when signed out
     4. Use Auth.openSignIn(el), Auth.signOut(), Auth.authedFetch(path, opts)

   The Clerk publishable key (pk_test_* / pk_live_*) is safe to expose --
   that's the whole point of "publishable". The frontend-API host is base64-
   encoded inside the PK; we decode it to know where to load clerk.browser.js
   from. No CDN guessing.

   Switch dev / prod by the hostname check, mirroring api.js. Update PK_LIVE
   to the prod publishable key once the prod Clerk env exists. */

const Auth = (() => {
  const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);

  const PK_TEST = 'pk_test_cm9idXN0LW1hbW1vdGgtOTAuY2xlcmsuYWNjb3VudHMuZGV2JA';
  const PK_LIVE = '';  // TODO: set when prod Clerk env is created

  const PK = isLocal ? PK_TEST : (PK_LIVE || PK_TEST);

  const API_BASE = isLocal
    ? `http://${window.location.hostname}:8000`
    : 'https://api.risingcompass.net';

  let clerk = null;
  let loadPromise = null;
  const listeners = new Set();
  let cachedMe = undefined;  // undefined = not fetched, null = anonymous
  let _prevSignedIn = null;  // null = unknown, true/false = last observed

  // ---------- header link + localStorage sync ----------
  // The inline script in the header partial reads localStorage('rc_authed')
  // on every page load and sets the link text + href. That works the
  // FIRST time but goes stale across the Clerk redirect chain. Every
  // auth transition through this module pushes the canonical state out
  // (a) to localStorage so the next page load is correct and (b) to
  // the current page's link DOM so the label flips without a reload.
  function _syncAuthState(authed) {
    try {
      if (authed) localStorage.setItem('rc_authed', '1');
      else localStorage.removeItem('rc_authed');
    } catch (_) {}
    const link = document.getElementById('rc-account-link');
    if (!link) return;
    if (authed) {
      link.textContent = 'Account';
      link.setAttribute('data-state', 'in');
      link.href = '/account/';
    } else {
      link.textContent = 'Sign in';
      link.setAttribute('data-state', 'out');
      const ret = window.location.pathname + window.location.search + window.location.hash;
      if (ret && !ret.startsWith('/account/')) {
        link.href = '/account/?returnTo=' + encodeURIComponent(ret);
      } else {
        link.href = '/account/';
      }
    }
  }

  function decodePkHost(pk) {
    // pk_(test|live)_<base64 of host plus trailing '$'>
    const parts = pk.split('_');
    const b64 = parts[parts.length - 1];
    try {
      return atob(b64).replace(/\$+$/, '');
    } catch (err) {
      throw new Error(`Could not decode Clerk frontend API host from publishable key: ${err.message}`);
    }
  }

  async function loadClerkScript() {
    if (window.Clerk) return;
    const host = decodePkHost(PK);
    const url = `https://${host}/npm/@clerk/clerk-js@5/dist/clerk.browser.js`;
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = url;
      s.async = true;
      s.crossOrigin = 'anonymous';
      s.dataset.clerkPublishableKey = PK;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`Failed to load Clerk script from ${url}`));
      document.head.appendChild(s);
    });
  }

  async function init() {
    if (clerk) return clerk;
    if (loadPromise) return loadPromise;
    loadPromise = (async () => {
      await loadClerkScript();
      // window.Clerk is a constructor on v5+; v4 was an instance. Handle both.
      const inst = typeof window.Clerk === 'function' ? new window.Clerk(PK) : window.Clerk;
      await inst.load({});
      clerk = inst;
      // Initial sync from whatever Clerk knows right now.
      const initialSignedIn = !!clerk.user;
      _prevSignedIn = initialSignedIn;
      _syncAuthState(initialSignedIn);
      clerk.addListener(() => {
        cachedMe = undefined;
        const isSignedIn = !!clerk.user;
        // Detect a transition into the signed-in state. We use this to
        // run our own post-sign-in redirect because Clerk's
        // signInForceRedirectUrl is not reliably honored across all
        // configurations / dashboard settings -- much simpler to own
        // the navigation here.
        const justSignedIn = (_prevSignedIn === false) && isSignedIn;
        const justSignedOut = (_prevSignedIn === true) && !isSignedIn;
        _prevSignedIn = isSignedIn;
        _syncAuthState(isSignedIn);
        const evt = { isSignedIn, justSignedIn, justSignedOut };
        for (const fn of listeners) {
          try { fn(evt); } catch (err) { console.error('Auth listener error', err); }
        }
      });
      return clerk;
    })();
    return loadPromise;
  }

  function require() {
    if (!clerk) throw new Error('Auth.init() must be awaited before use');
    return clerk;
  }

  function isSignedIn() {
    return !!(clerk && clerk.user);
  }

  async function getToken() {
    const c = require();
    if (!c.session) return null;
    return c.session.getToken();
  }

  async function authedFetch(path, opts = {}) {
    const token = await getToken();
    const headers = new Headers(opts.headers || {});
    if (token) headers.set('Authorization', `Bearer ${token}`);
    return fetch(`${API_BASE}${path}`, { ...opts, headers });
  }

  async function getMe({ force = false } = {}) {
    if (!isSignedIn()) {
      cachedMe = null;
      return null;
    }
    if (!force && cachedMe !== undefined) return cachedMe;
    const resp = await authedFetch('/api/users/me');
    if (resp.status === 401 || resp.status === 403) {
      cachedMe = null;
      return null;
    }
    if (!resp.ok) throw new Error(`GET /api/users/me failed: ${resp.status}`);
    cachedMe = await resp.json();
    return cachedMe;
  }

  // FastAPI returns either a string `detail` (HTTPException) or an array of
  // {type, loc, msg, input} dicts (Pydantic validation). Pull a clean
  // human-readable string out either way.
  function pickErrorMessage(body, fallback) {
    if (!body) return fallback;
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail) && body.detail.length) {
      const first = body.detail[0];
      if (first && typeof first.msg === 'string') {
        // Pydantic prefixes validator errors with "Value error, " -- trim.
        return first.msg.replace(/^Value error,\s*/, '');
      }
    }
    return fallback;
  }

  async function setupHandle(handle, avatarUrl) {
    const resp = await authedFetch('/api/users/me/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ handle, avatar_url: avatarUrl || null }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const msg = pickErrorMessage(body, `Handle setup failed: ${resp.status}`);
      const err = new Error(msg);
      err.status = resp.status;
      throw err;
    }
    cachedMe = body;
    return body;
  }

  // Clerk's default light theme clashes with the dark RC card. These
  // appearance variables map the Clerk widget surfaces to our :root tokens
  // (--rc-bg-card, --rc-text, --rc-accent, etc.) so the embedded form
  // reads as part of the page instead of a transplant. Callers can still
  // pass their own appearance overrides; ours acts as the base.
  const DEFAULT_APPEARANCE = {
    variables: {
      colorBackground: '#12121e',       // --rc-bg-panel
      colorInputBackground: '#1f1f30',  // slightly brighter than --rc-bg-card for input contrast
      colorText: '#eeeef4',             // --rc-text-bright (was --rc-text -- bumped for contrast)
      colorTextSecondary: '#b0b0c4',    // bumped from --rc-text-dim so labels/sub-copy stay readable
      colorInputText: '#ffffff',
      colorPrimary: '#00d4aa',          // --rc-accent
      colorTextOnPrimaryBackground: '#0a0a14',  // --rc-bg-dark
      colorNeutral: '#eeeef4',
      colorDanger: '#ff3333',           // --rc-red
      colorSuccess: '#33cc55',          // --rc-yellow
      colorWarning: '#ffbb33',          // --rc-orange
      borderRadius: '6px',
      fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    },
    elements: {
      card: { boxShadow: 'none', backgroundColor: 'transparent' },
      footer: { backgroundColor: 'transparent' },
      footerAction: { backgroundColor: 'transparent' },
      footerActionText: { color: '#b0b0c4' },
      footerActionLink: { color: '#00d4aa', fontWeight: 600 },
      headerSubtitle: { color: '#b0b0c4' },
      formFieldLabel: { color: '#eeeef4', fontWeight: 600 },
      formFieldInput: {
        backgroundColor: '#1f1f30',
        borderColor: '#3a3a52',
        color: '#ffffff',
      },
      formFieldInputShowPasswordButton: { color: '#b0b0c4' },
      identityPreviewText: { color: '#eeeef4' },
      identityPreviewEditButton: { color: '#00d4aa' },
      formFieldHintText: { color: '#b0b0c4' },
      formFieldErrorText: { color: '#ff6666' },
      dividerLine: { backgroundColor: '#3a3a52' },
      dividerText: { color: '#b0b0c4' },
    },
  };

  function mergeAppearance(extra) {
    if (!extra) return DEFAULT_APPEARANCE;
    return {
      ...DEFAULT_APPEARANCE,
      ...extra,
      variables: { ...DEFAULT_APPEARANCE.variables, ...(extra.variables || {}) },
      elements: { ...DEFAULT_APPEARANCE.elements, ...(extra.elements || {}) },
    };
  }

  // Both widgets live on /account/. SignIn's footer "Sign up" link routes
  // to ?mode=signup; SignUp's footer "Sign in" link routes back to /account/.
  // account.js reads ?mode= and mounts the right widget.
  //
  // Clerk v5 deprecated afterSignInUrl in favor of signInForceRedirectUrl
  // / signInFallbackRedirectUrl. We pass BOTH so older + newer clerk-js
  // builds behave the same. Callers pass either name in opts; we mirror
  // it to the v5 name for safety.
  function _mirrorRedirectOpts(opts, kind) {
    const force = kind === 'signIn' ? 'signInForceRedirectUrl' : 'signUpForceRedirectUrl';
    const fallback = kind === 'signIn' ? 'signInFallbackRedirectUrl' : 'signUpFallbackRedirectUrl';
    const legacy = kind === 'signIn' ? 'afterSignInUrl' : 'afterSignUpUrl';
    const ret = opts[force] || opts[fallback] || opts[legacy];
    if (!ret) return opts;
    return { ...opts, [force]: ret, [legacy]: ret };
  }

  function openSignIn(el, opts = {}) {
    require().mountSignIn(el, {
      signUpUrl: '/account/?mode=signup',
      ..._mirrorRedirectOpts(opts, 'signIn'),
      appearance: mergeAppearance(opts.appearance),
    });
  }

  function openSignUp(el, opts = {}) {
    require().mountSignUp(el, {
      signInUrl: '/account/',
      ..._mirrorRedirectOpts(opts, 'signUp'),
      appearance: mergeAppearance(opts.appearance),
    });
  }

  async function signOut() {
    // Sync our state OUT before awaiting Clerk -- if Clerk's signOut
    // network call hangs we still want the link to flip immediately so
    // the user can see something happened.
    cachedMe = null;
    _syncAuthState(false);
    try {
      await require().signOut();
    } finally {
      // Re-sync in case Clerk's listener already updated the state by
      // the time signOut() returns. Idempotent.
      _syncAuthState(false);
    }
  }

  function onChange(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  return {
    init,
    isSignedIn,
    getToken,
    authedFetch,
    getMe,
    setupHandle,
    openSignIn,
    openSignUp,
    signOut,
    onChange,
  };
})();

window.Auth = Auth;
