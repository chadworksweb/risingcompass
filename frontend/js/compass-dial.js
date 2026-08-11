/* === Compass Dial — display-only fork of js/compass.js ======================
 *
 * A FORK, deliberately. js/compass.js is the live instrument: it is wired to a
 * reading, it hovers, it focuses, it carries a tooltip, and five pages depend on
 * it behaving exactly as it does. This copy exists so the methodology page can
 * use the compass as an ILLUSTRATION without any of that being negotiated in the
 * shared file.
 *
 * What this fork drops:
 *   - hover, focus, tooltip, and the button semantics on the bands
 *   - the score / charge label / date readouts, and the ghost trail
 *   - the viewBox room those readouts needed, so the box ends where the dial
 *     ends and can be bottom-aligned against something
 *
 * What it adds:
 *   - setHotTier(i), to light a band from code instead of from a cursor, using
 *     the same treatment the live gauge uses on hover
 *   - setDegree(deg) with no second argument required
 *
 * SHARED WITH THE ORIGINAL: the tier cutoffs and the CSS. Both files render
 * .compass-arc / .compass-needle / .compass-tier-label and are styled by
 * css/compass.css. The band boundaries below mirror
 * backend/app/services/charge_calc.py, which is the single source of truth --
 * if those thresholds ever move, they move in the backend first and BOTH of
 * these files follow.
 */

const CompassDial = (() => {
  const COLORS = ['violet', 'blue', 'green', 'orange', 'red'];
  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };

  // 0 = Ascended (left) .. 180 = Corrupted (right). Ascended and Corrupted are
  // the narrow 22.5deg poles; the middle three are 45deg each.
  const BOUNDS = [0, 22.5, 67.5, 112.5, 157.5, 180];
  const TIER_LABELS = ['Ascended', 'Elevated', 'Decent', 'Degraded', 'Corrupted'];

  // Same geometry as the live gauge so the art is identical. The viewBox is the
  // one real difference: 205 units tall instead of 300, cropped just below the
  // needle hub (the dial's lowest ink is y=178) now that nothing is drawn
  // underneath it.
  const CX = 180, CY = 170, R = 139;
  const ARC_WIDTH = 34;
  const TICK_RING = 148;
  const VIEWBOX = '0 -10 360 205';

  function degToRad(deg) {
    return (Math.PI - (deg / 180) * Math.PI);
  }

  function polarToCart(angleDeg, radius) {
    const rad = degToRad(angleDeg);
    return { x: CX + radius * Math.cos(rad), y: CY - radius * Math.sin(rad) };
  }

  function arcPath(startDeg, endDeg, radius) {
    const s = polarToCart(startDeg, radius);
    const e = polarToCart(endDeg, radius);
    const largeArc = (endDeg - startDeg) > 90 ? 1 : 0;
    return `M ${s.x} ${s.y} A ${radius} ${radius} 0 ${largeArc} 1 ${e.x} ${e.y}`;
  }

  // Ids are namespaced (cdial-) so this can coexist with the live gauge on the
  // same document without either one grabbing the other's nodes.
  function render(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const labelR = TICK_RING + 17;
    let svg = `<svg class="compass-svg" viewBox="${VIEWBOX}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Compass dial showing the five charge tiers">`;

    svg += '<defs>';
    for (let i = 0; i < BOUNDS.length - 1; i++) {
      const s = polarToCart(BOUNDS[i], labelR);
      const e = polarToCart(BOUNDS[i + 1], labelR);
      svg += `<path id="cdial-path-${i}" d="M ${s.x} ${s.y} A ${labelR} ${labelR} 0 0 1 ${e.x} ${e.y}" fill="none" />`;
    }
    svg += '</defs>';

    // No tabindex and no role=button: there is nothing to activate here.
    COLORS.forEach((color, i) => {
      svg += `<path class="compass-arc ${color}" id="cdial-arc-${i}" data-tier="${i}" data-color="${color}" d="${arcPath(BOUNDS[i], BOUNDS[i + 1], R)}" />`;
    });

    TIER_LABELS.forEach((label, i) => {
      svg += `<text class="compass-tier-label" id="cdial-label-${i}"><textPath href="#cdial-path-${i}" startOffset="50%" text-anchor="middle">${label}</textPath></text>`;
    });

    svg += `<g class="compass-needle" id="cdial-needle">`;
    svg += `<polygon class="needle-line" points="${CX},${CY - (R - ARC_WIDTH / 2 - 8)} ${CX - 4},${CY} ${CX + 4},${CY}" />`;
    svg += `<circle class="needle-cap" cx="${CX}" cy="${CY}" r="8" />`;
    svg += `</g>`;
    svg += `</svg>`;

    container.innerHTML = svg;
  }

  // 0 compass = -90 SVG rotation, 180 compass = +90. Linear: rotation = deg - 90.
  function setDegree(degree) {
    const needle = document.getElementById('cdial-needle');
    if (needle) needle.style.transform = `rotate(${degree - 90}deg)`;
  }

  let _hot = -1;

  // The live gauge's hover treatment, applied from code. Kept byte-identical to
  // the filter in compass.js _wireHover so a band lit here reads the same as a
  // band lit by a cursor there.
  function _glow(i, on) {
    const arc = document.getElementById('cdial-arc-' + i);
    const lbl = document.getElementById('cdial-label-' + i);
    if (arc) {
      arc.classList.toggle('hot', on);
      arc.style.filter = on ? `brightness(1.5) drop-shadow(0 0 8px ${COLOR_HEX[COLORS[i]]})` : '';
    }
    if (lbl) lbl.classList.toggle('hot', on);
  }

  // Light one band, or pass -1 to clear.
  function setHotTier(i) {
    if (i === _hot) return;
    if (_hot >= 0) _glow(_hot, false);
    _hot = i;
    if (i >= 0) _glow(i, true);
  }

  // Which band a degree falls in. Mirrors charge_calc.py.
  function bandFor(degree) {
    if (degree <= 22.5) return 0;
    if (degree <= 67.5) return 1;
    if (degree <= 112.5) return 2;
    if (degree <= 157.5) return 3;
    return 4;
  }

  return { render, setDegree, setHotTier, bandFor };
})();
