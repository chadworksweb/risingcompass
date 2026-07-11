/* === Shop checkout: Stripe embedded Checkout with live Printify shipping ===
   Reads the cart from localStorage, creates an embedded session via
   /api/shop/cart-checkout, and mounts Stripe's embedded checkout. The
   onShippingDetailsChange callback recomputes shipping server-side
   (/api/shop/calculate-shipping -> Printify quote) once the buyer enters an
   address. Stripe redirects to /shop/thank-you/ on success. */

(function () {
  const IS_LOCAL = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const API_KEY = IS_LOCAL
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';
  const HDR = { 'X-Api-Key': API_KEY };
  const CART_KEY = 'rc_shop_cart';

  const statusEl = document.getElementById('checkout-status');
  const errorEl = document.getElementById('checkout-error');
  const mount = document.getElementById('shop-embedded');

  function fail(msg) {
    if (statusEl) statusEl.style.display = 'none';
    if (errorEl) { errorEl.textContent = msg; errorEl.style.display = ''; }
  }

  function getCart() {
    try {
      const raw = localStorage.getItem(CART_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (_) { return []; }
  }

  async function main() {
    const cart = getCart();
    if (cart.length === 0) {
      if (statusEl) statusEl.style.display = 'none';
      mount.innerHTML =
        '<p class="checkout-status">Your cart is empty. ' +
        '<a class="thanks-link" href="/shop/">Back to the shop</a></p>';
      return;
    }

    let cfg;
    try {
      const r = await fetch('/api/shop/config', { headers: HDR, credentials: 'same-origin' });
      cfg = await r.json();
    } catch (_) { return fail('Could not reach the shop. Please try again.'); }
    if (!cfg || !cfg.stripe_publishable_key) {
      return fail('Checkout is not available right now. Please try again later.');
    }
    if (typeof Stripe === 'undefined') {
      return fail('Payment library failed to load. Please refresh.');
    }

    let clientSecret;
    try {
      const r = await fetch('/api/shop/cart-checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...HDR },
        credentials: 'same-origin',
        body: JSON.stringify({
          items: cart.map((l) => ({ slug: l.slug, variant_id: l.variant_id, quantity: l.quantity || 1 })),
          return_url: window.location.origin + '/shop/thank-you/?session_id={CHECKOUT_SESSION_ID}',
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not start checkout.');
      clientSecret = data.client_secret;
    } catch (err) {
      return fail(err.message || 'Could not start checkout. Please try again.');
    }
    if (!clientSecret) return fail('Could not start checkout. Please try again.');

    async function onShippingDetailsChange(event) {
      try {
        const r = await fetch('/api/shop/calculate-shipping', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...HDR },
          credentials: 'same-origin',
          body: JSON.stringify({
            checkout_session_id: event.checkoutSessionId,
            shipping_details: event.shippingDetails,
          }),
        });
        const data = await r.json();
        if (data && data.type === 'accept') return { type: 'accept' };
        return { type: 'reject', errorMessage: (data && data.errorMessage) || "We couldn't calculate shipping for that address." };
      } catch (_) {
        return { type: 'reject', errorMessage: 'Network error calculating shipping. Please try again.' };
      }
    }

    try {
      const stripe = Stripe(cfg.stripe_publishable_key);
      const checkout = await stripe.initEmbeddedCheckout({ clientSecret, onShippingDetailsChange });
      if (statusEl) statusEl.style.display = 'none';
      checkout.mount('#shop-embedded');
    } catch (err) {
      return fail('Could not load the payment form. Please refresh and try again.');
    }
  }

  main();
})();
