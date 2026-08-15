/* On-site subscriber capture (Hockey Stick Build 2b) -- RC's own list.
 *
 * Drop-in: put `<div class="rc-subscribe" data-source="song_page"></div>` on a
 * page and load this script (defer). It renders the form, injects its styles
 * once, lazy-loads Cloudflare Turnstile on first focus (so a page view costs
 * nothing until the visitor engages), and POSTs to /api/subscribe.
 *
 * Bot protection mirrors the inquiry form: an always-present honeypot plus a
 * Turnstile token when the server has TURNSTILE configured. Double opt-in lives
 * server-side; on success we just tell them to check their inbox.
 */
(function () {
  const mounts = Array.from(document.querySelectorAll('.rc-subscribe'));
  if (!mounts.length) return;

  const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const API_KEY = isLocal
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';
  const API_HEADERS = API_KEY ? { 'X-Api-Key': API_KEY } : {};

  injectStyles();

  // Turnstile is shared across all mounts on the page: one widget, rendered on
  // demand into whichever card the visitor engages first.
  let turnstileSiteKey = null;
  let turnstileLoaded = false;
  let turnstileWidgetId = null;
  let turnstileMountEl = null;

  function getTurnstileToken() {
    if (!window.turnstile || turnstileWidgetId === null) return '';
    try { return window.turnstile.getResponse(turnstileWidgetId) || ''; } catch (_) { return ''; }
  }
  function resetTurnstile() {
    if (window.turnstile && turnstileWidgetId !== null) {
      try { window.turnstile.reset(turnstileWidgetId); } catch (_) {}
    }
  }
  async function ensureTurnstile(mountEl) {
    if (turnstileLoaded) { return; }
    turnstileLoaded = true;
    turnstileMountEl = mountEl;
    try {
      const resp = await fetch('/api/analyzer/config', { headers: API_HEADERS });
      if (!resp.ok) return;
      const cfg = await resp.json();
      if (!cfg.turnstile_site_key) return;
      turnstileSiteKey = cfg.turnstile_site_key;
      window.onRcSubscribeTurnstileLoad = function () {
        if (!turnstileMountEl || !window.turnstile) return;
        turnstileMountEl.hidden = false;
        turnstileWidgetId = window.turnstile.render(turnstileMountEl, {
          sitekey: turnstileSiteKey, theme: 'dark', size: 'flexible',
        });
      };
      const s = document.createElement('script');
      s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onRcSubscribeTurnstileLoad';
      s.async = true; s.defer = true;
      document.head.appendChild(s);
    } catch (_) { /* best-effort */ }
  }

  mounts.forEach(initMount);

  function initMount(mount) {
    const source = (mount.getAttribute('data-source') || 'other').slice(0, 40);
    const detail = (mount.getAttribute('data-source-detail') || window.location.pathname).slice(0, 200);
    const heading = mount.getAttribute('data-heading') ?? 'Follow the readings';
    const blurb = mount.getAttribute('data-blurb')
      || 'Get the daily reading straight to your inbox.';

    mount.innerHTML = `
      <div class="rc-subscribe-inner">
        <h3 class="rc-subscribe-h">${escapeHtml(heading)}</h3>
        <p class="rc-subscribe-blurb">${escapeHtml(blurb)}</p>
        <form class="rc-subscribe-form" novalidate>
          <div class="rc-subscribe-fields">
            <div class="rc-subscribe-row">
              <input type="text" class="rc-subscribe-first" autocomplete="given-name"
                     maxlength="80" placeholder="First name" aria-label="First name">
              <input type="text" class="rc-subscribe-last" autocomplete="family-name"
                     maxlength="80" placeholder="Last name" aria-label="Last name">
              <input type="email" class="rc-subscribe-email" required aria-required="true"
                     autocomplete="email" placeholder="you@email.com *" aria-label="Email address (required)">
              <button type="submit" class="rc-subscribe-btn">Subscribe</button>
            </div>
          </div>
          <input type="text" class="rc-subscribe-hp" tabindex="-1" autocomplete="off"
                 aria-hidden="true" style="position:absolute;left:-9999px;width:1px;height:1px">
          <div class="rc-subscribe-turnstile" hidden></div>
          <p class="rc-subscribe-msg" role="status" hidden></p>
        </form>
      </div>`;

    const form = mount.querySelector('.rc-subscribe-form');
    const email = mount.querySelector('.rc-subscribe-email');
    const firstName = mount.querySelector('.rc-subscribe-first');
    const lastName = mount.querySelector('.rc-subscribe-last');
    const hp = mount.querySelector('.rc-subscribe-hp');
    const turnstileEl = mount.querySelector('.rc-subscribe-turnstile');
    const btn = mount.querySelector('.rc-subscribe-btn');
    const msg = mount.querySelector('.rc-subscribe-msg');

    email.addEventListener('focus', () => ensureTurnstile(turnstileEl), { once: true });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const addr = email.value.trim();
      if (!addr || addr.indexOf('@') < 1) {
        showMsg(msg, 'Please enter a valid email.', true);
        return;
      }
      btn.disabled = true; btn.textContent = 'Subscribing...';
      try {
        const resp = await fetch('/api/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...API_HEADERS },
          body: JSON.stringify({
            email: addr,
            // Optional, and sent as empty strings when left blank -- the server
            // stores NULL for those rather than an empty name.
            first_name: firstName ? firstName.value.trim() : '',
            last_name: lastName ? lastName.value.trim() : '',
            source: source,
            source_detail: detail,
            hp_website: hp.value,
            turnstile_token: getTurnstileToken(),
          }),
        });
        resetTurnstile();
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Something went wrong.');
        // Collapse the form to the status line on any successful outcome.
        form.querySelector('.rc-subscribe-fields').style.display = 'none';
        if (turnstileEl) turnstileEl.hidden = true;
        showMsg(msg, data.message || 'Check your email to confirm.', false);
      } catch (err) {
        showMsg(msg, err.message || 'Something went wrong.', true);
        btn.disabled = false; btn.textContent = 'Subscribe';
      }
    });
  }

  function showMsg(el, text, isError) {
    el.textContent = text;
    el.hidden = false;
    el.classList.toggle('is-error', !!isError);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function injectStyles() {
    if (document.getElementById('rc-subscribe-styles')) return;
    // Geometry follows the Illus Caden subscribe block (.ic-subscribe): a wide
    // centred card on the 860px content measure, fluid 2rem-to-4rem padding,
    // the blurb capped at a 46ch reading measure, and the form held to 460px
    // in the middle of all that room rather than stretched across it. The
    // palette, the spectrum rule, and the accent button stay Rising Compass.
    const css = `
      .rc-subscribe{margin:4rem auto;max-width:860px;width:100%}
      /* Stay out of sight while the homepage splash/loading sequence runs. */
      body.rc-splash .rc-subscribe{display:none}
      .rc-subscribe-inner{position:relative;overflow:hidden;text-align:center;
        border:1px solid var(--rc-border-ui,#646490);border-radius:16px;
        padding:clamp(2rem,5vw,4rem);background:var(--rc-bg-card,#181828);
        box-shadow:inset 0 1px 0 rgba(255,255,255,.04),0 18px 44px -26px rgba(0,0,0,.85)}
      .rc-subscribe-inner::before{content:"";position:absolute;top:0;left:0;right:0;height:4px;
        background:linear-gradient(90deg,var(--rc-violet,#9933ff),var(--rc-blue,#3388ff),var(--rc-accent,#00d4aa))}
      .rc-subscribe-h{margin:0 0 .5rem;font-size:1.5625rem;font-weight:700;
        letter-spacing:-.01em;color:var(--rc-text-bright,#eeeef4)}
      .rc-subscribe-h:empty{display:none}
      .rc-subscribe-blurb{margin:0 auto 1.5rem;max-width:46ch;
        color:var(--rc-text-dim,#c0c0ca);font-size:1rem;line-height:1.55}
      /* One column, on the Illus Caden form measure. flex:none on every child
         because in a column the flex-basis would set HEIGHT, not width, and
         the button keeps its transparent 1px border so it stands exactly as
         tall as the inputs above it. */
      .rc-subscribe-form{max-width:460px;margin:0 auto}
      .rc-subscribe-row{display:flex;flex-direction:column;align-items:stretch;gap:.5rem}
      .rc-subscribe-first,.rc-subscribe-last,.rc-subscribe-email{
        flex:none;width:100%;min-width:0;padding:.72em 1em;border-radius:8px;line-height:1.3;
        border:1px solid var(--rc-border,#2a2a3e);background:var(--rc-bg-dark,#0a0a14);
        color:var(--rc-text-bright,#eeeef4);font-size:.875rem;font-family:inherit}
      .rc-subscribe-first::placeholder,.rc-subscribe-last::placeholder,
      .rc-subscribe-email::placeholder{color:var(--rc-text-dim,#c0c0ca);opacity:.55}
      .rc-subscribe-first:focus,.rc-subscribe-last:focus,.rc-subscribe-email:focus{
        outline:none;border-color:var(--rc-accent,#00d4aa);
        box-shadow:0 0 0 3px var(--rc-accent-subtle,rgba(0,212,170,.14))}
      .rc-subscribe-btn{flex:none;width:100%;padding:.72em 1.5em;border:1px solid transparent;
        border-radius:8px;line-height:1.3;
        background:var(--rc-accent,#00d4aa);color:var(--rc-bg-dark,#0a0a14);
        font-size:.875rem;font-weight:700;cursor:pointer;font-family:inherit;transition:filter .15s ease}
      .rc-subscribe-btn:hover{filter:brightness(1.08)}
      .rc-subscribe-btn:disabled{opacity:.6;cursor:default}
      .rc-subscribe-turnstile{margin-top:1rem;display:flex;justify-content:center}
      .rc-subscribe-msg{margin:1rem 0 0;min-height:1.2em;
        color:var(--rc-accent,#00d4aa);font-size:.875rem;font-weight:500}
      .rc-subscribe-msg.is-error{color:var(--rc-red,#ff3333)}
    `;
    const style = document.createElement('style');
    style.id = 'rc-subscribe-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }
})();
