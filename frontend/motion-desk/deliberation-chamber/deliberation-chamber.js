/* === Deliberation Chamber -- argument thread + Tier 2 posting ===

   Sub-room of Motion Desk. The page renders a single motion's thread
   chronologically with rebuttals visually nested under their parent.
   Tier 2 (id_verified) users can post; everyone can read. Threads on
   resolved motions stay public + read-only with a resolution banner. */

(() => {
  'use strict';

  const IS_LOCAL = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const API_HOST = '';  // same-origin relative; dev_server proxies /api -> :8000 locally
  const API_KEY = IS_LOCAL
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  const MIN_BODY = 50;

  const STATUS_COLORS = {
    filed: 'var(--md-status-filed)',
    in_deliberation: 'var(--md-status-in-deliberation)',
    ratified: 'var(--md-status-ratified)',
    covered: 'var(--md-status-covered)',
    rejected: 'var(--md-status-rejected)',
  };
  const STATUS_LABEL = {
    filed: 'Filed',
    in_deliberation: 'In deliberation',
    ratified: 'Ratified',
    covered: 'Covered',
    rejected: 'Rejected',
  };
  const TYPE_LABEL_MOTION = {
    amend_tenet: 'Amend tenet',
    new_tenet: 'New tenet',
    remove_tenet: 'Remove tenet',
    amend_rule: 'Amend rule',
    new_rule: 'New rule',
    remove_rule: 'Remove rule',
    process: 'Process',
  };
  const TYPE_LABEL_POST = {
    argument_for: 'For',
    argument_against: 'Against',
    rebuttal: 'Rebuttal',
    citation: 'Citation',
    clarification: 'Clarification',
  };
  const RESOLVED_STATUSES = new Set(['ratified', 'rejected', 'covered']);

  // ---------- helpers ----------

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function pickErrorMessage(body, fallback) {
    if (!body) return fallback;
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail) && body.detail.length) {
      const first = body.detail[0];
      if (first && typeof first.msg === 'string') {
        return first.msg.replace(/^Value error,\s*/, '');
      }
    }
    return fallback;
  }

  function fmtDate(iso) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
      });
    } catch (_) { return ''; }
  }

  function getMotionIdFromPath() {
    const m = window.location.pathname.match(/\/motion-desk\/deliberation-chamber\/(\d+)\/?$/);
    return m ? parseInt(m[1], 10) : null;
  }

  function parseCitations(raw) {
    if (!raw) return null;
    const list = raw.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    return list.length ? list : null;
  }

  // ---------- state ----------

  const state = {
    motionId: getMotionIdFromPath(),
    motion: null,
    args: [],
    argsError: null,
    rebutParentId: null,
    rebutParentSummary: null,
  };

  // ---------- motion header ----------

  async function loadMotion() {
    const resp = await fetch(`${API_HOST}/api/motions/${state.motionId}`, {
      headers: { 'X-Api-Key': API_KEY },
    });
    if (!resp.ok) {
      if (resp.status === 404) throw new Error('Motion not found');
      throw new Error(`Failed to load motion (${resp.status})`);
    }
    return resp.json();
  }

  function renderMotionHeader(m) {
    const header = $('motionHeader');
    const color = STATUS_COLORS[m.status] || 'var(--md-rule)';

    let targetBlock = '';
    if (m.target_kind && m.target_ref) {
      targetBlock = `<p class="dc-motion-target">Targets <b>${esc(m.target_kind)}</b> <code>${esc(m.target_ref)}</code></p>`;
    } else if (m.motion_type === 'new_tenet') {
      targetBlock = '<p class="dc-motion-target"><em>Proposes a new tenet</em></p>';
    } else if (m.motion_type === 'new_rule') {
      targetBlock = '<p class="dc-motion-target"><em>Proposes a new rule</em></p>';
    } else if (m.motion_type === 'process') {
      targetBlock = '<p class="dc-motion-target"><em>Methodology / morality / AI process</em></p>';
    }

    const reasoning = (m.reasoning || '').trim();
    const showExpand = reasoning.length > 240 || reasoning.split('\n').length > 3;
    const reasoningCls = showExpand ? 'dc-motion-reasoning collapsed' : 'dc-motion-reasoning';

    const filerHandle = m.filed_by_handle
      ? `@${esc(m.filed_by_handle)}`
      : esc(m.filed_by_anon_id || 'unknown');
    const verified = m.filed_by_verified
      ? '<span class="dc-motion-verified">verified</span>' : '';

    header.style.setProperty('--dc-status-color', color);
    header.innerHTML = `
      <p class="dc-motion-meta">
        <span class="dc-motion-type">${esc(TYPE_LABEL_MOTION[m.motion_type] || m.motion_type)}</span>
        <span class="dc-motion-status">${esc(STATUS_LABEL[m.status] || m.status)}</span>
        <span>#${m.id}</span>
        <span>filed ${esc(fmtDate(m.filed_at))}</span>
      </p>
      ${targetBlock}
      <h1 class="dc-motion-claim">${esc(m.claim)}</h1>
      <div class="${reasoningCls}" id="motionReasoning">${esc(reasoning)}</div>
      ${showExpand ? '<button class="dc-motion-expand" type="button" id="motionReasoningExpand">Read full reasoning</button>' : ''}
      <p class="dc-motion-filer">Filed by ${filerHandle} ${verified}</p>
    `;
    if (showExpand) {
      $('motionReasoningExpand').addEventListener('click', () => {
        const r = $('motionReasoning');
        const isOpen = r.classList.toggle('collapsed');
        $('motionReasoningExpand').textContent = isOpen ? 'Read full reasoning' : 'Collapse';
      });
    }
  }

  function renderResolutionBanner(m) {
    const banner = $('resolutionBanner');
    if (!RESOLVED_STATUSES.has(m.status) || !m.resolution_summary) {
      banner.hidden = true;
      return;
    }
    const color = STATUS_COLORS[m.status] || 'var(--md-rule)';
    banner.style.setProperty('--dc-status-color', color);
    banner.hidden = false;
    banner.innerHTML = `
      <p class="dc-resolution-label">
        ${esc(STATUS_LABEL[m.status] || m.status)}
        ${m.resolved_at ? ' &middot; ' + esc(fmtDate(m.resolved_at)) : ''}
      </p>
      <p class="dc-resolution-summary">${esc(m.resolution_summary)}</p>
    `;
  }

  // ---------- thread ----------

  async function loadArguments() {
    const resp = await fetch(
      `${API_HOST}/api/motions/${state.motionId}/arguments`,
      { headers: { 'X-Api-Key': API_KEY } },
    );
    if (!resp.ok) throw new Error(`Failed to load thread (${resp.status})`);
    return resp.json();
  }

  function orderThread(args) {
    // Chronological order, but each rebuttal is placed immediately after
    // its parent post (and any earlier rebuttals on the same parent). All
    // top-level posts sort by created_at; rebuttals attach to their
    // parent in the order they were posted.
    const byId = new Map(args.map((a) => [a.id, a]));
    const topLevel = args.filter((a) => !a.parent_id);
    topLevel.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
    const rebuttalsByParent = new Map();
    for (const a of args) {
      if (a.parent_id && byId.has(a.parent_id)) {
        if (!rebuttalsByParent.has(a.parent_id)) rebuttalsByParent.set(a.parent_id, []);
        rebuttalsByParent.get(a.parent_id).push(a);
      }
    }
    for (const list of rebuttalsByParent.values()) {
      list.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
    }
    const out = [];
    for (const parent of topLevel) {
      out.push(parent);
      const kids = rebuttalsByParent.get(parent.id) || [];
      out.push(...kids);
    }
    return out;
  }

  function renderPost(a, canRebut) {
    const isRebut = a.post_type === 'rebuttal';
    const verified = a.author_verified
      ? '<span class="dc-post-verified">verified</span>' : '';
    const handleText = a.author_handle
      ? `@${esc(a.author_handle)}`
      : esc(a.author_anon_id || 'unknown');
    // The Chamber shows the verified legal name when the author consented
    // to public display; the handle drops to a secondary line. Otherwise
    // the handle is the author identity (same as Lobby/Misread).
    const author = a.author_legal_name
      ? `${esc(a.author_legal_name)}<span class="dc-post-handle">${handleText}</span>`
      : handleText;
    const citations = (a.citations && a.citations.length)
      ? `<div class="dc-post-citations">
           <span class="label">Citations</span>
           ${a.citations.map((u) => `<div><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></div>`).join('')}
         </div>`
      : '';
    // Rebut button only appears when the user can post AND the post is
    // top-level (rebuttals can't be rebutted -- 2-level depth cap).
    const rebutBtn = (canRebut && !isRebut)
      ? `<div class="dc-post-actions">
           <button class="dc-post-rebut-btn" type="button" data-rebut-id="${a.id}" data-rebut-summary="${esc(a.summary)}">Rebut this</button>
         </div>`
      : '';

    return `
      <article class="dc-post ${isRebut ? 'rebuttal' : ''}" data-type="${esc(a.post_type)}" data-id="${a.id}">
        <div class="dc-post-head">
          <span class="dc-post-type">${esc(TYPE_LABEL_POST[a.post_type] || a.post_type)}</span>
          <span class="dc-post-author">${author}</span>
          ${verified}
          <span class="dc-post-date">${esc(fmtDate(a.created_at))}</span>
        </div>
        <h3 class="dc-post-summary">${esc(a.summary)}</h3>
        <p class="dc-post-body">${esc(a.body)}</p>
        ${citations}
        ${rebutBtn}
      </article>
    `;
  }

  function renderThread(args, canRebut) {
    const thread = $('thread');
    if (!args.length) {
      thread.innerHTML = '<p class="dc-empty">No arguments on the record yet. Be the first to post.</p>';
      return;
    }
    const ordered = orderThread(args);
    thread.innerHTML = ordered.map((a) => renderPost(a, canRebut)).join('');
    if (canRebut) bindRebutButtons();
  }

  // Renders the thread, or a thread-scoped error if the arguments fetch
  // failed (the motion still loaded). Keeps an args error from being
  // silently replaced by an empty "be the first to post" state when auth
  // resolves and re-renders.
  function paintThread(canRebut) {
    if (state.argsError) {
      $('thread').innerHTML = `<p class="dc-empty">${esc(state.argsError)}</p>`;
      return;
    }
    renderThread(state.args, canRebut);
  }

  function bindRebutButtons() {
    document.querySelectorAll('.dc-post-rebut-btn').forEach((b) => {
      b.addEventListener('click', () => {
        const id = parseInt(b.dataset.rebutId, 10);
        const summary = b.dataset.rebutSummary || '';
        setRebutTarget(id, summary);
      });
    });
  }

  // ---------- post form ----------

  function setRebutTarget(parentId, parentSummary) {
    state.rebutParentId = parentId;
    state.rebutParentSummary = parentSummary;
    $('rebutTarget').hidden = false;
    $('rebutSummary').textContent = parentSummary;
    $('postType').value = 'rebuttal';
    // Scroll the form into view so it's obvious where to type.
    $('postFormWrap').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('summary').focus();
  }

  function clearRebutTarget() {
    state.rebutParentId = null;
    state.rebutParentSummary = null;
    $('rebutTarget').hidden = true;
    if ($('postType').value === 'rebuttal') {
      $('postType').value = 'argument_for';
    }
  }

  function bindCounter(input, counter, min, max) {
    const update = () => {
      const len = (input.value || '').length;
      const minTxt = min ? ` (${min} min)` : '';
      counter.textContent = `${len} / ${max}${minTxt}`;
      if (min && len > 0 && len < min) counter.classList.add('under');
      else counter.classList.remove('under');
    };
    input.addEventListener('input', update);
    update();
  }

  function setStatus(el, msg, type) {
    el.textContent = msg;
    el.className = `dc-form-status${type ? ' ' + type : ''}`;
  }

  function bindForm() {
    const form = $('postForm');
    const status = $('postStatus');
    const postType = $('postType');

    bindCounter($('summary'), $('summaryCount'), 0, 280);
    bindCounter($('body'), $('bodyCount'), MIN_BODY, 5000);

    // If user switches the type away from rebuttal manually, clear the target.
    postType.addEventListener('change', () => {
      if (postType.value !== 'rebuttal') clearRebutTarget();
    });

    $('rebutClear').addEventListener('click', clearRebutTarget);

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      setStatus(status, '');
      const type = postType.value;
      const summary = $('summary').value.trim();
      const body = $('body').value.trim();
      const citations = parseCitations($('citations').value);

      if (type === 'rebuttal' && !state.rebutParentId) {
        setStatus(status, 'Pick a post to rebut first (use Rebut this on a post).', 'error');
        return;
      }
      if (summary.length < 1) {
        setStatus(status, 'Summary is required.', 'error');
        return;
      }
      if (body.length < MIN_BODY) {
        setStatus(status, `Body must be at least ${MIN_BODY} characters.`, 'error');
        return;
      }

      const submitBtn = form.querySelector('.dc-submit');
      submitBtn.disabled = true;
      try {
        const resp = await window.Auth.authedFetch(`/api/motions/${state.motionId}/arguments`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            post_type: type,
            parent_id: type === 'rebuttal' ? state.rebutParentId : null,
            summary, body, citations,
          }),
        });
        const out = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(pickErrorMessage(out, `Post failed (${resp.status})`));
        setStatus(status, 'Posted. Refreshing the thread...', 'success');
        form.reset();
        clearRebutTarget();
        bindCounter($('summary'), $('summaryCount'), 0, 280);
        bindCounter($('body'), $('bodyCount'), MIN_BODY, 5000);
        // Reload the thread to surface the new post.
        const args = await loadArguments();
        state.args = args;
        renderThread(args, true);
      } catch (err) {
        setStatus(status, err.message || String(err), 'error');
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  // ---------- gate ----------

  function accountUrl() {
    const ret = window.location.pathname + window.location.search + window.location.hash;
    return `/account/?returnTo=${encodeURIComponent(ret)}`;
  }

  function showGate(html) {
    const gate = $('gate');
    gate.hidden = false;
    gate.innerHTML = html;
    $('postFormWrap').hidden = true;
  }

  function hideGate() {
    $('gate').hidden = true;
  }

  function showForm() {
    hideGate();
    $('postFormWrap').hidden = false;
  }

  function applyAuthAndStatus(me) {
    const m = state.motion;
    if (!m) return false;

    if (RESOLVED_STATUSES.has(m.status)) {
      showGate(`
        <h2 class="dc-gate-title">This deliberation has closed</h2>
        <p class="dc-gate-body">
          The thread above is preserved as the public record of how this
          motion was deliberated. The Chamber is read-only.
        </p>
      `);
      return false;
    }
    if (m.status !== 'in_deliberation') {
      showGate(`
        <h2 class="dc-gate-title">Not yet open for deliberation</h2>
        <p class="dc-gate-body">
          This motion is still in the filed queue. The admin moves
          motions into deliberation once they are ready to be argued.
          Until then, posting is closed.
        </p>
      `);
      return false;
    }
    // Motion is in_deliberation. Now the user gate.
    if (!me) {
      showGate(`
        <h2 class="dc-gate-title">Sign in to post in the Chamber</h2>
        <p class="dc-gate-body">
          Anyone can read the thread. Posting requires a
          verified-identity account.
        </p>
        <a class="dc-gate-cta" href="${esc(accountUrl())}">Sign in or create an account</a>
      `);
      return false;
    }
    if (!me.handle) {
      showGate(`
        <h2 class="dc-gate-title">Pick a handle first</h2>
        <p class="dc-gate-body">
          Your account exists but doesn't have a handle yet. Finish
          onboarding, then come back here to verify your identity.
        </p>
        <a class="dc-gate-cta" href="${esc(accountUrl())}">Finish onboarding</a>
      `);
      return false;
    }
    if (me.tier !== 'id_verified') {
      showGate(`
        <h2 class="dc-gate-title">Verify identity to post</h2>
        <p class="dc-gate-body">
          Chamber posts carry real-name accountability, so posting
          requires ID verification through Stripe Identity. Reading the
          thread does not.
        </p>
        <a class="dc-gate-cta" href="${esc(accountUrl())}">Verify identity</a>
      `);
      return false;
    }
    showForm();
    return true;
  }

  // ---------- boot ----------

  async function boot() {
    if (!state.motionId) {
      $('motionHeader').innerHTML = '<p class="dc-empty">No motion id in the URL.</p>';
      $('thread').innerHTML = '';
      return;
    }
    // The motion fetch is authoritative: a bad id 404s here and that is
    // the error worth showing ("Motion not found"). Loading arguments in
    // the same Promise.all let the thread's generic "Failed to load thread
    // (404)" win the rejection race instead. Load the motion first; only
    // then fetch the thread, and let a thread-only failure degrade
    // gracefully without blanking the whole page.
    let m;
    try {
      m = await loadMotion();
    } catch (err) {
      $('motionHeader').innerHTML = `<p class="dc-empty">${esc(err.message)}</p>`;
      $('thread').innerHTML = '';
      return;
    }
    state.motion = m;
    renderMotionHeader(m);
    renderResolutionBanner(m);

    try {
      state.args = await loadArguments();
      state.argsError = null;
    } catch (err) {
      state.args = [];
      state.argsError = err.message || 'Failed to load the thread.';
    }

    // First render of the thread before auth resolves -- show it
    // read-only (no rebut buttons). Re-render with rebut buttons once
    // we know the user can post.
    paintThread(false);

    try {
      await window.Auth.init();
      const me = await window.Auth.getMe();
      const canPost = applyAuthAndStatus(me);
      if (canPost) {
        paintThread(true);
        bindForm();
      }
      window.Auth.onChange(async () => {
        const m2 = await window.Auth.getMe({ force: true });
        const canPost2 = applyAuthAndStatus(m2);
        paintThread(canPost2);
        if (canPost2 && !$('postForm').dataset.bound) {
          bindForm();
          $('postForm').dataset.bound = '1';
        }
      });
    } catch (err) {
      console.error('Chamber auth init failed', err);
      showGate('<h2 class="dc-gate-title">Sign-in is offline right now.</h2><p class="dc-gate-body">You can still read the thread.</p>');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
