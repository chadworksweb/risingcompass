/* Founder's essay toast for Rising Compass.
 *
 * A bottom-center pill that promotes the founder's essay ("Making the Vibe
 * Visible", hosted on chadlewine.com -- RC gets a pointer, not a copy). Modeled
 * after chadworks' fixed pill button (visual) and chadlewine's come-see-me-live
 * floating tag (mechanism: idle-reveal, snooze-to-edge paddle, session-scoped
 * dismissal). Ported into a standalone vanilla IIFE because RC's frontend is
 * static HTML + build-time partials. Loaded on every page via the footer
 * partial.
 *
 * Behavior:
 *   - Reveals after IDLE_MS of no scrolling (resets on every scroll, so it
 *     surfaces once the reader settles).
 *   - Close (x) snoozes it to a small right-edge paddle for the rest of the
 *     browsing session (sessionStorage). Clicking the paddle brings it back.
 *   - Clicking through to the essay also snoozes it (they engaged).
 *   - Dismissal is session-scoped: a fresh visit shows it again.
 */
(function () {
  if (window.__rcEssayToastLoaded) return;
  window.__rcEssayToastLoaded = true;

  // The essay's canonical home is chadlewine.com. Update this single constant
  // if the published URL changes.
  var ESSAY_URL = 'https://chadlewine.com/observations/making-the-vibe-visible';

  var SNOOZE_KEY = 'rc-essay-toast-snoozed';
  var IDLE_MS = 5000;

  // A page that already carries the essay in its own content does not also get
  // the floating nudge -- the methodology hero's manifesto pill, for one. Keyed
  // on an in-content link to the essay rather than on a list of paths, so any
  // page that adds one opts out on its own. The toast's own link is excluded,
  // since init() points it at this same URL.
  function pageAlreadyLinksEssay(toast) {
    var links = document.querySelectorAll('a[href="' + ESSAY_URL + '"]');
    for (var i = 0; i < links.length; i++) {
      if (!toast.contains(links[i])) return true;
    }
    return false;
  }

  function init() {
    var toast = document.getElementById('rc-essay-toast');
    if (!toast) return;

    // Removed, not just hidden, so the snoozed-paddle state cannot surface here
    // either.
    if (pageAlreadyLinksEssay(toast)) {
      toast.parentNode.removeChild(toast);
      return;
    }

    var link = toast.querySelector('.rc-essay-toast__link');
    var closeBtn = toast.querySelector('.rc-essay-toast__close');
    var expandBtn = toast.querySelector('.rc-essay-toast__expand');
    if (link) link.setAttribute('href', ESSAY_URL);

    var idleTimer = null;
    var snoozed = false;

    try {
      snoozed = sessionStorage.getItem(SNOOZE_KEY) === '1';
    } catch (e) {
      // sessionStorage may be unavailable (private mode) -- treat as not snoozed.
    }

    function setVisible(on) {
      toast.classList.toggle('rc-essay-toast--visible', on);
      toast.setAttribute('aria-hidden', on ? 'false' : 'true');
    }

    function resetIdle() {
      if (snoozed) return;
      setVisible(false);
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(function () { setVisible(true); }, IDLE_MS);
    }

    function snooze() {
      snoozed = true;
      if (idleTimer) clearTimeout(idleTimer);
      toast.classList.add('rc-essay-toast--snoozed');
      setVisible(false);
      toast.setAttribute('aria-hidden', 'false'); // paddle stays reachable
      try { sessionStorage.setItem(SNOOZE_KEY, '1'); } catch (e) {}
    }

    function unsnooze() {
      snoozed = false;
      toast.classList.remove('rc-essay-toast--snoozed');
      setVisible(true);
      try { sessionStorage.removeItem(SNOOZE_KEY); } catch (e) {}
    }

    if (closeBtn) closeBtn.addEventListener('click', snooze);
    if (expandBtn) expandBtn.addEventListener('click', unsnooze);
    // Engaging with the essay collapses the nudge for the session.
    if (link) link.addEventListener('click', snooze);

    if (snoozed) {
      toast.classList.add('rc-essay-toast--snoozed');
      toast.setAttribute('aria-hidden', 'false');
    } else {
      resetIdle();
      window.addEventListener('scroll', resetIdle, { passive: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
