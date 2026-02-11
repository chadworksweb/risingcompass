/* === Contamination Counter — Badge Display === */

const Contamination = (() => {

  function render(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = `
      <div class="contam-badge zero" id="contam-badge">
        <svg class="contam-icon" viewBox="0 0 24 24" width="12" height="12"><path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.1 0 2 .9 2 2s-.9 2-2 2-2-.9-2-2 .9-2 2-2zm-3.5 4.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zm10 0a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zM8.5 16.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zm10 0a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zM12 15c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/></svg>
        <span class="contam-badge-num" id="contam-number">0</span>
        <div class="contam-tooltip"><strong>Contaminated songs:</strong> Songs in the top 3 tiers that carry content undercutting or distracting from being 100% pure.</div>
      </div>
    `;
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
