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
    const heading = mount.getAttribute('data-heading') || 'Follow the readings';
    const blurb = mount.getAttribute('data-blurb')
      || 'A short note from The Rising Compass as new songs are read. No noise, leave any time.';

    mount.innerHTML = `
      <div class="rc-subscribe-inner">
        <h3 class="rc-subscribe-h">${escapeHtml(heading)}</h3>
        <p class="rc-subscribe-blurb">${escapeHtml(blurb)}</p>
        <form class="rc-subscribe-form" novalidate>
          <div class="rc-subscribe-row">
            <input type="email" class="rc-subscribe-email" required
                   autocomplete="email" placeholder="you@email.com" aria-label="Email address">
            <button type="submit" class="rc-subscribe-btn">Subscribe</button>
          </div>
          <input type="text" class="rc-subscribe-hp" tabindex="-1" autocomplete="off"
                 aria-hidden="true" style="position:absolute;left:-9999px;width:1px;height:1px">
          <div class="rc-subscribe-turnstile" hidden></div>
          <p class="rc-subscribe-msg" role="status" hidden></p>
        </form>
      </div>`;

    const form = mount.querySelector('.rc-subscribe-form');
    const email = mount.querySelector('.rc-subscribe-email');
    const hp = mount.querySelector('.rc-subscribe-hp');
    const turnstileEl = mount.querySelector('.rc-subscribe-turnstile');
    const btn = mount.querySelector('.rc-subscribe-btn');
    const msg = mount.querySelector('.rc-subscribe-msg');

    email.addEventListener('focus', () => ensureTurnstile(turnstileEl), { once: true });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const addr = email.value.trim();
      if (!addr || addr.indexOf('@') < 1) {
        showMsg(msg, 'Enter a valid email.', true);
        return;
      }
      btn.disabled = true; btn.textContent = 'Subscribing...';
      try {
        const resp = await fetch('/api/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...API_HEADERS },
          body: JSON.stringify({
            email: addr,
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
        form.querySelector('.rc-subscribe-row').style.display = 'none';
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
    const css = `
      .rc-subscribe{margin:2.5rem auto;max-width:var(--rc-max-width,720px)}
      .rc-subscribe-inner{border:1px solid rgba(255,255,255,.12);border-radius:10px;
        padding:1.5rem 1.6rem;background:rgba(255,255,255,.03)}
      .rc-subscribe-h{margin:0 0 .35rem;font-size:1.15rem;font-weight:600}
      .rc-subscribe-blurb{margin:0 0 1rem;color:var(--rc-muted,#b6b7bd);font-size:.92rem;line-height:1.5}
      .rc-subscribe-row{display:flex;gap:.5rem;flex-wrap:wrap}
      .rc-subscribe-email{flex:1 1 220px;min-width:0;padding:.7rem .8rem;border-radius:6px;
        border:1px solid rgba(255,255,255,.18);background:rgba(0,0,0,.25);color:inherit;font-size:1rem}
      .rc-subscribe-email:focus{outline:none;border-color:#3388ff}
      .rc-subscribe-btn{padding:.7rem 1.3rem;border:none;border-radius:6px;background:#3388ff;
        color:#fff;font-size:1rem;cursor:pointer;font-family:inherit}
      .rc-subscribe-btn:disabled{opacity:.6;cursor:default}
      .rc-subscribe-turnstile{margin-top:.7rem}
      .rc-subscribe-msg{margin:.8rem 0 0;color:#7fd18b;font-size:.95rem}
      .rc-subscribe-msg.is-error{color:#ff8f8f}
    `;
    const style = document.createElement('style');
    style.id = 'rc-subscribe-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }
})();
