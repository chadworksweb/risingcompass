/* === Contamination Counter — Badge Display === */

const Contamination = (() => {

  function render(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = `
      <div class="contam-badge zero" id="contam-badge">
        <span class="contam-icon">&#x2622;</span>
        <span class="contam-badge-num" id="contam-number">0</span>
        <div class="contam-tooltip"><strong>Contaminated songs:</strong> Songs in the top 3 tiers that carry content undercutting or distracting from being 100% pure.</div>
      </div>
    `;

    // Tap support for mobile
    const badge = document.getElementById('contam-badge');
    if (badge) {
      badge.addEventListener('click', () => badge.classList.toggle('active'));
      document.addEventListener('click', (e) => {
        if (!e.target.closest('#contam-badge')) badge.classList.remove('active');
      });
    }
  }

  function setCount(count, total) {
    const badge = document.getElementById('contam-badge');
    const numEl = document.getElementById('contam-number');
    if (!badge || !numEl) return;

    numEl.textContent = count;

    if (count > 0) {
      badge.classList.remove('zero');
    } else {
      badge.classList.add('zero');
    }
  }

  return { render, setCount };
})();
