/* === The Tenets — render constitution + organ from /api/tenets === */

(() => {
  'use strict';

  const SVG_NS = 'http://www.w3.org/2000/svg';

  // ─── API config ─────────────────────────────────────────────────
  const IS_LOCAL = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const API_HOST = '';  // same-origin relative; dev_server proxies /api -> :8000 locally
  const API_KEY = IS_LOCAL
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  async function fetchTenets() {
    // Turso cold-starts + nginx 502s during container restarts justify two retries.
    let lastErr;
    for (let i = 0; i < 3; i++) {
      try {
        const resp = await fetch(`${API_HOST}/api/tenets`, {
          headers: { 'X-Api-Key': API_KEY },
          signal: AbortSignal.timeout(8000),
        });
        if (resp.ok) return resp.json();
        if (resp.status >= 400 && resp.status < 500) {
          throw new Error(`API error ${resp.status}`);
        }
        lastErr = new Error(`API error ${resp.status}`);
      } catch (err) {
        lastErr = err;
      }
      if (i < 2) await new Promise((r) => setTimeout(r, 400 * (i + 1)));
    }
    throw lastErr;
  }

  // Organ geometry — pipes and pillars are data-driven; these are the only
  // knobs. If a tenet is added or removed, the silhouette shifts naturally.
  const PIPE_WIDTH   = 22;
  const PIPE_GAP     = 3;
  const PILLAR_GAP   = 34;
  const UNIT         = 20;      // pipe-height floor — no pipe renders shorter
  const ARCH_DIP     = 0.32;    // outer pipes shrink to 68% of the center's height
  const LABEL_ROOM   = 78;      // space beneath pipes for the tier label
  const BASE_HEIGHT  = 10;
  const TOP_PAD      = 24;
  // Pillar height grows quadratically above a 7-tenet baseline so bookend
  // pillars (13 tenets) tower over the middle pillars (10–11) like a real organ.
  // Small linear term nudges the 10–11 tenet pillars up a hair without
  // disturbing the 13-tenet bookends much.
  const BASELINE_N    = 7;
  const HEIGHT_FACTOR = 14;
  const HEIGHT_LINEAR = 8;

  // ─── tooltip (pipe hover/focus label) ──────────────────────────

  const tooltipEl = () => document.getElementById('pipe-tooltip');

  function showTooltip(target, text, color) {
    const tip = tooltipEl();
    if (!tip) return;
    tip.textContent = text;
    tip.style.setProperty('--tier-color', color);
    const rect = target.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top - 10;
    tip.style.transform = `translate(${x}px, ${y}px) translate(-50%, -100%)`;
    tip.classList.add('visible');
    tip.setAttribute('aria-hidden', 'false');
  }

  function hideTooltip() {
    const tip = tooltipEl();
    if (!tip) return;
    tip.classList.remove('visible');
    tip.setAttribute('aria-hidden', 'true');
  }

  // ─── helpers ────────────────────────────────────────────────────

  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      node.setAttribute(k, String(v));
    }
    for (const c of [].concat(children)) {
      if (c != null) node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return node;
  };

  const html = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === 'class') node.className = v;
      else if (k === 'style') node.setAttribute('style', v);
      else if (k.startsWith('data-') || k === 'id' || k === 'role' || k === 'tabindex' || k === 'aria-label') {
        node.setAttribute(k, v);
      } else {
        node[k] = v;
      }
    }
    for (const c of [].concat(children)) {
      if (c != null) node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return node;
  };

  /** Simple **bold** → <strong>bold</strong> parser for prose fields from JSON. */
  function renderMarkdownInline(text) {
    const frag = document.createDocumentFragment();
    const re = /\*\*(.+?)\*\*/g;
    let last = 0, m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const strong = document.createElement('strong');
      strong.textContent = m[1];
      frag.appendChild(strong);
      last = re.lastIndex;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    return frag;
  }

  // ─── pipe path: flat bottom, half-circle top ────────────────────

  function pipePath(w, h) {
    const r = w / 2;
    // M 0,r  arc to w,r over the top  L w,h  L 0,h  Z
    return `M 0 ${r} A ${r} ${r} 0 0 1 ${w} ${r} L ${w} ${h} L 0 ${h} Z`;
  }

  /** pipe height for pipe index i within a pillar whose center pipe is `peak` tall. */
  function pipeHeight(i, n, peak) {
    if (n <= 1) return peak;
    const center = (n - 1) / 2;
    const dist = Math.abs(i - center) / center;  // 0 at center, 1 at edges
    const factor = 1 - ARCH_DIP * dist;
    return Math.max(UNIT * 2, peak * factor);
  }

  // ─── organ ──────────────────────────────────────────────────────

  function renderOrgan(svg, data) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    const tiers = data.tiers;

    // Per-pillar width + tallest pipe
    const pillars = tiers.map((tier) => {
      const n = tier.tenets.length;
      const width = n * PIPE_WIDTH + (n - 1) * PIPE_GAP;
      const d = n - BASELINE_N;
      const peak = Math.max(UNIT * 3, d * d * HEIGHT_FACTOR + d * HEIGHT_LINEAR);
      return { tier, n, width, peak };
    });

    const totalWidth = pillars.reduce((s, p) => s + p.width, 0) + PILLAR_GAP * (pillars.length - 1);
    const tallest = Math.max(...pillars.map((p) => p.peak));
    const totalHeight = TOP_PAD + tallest + LABEL_ROOM + BASE_HEIGHT;

    svg.setAttribute('viewBox', `0 0 ${totalWidth} ${totalHeight}`);
    // Cap intrinsic height so the organ doesn't dominate on tall viewports
    svg.style.maxHeight = '680px';

    // gradient defs — one per tier
    const defs = el('defs');
    tiers.forEach((tier) => {
      const grad = el('linearGradient', {
        id: `grad-${tier.slug}`,
        x1: '0%', y1: '0%', x2: '0%', y2: '100%',
      });
      grad.appendChild(el('stop', { offset: '0%', 'stop-color': tier.color_hex, 'stop-opacity': '0.95' }));
      grad.appendChild(el('stop', { offset: '55%', 'stop-color': tier.color_hex, 'stop-opacity': '0.78' }));
      grad.appendChild(el('stop', { offset: '100%', 'stop-color': tier.color_hex, 'stop-opacity': '0.45' }));
      defs.appendChild(grad);
    });
    svg.appendChild(defs);

    // baseline y — where pipe bottoms sit
    const baselineY = TOP_PAD + tallest;

    let x = 0;
    pillars.forEach(({ tier, n, width, peak }) => {
      // Pillar group
      const pillarG = el('g', {
        class: 'pillar',
        transform: `translate(${x}, 0)`,
        'data-tier': tier.slug,
      });

      // Pipes
      tier.tenets.forEach((tenet, i) => {
        const h = pipeHeight(i, n, peak);
        const px = i * (PIPE_WIDTH + PIPE_GAP);
        const py = baselineY - h;

        const code = `${tier.code}${tenet.number}`;
        const group = el('g', {
          class: 'pipe-group',
          transform: `translate(${px}, ${py})`,
          'data-tenet-id': tenet.id,
          tabindex: '0',
          role: 'link',
          'aria-label': `Tenet ${code} — ${tier.label}`,
          style: `color: ${tier.color_hex};`,
        });
        // Native SVG tooltip
        group.appendChild(el('title', {}, `Tenet ${code}`));

        // Shadow/backplate — a thin dark line behind the pipe gives depth
        group.appendChild(el('path', {
          d: pipePath(PIPE_WIDTH, h),
          fill: '#000',
          opacity: '0.35',
          transform: `translate(1, 1)`,
        }));

        // Pipe body
        group.appendChild(el('path', {
          class: 'pipe-body',
          d: pipePath(PIPE_WIDTH, h),
          fill: `url(#grad-${tier.slug})`,
          stroke: tier.color_hex,
          'stroke-opacity': '0.3',
          'stroke-width': '1',
        }));

        // Highlight strip — vertical sheen on left edge
        group.appendChild(el('rect', {
          x: 3, y: PIPE_WIDTH / 2 + 1,
          width: 2, height: h - PIPE_WIDTH / 2 - 6,
          fill: '#ffffff',
          opacity: '0.18',
          rx: '1',
        }));

        // Mouth — the little slot near the base
        const mouthH = Math.max(4, PIPE_WIDTH * 0.22);
        const mouthY = h - PIPE_WIDTH * 0.75;
        group.appendChild(el('rect', {
          x: PIPE_WIDTH * 0.22, y: mouthY,
          width: PIPE_WIDTH * 0.56, height: mouthH,
          fill: '#000',
          opacity: '0.55',
          rx: '0.5',
        }));

        // Foot notch — dim lower stub below the mouth, present on every pipe
        // so the "standing on a foot" silhouette reads uniformly across pillars.
        const footY = mouthY + mouthH;
        group.appendChild(el('rect', {
          x: 0, y: footY,
          width: PIPE_WIDTH, height: h - footY,
          fill: '#000',
          opacity: '0.42',
        }));

        // Click → jump to tenet anchor + highlight
        const jump = () => jumpToTenet(tenet.id);
        const tooltipText = `Tenet ${code}`;
        group.addEventListener('click', jump);
        group.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            jump();
          }
        });
        group.addEventListener('pointerenter', () => showTooltip(group, tooltipText, tier.color_hex));
        group.addEventListener('pointerleave', hideTooltip);
        group.addEventListener('focus', () => showTooltip(group, tooltipText, tier.color_hex));
        group.addEventListener('blur', hideTooltip);

        pillarG.appendChild(group);
      });

      // Tier label under the pillar
      const labelY = baselineY + LABEL_ROOM * 0.42;
      const label = el('text', {
        class: 'pillar-label',
        x: width / 2,
        y: labelY,
        'text-anchor': 'middle',
        'font-size': '18',
      }, tier.label.toUpperCase());
      pillarG.appendChild(label);

      // Tenet count beneath the label
      const count = el('text', {
        class: 'pillar-label',
        x: width / 2,
        y: labelY + 25,
        'text-anchor': 'middle',
        'font-size': '14',
        'fill-opacity': '0.55',
      }, `${n} tenets`);
      pillarG.appendChild(count);

      svg.appendChild(pillarG);
      x += width + PILLAR_GAP;
    });

    // Base pedestal running across
    svg.appendChild(el('rect', {
      class: 'organ-base',
      x: -8,
      y: baselineY,
      width: totalWidth + 16,
      height: BASE_HEIGHT,
    }));
  }

  // ─── chambers ───────────────────────────────────────────────────

  function renderChambers(root, data) {
    root.replaceChildren();
    data.tiers.forEach((tier) => {
      const chamber = html('section', {
        class: 'chamber',
        id: `chamber-${tier.slug}`,
        style: `--tier-color: ${tier.color_hex};`,
      });

      chamber.appendChild(html('h2', { class: 'chamber-label' }, tier.label.toUpperCase()));
      chamber.appendChild(html('p', { class: 'chamber-definition' }, tier.definition));

      tier.tenets.forEach((tenet) => {
        const row = html('article', {
          class: 'tenet',
          id: `tenet-${tenet.id}`,
        });
        row.appendChild(html('div', { class: 'tenet-number' }, `${tier.code}${tenet.number}`));
        row.appendChild(html('div', { class: 'tenet-body' }, tenet.text));
        chamber.appendChild(row);
      });

      (tier.notes || []).forEach((note) => {
        const n = html('aside', { class: 'chamber-note' });
        n.appendChild(html('h3', { class: 'chamber-note-title' }, note.title));
        const body = html('p', { class: 'chamber-note-body' });
        body.appendChild(renderMarkdownInline(note.text));
        n.appendChild(body);
        chamber.appendChild(n);
      });

      root.appendChild(chamber);
    });
  }

  // ─── procedural ─────────────────────────────────────────────────

  // Render a flag's body (agent-prompt markdown) into procedural blocks:
  // "## x" -> subheading, "- x" -> list item, blank-separated -> paragraphs.
  // The flag body is the SAME core.json field the agent prompt renders, so the
  // public page and the calibrator can never drift.
  function renderFlagBody(parent, text) {
    let para = [], ul = null;
    const flushPara = () => {
      if (!para.length) return;
      const p = html('p', { class: 'procedural-block-body' });
      p.appendChild(renderMarkdownInline(para.join(' ')));
      parent.appendChild(p);
      para = [];
    };
    const flushUl = () => { if (ul) { parent.appendChild(ul); ul = null; } };
    (text || '').split('\n').forEach((raw) => {
      const line = raw.trim();
      if (!line) { flushPara(); flushUl(); return; }
      if (line.startsWith('## ')) {
        flushPara(); flushUl();
        parent.appendChild(html('h4', { class: 'procedural-flag-sub' }, line.slice(3)));
        return;
      }
      if (line.startsWith('- ')) {
        flushPara();
        if (!ul) ul = html('ul', { class: 'procedural-list' });
        const li = html('li');
        li.appendChild(renderMarkdownInline(line.slice(2)));
        ul.appendChild(li);
        return;
      }
      para.push(line);
    });
    flushPara(); flushUl();
  }

  function renderProcedural(root, data) {
    root.replaceChildren();

    root.appendChild(html('h2', { class: 'procedural-heading' }, 'PROCEDURAL'));
    root.appendChild(html('p', { class: 'procedural-kicker' }, 'Modifiers and rules that govern how the tenets are applied.'));

    (data.modifiers || []).forEach((mod) => {
      const block = html('section', { class: 'procedural-block', id: `modifier-${mod.id}` });
      block.appendChild(html('h3', { class: 'procedural-block-title' }, mod.label.toUpperCase()));

      const def = html('p', { class: 'procedural-block-body' });
      def.appendChild(renderMarkdownInline(mod.definition));
      block.appendChild(def);

      if (mod.body) {
        const body = html('p', { class: 'procedural-block-body' });
        body.appendChild(renderMarkdownInline(mod.body));
        block.appendChild(body);
      }

      if (Array.isArray(mod.examples) && mod.examples.length) {
        block.appendChild(html('p', { class: 'procedural-block-body' }, 'Examples of contamination:'));
        const ul = html('ul', { class: 'procedural-list' });
        mod.examples.forEach((ex) => {
          const li = html('li');
          li.appendChild(renderMarkdownInline(ex));
          ul.appendChild(li);
        });
        block.appendChild(ul);
      }

      if (mod.closing) {
        const closing = html('p', { class: 'procedural-block-body' });
        closing.appendChild(renderMarkdownInline(mod.closing));
        block.appendChild(closing);
      }

      root.appendChild(block);
    });

    (data.flags || []).forEach((flag) => {
      const block = html('section', { class: 'procedural-block', id: `flag-${flag.id}` });
      block.appendChild(html('h3', { class: 'procedural-block-title' }, (flag.label || flag.id).toUpperCase()));
      renderFlagBody(block, flag.body || '');
      root.appendChild(block);
    });

    (data.rules || []).forEach((rule) => {
      const block = html('section', { class: 'procedural-block', id: `rule-${rule.id}` });
      block.appendChild(html('h3', { class: 'procedural-block-title' }, `RULE ${rule.id}`));
      const body = html('p', { class: 'procedural-block-body' });
      body.appendChild(renderMarkdownInline(rule.text));
      block.appendChild(body);
      root.appendChild(block);
    });
  }

  // ─── navigation / highlight ────────────────────────────────────

  let _flashTimeout = null;

  function jumpToTenet(tenetId) {
    const target = document.getElementById(`tenet-${tenetId}`);
    if (!target) return;
    // Update URL hash without re-scrolling (we'll scroll ourselves)
    if (history.replaceState) {
      history.replaceState(null, '', `#tenet-${tenetId}`);
    }
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    // Strip flash from anything still flashing, then re-trigger on this one
    document.querySelectorAll('.tenet.flash').forEach((n) => n.classList.remove('flash'));
    if (_flashTimeout) clearTimeout(_flashTimeout);
    // Force reflow so the animation restarts even if clicked twice quickly
    void target.offsetWidth;
    target.classList.add('flash');
    _flashTimeout = setTimeout(() => target.classList.remove('flash'), 2400);
  }

  function handleInitialHash() {
    const hash = window.location.hash || '';
    const m = hash.match(/^#tenet-([a-z]+-\d+)$/i);
    if (!m) return;
    // Defer a tick so layout settles before we scroll
    requestAnimationFrame(() => jumpToTenet(m[1]));
  }

  // ─── footer metadata ────────────────────────────────────────────

  function renderFooterMeta(data) {
    const v = document.getElementById('schema-version');
    const r = document.getElementById('ratified-at');
    if (v) v.textContent = `v${data.schema_version}`;
    if (r) r.textContent = data.ratified_at;
  }

  // ─── boot ───────────────────────────────────────────────────────

  async function boot() {
    const svg = document.getElementById('organ');
    const chambers = document.getElementById('chambers');
    const procedural = document.getElementById('procedural');

    try {
      const data = await fetchTenets();
      renderOrgan(svg, data);
      renderChambers(chambers, data);
      renderProcedural(procedural, data);
      renderFooterMeta(data);
      handleInitialHash();
    } catch (err) {
      console.error('Failed to load tenets:', err);
      const msg = html('p', { class: 'organ-legend' }, 'The tenets are momentarily out of reach. Please reload.');
      svg.parentElement.appendChild(msg);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
