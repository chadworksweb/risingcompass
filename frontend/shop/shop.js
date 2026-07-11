/* === Shop: grid, product detail, cart drawer ===
   Products come from /api/shop/*. Cart lives in localStorage (rc_shop_cart).
   Checkout hands off to /shop/checkout/ (Stripe embedded). Anonymous checkout
   is fine; the __session cookie (Clerk) rides same-origin when signed in, so
   the backend attributes the order automatically. */

(function () {
  if (window.__rcShopInit) return;
  window.__rcShopInit = true;

  const IS_LOCAL = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const API_KEY = IS_LOCAL
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';
  const HDR = { 'X-Api-Key': API_KEY };
  const CART_KEY = 'rc_shop_cart';
  const SIZE_ORDER = ['XS', 'S', 'M', 'L', 'XL', '2XL', '3XL', '4XL', '5XL'];

  // Garment colors -> swatch hue. The "Feelin'" tee colors track the compass
  // charge tiers (violet / blue / green / orange / red); other garments show
  // their real color. Unknown names fall back to a neutral dot.
  const COLOR_HEX = {
    'Violet': '#aa54ff',
    'Royal Caribe': '#3388ff',
    'Island Green': '#33cc55',
    'Citrus': '#ffbb33',
    'Red': '#ff3333',
    'Black': '#1a1a22',
    'Navy Blue': '#20304f',
    'True Navy': '#20304f',
  };
  const swatchHex = (name) => COLOR_HEX[name] || '#8a8a9a';

  // The "Feelin'" tee: each garment color IS a compass charge tier (the word
  // printed on the shirt). Maps the Printify color name -> {tier label, hue}.
  // A product whose every color is in this map is treated as a "tier product"
  // (the color picker becomes a Tier picker).
  const TIER_BY_COLOR = {
    'Violet': { label: 'Ascended', hex: '#aa54ff' },
    'Royal Caribe': { label: 'Elevated', hex: '#3388ff' },
    'Island Green': { label: 'Decent', hex: '#33cc55' },
    'Citrus': { label: 'Degraded', hex: '#ffbb33' },
    'Red': { label: 'Corrupted', hex: '#ff3333' },
  };
  // Canonical tier order, high charge -> low.
  const TIER_ORDER = ['Ascended', 'Elevated', 'Decent', 'Degraded', 'Corrupted'];
  const tierRank = (colorName) => {
    const t = TIER_BY_COLOR[colorName];
    const i = t ? TIER_ORDER.indexOf(t.label) : -1;
    return i === -1 ? 99 : i;
  };

  const $ = (s, c = document) => c.querySelector(s);
  const el = (tag, cls, txt) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  };
  const fmt = (cents) => '$' + (Number(cents || 0) / 100).toFixed(2);

  async function apiGet(path) {
    const r = await fetch(path, { headers: HDR, credentials: 'same-origin' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }
  async function apiPost(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...HDR },
      credentials: 'same-origin',
      body: JSON.stringify(body || {}),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'HTTP ' + r.status);
    return data;
  }

  // --- Coming-soon note + subscribe (preview mode) -----------------------
  // Built under the greyed-out buy button while the shop is browsable but not
  // yet selling. Returns a DOM node.
  function buildSubscribeNote(message) {
    const wrap = el('div', 'shop-soon');
    wrap.innerHTML =
      '<p class="shop-soon__msg"></p>' +
      '<form class="shop-soon__form" novalidate>' +
        '<input type="email" class="shop-soon__input js-email" placeholder="you@example.com" autocomplete="email" required>' +
        '<input type="text" class="shop-soon__hp js-hp" tabindex="-1" autocomplete="off" aria-hidden="true">' +
        '<button type="submit" class="shop-soon__btn js-btn">Notify me</button>' +
      '</form>' +
      '<p class="shop-soon__status js-status"></p>';
    wrap.querySelector('.shop-soon__msg').textContent = message;
    const form = wrap.querySelector('form');
    const status = wrap.querySelector('.js-status');
    const btn = wrap.querySelector('.js-btn');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const email = wrap.querySelector('.js-email').value.trim();
      if (!email) return;
      btn.disabled = true; btn.textContent = 'Sending...';
      status.className = 'shop-soon__status js-status';
      status.textContent = '';
      try {
        const res = await apiPost('/api/shop/subscribe', {
          email, hp_website: wrap.querySelector('.js-hp').value,
        });
        status.classList.add('shop-soon__status--ok');
        status.textContent = res.message || "Thanks. We'll be in touch.";
        form.style.display = 'none';
      } catch (err) {
        status.classList.add('shop-soon__status--err');
        status.textContent = err.message || 'Something went wrong. Please try again.';
        btn.disabled = false; btn.textContent = 'Notify me';
      }
    });
    return wrap;
  }

  // --- Cart state ---------------------------------------------------------
  function getCart() {
    try {
      const raw = localStorage.getItem(CART_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (_) { return []; }
  }
  function saveCart(cart) {
    try { localStorage.setItem(CART_KEY, JSON.stringify(cart)); } catch (_) {}
    renderDrawer();
    updateCartCount();
  }
  function cartCount() { return getCart().reduce((n, l) => n + (l.quantity || 1), 0); }
  function cartSubtotal() { return getCart().reduce((n, l) => n + (l.price_cents || 0) * (l.quantity || 1), 0); }

  function addToCart(item) {
    const cart = getCart();
    const found = cart.find((l) => l.slug === item.slug && l.variant_id === item.variant_id);
    if (found) found.quantity = Math.min((found.quantity || 1) + 1, 10);
    else cart.push({ ...item, quantity: 1 });
    saveCart(cart);
    openDrawer();
  }
  function setQty(idx, q) {
    const cart = getCart();
    if (!cart[idx]) return;
    if (q <= 0) cart.splice(idx, 1);
    else cart[idx].quantity = Math.min(q, 10);
    saveCart(cart);
  }
  function removeLine(idx) {
    const cart = getCart();
    cart.splice(idx, 1);
    saveCart(cart);
  }

  // --- Cart drawer UI -----------------------------------------------------
  let drawerEl = null, scrimEl = null, countEl = null;

  function buildCartUI() {
    if (document.getElementById('shop-cart-btn')) return;

    const btn = el('button', 'shop-cart-btn');
    btn.id = 'shop-cart-btn';
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Open cart');
    btn.innerHTML = 'Cart <span class="shop-cart-btn__count" id="shop-cart-count">0</span>';
    btn.addEventListener('click', openDrawer);
    document.body.appendChild(btn);
    countEl = $('#shop-cart-count');

    scrimEl = el('div', 'shop-drawer-scrim');
    scrimEl.addEventListener('click', closeDrawer);
    document.body.appendChild(scrimEl);

    drawerEl = el('aside', 'shop-drawer');
    drawerEl.setAttribute('aria-label', 'Cart');
    drawerEl.innerHTML =
      '<div class="shop-drawer__head">' +
        '<span class="shop-drawer__title">Your cart</span>' +
        '<button type="button" class="shop-drawer__close" aria-label="Close cart">&times;</button>' +
      '</div>' +
      '<div class="shop-drawer__items" id="shop-drawer-items"></div>' +
      '<div class="shop-drawer__foot" id="shop-drawer-foot"></div>';
    document.body.appendChild(drawerEl);
    drawerEl.querySelector('.shop-drawer__close').addEventListener('click', closeDrawer);

    updateCartCount();
    renderDrawer();
  }

  function openDrawer() { if (scrimEl) { scrimEl.classList.add('shop-drawer-scrim--open'); drawerEl.classList.add('shop-drawer--open'); } }
  function closeDrawer() { if (scrimEl) { scrimEl.classList.remove('shop-drawer-scrim--open'); drawerEl.classList.remove('shop-drawer--open'); } }
  function updateCartCount() { if (countEl) countEl.textContent = String(cartCount()); }

  function renderDrawer() {
    const items = document.getElementById('shop-drawer-items');
    const foot = document.getElementById('shop-drawer-foot');
    if (!items || !foot) return;
    const cart = getCart();
    items.innerHTML = '';
    if (cart.length === 0) {
      items.appendChild(el('div', 'shop-drawer__empty', 'Your cart is empty.'));
      foot.innerHTML = '';
      return;
    }
    cart.forEach((line, idx) => {
      const row = el('div', 'cart-line');
      const img = el('img', 'cart-line__img');
      img.src = line.image_url || ''; img.alt = line.title || ''; img.loading = 'lazy';
      const mid = el('div', 'cart-line__mid');
      mid.appendChild(el('div', 'cart-line__title', line.title));
      if (line.variant_label) mid.appendChild(el('div', 'cart-line__variant', line.variant_label));
      const qty = el('div', 'cart-line__qty');
      const minus = el('button', 'cart-line__qbtn', '−'); minus.type = 'button';
      const num = el('span', 'cart-line__qnum', String(line.quantity || 1));
      const plus = el('button', 'cart-line__qbtn', '+'); plus.type = 'button';
      minus.addEventListener('click', () => setQty(idx, (line.quantity || 1) - 1));
      plus.addEventListener('click', () => setQty(idx, (line.quantity || 1) + 1));
      qty.append(minus, num, plus);
      mid.appendChild(qty);
      const right = el('div', 'cart-line__right');
      right.appendChild(el('div', 'cart-line__price', fmt((line.price_cents || 0) * (line.quantity || 1))));
      const rm = el('button', 'cart-line__remove', 'Remove'); rm.type = 'button';
      rm.addEventListener('click', () => removeLine(idx));
      right.appendChild(rm);
      row.append(img, mid, right);
      items.appendChild(row);
    });

    foot.innerHTML = '';
    const sub = el('div', 'shop-drawer__row');
    sub.innerHTML = '<span>Subtotal</span><strong>' + fmt(cartSubtotal()) + '</strong>';
    foot.appendChild(sub);
    foot.appendChild(el('div', 'shop-drawer__note', 'Shipping and taxes are calculated at checkout.'));
    const co = el('button', 'shop-checkout-btn', 'Checkout'); co.type = 'button';
    co.addEventListener('click', () => { window.location.href = '/shop/checkout/'; });
    foot.appendChild(co);
  }

  // --- Grid ---------------------------------------------------------------
  async function renderGrid(root) {
    try {
      const data = await apiGet('/api/shop/products');
      const products = (data && data.products) || [];
      root.innerHTML = '';
      if (products.length === 0) {
        root.appendChild(el('div', 'shop-empty', 'Nothing in the shop right now. Check back soon.'));
        return;
      }
      products.forEach((p) => {
        const card = el('a', 'shop-card');
        card.href = '/shop/product.html?p=' + encodeURIComponent(p.slug);
        const img = el('img', 'shop-card__img');
        img.src = p.image_url || ''; img.alt = p.title || ''; img.loading = 'lazy';
        const body = el('div', 'shop-card__body');
        body.appendChild(el('div', 'shop-card__title', p.title));
        if (p.price != null) body.appendChild(el('div', 'shop-card__price', 'from $' + Number(p.price).toFixed(2)));
        const colors = p.colors || [];
        if (colors.length > 1) {
          const defaultSrc = p.image_url || '';
          const sw = el('div', 'shop-card__swatches');
          sw.setAttribute('aria-label', colors.length + ' colors');
          colors.slice(0, 6).forEach((c) => {
            const dot = el('span', 'shop-swatch');
            dot.style.background = swatchHex(c.name);
            dot.title = c.name;
            dot.setAttribute('role', 'button');
            dot.setAttribute('aria-label', 'Preview ' + c.name);
            const show = () => { if (c.image) img.src = c.image; };
            // Desktop: hover to preview. Mobile: tap the dot to preview (and
            // swallow the tap so it doesn't follow the card link).
            dot.addEventListener('mouseenter', show);
            dot.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); show(); });
            sw.appendChild(dot);
          });
          // Leaving the card restores the default mockup (desktop hover reset;
          // on mobile there's no mouseleave, so a tapped color stays shown).
          card.addEventListener('mouseleave', () => { img.src = defaultSrc; });
          sw.appendChild(el('span', 'shop-card__swatch-count', colors.length + ' colors'));
          body.appendChild(sw);
        }
        card.append(img, body);
        root.appendChild(card);
      });
    } catch (_) {
      root.innerHTML = '';
      root.appendChild(el('div', 'shop-empty', 'Could not load the shop. Please refresh.'));
    }
  }

  // --- Product detail -----------------------------------------------------
  function sortSizes(sizes) {
    return sizes.slice().sort((a, b) => {
      const ai = SIZE_ORDER.indexOf(String(a).toUpperCase());
      const bi = SIZE_ORDER.indexOf(String(b).toUpperCase());
      if (ai === -1 && bi === -1) return String(a).localeCompare(String(b));
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });
  }

  async function renderDetail(root, available, comingMsg) {
    const slug = new URLSearchParams(window.location.search).get('p');
    if (!slug) { root.innerHTML = '<p class="shop-empty">Product not found.</p>'; return; }
    let p;
    try { p = await apiGet('/api/shop/products/' + encodeURIComponent(slug)); }
    catch (_) { root.innerHTML = '<p class="shop-empty">Product not found.</p>'; return; }

    document.title = p.title + ' — The Rising Compass';
    const variants = p.variants || [];
    const images = (p.image_urls && p.image_urls.length) ? p.image_urls : (p.image_url ? [p.image_url] : []);

    const colors = [];
    const sizesSet = [];
    variants.forEach((v) => {
      if (v.color && !colors.includes(v.color)) colors.push(v.color);
      if (v.size && !sizesSet.includes(v.size)) sizesSet.push(v.size);
    });
    const sizes = sortSizes(sizesSet);

    // Each color is its own SKU with its own front mockup -> swap the hero on
    // color select.
    const colorImage = {};
    variants.forEach((v) => { if (v.color && v.image && !colorImage[v.color]) colorImage[v.color] = v.image; });

    // Tier product: every color maps to a compass charge tier -> the color
    // picker becomes a Tier picker (tier word, JetBrains Mono, tier hue).
    const isTierProduct = colors.length > 0 && colors.every((c) => TIER_BY_COLOR[c]);

    let selColor = colors.length === 1 ? colors[0] : null;
    let selSize = sizes.length === 1 ? sizes[0] : null;

    root.innerHTML =
      '<div class="product__grid">' +
        '<div class="product__art">' +
          '<img class="product__cover" id="pd-cover" alt="">' +
          '<div class="product__thumbs" id="pd-thumbs"></div>' +
        '</div>' +
        '<div class="product__info">' +
          '<h1 class="product__title" id="pd-title"></h1>' +
          '<div class="product__price" id="pd-price"></div>' +
          '<div id="pd-opts"></div>' +
          '<button type="button" class="product__buy" id="pd-buy"></button>' +
          '<div class="product__desc" id="pd-desc"></div>' +
          '<p class="product__disclaimer">All sales are final. No returns, exchanges, or refunds, except for a damaged or incorrect item.</p>' +
          '<a class="product__back" href="/shop/">&larr; Back to the shop</a>' +
        '</div>' +
      '</div>';

    $('#pd-title').textContent = p.title;
    $('#pd-desc').textContent = p.description || '';

    const cover = $('#pd-cover');
    cover.src = images[0] || ''; cover.alt = p.title;
    const thumbs = $('#pd-thumbs');
    if (images.length > 1) {
      images.forEach((src) => {
        const b = el('button', 'product__thumb'); b.type = 'button';
        const im = el('img'); im.src = src; im.alt = ''; im.loading = 'lazy';
        b.appendChild(im);
        b.addEventListener('click', () => {
          cover.src = src;
          thumbs.querySelectorAll('.product__thumb').forEach((t) => t.classList.remove('product__thumb--active'));
          b.classList.add('product__thumb--active');
        });
        thumbs.appendChild(b);
      });
      thumbs.firstChild.classList.add('product__thumb--active');
    }

    function currentVariant() {
      return variants.find((v) =>
        (colors.length === 0 || v.color === selColor) &&
        (sizes.length === 0 || v.size === selSize)
      ) || null;
    }
    function sizeAvailableForColor(size) {
      if (colors.length === 0 || !selColor) return true;
      return variants.some((v) => v.color === selColor && v.size === size);
    }

    const priceEl = $('#pd-price');
    const buyEl = $('#pd-buy');
    const optsEl = $('#pd-opts');

    function renderOpts() {
      optsEl.innerHTML = '';
      if (colors.length > 0 && isTierProduct) {
        // Tier picker: each garment color is a compass charge tier. Chips show
        // the tier word in JetBrains Mono, knockout-boxed like the shirt print,
        // in the tier color (not white).
        const g = el('div', 'product__opt-group');
        g.appendChild(el('span', 'product__opt-label', 'Tier'));
        const row = el('div', 'product__tiers');
        colors.slice().sort((a, b) => tierRank(a) - tierRank(b)).forEach((c) => {
          const t = TIER_BY_COLOR[c];
          const b = el('button', 'product__tier' + (c === selColor ? ' product__tier--active' : ''), t.label);
          b.type = 'button';
          // Inverted / true knockout: word knocked out (dark) of a solid
          // tier-color block.
          b.style.background = t.hex;
          b.style.color = '#0a0a14';
          b.style.borderColor = t.hex;
          b.title = t.label;
          b.addEventListener('click', () => {
            selColor = c;
            if (colorImage[c]) cover.src = colorImage[c];
            if (selSize && !sizeAvailableForColor(selSize)) selSize = null;
            renderOpts(); refresh();
          });
          row.appendChild(b);
        });
        g.appendChild(row);
        if (!selColor) g.appendChild(el('span', 'product__tier-hint', 'Select a tier'));
        optsEl.appendChild(g);
      } else if (colors.length > 0) {
        const g = el('div', 'product__opt-group');
        g.appendChild(el('span', 'product__opt-label', 'Color'));
        const row = el('div', 'product__swatches');
        colors.forEach((c) => {
          const b = el('button', 'product__swatch' + (c === selColor ? ' product__swatch--active' : ''));
          b.type = 'button';
          b.style.background = swatchHex(c);
          b.title = c;
          b.setAttribute('aria-label', c);
          b.addEventListener('click', () => {
            selColor = c;
            if (colorImage[c]) cover.src = colorImage[c];
            if (selSize && !sizeAvailableForColor(selSize)) selSize = null;
            renderOpts(); refresh();
          });
          row.appendChild(b);
        });
        row.appendChild(el('span', 'product__swatch-name', selColor || 'Choose a color'));
        g.appendChild(row);
        optsEl.appendChild(g);
      }
      if (sizes.length > 0) {
        const g = el('div', 'product__opt-group');
        g.appendChild(el('span', 'product__opt-label', 'Size'));
        const row = el('div', 'product__opts');
        sizes.forEach((s) => {
          const out = !sizeAvailableForColor(s);
          const b = el('button', 'product__opt' + (s === selSize ? ' product__opt--active' : '') + (out ? ' product__opt--out' : ''), s);
          b.type = 'button';
          if (out) b.disabled = true;
          else b.addEventListener('click', () => { selSize = s; renderOpts(); refresh(); });
          row.appendChild(b);
        });
        g.appendChild(row);
        optsEl.appendChild(g);
      }
    }

    function refresh() {
      const v = currentVariant();
      const lowest = variants.length ? Math.min(...variants.map((x) => x.price_cents || 0)) : 0;
      priceEl.textContent = v ? fmt(v.price_cents) : (lowest ? 'from ' + fmt(lowest) : '');
      if (!available) { buyEl.textContent = 'Coming soon'; buyEl.disabled = true; return; }
      const needsChoice = (colors.length > 0 && !selColor) || (sizes.length > 0 && !selSize);
      if (needsChoice) { buyEl.textContent = 'Select an option'; buyEl.disabled = true; }
      else if (!v) { buyEl.textContent = 'Unavailable'; buyEl.disabled = true; }
      else { buyEl.textContent = 'Add to cart'; buyEl.disabled = false; }
    }

    buyEl.addEventListener('click', () => {
      if (!available) return;
      const v = currentVariant();
      if (!v) return;
      addToCart({
        slug: p.slug,
        title: p.title,
        image_url: cover.src || p.image_url,
        variant_id: v.id,
        variant_label: [v.color, v.size].filter(Boolean).join(' / ') || v.title || '',
        price_cents: v.price_cents,
      });
    });

    renderOpts();
    refresh();
    if (selColor && colorImage[selColor]) cover.src = colorImage[selColor];

    // Preview mode: coming-soon note + subscribe under the greyed buy button.
    if (!available) {
      const note = buildSubscribeNote(comingMsg || 'Coming soon. Subscribe to be notified.');
      buyEl.insertAdjacentElement('afterend', note);
    }
  }

  // --- Boot ---------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', async () => {
    const grid = document.getElementById('shop-grid');
    const detail = document.getElementById('product-root');
    // The catalog is always browsable. `available` (shop.enabled) only decides
    // whether buying is on: when off, the buy button is greyed with a
    // coming-soon + subscribe note, and no cart is shown.
    let cfg = null;
    try { cfg = await apiGet('/api/shop/config'); } catch (_) {}
    const available = cfg ? cfg.available === true : true;
    const comingMsg = (cfg && cfg.coming_soon_message)
      || 'Product coming soon. Subscribe to be notified.';
    if (available) buildCartUI();
    if (grid) renderGrid(grid);
    if (detail) renderDetail(detail, available, comingMsg);
  });

  // Expose for the checkout page (reads cart, clears it after payment).
  window.RCShop = {
    getCart,
    clearCart: () => saveCart([]),
    API_KEY,
  };
})();
