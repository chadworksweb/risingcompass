/* === Charge Spectrum — Gradient Bar with Glowing Point === */

const Charge = (() => {
  const COLOR_HEX = {
    bright_green: '#9933ff',
    green: '#3388ff',
    yellow: '#33cc55',
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

  function setLevel(chargeColor, redCount, totalSongs) {
    const point = document.getElementById('charge-point');
    if (!point) return;

    // Map charge color to position on gradient (0% = left/green, 100% = right/red)
    const positions = {
      bright_green: 0,
      green: 25,
      yellow: 50,
      orange: 75,
      red: 100,
    };

    const pct = positions[chargeColor] ?? 50;
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
      label.innerHTML = `Showing historical aggregate`;
    }
  }

  return { render, setLevel };
})();
