/* === SVG Compass Gauge === */

const Compass = (() => {
  const COLORS = ['violet', 'blue', 'green', 'yellow', 'red'];
  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    yellow: '#ffbb33',
    red: '#ff3333',
  };

  // SVG geometry: half-circle, center at (180, 170), radius 130
  const CX = 180, CY = 170, R = 130;
  const ARC_WIDTH = 18;

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

  function render(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Build SVG — expanded viewBox for outer labels + date text
    let svg = `<svg class="compass-svg" viewBox="0 -10 360 300" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Compass gauge showing current charge level">`;

    const arcSpan = 36; // 180/5
    const TIER_LABELS = ['Ascended', 'Elevated', 'Decent', 'Degraded', 'Corrupted'];
    const labelR = R + ARC_WIDTH / 2 + 11;

    // Defs: arc paths for curved text labels
    svg += '<defs>';
    TIER_LABELS.forEach((label, i) => {
      const startDeg = i * arcSpan;
      const endDeg = startDeg + arcSpan;
      const s = polarToCart(startDeg, labelR);
      const e = polarToCart(endDeg, labelR);
      svg += `<path id="tier-path-${i}" d="M ${s.x} ${s.y} A ${labelR} ${labelR} 0 0 1 ${e.x} ${e.y}" fill="none" />`;
    });
    svg += '</defs>';

    // Draw 5 color arcs
    COLORS.forEach((color, i) => {
      const startDeg = i * arcSpan;
      const endDeg = startDeg + arcSpan;
      const path = arcPath(startDeg, endDeg, R);
      svg += `<path class="compass-arc ${color}" data-color="${color}" d="${path}" />`;
    });

    // Tick marks every 18°
    for (let deg = 0; deg <= 180; deg += 18) {
      const isMajor = deg % 36 === 0;
      const outerR = R + (ARC_WIDTH / 2) + 4;
      const innerR = R + (ARC_WIDTH / 2) + (isMajor ? 12 : 8);
      const o = polarToCart(deg, outerR);
      const inner = polarToCart(deg, innerR);
      svg += `<line class="${isMajor ? 'compass-tick-major' : 'compass-tick'}" x1="${o.x}" y1="${o.y}" x2="${inner.x}" y2="${inner.y}" />`;
    }

    // Curved tier labels following the arcs
    TIER_LABELS.forEach((label, i) => {
      svg += `<text class="compass-tier-label"><textPath href="#tier-path-${i}" startOffset="50%" text-anchor="middle">${label}</textPath></text>`;
    });

    // Ghost trail layer (past 30 days — light trails)
    svg += `<g class="compass-ghost-trail" id="compass-ghost-trail"></g>`;

    // Needle group — starts at 90° (straight up)
    svg += `<g class="compass-needle" id="compass-needle">`;
    // Needle: thin triangle pointing up from center
    svg += `<polygon class="needle-line" points="${CX},${CY - R + 15} ${CX - 4},${CY} ${CX + 4},${CY}" />`;
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
      yellow: 'DEGRADED',
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

  function setGhostTrail(readings) {
    const group = document.getElementById('compass-ghost-trail');
    if (!group || !readings.length) return;

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
      const tipR = R - 15;
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

  return { render, setDegree, setGhostTrail };
})();
