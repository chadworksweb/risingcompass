/* === Trajectory panel: Expand-over-compass toggle ===
   The trajectory (Daily / Historical Charge Index) panel expands left over the
   compass to a full-width exploratory view. Collapsed by default, no persistence.
   Collapse via the same button or Esc.

   Mechanics:
   - Collapsed state is the untouched .dash-row1 grid (compass 42%, trajectory 1fr).
   - On expand the trajectory card is lifted out of the grid into an absolute
     overlay and its WIDTH is animated from the grid cell to the full row. Height
     is content-driven (NOT pinned), so opening the Time Machine drawer grows the
     panel; a ResizeObserver keeps the row's min-height synced to the panel so the
     overlay never clips and Row 2 flows below it.
   - The daily chart's drawn height is locked to the row height before widening, so
     the plot stays row-height. The chart is then RE-RENDERED at the new width
     (js/app.js listens for 'rc:trajectory-resized' and recomputes the viewBox), so
     it gains real horizontal resolution instead of being stretched/distorted. */
(function () {
  'use strict';

  function init() {
    const btn = document.getElementById('trajectory-expand-btn');
    const panel = document.getElementById('trajectory-panel');
    const row = panel && panel.closest('.dash-row1');
    const lbl = btn && btn.querySelector('.trajectory-expand-lbl');
    if (!btn || !panel || !row) return;

    const MOBILE = '(max-width: 768px)';
    const ANIM_MS = 420;
    const EASE = 'cubic-bezier(0.4,0,0.15,1)';
    let expanded = false;
    let animating = false;
    let finalizeTimer = null;
    let resizeRaf = null;
    let ro = null;
    let coverH = 0; // compass-card height to cover while expanded

    const isMobile = () => window.matchMedia(MOBILE).matches;
    // The chart area of the currently visible trajectory tab (Daily or Historical).
    const activeChartArea = () =>
      panel.querySelector('.era-content.active .traj-chart-area') || panel.querySelector('.traj-chart-area');
    const fireResize = () => window.dispatchEvent(new CustomEvent('rc:trajectory-resized'));

    // Re-render the chart on each frame for `ms` so it reshapes smoothly with the
    // width animation (the viewBox is recomputed live) instead of stretching and
    // snapping into place at the end. Light throttle keeps it cheap.
    let reflowId = null;
    function reflowChartFor(ms) {
      cancelAnimationFrame(reflowId);
      const start = performance.now();
      let last = 0;
      const tick = (t) => {
        if (t - last >= 28) { fireResize(); last = t; }
        if (t - start < ms) reflowId = requestAnimationFrame(tick);
        else fireResize(); // exact final frame
      };
      reflowId = requestAnimationFrame(tick);
    }

    // Keep the row tall enough to contain the (content-sized) overlay. Runs while
    // expanded, so opening the Time Machine drawer grows the row too.
    function syncRowHeight() {
      if (expanded) row.style.minHeight = panel.offsetHeight + 'px';
    }
    function startObserving() {
      if (ro || typeof ResizeObserver === 'undefined') return;
      ro = new ResizeObserver(syncRowHeight);
      ro.observe(panel);
    }
    function stopObserving() {
      if (ro) { ro.disconnect(); ro = null; }
    }

    function clearInline() {
      ['position', 'top', 'left', 'width', 'height', 'minHeight', 'margin', 'zIndex', 'transition', 'boxShadow']
        .forEach((p) => { panel.style[p] = ''; });
      panel.classList.remove('traj-chart-locked');
      panel.style.removeProperty('--rc-locked-chart-h');
      row.style.minHeight = '';
    }

    function expand() {
      if (expanded || animating || isMobile()) return;
      const first = panel.getBoundingClientRect();
      const rowRect = row.getBoundingClientRect();

      // Lock the plot height (CSS var, applied to BOTH tabs' chart areas) to the
      // active chart's current height, so the chart re-renders WIDER (not taller)
      // when the panel widens. app.js matches the viewBox to the box aspect while
      // .traj-chart-locked is present. Switching to Historical keeps the same lock.
      const ca = activeChartArea();
      if (ca) panel.style.setProperty('--rc-locked-chart-h', ca.getBoundingClientRect().height + 'px');
      panel.classList.add('traj-chart-locked');

      // Cover the compass fully: the overlay is content-sized (~1px shorter than
      // the dial card), so without this the compass border/glow peeks out below.
      coverH = rowRect.height;
      panel.style.minHeight = coverH + 'px';
      row.style.minHeight = rowRect.height + 'px';

      // Freeze at the grid-cell rect as an absolute overlay (height stays auto ->
      // the panel sizes to its content, which the Time Machine drawer can grow).
      panel.style.transition = 'none';
      panel.style.position = 'absolute';
      panel.style.margin = '0';
      panel.style.top = (first.top - rowRect.top) + 'px';
      panel.style.left = (first.left - rowRect.left) + 'px';
      panel.style.width = first.width + 'px';
      panel.style.zIndex = '30';
      panel.classList.add('trajectory-expanded');
      void panel.offsetWidth; // commit the freeze before animating

      animating = true;
      panel.style.transition = `left ${ANIM_MS}ms ${EASE}, width ${ANIM_MS}ms ${EASE}, box-shadow ${ANIM_MS}ms ease`;
      panel.style.top = '0px';
      panel.style.left = '0px';
      panel.style.width = '100%';
      reflowChartFor(ANIM_MS); // smooth chart reshape during the widen

      expanded = true;
      btn.setAttribute('aria-expanded', 'true');
      if (lbl) lbl.textContent = 'Collapse';
      btn.title = 'Collapse panel';

      clearTimeout(finalizeTimer);
      finalizeTimer = setTimeout(() => {
        animating = false;
        fireResize();       // re-render the chart at the final (full) width
        startObserving();   // track Time Machine drawer / content growth
        syncRowHeight();
      }, ANIM_MS + 40);
    }

    function collapse(focusBtn) {
      if (!expanded || animating) return;
      stopObserving();
      const rowRect = row.getBoundingClientRect();
      const first = panel.getBoundingClientRect();

      // Snap to the current (full) rect, then animate back to the grid cell.
      panel.style.transition = 'none';
      panel.style.top = (first.top - rowRect.top) + 'px';
      panel.style.left = (first.left - rowRect.left) + 'px';
      panel.style.width = first.width + 'px';
      void panel.offsetWidth;

      // Target grid cell = column 2 of the 42%/1fr row, derived from the compass.
      const compass = document.getElementById('compass-panel');
      const cRect = compass ? compass.getBoundingClientRect() : null;
      let targetLeft, targetWidth;
      if (cRect) {
        targetLeft = (cRect.left - rowRect.left) + cRect.width + (parseFloat(getComputedStyle(row).columnGap) || 0);
        targetWidth = rowRect.width - targetLeft;
      } else {
        targetLeft = rowRect.width * 0.42;
        targetWidth = rowRect.width * 0.58;
      }

      animating = true;
      // Enable the transition FIRST, then drop the class so the lifted-card
      // box-shadow fades out over the close instead of snapping off.
      panel.style.transition = `left ${ANIM_MS}ms ${EASE}, width ${ANIM_MS}ms ${EASE}, box-shadow ${ANIM_MS}ms ease`;
      panel.classList.remove('trajectory-expanded');
      panel.style.left = targetLeft + 'px';
      panel.style.width = targetWidth + 'px';
      reflowChartFor(ANIM_MS); // smooth chart reshape during the close

      expanded = false;
      btn.setAttribute('aria-expanded', 'false');
      if (lbl) lbl.textContent = 'Expand';
      btn.title = 'Expand panel';
      if (focusBtn) btn.focus();

      clearTimeout(finalizeTimer);
      finalizeTimer = setTimeout(() => {
        clearInline();
        animating = false;
        fireResize();       // re-render the chart back at its normal (320) width
      }, ANIM_MS + 40);
    }

    btn.addEventListener('click', () => { expanded ? collapse(false) : expand(); });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && expanded) collapse(true);
    });

    // Switching Daily <-> Historical while expanded: app.js swaps the tab content
    // (and momentarily sets/clears the panel min-height). Re-render the now-active
    // chart at the expanded width and re-assert the compass-cover min-height after
    // app.js's own rAF has run.
    panel.querySelectorAll('.era-tab').forEach((tab) => {
      tab.addEventListener('click', () => {
        if (!expanded) return;
        requestAnimationFrame(() => {
          if (!expanded) return;
          fireResize(); // re-render the now-visible chart undistorted at full width
          requestAnimationFrame(() => {
            if (!expanded) return;
            panel.style.minHeight = coverH + 'px';
            syncRowHeight();
          });
        });
      });
    });

    window.addEventListener('resize', () => {
      // Drop the overlay if we fall to the stacked mobile layout while expanded.
      if (expanded && isMobile()) {
        stopObserving();
        panel.style.transition = 'none';
        panel.classList.remove('trajectory-expanded');
        clearInline();
        expanded = false;
        animating = false;
        btn.setAttribute('aria-expanded', 'false');
        if (lbl) lbl.textContent = 'Expand';
        fireResize();
        return;
      }
      // On a desktop resize while expanded, re-fit the chart to the new width.
      if (expanded && !animating) {
        cancelAnimationFrame(resizeRaf);
        resizeRaf = requestAnimationFrame(() => { syncRowHeight(); fireResize(); });
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
