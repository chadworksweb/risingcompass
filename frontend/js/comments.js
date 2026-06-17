/* === Public Participation Discussion comment widget ===

   Mount onto any container with:

     Comments.mount(el, {
       targetType:   'song' | 'artist' | 'release',
       targetSource: 'compass' | 'library' | 'submitted' | null,  // songs only
       targetId:     123,
     });

   The widget loads anonymously (no JWT required for reads). Posting,
   reply, report, and delete-own all call Auth.* and trigger a sign-in
   modal if the visitor isn't authed yet. Auth.js must be loaded before
   this script -- pages that want comments add both <script> tags.

   API surface used:
     GET    /api/comments?target_type=...&target_id=...
     POST   /api/comments              { target_type, target_source, target_id, content }
     POST   /api/comments/{id}/reply   { content }
     POST   /api/comments/{id}/report  { reason, notes }
     DELETE /api/comments/{id}

   Two-level threading is the backend's job; the UI just renders nested
   replies one deep and hides the reply button on reply rows. */

const Comments = (() => {
  const isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const API_BASE = '';  // same-origin relative; dev_server proxies /api -> :8000 locally

  const SORT_OPTIONS = [
    { value: 'new', label: 'New' },
    { value: 'most_replied', label: 'Most replied' },
    { value: 'most_active', label: 'Most active' },
  ];

  const REPORT_REASONS = [
    { value: 'spam', label: 'Spam' },
    { value: 'harassment', label: 'Harassment / abuse' },
    { value: 'off_topic', label: 'Off topic' },
    { value: 'misinformation', label: 'Misinformation' },
    { value: 'other', label: 'Other' },
  ];

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') node.className = v;
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === false || v == null) continue;
      else if (v === true) node.setAttribute(k, '');
      else node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) {
      if (c == null) continue;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return node;
  }

  // ---------- Modal + toast (in-UI, no native dialogs) ----------

  function buildModalShell() {
    const backdrop = el('div', { class: 'rc-modal-backdrop', role: 'presentation' });
    const dialog = el('div', { class: 'rc-modal', role: 'dialog', 'aria-modal': 'true' });
    backdrop.appendChild(dialog);
    return { backdrop, dialog };
  }

  function _trapKeys(close) {
    return (ev) => { if (ev.key === 'Escape') { ev.preventDefault(); close(false); } };
  }

  function confirmModal({ title, body, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false }) {
    return new Promise((resolve) => {
      const { backdrop, dialog } = buildModalShell();
      let settled = false;
      const close = (v) => {
        if (settled) return; settled = true;
        document.removeEventListener('keydown', keyHandler);
        backdrop.remove();
        resolve(v);
      };
      const keyHandler = _trapKeys(close);
      document.addEventListener('keydown', keyHandler);
      backdrop.addEventListener('click', (ev) => { if (ev.target === backdrop) close(false); });

      const titleEl = el('h3', { class: 'rc-modal-title' }, title);
      const bodyEl = el('div', { class: 'rc-modal-body' }, body);
      const cancelBtn = el('button', { type: 'button', class: 'rc-modal-btn', onclick: () => close(false) }, cancelLabel);
      const confirmBtn = el('button', {
        type: 'button',
        class: 'rc-modal-btn ' + (danger ? 'rc-modal-btn-danger' : 'rc-modal-btn-primary'),
        onclick: () => close(true),
      }, confirmLabel);
      const actions = el('div', { class: 'rc-modal-actions' }, [cancelBtn, confirmBtn]);

      dialog.appendChild(titleEl);
      dialog.appendChild(bodyEl);
      dialog.appendChild(actions);
      document.body.appendChild(backdrop);
      confirmBtn.focus();
    });
  }

  function reportModal({ reasons }) {
    return new Promise((resolve) => {
      const { backdrop, dialog } = buildModalShell();
      let settled = false;
      const close = (v) => {
        if (settled) return; settled = true;
        document.removeEventListener('keydown', keyHandler);
        backdrop.remove();
        resolve(v);
      };
      const keyHandler = _trapKeys(close);
      document.addEventListener('keydown', keyHandler);
      backdrop.addEventListener('click', (ev) => { if (ev.target === backdrop) close(null); });

      const titleEl = el('h3', { class: 'rc-modal-title' }, 'Report this comment');
      const subEl = el('p', { class: 'rc-modal-sub' }, 'Pick the reason that best fits. Reports go to the moderation queue and stay on the record.');

      const reasonsWrap = el('div', { class: 'rc-modal-reasons' });
      const reasonInputs = [];
      reasons.forEach((r, idx) => {
        const input = el('input', {
          type: 'radio', name: 'rc-report-reason', value: r.value, id: `rc-report-${r.value}`,
        });
        if (idx === 0) input.checked = true;
        reasonInputs.push(input);
        const labelEl = el('label', { class: 'rc-modal-reason', for: `rc-report-${r.value}` }, [input, el('span', {}, r.label)]);
        reasonsWrap.appendChild(labelEl);
      });

      const notesLabel = el('label', { class: 'rc-modal-notes-label', for: 'rc-report-notes' }, 'Notes (optional)');
      const notes = el('textarea', {
        id: 'rc-report-notes', class: 'rc-modal-notes', rows: '2', maxlength: '1000',
        placeholder: 'Anything the moderator should know...',
      });

      const cancelBtn = el('button', { type: 'button', class: 'rc-modal-btn', onclick: () => close(null) }, 'Cancel');
      const submitBtn = el('button', {
        type: 'button', class: 'rc-modal-btn rc-modal-btn-primary',
        onclick: () => {
          const picked = reasonInputs.find((i) => i.checked);
          if (!picked) { close(null); return; }
          close({ reason: picked.value, notes: notes.value.trim() });
        },
      }, 'File report');
      const actions = el('div', { class: 'rc-modal-actions' }, [cancelBtn, submitBtn]);

      dialog.appendChild(titleEl);
      dialog.appendChild(subEl);
      dialog.appendChild(reasonsWrap);
      dialog.appendChild(notesLabel);
      dialog.appendChild(notes);
      dialog.appendChild(actions);
      document.body.appendChild(backdrop);
      reasonInputs[0].focus();
    });
  }

  function toast(msg, { kind = 'info', durationMs = 3500 } = {}) {
    let stack = document.querySelector('.rc-toast-stack');
    if (!stack) {
      stack = el('div', { class: 'rc-toast-stack', role: 'status', 'aria-live': 'polite' });
      document.body.appendChild(stack);
    }
    const t = el('div', { class: 'rc-toast rc-toast-' + kind }, msg);
    stack.appendChild(t);
    setTimeout(() => {
      t.classList.add('rc-toast-leaving');
      setTimeout(() => t.remove(), 220);
    }, durationMs);
  }

  function formatTime(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const now = new Date();
      const sec = Math.floor((now - d) / 1000);
      if (sec < 60) return 'just now';
      if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
      if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
      if (sec < 604800) return Math.floor(sec / 86400) + 'd ago';
      return d.toLocaleDateString();
    } catch (_) { return ''; }
  }

  // Fetch threads. Anonymous read works, but when the user is signed in we
  // need to send the JWT so the backend can populate is_own (which gates
  // the Take Back button on the caller's own rows). Auth.authedFetch sends
  // Authorization only when a session exists, so falling back to a raw fetch
  // only matters when auth.js isn't loaded on the page.
  async function fetchThreads(opts, sort) {
    const params = new URLSearchParams({
      target_type: opts.targetType,
      target_id: String(opts.targetId),
      sort,
    });
    if (opts.targetSource) params.set('target_source', opts.targetSource);
    const path = `/api/comments?${params.toString()}`;
    const resp = typeof Auth !== 'undefined'
      ? await Auth.authedFetch(path)
      : await fetch(`${API_BASE}${path}`);
    if (!resp.ok) throw new Error(`Failed to load comments (HTTP ${resp.status})`);
    return resp.json();
  }

  async function postComment(opts, content) {
    const resp = await Auth.authedFetch('/api/comments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_type: opts.targetType,
        target_source: opts.targetSource || null,
        target_id: opts.targetId,
        content,
      }),
    });
    return handleApiResponse(resp, 'Could not post comment');
  }

  async function postReply(commentId, content) {
    const resp = await Auth.authedFetch(`/api/comments/${commentId}/reply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    return handleApiResponse(resp, 'Could not post reply');
  }

  async function postReport(commentId, reason, notes) {
    const resp = await Auth.authedFetch(`/api/comments/${commentId}/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason, notes: notes || null }),
    });
    return handleApiResponse(resp, 'Could not file report');
  }

  async function deleteOwn(commentId) {
    const resp = await Auth.authedFetch(`/api/comments/${commentId}`, { method: 'DELETE' });
    return handleApiResponse(resp, 'Could not delete comment');
  }

  async function handleApiResponse(resp, fallback) {
    const body = await resp.json().catch(() => ({}));
    if (resp.ok) return body;
    // FastAPI returns either string detail or array of validation errors.
    let msg = fallback;
    if (typeof body.detail === 'string') msg = body.detail;
    else if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      msg = body.detail[0].msg.replace(/^Value error,\s*/, '');
    }
    const err = new Error(msg);
    err.status = resp.status;
    throw err;
  }

  // Render one comment row (top-level or reply)
  function renderComment(c, ctx) {
    const header = el('div', { class: 'rc-cmt-head' }, [
      c.author.avatar_url
        ? el('img', { class: 'rc-cmt-avatar', src: c.author.avatar_url, alt: '' })
        : el('span', { class: 'rc-cmt-avatar rc-cmt-avatar-blank', 'aria-hidden': 'true' }),
      el('span', { class: 'rc-cmt-handle' }, c.author.handle ? '@' + c.author.handle : (c.deleted ? '[removed]' : 'User')),
      el('time', { class: 'rc-cmt-time', datetime: c.created_at, title: c.created_at }, formatTime(c.created_at)),
      c.edited_at ? el('span', { class: 'rc-cmt-meta' }, '(edited)') : null,
      c.is_own ? el('span', { class: 'rc-cmt-meta rc-cmt-meta-own' }, 'you') : null,
    ]);

    const bodyClass = (c.deleted || c.hidden || c.withdrawn) ? 'rc-cmt-body rc-cmt-body-removed' : 'rc-cmt-body';
    let body;
    if (c.withdrawn && c.original_content) {
      // Withdrawn but not admin-hidden -- offer a press-and-hold reveal.
      // The placeholder stays the default; pressing the body swaps in the
      // original text. Releasing (or moving off / cancelling) hides it
      // again. Touch path mirrors mouse path; we prevent default on
      // touchstart so iOS doesn't pop the text-selection menu.
      const placeholderText = c.content;
      const revealText = c.original_content;
      const hint = el('span', { class: 'rc-cmt-reveal-hint' }, ' (press and hold to reveal)');
      const label = el('span', { class: 'rc-cmt-reveal-label' }, [placeholderText, hint]);
      body = el('div', {
        class: bodyClass + ' rc-cmt-revealable',
        tabindex: '0',
        role: 'button',
        'aria-label': 'Press and hold to reveal the original withdrawn comment',
      }, [label]);

      let revealed = false;
      function show(ev) {
        if (revealed) return;
        if (ev && ev.preventDefault) ev.preventDefault();
        revealed = true;
        body.classList.add('is-revealed');
        label.textContent = revealText;
      }
      function hide() {
        if (!revealed) return;
        revealed = false;
        body.classList.remove('is-revealed');
        label.innerHTML = '';
        label.appendChild(document.createTextNode(placeholderText));
        label.appendChild(hint);
      }
      body.addEventListener('mousedown', show);
      body.addEventListener('touchstart', show, { passive: false });
      ['mouseup', 'mouseleave', 'touchend', 'touchcancel', 'blur'].forEach((evt) => {
        body.addEventListener(evt, hide);
      });
      // Keyboard accessibility: Space/Enter held = revealed; released = hidden.
      body.addEventListener('keydown', (ev) => {
        if (ev.key === ' ' || ev.key === 'Enter') show(ev);
      });
      body.addEventListener('keyup', (ev) => {
        if (ev.key === ' ' || ev.key === 'Enter') hide();
      });
    } else {
      body = el('div', { class: bodyClass }, c.content);
    }

    const footerKids = [];
    // Reply only on top-level (depth cap)
    const isRemoved = c.deleted || c.hidden || c.withdrawn;
    if (!c.parent_id && !isRemoved) {
      footerKids.push(el('button', {
        class: 'rc-cmt-action', type: 'button',
        onclick: (ev) => ctx.toggleReplyForm(c.id, ev.target),
      }, 'Reply'));
    }
    if (!isRemoved && !c.is_own) {
      footerKids.push(el('button', {
        class: 'rc-cmt-action', type: 'button',
        onclick: () => openReportPrompt(c.id, ctx),
      }, 'Report'));
    }
    if (c.is_own && !isRemoved) {
      footerKids.push(el('button', {
        class: 'rc-cmt-action rc-cmt-action-danger', type: 'button',
        onclick: () => confirmWithdraw(c.id, ctx),
      }, 'Take back'));
    }
    const footer = footerKids.length
      ? el('div', { class: 'rc-cmt-foot' }, footerKids)
      : null;

    const replyMount = el('div', { class: 'rc-cmt-reply-mount', dataset: { commentId: String(c.id) } });

    const repliesEl = el('div', { class: 'rc-cmt-replies' },
      (c.replies || []).map((r) => renderComment(r, ctx)));

    const hidReason = c.hidden && c.hidden_reason
      ? el('div', { class: 'rc-cmt-hidden-reason' }, c.hidden_reason)
      : null;

    return el('article', {
      class: 'rc-cmt' + (c.parent_id ? ' rc-cmt-reply' : ''),
      dataset: { commentId: String(c.id) },
    }, [header, body, hidReason, footer, replyMount, repliesEl]);
  }

  async function openReportPrompt(commentId, ctx) {
    const picked = await reportModal({ reasons: REPORT_REASONS });
    if (!picked) return;
    try {
      const res = await postReport(commentId, picked.reason, picked.notes);
      toast(res.auto_hidden
        ? 'Report filed. Threshold reached -- comment hidden pending review.'
        : 'Report filed.', { kind: res.auto_hidden ? 'warn' : 'info' });
    } catch (err) {
      toast(err.message || 'Could not file report.', { kind: 'error' });
    }
  }

  async function confirmWithdraw(commentId, ctx) {
    const ok = await confirmModal({
      title: 'Take back this comment?',
      body: 'It stays on the record as withdrawn, attributed to you, but is no longer part of the live conversation. This cannot be undone.',
      confirmLabel: 'Take it back',
      cancelLabel: 'Keep it',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteOwn(commentId);
      await ctx.reload();
      toast('Withdrawn.', { kind: 'info' });
    } catch (err) {
      toast(err.message || 'Could not withdraw.', { kind: 'error' });
    }
  }

  // Composer (top-level post)
  function renderComposer(ctx) {
    if (!ctx.signedIn) {
      return el('div', { class: 'rc-cmt-composer rc-cmt-composer-gate' }, [
        el('p', { class: 'rc-cmt-gate-msg' }, 'Sign in to comment.'),
        el('a', { class: 'rc-cmt-gate-link', href: '/account/' }, 'Sign in or create an account'),
      ]);
    }
    if (!ctx.hasHandle) {
      return el('div', { class: 'rc-cmt-composer rc-cmt-composer-gate' }, [
        el('p', { class: 'rc-cmt-gate-msg' }, 'Pick a handle before posting.'),
        el('a', { class: 'rc-cmt-gate-link', href: '/account/' }, 'Set up your handle'),
      ]);
    }
    const ta = el('textarea', {
      class: 'rc-cmt-textarea',
      placeholder: 'Add a comment...',
      rows: '3',
      maxlength: '5000',
    });
    const errEl = el('p', { class: 'rc-cmt-error', hidden: true });
    const btn = el('button', {
      class: 'rc-cmt-submit', type: 'button',
      onclick: async () => {
        const txt = ta.value.trim();
        if (!txt) return;
        btn.disabled = true; btn.textContent = 'Posting...';
        errEl.hidden = true;
        try {
          await postComment(ctx.opts, txt);
          ta.value = '';
          await ctx.reload();
        } catch (err) {
          errEl.textContent = err.message || 'Could not post.';
          errEl.hidden = false;
        } finally {
          btn.disabled = false; btn.textContent = 'Post';
        }
      },
    }, 'Post');
    return el('div', { class: 'rc-cmt-composer' }, [ta, errEl, btn]);
  }

  // Reply form rendered into a comment's reply-mount on demand
  function toggleReplyForm(commentId, anchor, ctx) {
    const mount = ctx.container.querySelector(
      `.rc-cmt-reply-mount[data-comment-id="${commentId}"]`
    );
    if (!mount) return;
    if (mount.firstChild) {
      mount.innerHTML = '';
      return;
    }
    const ta = el('textarea', {
      class: 'rc-cmt-textarea',
      placeholder: 'Write a reply...',
      rows: '3',
      maxlength: '5000',
    });
    const errEl = el('p', { class: 'rc-cmt-error', hidden: true });
    const submit = el('button', {
      class: 'rc-cmt-submit', type: 'button',
      onclick: async () => {
        const txt = ta.value.trim();
        if (!txt) return;
        submit.disabled = true; submit.textContent = 'Posting...';
        errEl.hidden = true;
        try {
          await postReply(commentId, txt);
          mount.innerHTML = '';
          await ctx.reload();
        } catch (err) {
          errEl.textContent = err.message || 'Could not post.';
          errEl.hidden = false;
        } finally {
          submit.disabled = false; submit.textContent = 'Post';
        }
      },
    }, 'Post');
    const cancel = el('button', {
      class: 'rc-cmt-submit-secondary', type: 'button',
      onclick: () => { mount.innerHTML = ''; },
    }, 'Cancel');
    mount.appendChild(el('div', { class: 'rc-cmt-reply-form' }, [
      ta, errEl, el('div', { class: 'rc-cmt-reply-actions' }, [submit, cancel]),
    ]));
    ta.focus();
  }

  async function loadAuthContext() {
    // Best-effort -- if Auth isn't loaded we just render as signed-out.
    if (typeof Auth === 'undefined') return { signedIn: false, hasHandle: false };
    try {
      await Auth.init();
      const me = await Auth.getMe();
      if (!me) return { signedIn: false, hasHandle: false };
      return { signedIn: true, hasHandle: !!me.handle };
    } catch (_) {
      return { signedIn: false, hasHandle: false };
    }
  }

  function mount(container, opts) {
    if (!container) throw new Error('Comments.mount: container required');
    if (!opts || !opts.targetType || !opts.targetId) {
      throw new Error('Comments.mount: targetType + targetId required');
    }
    container.innerHTML = '';
    container.classList.add('rc-comments');

    let sort = 'new';
    let signedIn = false;
    let hasHandle = false;

    const headerEl = el('div', { class: 'rc-cmt-header' });
    const composerSlot = el('div', { class: 'rc-cmt-composer-slot' });
    const listEl = el('div', { class: 'rc-cmt-list' }, [
      el('p', { class: 'rc-cmt-loading' }, 'Loading comments...'),
    ]);
    container.appendChild(headerEl);
    container.appendChild(composerSlot);
    container.appendChild(listEl);

    async function reload() {
      try {
        const data = await fetchThreads(opts, sort);
        renderHeader(data.total_comments, data.total_threads);
        renderList(data.threads);
      } catch (err) {
        listEl.innerHTML = '';
        listEl.appendChild(el('p', { class: 'rc-cmt-error' }, err.message || 'Could not load comments.'));
      }
    }

    function renderHeader(totalComments, totalThreads) {
      headerEl.innerHTML = '';
      const title = el('h2', { class: 'rc-cmt-title' },
        totalComments === 0 ? 'Discussion' : `Discussion (${totalComments})`);
      const sortSel = el('select', {
        class: 'rc-cmt-sort', 'aria-label': 'Sort comments',
        onchange: (ev) => { sort = ev.target.value; reload(); },
      }, SORT_OPTIONS.map((o) =>
        el('option', { value: o.value, selected: sort === o.value }, o.label)
      ));
      headerEl.appendChild(title);
      headerEl.appendChild(sortSel);
    }

    function renderList(threads) {
      listEl.innerHTML = '';
      if (!threads.length) {
        listEl.appendChild(el('p', { class: 'rc-cmt-empty' }, 'No comments yet. Start one.'));
        return;
      }
      const ctx = {
        opts, signedIn, hasHandle, reload, container,
        toggleReplyForm: (id, anchor) => toggleReplyForm(id, anchor, ctx),
      };
      for (const t of threads) {
        listEl.appendChild(renderComment(t, ctx));
      }
    }

    function rerenderComposer() {
      composerSlot.innerHTML = '';
      composerSlot.appendChild(renderComposer({
        opts, signedIn, hasHandle, reload,
      }));
    }

    // Initial paint
    (async () => {
      const auth = await loadAuthContext();
      signedIn = auth.signedIn;
      hasHandle = auth.hasHandle;
      rerenderComposer();
      await reload();
    })();

    // React to sign-in/out events from Auth
    if (typeof Auth !== 'undefined' && Auth.onChange) {
      Auth.onChange(async () => {
        const auth = await loadAuthContext();
        signedIn = auth.signedIn;
        hasHandle = auth.hasHandle;
        rerenderComposer();
        reload();
      });
    }
  }

  return { mount };
})();

window.Comments = Comments;
