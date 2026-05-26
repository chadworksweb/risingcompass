/* === Charge Spectrum — Gradient Bar with Glowing Point === */

const Charge = (() => {
  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };

  // The charge SPECTRUM: the same 5 evenly-spaced stops as the .charge-gradient
  // bar (violet=+100/Ascended ... red=-100/Corrupted). Sampling it gives an
  // exact hex for any integer score, instead of the flat 5 tier colors.
  const SPECTRUM = ['#aa54ff', '#3388ff', '#33cc55', '#ffbb33', '#ff3333'];

  function _hexToRgb(h) {
    h = h.replace('#', '');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  function _rgbToHex(r, g, b) {
    const c = (v) => ('0' + Math.round(v).toString(16)).slice(-2);
    return '#' + c(r) + c(g) + c(b);
  }
  function _rgba(hex, a) {
    const c = _hexToRgb(hex);
    return `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})`;
  }
  // t in [0,1]: 0 = violet (Ascended), 1 = red (Corrupted). Linear RGB lerp
  // between the two adjacent stops -- matches the CSS gradient exactly.
  function spectrumHexFromT(t) {
    t = Math.max(0, Math.min(1, t));
    const seg = t * (SPECTRUM.length - 1);
    const i = Math.min(SPECTRUM.length - 2, Math.floor(seg));
    const f = seg - i;
    const a = _hexToRgb(SPECTRUM[i]);
    const b = _hexToRgb(SPECTRUM[i + 1]);
    return _rgbToHex(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f);
  }
  // score in [-100,+100] (+100 = violet/Ascended). Exact gradient color.
  function spectrumHex(score) {
    return spectrumHexFromT((100 - score) / 200);
  }
  // Same spectrum color as an rgba string, for the inner-glow box-shadow.
  function spectrumRgba(score, a) {
    return _rgba(spectrumHex(score), a == null ? 0.45 : a);
  }

  function render(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = `
      <div class="charge-spectrum">
        <div class="charge-gradient">
          <div class="charge-point" id="charge-point"></div>
        </div>
        <div class="charge-spectrum-labels">
          <span>Ascended</span>
          <span>Corrupted</span>
        </div>
      </div>
    `;
  }

  function setLevel(chargeColor, redCount, totalSongs, degree) {
    // Home compass panel: border + inner glow in the reading's exact spectrum
    // color. Done BEFORE the #charge-point guard below, since the home page has
    // no charge bar / point (the guard would otherwise return first). No-op on
    // pages without #compass-panel (e.g. the song page).
    const compassPanel = document.getElementById('compass-panel');
    if (compassPanel && degree != null) {
      const sHex = spectrumHexFromT(degree / 180);
      compassPanel.style.borderColor = sHex;
      compassPanel.style.setProperty('--charge-glow', _rgba(sHex, 0.45));
    }

    const point = document.getElementById('charge-point');
    if (!point) return;

    // Use precise degree when available (0°=Ascended/left, 180°=Corrupted/right)
    // Fall back to discrete tier positions when degree not provided
    const positions = {
      violet: 0,
      blue: 25,
      green: 50,
      orange: 75,
      red: 100,
    };

    const pct = (degree != null) ? (degree / 180) * 100 : (positions[chargeColor] ?? 50);
    const hex = COLOR_HEX[chargeColor] || '#888';

    point.style.left = pct + '%';
    point.style.background = hex;
    point.style.boxShadow = `0 0 10px ${hex}, 0 0 20px ${hex}`;

    // Update label
    const label = document.getElementById('charge-label');
    if (!label) return;

    if (totalSongs > 0) {
      label.innerHTML = `${redCount} of ${totalSongs} top songs carry red-charge lyrics`;
    } else {
      label.innerHTML = `Showing historical reading`;
    }
  }

  return { render, setLevel, spectrumHex, spectrumHexFromT, spectrumRgba };
})();
