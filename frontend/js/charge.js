/* === Charge Spectrum — Gradient Bar with Glowing Point === */

const Charge = (() => {
  const COLOR_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };

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

  return { render, setLevel };
})();
