/* Cookie consent bar for Rising Compass.
 *
 * Ported from chadlewine's React consent flow into a standalone vanilla IIFE
 * (RC's frontend is static HTML + build-time partials). Loaded on every page
 * via the footer partial.
 *
 * Two categories only: Essential (always on) + Analytics (PostHog + Google
 * Analytics). The choice is stored in the `rc_cookie_consent` cookie
 * (essential:1|analytics:X), 1 year, SameSite=Lax.
 *
 * GEO-AWARE DEFAULT (before the visitor chooses):
 *   EU / UK / EEA  -> opt-IN  (analytics stay OFF until Accept)
 *   everywhere else -> opt-OUT (analytics load unless Rejected)
 * Country comes from GET /api/geo-country (MaxMind, server-side). While it
 * resolves, analytics stay OFF. If it returns null (DB missing / private IP),
 * we fail closed = treat as opt-in.
 *
 * Analytics are actually started by window.rcInitAnalytics() (defined in the
 * analytics partial), which ALSO enforces the hard overrides (prod host only,
 * admin rc_ph_optout cookie, personal rc_skip_analytics, Do Not Track). So
 * consent never overrides those -- it only decides whether to call init.
 */
(function () {
  if (window.__rcConsentLoaded) return;
  window.__rcConsentLoaded = true;

  var COOKIE = 'rc_cookie_consent';
  var MAX_AGE_DAYS = 365;

  // EU + EEA + UK + Switzerland (ISO-3166-1 alpha-2). These get opt-in.
  var OPT_IN = {
    AT: 1, BE: 1, BG: 1, HR: 1, CY: 1, CZ: 1, DK: 1, EE: 1, FI: 1, FR: 1,
    DE: 1, GR: 1, HU: 1, IE: 1, IT: 1, LV: 1, LT: 1, LU: 1, MT: 1, NL: 1,
    PL: 1, PT: 1, RO: 1, SK: 1, SI: 1, ES: 1, SE: 1,            // EU27
    IS: 1, LI: 1, NO: 1,                                        // EEA
    GB: 1, CH: 1                                                // UK + Switzerland
  };

  function readCookie() {
    var m = document.cookie.match(new RegExp('(?:^|; )' + COOKIE + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : null;
  }

  // Returns {analytics: 0|1} or null when undecided.
  function parse(str) {
    if (!str) return null;
    var analytics = 0, sawAnalytics = false;
    str.split('|').forEach(function (part) {
      var kv = part.split(':');
      if (kv[0] === 'analytics') { analytics = parseInt(kv[1], 10) ? 1 : 0; sawAnalytics = true; }
    });
    return sawAnalytics ? { analytics: analytics } : null;
  }

  function persist(analytics) {
    var d = new Date();
    d.setTime(d.getTime() + MAX_AGE_DAYS * 86400000);
    document.cookie = COOKIE + '=' + encodeURIComponent('essential:1|analytics:' + (analytics ? 1 : 0)) +
      ';expires=' + d.toUTCString() + ';path=/;SameSite=Lax';
  }

  // Hard "never even ask" cases: analytics are already force-off, so showing a
  // consent bar would only nag. (rcInitAnalytics enforces these regardless.)
  function suppressedBar() {
    try { if (localStorage.getItem('rc_skip_analytics') === '1') return true; } catch (e) {}
    if (document.cookie.split('; ').indexOf('rc_ph_optout=1') !== -1) return true; // admin
    // Browser-level opt-out signals -- treat as an expressed choice (off) and
    // don't nag. Do Not Track + Global Privacy Control (CCPA/CPRA). Honored as
    // a hard gate in rcInitAnalytics too, so analytics never load either way.
    var dnt = navigator.doNotTrack || window.doNotTrack || navigator.msDoNotTrack;
    if (dnt === '1' || dnt === 'yes') return true;
    if (navigator.globalPrivacyControl === true) return true;
    return false;
  }

  function startAnalytics() {
    try { if (typeof window.rcInitAnalytics === 'function') window.rcInitAnalytics(); } catch (e) {}
  }

  // ---------- banner DOM ----------
  var root = null;

  function buildBar(analyticsDefault, openDetails) {
    if (root) root.remove();
    root = document.createElement('div');
    root.className = 'rc-consent';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-label', 'Cookie preferences');
    root.setAttribute('aria-live', 'polite');
    root.innerHTML =
      '<div class="rc-consent__bar">' +
        '<span class="rc-consent__label" aria-hidden="true">cookies</span>' +
        '<div class="rc-consent__text">We use cookies for essential site functions and (with your OK) ' +
          'analytics to understand how the site is used. <a href="/privacy.html">Learn more</a>.</div>' +
        '<div class="rc-consent__actions">' +
          '<button type="button" class="rc-consent__btn rc-consent__btn--primary" data-act="accept">Accept all</button>' +
          '<button type="button" class="rc-consent__btn rc-consent__btn--ghost" data-act="reject">Reject optional</button>' +
          '<button type="button" class="rc-consent__btn rc-consent__btn--ghost" data-act="manage" aria-expanded="' + (openDetails ? 'true' : 'false') + '">Manage</button>' +
        '</div>' +
      '</div>' +
      '<div class="rc-consent__details" ' + (openDetails ? '' : 'style="display:none;"') + '>' +
        '<div class="rc-consent__category">' +
          '<div class="rc-consent__cat-info">' +
            '<div class="rc-consent__cat-name">Essential</div>' +
            '<div class="rc-consent__cat-desc">Sign-in, bot protection, and your cookie choice. Always on.</div>' +
          '</div>' +
          '<span class="rc-consent__always">Always on</span>' +
        '</div>' +
        '<div class="rc-consent__category">' +
          '<div class="rc-consent__cat-info">' +
            '<div class="rc-consent__cat-name">Analytics</div>' +
            '<div class="rc-consent__cat-desc">PostHog and Google Analytics, served first-party. Helps us understand how the site is used. Anonymous for signed-out visitors.</div>' +
          '</div>' +
          '<label class="rc-consent__toggle">' +
            '<input type="checkbox" data-role="analytics-toggle" aria-label="Allow analytics"' + (analyticsDefault ? ' checked' : '') + '>' +
            '<span class="rc-consent__slider"></span>' +
          '</label>' +
        '</div>' +
        '<div class="rc-consent__save-row">' +
          '<button type="button" class="rc-consent__btn rc-consent__btn--primary" data-act="save">Save preferences</button>' +
        '</div>' +
      '</div>';

    root.addEventListener('click', function (ev) {
      var act = ev.target && ev.target.getAttribute && ev.target.getAttribute('data-act');
      if (!act) return;
      if (act === 'accept') decide(1);
      else if (act === 'reject') decide(0);
      else if (act === 'save') decide(toggleState() ? 1 : 0);
      else if (act === 'manage') {
        var det = root.querySelector('.rc-consent__details');
        var btn = ev.target;
        var show = det.style.display === 'none';
        det.style.display = show ? '' : 'none';
        btn.setAttribute('aria-expanded', show ? 'true' : 'false');
      }
    });

    document.body.appendChild(root);
  }

  function toggleState() {
    var t = root && root.querySelector('[data-role="analytics-toggle"]');
    return !!(t && t.checked);
  }

  function closeBar() { if (root) { root.remove(); root = null; } }

  // Persist + react. `prevAllowed` = analytics state before this decision.
  function decide(analytics) {
    var prevAllowed = currentAllowed();
    persist(analytics);
    closeBar();
    if (analytics && !prevAllowed) startAnalytics();          // turn on: init now
    else if (!analytics && prevAllowed) window.location.reload(); // turn off: clean reload
  }

  // Is analytics currently ON (per the stored cookie)? Used to know whether a
  // reload is needed to actually stop a running PostHog/GA.
  function currentAllowed() {
    var c = parse(readCookie());
    return !!(c && c.analytics);
  }

  // Re-open the manager from a footer / privacy-page link.
  window.rcOpenCookiePrefs = function () {
    buildBar(currentAllowed(), true);
  };

  // ---------- boot ----------
  function boot() {
    var decided = parse(readCookie());
    if (decided) {
      if (decided.analytics) startAnalytics();
      return; // choice already made; no bar
    }
    if (suppressedBar()) return; // admin / personal opt-out: analytics off, no nag

    // Undecided: resolve country, then default by region. A 2.5s timeout means
    // a slow/hanging endpoint can't suppress the bar AND analytics forever --
    // it falls through to null (= opt-in, the safe default).
    var ctl = null;
    try { ctl = AbortSignal.timeout ? AbortSignal.timeout(2500) : null; } catch (e) {}
    fetch('/api/geo-country', { headers: { 'Accept': 'application/json' }, signal: ctl })
      .then(function (r) { return r.ok ? r.json() : { country: null }; })
      .catch(function () { return { country: null }; })
      .then(function (data) {
        var country = data && data.country;
        var optIn = !country || !!OPT_IN[country]; // null -> fail closed (opt-in)
        if (optIn) {
          buildBar(false, false); // analytics off until Accept
        } else {
          startAnalytics();        // opt-out region: load now...
          buildBar(true, false);   // ...but show the bar so they can reject
        }
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
