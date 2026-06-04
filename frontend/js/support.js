/* === Homepage support callout — Stripe Checkout donate widget ===
   Posts to /api/donate with a UNIQUE source ('homepage_support') so every
   donation from this form is tracked separately from the Lyrical Charger
   widget (source='lyrical_charger'). The source flows into both the
   Donation.source column and the Stripe Checkout product/metadata. */

(function () {
  // Idempotent: the partial ships its own <script>, so guard against a page
  // that somehow loads support.js twice.
  if (window.__rcSupportInit) return;
  window.__rcSupportInit = true;

  const IS_LOCAL = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const API_KEY = IS_LOCAL
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  const widget = $('#rc-donate-widget');
  if (!widget) return;

  // Per-form tracking source. Each page can override via
  // data-donate-source on .support-callout; defaults to 'site_support'.
  const panel = widget.closest('.support-callout');
  const DONATE_SOURCE = (panel && panel.dataset.donateSource) || 'site_support';

  const presets = $$('.rc-donate-preset', widget);
  const customInput = $('#rc-donate-custom-input');
  const consent = $('#rc-donate-consent');
  const submit = $('#rc-donate-submit');
  const status = $('#rc-donate-status');
  const terms = $('#rc-donate-terms');
  const termsTrigger = $('#rc-donate-terms-trigger');

  function effectiveAmount() {
    const custom = parseFloat(customInput.value);
    if (!Number.isNaN(custom) && custom >= 1) return custom;
    const active = $('.rc-donate-preset--active', widget);
    return active ? parseFloat(active.dataset.amount) : 0;
  }

  function refreshSubmit() {
    const amt = effectiveAmount();
    submit.textContent = `Donate $${amt.toFixed(amt % 1 === 0 ? 0 : 2)}`;
    submit.disabled = !(amt >= 1 && consent.checked);
  }

  presets.forEach((btn) => {
    btn.addEventListener('click', () => {
      presets.forEach((b) => b.classList.remove('rc-donate-preset--active'));
      btn.classList.add('rc-donate-preset--active');
      customInput.value = '';
      refreshSubmit();
    });
  });

  customInput.addEventListener('input', () => {
    if (customInput.value !== '') {
      presets.forEach((b) => b.classList.remove('rc-donate-preset--active'));
    } else {
      const def = presets[0];
      if (def) def.classList.add('rc-donate-preset--active');
    }
    refreshSubmit();
  });

  consent.addEventListener('change', refreshSubmit);

  if (termsTrigger) {
    termsTrigger.addEventListener('click', () => terms.classList.toggle('hidden'));
  }

  submit.addEventListener('click', async () => {
    const amt = effectiveAmount();
    if (amt < 1 || !consent.checked) return;

    submit.disabled = true;
    const prevText = submit.textContent;
    submit.textContent = 'Redirecting...';
    status.className = 'rc-donate-status';
    status.textContent = '';

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (API_KEY) headers['X-Api-Key'] = API_KEY;
      const origin = window.location.origin;
      const path = window.location.pathname;
      const resp = await fetch('/api/donate', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          amount: amt,
          source: DONATE_SOURCE,
          success_url: `${origin}${path}?donated={CHECKOUT_SESSION_ID}`,
          cancel_url: `${origin}${path}?donate_cancelled=1`,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        const detail = err.detail || `HTTP ${resp.status}`;
        throw new Error(typeof detail === 'string' ? detail : 'Stripe error');
      }
      const data = await resp.json();
      if (!data.url) throw new Error('No checkout URL returned.');
      // Stripe replaces {CHECKOUT_SESSION_ID} with the real id on success_url
      window.location.href = data.url;
    } catch (err) {
      status.className = 'rc-donate-status error';
      status.textContent = err.message || 'Could not start checkout. Try again.';
      submit.disabled = false;
      submit.textContent = prevText;
    }
  });

  refreshSubmit();

  // Donation return: ?donated=cs_... means Stripe redirected back after a
  // successful checkout. Show a thank-you in the widget status line.
  const params = new URLSearchParams(window.location.search);
  if (params.has('donated')) {
    status.className = 'rc-donate-status success';
    status.textContent = 'Thank you. Your donation goes straight to API credits and hosting.';
    widget.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } else if (params.has('donate_cancelled')) {
    status.className = 'rc-donate-status';
    status.textContent = 'Checkout cancelled. No charge was made.';
  }
})();
