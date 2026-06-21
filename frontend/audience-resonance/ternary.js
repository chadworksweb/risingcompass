// Ternary plot component (vanilla SVG), reusable for Audience Resonance.
// Corners: True (apex), Camouflage (bottom-left), Adjacent (bottom-right).
// Three proportions sum to 100, which is two degrees of freedom, so a triangle
// is the correct (non-distorting) plot. Look harvested from the homepage
// trajectory charts: main.css :root tokens, .traj-tooltip for hover, subtle
// gridlines and dot styling.
//
// Each point: { t, c, a } fractions (0..1), plus optional
//   color, r (radius), id, href, tooltipHtml, dim (bool).
// Returns { svg, dotById } so callers can drive linked brushing.

const SVGNS = 'http://www.w3.org/2000/svg';

const VB_W = 520;
const VB_H = 480;
const APEX = { x: 260, y: 60 };  // True
const BL = { x: 60, y: 406 };    // Camouflage (bottom-left)
const BR = { x: 460, y: 406 };   // Adjacent (bottom-right)

function bary(t, c, a) {
  return { x: APEX.x * t + BL.x * c + BR.x * a, y: APEX.y * t + BL.y * c + BR.y * a };
}

function el(name, attrs) {
  const node = document.createElementNS(SVGNS, name);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  return node;
}

function gridline(parent, p1, p2) {
  parent.appendChild(el('line', { class: 'ar-tern-gridline', x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y }));
}

function cornerLabel(svg, x, y, label, sub, anchor) {
  const t = el('text', { class: 'ar-tern-corner', x, y, 'text-anchor': anchor });
  t.textContent = label;
  svg.appendChild(t);
  const s = el('text', { class: 'ar-tern-corner-sub', x, y: y + 14, 'text-anchor': anchor });
  s.textContent = sub;
  svg.appendChild(s);
}

export function renderTernary(mount, opts) {
  const { points = [], caption = '', onPointClick = null } = opts || {};
  if (!mount) return { svg: null, dotById: new Map() };
  mount.textContent = '';
  mount.style.position = 'relative';

  const svg = el('svg', {
    class: 'ar-ternary-svg',
    viewBox: `0 0 ${VB_W} ${VB_H}`,
    preserveAspectRatio: 'xMidYMid meet',
    role: 'img',
    'aria-label': 'Audience Resonance ternary plot',
  });

  // gridlines for each axis at 25 / 50 / 75 percent
  const grid = el('g', { class: 'ar-tern-grid' });
  for (const k of [0.25, 0.5, 0.75]) {
    gridline(grid, bary(k, 1 - k, 0), bary(k, 0, 1 - k));   // constant True
    gridline(grid, bary(1 - k, k, 0), bary(0, k, 1 - k));   // constant Camouflage
    gridline(grid, bary(1 - k, 0, k), bary(0, 1 - k, k));   // constant Adjacent
  }
  svg.appendChild(grid);

  svg.appendChild(el('polygon', {
    class: 'ar-tern-edge',
    points: `${APEX.x},${APEX.y} ${BR.x},${BR.y} ${BL.x},${BL.y}`,
  }));

  cornerLabel(svg, APEX.x, APEX.y - 24, 'TRUE', 'the song did it', 'middle');
  cornerLabel(svg, BL.x, BL.y + 28, 'CAMOUFLAGE', 'counterfeit lift', 'middle');
  cornerLabel(svg, BR.x, BR.y + 28, 'ADJACENT', 'your life did it', 'middle');

  // tooltip (reuses the .traj-tooltip look)
  const tip = document.createElement('div');
  tip.className = 'traj-tooltip ar-tern-tooltip';
  tip.style.display = 'none';

  function showTip(p, node) {
    if (!p.tooltipHtml) return;
    tip.innerHTML = p.tooltipHtml;
    tip.style.display = 'block';
    const mr = mount.getBoundingClientRect();
    const dr = node.getBoundingClientRect();
    tip.style.left = (dr.left - mr.left + dr.width / 2) + 'px';
    tip.style.top = (dr.top - mr.top - 8) + 'px';
    tip.style.transform = 'translate(-50%, -100%)';
  }
  function hideTip() { tip.style.display = 'none'; }

  // dots (drawn last so they sit above the grid)
  const dotById = new Map();
  const dotsG = el('g', { class: 'ar-tern-dots' });
  for (const p of points) {
    const pt = bary(p.t, p.c, p.a);
    const dot = el('circle', {
      class: 'ar-tern-dot' + (p.dim ? ' is-dim' : ''),
      cx: pt.x, cy: pt.y, r: p.r || 6, fill: p.color || '#888888',
    });
    if (p.id != null) { dot.setAttribute('data-pid', p.id); dotById.set(p.id, dot); }
    if (!p.dim) {
      dot.addEventListener('mouseenter', () => showTip(p, dot));
      dot.addEventListener('mouseleave', hideTip);
      if (onPointClick) {
        dot.style.cursor = 'pointer';
        dot.addEventListener('click', () => onPointClick(p));
      }
    }
    dotsG.appendChild(dot);
  }
  svg.appendChild(dotsG);

  mount.appendChild(svg);
  mount.appendChild(tip);

  if (caption) {
    const cap = document.createElement('div');
    cap.className = 'ar-tern-caption';
    cap.textContent = caption;
    mount.appendChild(cap);
  }

  return { svg, dotById };
}
