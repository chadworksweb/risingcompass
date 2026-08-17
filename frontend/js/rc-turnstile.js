/* Shared Cloudflare Turnstile helper.
 *
 * WHY THIS EXISTS. Every write endpoint behind _check_bot_protection REQUIRES a
 * Turnstile token whenever TURNSTILE_SECRET is set on the server, and it is set
 * in prod. A form without a rendered widget does not merely go unhardened -- it
 * gets a flat 422 "Bot verification required" on every submit. That is what had
 * the Sentinel apply and finding forms dead before they ever opened.
 *
 * Explicit rendering, one widget per mount. Turnstile will not render into a
 * hidden container, and re-rendering into a reused mount leaks widget ids, so
 * each form owns its own mount and renders lazily once that form is shown.
 *
 * NOTE: lyrical-charger/charger.js still carries its own older copy of this
 * logic. It works and is on the live charger path, so it was left alone rather
 * than churned on a deploy-bound change. Converge it when the charger is next
 * touched for its own reasons.
 */
(function () {
  const widgets = {};        // mountId -> Turnstile widget id
  let sitekey = null;
  let loading = null;

  function loadScript() {
    if (window.turnstile) return Promise.resolve();
    if (loading) return loading;
    loading = new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
      s.async = true;
      s.defer = true;
      // Fail soft either way: if the script never loads there is no token to
      // send, and the server's 422 is the honest thing for the user to see.
      s.onload = () => resolve();
      s.onerror = () => resolve();
      document.head.appendChild(s);
    });
    return loading;
  }

  window.RCTurnstile = {
    /* Call once with the value of `turnstile_site_key` from a /config endpoint.
       A null/empty key means Turnstile is not configured, and every other call
       here becomes a no-op. */
    configure(key) { sitekey = key || null; },

    configured() { return !!sitekey; },

    /* Render into `mountId` if not already rendered. Safe to call repeatedly. */
    async mount(mountId, opts) {
      if (!sitekey || widgets[mountId] != null) return;
      const el = document.getElementById(mountId);
      if (!el) return;
      await loadScript();
      if (!window.turnstile) return;
      el.hidden = false;
      try {
        widgets[mountId] = window.turnstile.render(el, Object.assign({
          sitekey: sitekey, theme: 'dark', size: 'flexible',
        }, opts || {}));
      } catch (_) {
        el.hidden = true;
      }
    },

    /* Current token, or null. Send it as `turnstile_token`. */
    token(mountId) {
      if (!window.turnstile || widgets[mountId] == null) return null;
      try { return window.turnstile.getResponse(widgets[mountId]) || null; } catch (_) { return null; }
    },

    /* A token is SINGLE USE. Without a reset after a failed submit, the retry
       replays a spent token and fails verification for a reason the user cannot
       see. Call this on every error path that leaves the form open. */
    reset(mountId) {
      if (!window.turnstile || widgets[mountId] == null) return;
      try { window.turnstile.reset(widgets[mountId]); } catch (_) {}
    },
  };
})();
