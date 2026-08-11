/* === SVG Compass Gauge === */

const Compass = (() => {
  const COLORS = ['violet', 'blue', 'green', 'orange', 'red'];
  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };

  // Tier band boundaries in degrees (0 = Ascended/left .. 180 = Corrupted/right).
  // Mirrors backend/app/services/charge_calc.py CHARGE_TIERS thresholds
  // (22.5 / 67.5 / 112.5 / 157.5). SINGLE SOURCE OF TRUTH lives there — keep these
  // in lockstep, do not fork a second set of cutoffs. Ascended/Corrupted are the
  // narrow 22.5deg poles; the middle three are 45deg each.
  const BOUNDS = [0, 22.5, 67.5, 112.5, 157.5, 180];
  const TIER_LABELS = ['Ascended', 'Elevated', 'Decent', 'Degraded', 'Corrupted'];
  // Non-overlapping bands that mirror charge_calc.py exactly. Symmetric about the
  // neutral center: each boundary score rounds toward the MORE EXTREME tier, so
  // +75 -> Ascended mirrors -75 -> Corrupted, and +25 -> Elevated mirrors -25 ->
  // Degraded. Decent is the symmetric center. Negatives read near-zero-first.
  const TIER_RANGE = ['+75 to +100', '+25 to +74', '-24 to +24', '-25 to -74', '-75 to -100'];
  const TIER_DESC = [
    'Collective consciousness. Expands the listener.',
    'Processes life with dignity. Lifts.',
    'Neutral baseline. Pleasant, does no harm.',
    'Agitates, wallows, or diminishes.',
    'Ego black-hole. The deepest negative.',
  ];

  // SVG geometry: half-circle, center at (180, 170). The band's inner edge stays
  // at radius 122; its outer edge is stretched out to the tick tops (radius 156).
  // Ticks + labels are anchored to a FIXED ring (TICK_RING) so stretching the
  // band doesn't move them. "CORRUPTED" fits the 22.5deg pole at the label arc.
  const CX = 180, CY = 170, R = 139;
  const ARC_WIDTH = 34;
  // Fixed reference ring the curved labels sit just beyond (independent of band
  // thickness). Was also the tick ring before the ticks were removed.
  const TICK_RING = 148;

  function degToRad(deg) {
    // 0° = left (Ascended), 180° = right (Corrupted)
    // SVG angle: 180° to 0° (left to right across the top)
    return (Math.PI - (deg / 180) * Math.PI);
  }

  function polarToCart(angleDeg, radius) {
    const rad = degToRad(angleDeg);
    return {
      x: CX + radius * Math.cos(rad),
      y: CY - radius * Math.sin(rad),
    };
  }

  function arcPath(startDeg, endDeg, radius) {
    const s = polarToCart(startDeg, radius);
    const e = polarToCart(endDeg, radius);
    const largeArc = (endDeg - startDeg) > 90 ? 1 : 0;
    // SVG arc: sweep direction matters — we go counterclockwise (0=CCW for our coord)
    return `M ${s.x} ${s.y} A ${radius} ${radius} 0 ${largeArc} 1 ${e.x} ${e.y}`;
  }

  // opts.interactive: pass false to render a DISPLAY-ONLY gauge -- no band
  // hover, no focus targets, no tooltip. Added for the methodology page, where
  // the needle is driven by scroll and a band lighting up under the cursor
  // would fight the reading it is illustrating. Every existing caller omits
  // opts and keeps the interactive gauge unchanged.
  function render(containerId, opts) {
    const interactive = !opts || opts.interactive !== false;
    const container = document.getElementById(containerId);
    if (!container) return;

    // Build SVG — expanded viewBox for outer labels + date text
    let svg = `<svg class="compass-svg" viewBox="0 -10 360 300" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Compass gauge showing current charge level">`;

    const labelR = TICK_RING + 17;

    // Defs: arc paths for curved text labels — one per tier band (uneven widths)
    svg += '<defs>';
    for (let i = 0; i < BOUNDS.length - 1; i++) {
      const s = polarToCart(BOUNDS[i], labelR);
      const e = polarToCart(BOUNDS[i + 1], labelR);
      svg += `<path id="tier-path-${i}" d="M ${s.x} ${s.y} A ${labelR} ${labelR} 0 0 1 ${e.x} ${e.y}" fill="none" />`;
    }
    svg += '</defs>';

    // Five color arcs sized to the true tier bands (BOUNDS). Each is hover-
    // targetable (id + data-tier) and keyboard-focusable for the tooltip.
    COLORS.forEach((color, i) => {
      const path = arcPath(BOUNDS[i], BOUNDS[i + 1], R);
      // A display-only gauge drops the button semantics with the hover: an
      // unfocusable band is correct when there is nothing to activate.
      const hooks = interactive
        ? ` tabindex="0" role="button" aria-label="${TIER_LABELS[i]}, charge ${TIER_RANGE[i]}"`
        : '';
      svg += `<path class="compass-arc ${color}" id="compass-arc-${i}" data-tier="${i}" data-color="${color}"${hooks} d="${path}" />`;
    });

    // (Tick marks removed by design — the band's outer rim is the reference edge.)

    // Curved tier labels following the arcs
    TIER_LABELS.forEach((label, i) => {
      svg += `<text class="compass-tier-label" id="compass-tier-label-${i}"><textPath href="#tier-path-${i}" startOffset="50%" text-anchor="middle">${label}</textPath></text>`;
    });

    // Ghost trail layer (past 30 days — light trails)
    svg += `<g class="compass-ghost-trail" id="compass-ghost-trail"></g>`;

    // Needle group — starts at 90° (straight up)
    svg += `<g class="compass-needle" id="compass-needle">`;
    // Needle: thin triangle pointing up from center
    svg += `<polygon class="needle-line" points="${CX},${CY - (R - ARC_WIDTH / 2 - 8)} ${CX - 4},${CY} ${CX + 4},${CY}" />`;
    svg += `<circle class="needle-cap" cx="${CX}" cy="${CY}" r="8" />`;
    svg += `</g>`;

    // Center score + charge label with dashboard backing
    svg += `<rect class="compass-score-bg" x="${CX - 48}" y="${CY + 22}" width="96" height="38" rx="4" />`;
    svg += `<text class="compass-score-text" id="compass-score" x="${CX}" y="${CY + 50}">--</text>`;
    svg += `<rect class="compass-label-bg" x="${CX - 62}" y="${CY + 66}" width="124" height="29" rx="3" />`;
    svg += `<text class="compass-label-text" id="compass-charge-text" x="${CX}" y="${CY + 86}">LOADING</text>`;

    // Date line — snug below label
    svg += `<text class="compass-date-text" id="compass-date-svg" x="${CX}" y="${CY + 110}"></text>`;

    svg += `</svg>`;
    container.innerHTML = svg;

    if (interactive) _wireHover(container);
  }

  // ---- Band hover / focus tooltip ------------------------------------------
  // The tooltip is an HTML overlay (not SVG), parented to the compass CARD so it
  // can escape #compass-container's overflow:hidden and sit above the dial.
  function _ensureTooltip(host) {
    if (!host) return null;
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    let tip = host.querySelector('#compass-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'compass-tip';
      tip.className = 'compass-tip';
      tip.setAttribute('role', 'tooltip');
      tip.innerHTML =
        '<div class="compass-tip-head">' +
          '<span class="compass-tip-dot"></span>' +
          '<span class="compass-tip-name"></span>' +
          '<span class="compass-tip-range"></span>' +
        '</div>' +
        '<div class="compass-tip-body"></div>' +
        '<div class="compass-tip-tail"></div>';
      host.appendChild(tip);
    }
    return tip;
  }

  let _hotTier = -1;

  // THE band glow, in one place. Hover uses it and so does setHotTier, so a
  // band lit by the needle looks exactly like a band lit by the cursor.
  function _glowBand(i, on) {
    const arc = document.getElementById('compass-arc-' + i);
    const lbl = document.getElementById('compass-tier-label-' + i);
    if (arc) {
      arc.classList.toggle('hot', on);
      arc.style.filter = on
        ? `brightness(1.5) drop-shadow(0 0 8px ${COLOR_HEX[COLORS[i]]})`
        : '';
    }
    if (lbl) lbl.classList.toggle('hot', on);
  }

  // Light a band without a cursor. Pass -1 to clear. Used by the methodology
  // page to mark whichever tier the scroll-driven needle is sitting in.
  function setHotTier(i) {
    if (i === _hotTier) return;
    if (_hotTier >= 0) _glowBand(_hotTier, false);
    _hotTier = i;
    if (i >= 0) _glowBand(i, true);
  }

  function _wireHover(container) {
    const host = container.closest('.card') || container.parentElement;
    const tip = _ensureTooltip(host);
    if (!tip || !host) return;
    const svgEl = container.querySelector('.compass-svg');
    if (!svgEl) return;

    function hide(i) {
      _glowBand(i, false);
      tip.classList.remove('show');
      if (_hotTier === i) _hotTier = -1;
    }

    function show(i) {
      if (_hotTier === i) return;
      if (_hotTier >= 0) hide(_hotTier); // only one band hot at a time
      _hotTier = i;
      const hex = COLOR_HEX[COLORS[i]];
      _glowBand(i, true);
      tip.querySelector('.compass-tip-dot').style.cssText = `background:${hex};color:${hex};`;
      tip.querySelector('.compass-tip-name').textContent = TIER_LABELS[i];
      tip.querySelector('.compass-tip-range').textContent = TIER_RANGE[i];
      tip.querySelector('.compass-tip-body').textContent = TIER_DESC[i];
      // Anchor above the band's mid-angle point. Map SVG viewBox coords
      // (0 -10 360 300) to host pixel coords via the rendered svg rect.
      const midDeg = (BOUNDS[i] + BOUNDS[i + 1]) / 2;
      const p = polarToCart(midDeg, R + ARC_WIDTH / 2 + 6);
      const box = svgEl.getBoundingClientRect();
      const hb = host.getBoundingClientRect();
      const sx = box.width / 360;
      const sy = box.height / 300;
      const anchorX = p.x * sx + (box.left - hb.left);
      // Clamp the (center-anchored) tooltip inside the card so the pole bands'
      // tooltips don't overflow the edge; keep the tail pointed at the band.
      const half = (tip.offsetWidth || 230) / 2;
      const margin = 8;
      const centerX = Math.max(half + margin, Math.min(hb.width - half - margin, anchorX));
      tip.style.left = centerX + 'px';
      tip.style.top = ((p.y - (-10)) * sy + (box.top - hb.top)) + 'px';
      const tail = tip.querySelector('.compass-tip-tail');
      if (tail) {
        const dx = Math.max(-half + 16, Math.min(half - 16, anchorX - centerX));
        tail.style.left = `calc(50% + ${dx.toFixed(1)}px)`;
      }
      tip.classList.add('show');
    }

    COLORS.forEach((color, i) => {
      const arc = document.getElementById('compass-arc-' + i);
      if (!arc) return;
      arc.addEventListener('mouseenter', () => show(i));
      arc.addEventListener('mouseleave', () => hide(i));
      arc.addEventListener('focus', () => show(i));
      arc.addEventListener('blur', () => hide(i));
    });
  }

  function setDegree(degree, chargeLevel) {
    const needle = document.getElementById('compass-needle');
    if (!needle) return;

    // Needle rotation: 0° compass = -90° SVG rotation, 180° compass = +90° SVG rotation
    // Linear map: rotation = degree - 90
    const rotation = degree - 90;
    needle.style.transform = `rotate(${rotation}deg)`;

    // Update score
    const scoreEl = document.getElementById('compass-score');
    if (scoreEl) {
      const score = Math.round((90 - degree) * 100 / 90);
      scoreEl.textContent = (score > 0 ? '+' : '') + score;
    }

    // Update charge label
    const labels = {
      violet: 'ASCENDED',
      blue: 'ELEVATED',
      green: 'DECENT',
      orange: 'DEGRADED',
      red: 'CORRUPTED',
    };
    const chargeText = document.getElementById('compass-charge-text');
    if (chargeText) {
      chargeText.textContent = labels[chargeLevel] || chargeLevel.toUpperCase();
    }

    // Update SVG aria-label
    const svgEl = document.querySelector('.compass-svg');
    if (svgEl) {
      const score = Math.round((90 - degree) * 100 / 90);
      const label = labels[chargeLevel] || chargeLevel;
      svgEl.setAttribute('aria-label', `Compass: ${(score > 0 ? '+' : '')}${score}, ${label}`);
    }

    // Color the label background with the current charge
    const labelBg = document.querySelector('.compass-label-bg');
    if (labelBg) {
      const hex = COLOR_HEX[chargeLevel] || '#888';
      labelBg.setAttribute('fill', hex);
      labelBg.setAttribute('opacity', '0.15');
      labelBg.setAttribute('stroke', hex);
      labelBg.setAttribute('stroke-opacity', '0.4');
    }
  }

  // Skipped-day visual placement: ghost needle for a skipped reading
  // (no cron data) sits at the mean of nearest non-skipped neighbors so
  // the trail stays smooth. Color stays gray (charge_level === 'skipped'
  // falls through the COLOR_HEX || '#888' below). Visual only.
  function _interpolateSkipped(readings) {
    const n = readings.length;
    if (!n) return readings;
    // readings arrive newest-first; build neighbor maps in array order
    const prev = new Array(n).fill(-1);
    const next = new Array(n).fill(-1);
    let last = -1;
    for (let i = 0; i < n; i++) {
      prev[i] = last;
      if (readings[i].charge_level !== 'skipped') last = i;
    }
    last = -1;
    for (let i = n - 1; i >= 0; i--) {
      next[i] = last;
      if (readings[i].charge_level !== 'skipped') last = i;
    }
    return readings.map((r, i) => {
      if (r.charge_level !== 'skipped') return r;
      const p = prev[i], x = next[i];
      let interp = r.compass_degree;
      if (p >= 0 && x >= 0) interp = (readings[p].compass_degree + readings[x].compass_degree) / 2;
      else if (p >= 0) interp = readings[p].compass_degree;
      else if (x >= 0) interp = readings[x].compass_degree;
      return Object.assign({}, r, { compass_degree: interp });
    });
  }

  function setGhostTrail(readings) {
    const group = document.getElementById('compass-ghost-trail');
    if (!group || !readings.length) return;

    readings = _interpolateSkipped(readings);

    // readings: newest first, each has compass_degree + charge_level
    // Render as thin radial sweeps from center, fading with age
    const total = readings.length;
    let html = '';

    // Render oldest first so newest paints on top
    for (let i = total - 1; i >= 0; i--) {
      const r = readings[i];
      const age = i; // 0 = newest, total-1 = oldest
      const opacity = 0.04 + (1 - age / total) * 0.18; // newest ~0.22, oldest ~0.04
      const hex = COLOR_HEX[r.charge_level] || '#888';

      // Ghost needle: thin triangle like the real needle but thinner
      const rad = degToRad(r.compass_degree);
      const tipR = R - ARC_WIDTH / 2 - 8;
      const tipX = CX + tipR * Math.cos(rad);
      const tipY = CY - tipR * Math.sin(rad);

      // Spread at base (wider = more glow feel)
      const spreadAngle = 0.03; // radians
      const baseR = 12;
      const lx = CX + baseR * Math.cos(rad + spreadAngle);
      const ly = CY - baseR * Math.sin(rad + spreadAngle);
      const rx = CX + baseR * Math.cos(rad - spreadAngle);
      const ry = CY - baseR * Math.sin(rad - spreadAngle);

      html += `<polygon points="${tipX},${tipY} ${lx},${ly} ${rx},${ry}" fill="${hex}" opacity="${opacity.toFixed(3)}" />`;

      // Glow halo at the tip
      html += `<circle cx="${tipX}" cy="${tipY}" r="4" fill="${hex}" opacity="${(opacity * 0.6).toFixed(3)}" />`;
    }

    group.innerHTML = html;
  }

  return { render, setDegree, setGhostTrail, setHotTier };
})();
