/* === Motion Desk -- filing UX + public record ===

   Motions deliberate the framework. The form has one motion_type
   dropdown spanning tenet/rule/process variants. target_kind +
   target_ref shape conditionally based on the type. Songs do not
   appear here -- per-song corrections live in Misread Reports. */

(() => {
  'use strict';

  const IS_LOCAL = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const API_HOST = IS_LOCAL
    ? `http://${window.location.hostname}:8000`
    : 'https://api.risingcompass.net';
  const API_KEY = IS_LOCAL
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  const STATUS_COLORS = {
    filed: 'var(--md-status-filed)',
    in_deliberation: 'var(--md-status-in-deliberation)',
    ratified: 'var(--md-status-ratified)',
    covered: 'var(--md-status-covered)',
    rejected: 'var(--md-status-rejected)',
  };
  const TYPE_LABEL = {
    amend_tenet: 'Amend tenet',
    new_tenet: 'New tenet',
    remove_tenet: 'Remove tenet',
    amend_rule: 'Amend rule',
    new_rule: 'New rule',
    remove_rule: 'Remove rule',
    process: 'Process',
  };
  const STATUS_LABEL = {
    filed: 'Filed',
    in_deliberation: 'In deliberation',
    ratified: 'Ratified',
    covered: 'Covered',
    rejected: 'Rejected',
  };

  const MIN_REASONING = 50;

  // Which motion_types take a target_ref of which kind. The target_kind
  // dropdown only appears for amend_rule / remove_rule where the user
  // chooses between rule and modifier.
  const TYPE_TARGET_RULES = {
    amend_tenet:  { needsRef: true,  fixedKind: 'tenet',    refHint: 'tenet id, e.g. violet-01', browse: '/tenets/' },
    new_tenet:    { needsRef: false, fixedKind: 'tenet',    refHint: null, browse: '/tenets/' },
    remove_tenet: { needsRef: true,  fixedKind: 'tenet',    refHint: 'tenet id, e.g. violet-01', browse: '/tenets/' },
    amend_rule:   { needsRef: true,  fixedKind: null,       refHint: 'rule id (e.g. R1) or modifier id (contamination)', browse: '/tenets/#procedural' },
    new_rule:     { needsRef: false, fixedKind: 'rule',     refHint: null, browse: '/tenets/#procedural' },
    remove_rule:  { needsRef: true,  fixedKind: null,       refHint: 'rule id (e.g. R1) or modifier id (contamination)', browse: '/tenets/#procedural' },
    process:      { needsRef: false, fixedKind: null,       refHint: null, browse: null },
  };

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

  function parseCitations(raw) {
    if (!raw) return null;
    const list = raw.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    if (!list.length) return null;
    return list;
  }

  // ---------- auth gate ----------

  // Build /account/ URL that bounces back here after sign-in / onboarding.
  function accountUrl() {
    const ret = window.location.pathname + window.location.search + window.location.hash;
    return `/account/?returnTo=${encodeURIComponent(ret)}`;
  }

  async function renderGate(me) {
    const gate = $('gate');
    const file = $('file');
    const acctHref = accountUrl();
    if (!me) {
      gate.innerHTML = `
        <h2 class="md-gate-title">Sign in to file a motion</h2>
        <p class="md-gate-body">
          Filing requires a verified-identity account. Reading the
          <a href="/motion-desk/motion-ledger/">ledger</a> does not.
        </p>
        <a class="md-gate-cta" href="${esc(acctHref)}">Sign in or create an account</a>
        <p class="md-gate-meta">You'll land back here after signing in.</p>
      `;
      file.hidden = true;
      return;
    }
    if (!me.handle) {
      gate.innerHTML = `
        <h2 class="md-gate-title">Pick a handle first</h2>
        <p class="md-gate-body">
          Your account exists but doesn't have a handle yet. Finish
          onboarding, then come back here to verify your identity.
        </p>
        <a class="md-gate-cta" href="${esc(acctHref)}">Finish onboarding</a>
      `;
      file.hidden = true;
      return;
    }
    if (me.tier !== 'id_verified') {
      gate.innerHTML = `
        <h2 class="md-gate-title">Verify identity to file</h2>
        <p class="md-gate-body">
          Motions carry your real-name accountability, so filing requires
          ID verification through Stripe Identity. It takes a few
          minutes. You'll come back to a verified badge on your
          submissions.
        </p>
        <a class="md-gate-cta" href="${esc(acctHref)}">Verify identity</a>
        <p class="md-gate-meta">Verifying once unlocks both motion filing and Chamber participation.</p>
      `;
      file.hidden = true;
      return;
    }
    gate.innerHTML = `
      <h2 class="md-gate-title">Filing as @${esc(me.handle)}</h2>
      <p class="md-gate-body">
        Verified identity confirmed. Your motion will be public from the
        moment you file it, attributed to your handle with a verified
        badge.
      </p>
    `;
    file.hidden = false;
  }

  // ---------- form shape ----------

  function syncFormShape() {
    const motionType = $('motionType').value;
    const rules = TYPE_TARGET_RULES[motionType] || {};
    const targetKindField = $('targetKindField');
    const targetRefField = $('targetRefField');
    const targetKind = $('targetKind');
    const targetRef = $('targetRef');
    const targetRefHint = $('targetRefHint');

    // target_kind dropdown only shows when the user must pick rule vs modifier.
    const showKindPicker = (motionType === 'amend_rule' || motionType === 'remove_rule');
    targetKindField.hidden = !showKindPicker;

    // target_ref input is required for amend_*/remove_*; hidden otherwise.
    targetRefField.hidden = !rules.needsRef;
    if (rules.needsRef) {
      targetRef.placeholder = rules.refHint || '';
      targetRefHint.innerHTML = rules.browse
        ? `<a href="${esc(rules.browse)}" target="_blank" rel="noopener">browse the framework</a>`
        : '';
    } else {
      targetRef.value = '';
    }
  }

  // ---------- form submission ----------

  function setStatus(el, msg, type) {
    el.textContent = msg;
    el.className = `md-form-status${type ? ' ' + type : ''}`;
  }

  function bindForm() {
    const form = $('motionForm');
    const status = $('motionStatus');
    const motionTypeEl = $('motionType');
    const targetKindEl = $('targetKind');

    bindCounter($('claim'), $('claimCount'), 0, 280);
    bindCounter($('reasoning'), $('reasoningCount'), MIN_REASONING, 5000);

    motionTypeEl.addEventListener('change', syncFormShape);
    syncFormShape();

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      setStatus(status, '');
      const motionType = motionTypeEl.value;
      const rules = TYPE_TARGET_RULES[motionType] || {};
      let targetKind = null;
      let targetRef = null;
      if (rules.needsRef) {
        targetRef = ($('targetRef').value || '').trim();
        if (!targetRef) {
          setStatus(status, 'Target id is required for this motion type.', 'error');
          return;
        }
        targetKind = rules.fixedKind || targetKindEl.value;
      }
      const claim = $('claim').value.trim();
      const reasoning = $('reasoning').value.trim();
      const citations = parseCitations($('citations').value);

      if (claim.length < 1) {
        setStatus(status, 'Claim is required.', 'error');
        return;
      }
      if (reasoning.length < MIN_REASONING) {
        setStatus(status, `Reasoning must be at least ${MIN_REASONING} characters.`, 'error');
        return;
      }

      const submitBtn = form.querySelector('.md-submit');
      submitBtn.disabled = true;
      try {
        const resp = await window.Auth.authedFetch('/api/motions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            motion_type: motionType,
            target_kind: targetKind,
            target_ref: targetRef,
            claim, reasoning, citations,
          }),
        });
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(pickErrorMessage(body, `File failed (${resp.status})`));
        setStatus(status, `Motion #${body.id} filed. Taking you to the ledger...`, 'success');
        form.reset();
        syncFormShape();
        bindCounter($('claim'), $('claimCount'), 0, 280);
        bindCounter($('reasoning'), $('reasoningCount'), MIN_REASONING, 5000);
        // Filing and the ledger live on separate pages now. After a
        // successful submission, drop the user on the ledger so they
        // see their motion on the record.
        window.setTimeout(() => {
          window.location.href = `/motion-desk/motion-ledger/#motion-${body.id}`;
        }, 800);
      } catch (err) {
        setStatus(status, err.message || String(err), 'error');
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  // ---------- public record ----------

  let currentStatus = 'open';
  let currentType = 'all';

  function bindRecordFilters() {
    document.querySelectorAll('.md-filter[data-status]').forEach((b) => {
      b.addEventListener('click', () => {
        currentStatus = b.dataset.status;
        document.querySelectorAll('.md-filter[data-status]').forEach((x) => x.classList.toggle('active', x === b));
        loadRecord();
      });
    });
    document.querySelectorAll('.md-filter[data-type]').forEach((b) => {
      b.addEventListener('click', () => {
        currentType = b.dataset.type;
        document.querySelectorAll('.md-filter[data-type]').forEach((x) => x.classList.toggle('active', x === b));
        loadRecord();
      });
    });
  }

  async function loadRecord() {
    const list = $('recordList');
    list.innerHTML = '<p class="md-empty">Loading...</p>';
    const params = new URLSearchParams();
    if (currentStatus !== 'all' && currentStatus !== 'open') params.set('status', currentStatus);
    if (currentType !== 'all') params.set('motion_type', currentType);
    try {
      const resp = await fetch(`${API_HOST}/api/motions?${params.toString()}`, {
        headers: { 'X-Api-Key': API_KEY },
      });
      if (!resp.ok) throw new Error(`Load failed: ${resp.status}`);
      let items = await resp.json();
      if (currentStatus === 'open') {
        items = items.filter((m) => m.status === 'filed' || m.status === 'in_deliberation');
      }
      renderRecord(items);
    } catch (err) {
      list.innerHTML = `<p class="md-empty">${esc(err.message)}</p>`;
    }
  }

  function renderRecord(items) {
    const list = $('recordList');
    if (!items.length) {
      list.innerHTML = '<p class="md-empty">No motions on the ledger for this filter yet.</p>';
      return;
    }
    list.innerHTML = items.map(renderMotion).join('');
    list.querySelectorAll('.md-motion-expand').forEach((btn) => {
      btn.addEventListener('click', () => {
        const body = btn.previousElementSibling;
        const isOpen = body.classList.toggle('expanded');
        body.classList.toggle('collapsed', !isOpen);
        btn.textContent = isOpen ? 'Collapse' : 'Read full reasoning';
      });
    });
  }

  function renderMotion(m) {
    const color = STATUS_COLORS[m.status] || 'var(--md-rule)';

    let targetBlock = '';
    if (m.target_kind && m.target_ref) {
      targetBlock = `<p class="md-motion-target">Targets <b>${esc(m.target_kind)}</b> <code>${esc(m.target_ref)}</code></p>`;
    } else if (m.motion_type === 'new_tenet') {
      targetBlock = '<p class="md-motion-target"><em>Proposes a new tenet</em></p>';
    } else if (m.motion_type === 'new_rule') {
      targetBlock = '<p class="md-motion-target"><em>Proposes a new rule</em></p>';
    } else if (m.motion_type === 'process') {
      targetBlock = '<p class="md-motion-target"><em>Methodology / morality / AI process</em></p>';
    }

    const verifiedBadge = m.filed_by_verified
      ? `<span class="md-motion-verified">verified</span>` : '';
    const filerName = m.filed_by_handle
      ? `@${esc(m.filed_by_handle)}`
      : esc(m.filed_by_anon_id || 'unknown');
    const filedDate = m.filed_at
      ? new Date(m.filed_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
      : '';

    const reasoning = (m.reasoning || '').trim();
    const showExpand = reasoning.length > 200 || reasoning.split('\n').length > 3;
    const reasoningCls = showExpand ? 'md-motion-reasoning collapsed' : 'md-motion-reasoning expanded';

    const citationsBlock = (m.citations && m.citations.length)
      ? `<div class="md-motion-citations">
           <span class="label">Citations</span>
           ${m.citations.map((u) => `<div><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></div>`).join('')}
         </div>`
      : '';

    const resolutionBlock = m.resolution_summary
      ? `<div class="md-motion-resolution" style="--md-status-color: ${color};">
           <span class="label">Resolution &middot; ${m.resolved_at ? new Date(m.resolved_at).toLocaleDateString() : ''}</span>
           <p>${esc(m.resolution_summary)}</p>
         </div>`
      : '';

    // Surface the Chamber link the moment a motion is moved into
    // deliberation, and keep it after resolution so the deliberation
    // record stays public. Filed motions don't get a link -- the
    // Chamber isn't open until an admin moves them in.
    const showChamberLink = m.status !== 'filed';
    const chamberLinkLabel = m.status === 'in_deliberation'
      ? 'Open the Deliberation Chamber'
      : 'See the deliberation record';
    const chamberLink = showChamberLink
      ? `<a class="md-motion-chamber-link" href="/motion-desk/deliberation-chamber/${m.id}/">${chamberLinkLabel} &rarr;</a>`
      : '';

    return `
      <article class="md-motion" style="--md-status-color: ${color};">
        <div class="md-motion-head">
          <span class="md-motion-type">${esc(TYPE_LABEL[m.motion_type] || m.motion_type)}</span>
          <span class="md-motion-status">${esc(STATUS_LABEL[m.status] || m.status)}</span>
          <span class="md-motion-filer">filed by ${filerName}</span>
          ${verifiedBadge}
          <span class="md-motion-date">${esc(filedDate)} &middot; #${m.id}</span>
        </div>
        ${targetBlock}
        <h3 class="md-motion-claim">${esc(m.claim)}</h3>
        <div class="${reasoningCls}">${esc(reasoning)}</div>
        ${showExpand ? '<button class="md-motion-expand" type="button">Read full reasoning</button>' : ''}
        ${citationsBlock}
        ${resolutionBlock}
        ${chamberLink}
      </article>
    `;
  }

  // ---------- boot ----------
  // The Motion Desk is split into three pages that share this script:
  //   /motion-desk/                  -- landing (no form, no ledger)
  //   /motion-desk/file-a-motion/    -- form + gate panel
  //   /motion-desk/motion-ledger/    -- ledger list + filters
  // Boot only the sections that exist on the current page. The shared
  // script keeps helpers in one place instead of duplicating.

  async function boot() {
    const hasForm = !!$('motionForm');
    const hasLedger = !!$('recordList');
    const hasGate = !!$('gate');

    if (hasForm) bindForm();
    if (hasLedger) {
      bindRecordFilters();
      loadRecord();
    }

    if (hasGate) {
      try {
        await window.Auth.init();
        const me = await window.Auth.getMe();
        await renderGate(me);
        window.Auth.onChange(async () => {
          const m = await window.Auth.getMe({ force: true });
          await renderGate(m);
        });
      } catch (err) {
        console.error('Motion Desk auth init failed', err);
        $('gate').innerHTML = `<p class="md-loading">Sign-in is offline right now.</p>`;
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
