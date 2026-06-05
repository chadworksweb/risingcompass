/* === Dev Ledger -- public frontend ===
   One script drives all four /dev pages; it branches on which container is
   present. Reads are public (API.get); when signed in we route reads through
   Auth.authedFetch so the API can mark which items the viewer has voted on.
   Submit + vote require a signed-in Clerk Tier 1 account. */

(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function isSignedIn() {
    try { return localStorage.getItem('rc_authed') === '1'; } catch (_) { return false; }
  }

  function fmtDate(iso) {
    if (!iso) return '';
    try { return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
    catch (_) { return ''; }
  }

  function submitter(i) {
    if (i.submitted_by_handle) return '@' + esc(i.submitted_by_handle);
    if (i.submitted_by_anon_id) return esc(i.submitted_by_anon_id);
    return 'the team';
  }

  // Public read. Authed when signed in so viewer_has_voted comes back.
  async function readLedger(path) {
    if (isSignedIn() && window.Auth) {
      const r = await window.Auth.authedFetch(path);
      if (!r.ok) throw new Error('Failed to load (' + r.status + ').');
      return r.json();
    }
    return API.get(path);
  }

  function promptSignIn() {
    // Send them to /account with a returnTo so they come back here.
    const ret = window.location.pathname + window.location.search + window.location.hash;
    window.location.href = '/account/?returnTo=' + encodeURIComponent(ret);
  }

  // ---------------------------------------------------------------- version

  async function loadVersion(el) {
    try {
      const data = await API.get('/api/dev-ledger/version');
      if (data && data.version) {
        el.innerHTML = 'Current release <span class="v">' + esc(data.version) +
          '</span> &middot; <a href="/dev/changelog/">see the changelog</a>';
      } else {
        el.innerHTML = '<a href="/dev/changelog/">See the changelog</a>';
      }
    } catch (_) { el.textContent = ''; }
  }

  // ---------------------------------------------------------------- changelog

  async function loadChangelog(el) {
    el.innerHTML = '<p class="dl-empty">Loading...</p>';
    let items;
    try { items = await readLedger('/api/dev-ledger/changelog'); }
    catch (e) { el.innerHTML = '<p class="dl-empty">' + esc(e.message) + '</p>'; return; }
    if (!items.length) { el.innerHTML = '<p class="dl-empty">No releases yet.</p>'; return; }

    // Group by version, preserving the API's newest-first order.
    const order = [];
    const groups = {};
    items.forEach((i) => {
      const v = i.version || 'unversioned';
      if (!groups[v]) { groups[v] = []; order.push(v); }
      groups[v].push(i);
    });

    el.innerHTML = order.map((v) => {
      const rows = groups[v];
      const date = fmtDate(rows[0].shipped_at);
      const entries = rows.map((i) => `
        <div class="dl-entry ${esc(i.item_type)}">
          <div class="dl-entry-head">
            <span class="dl-badge ${esc(i.item_type)}">${esc(i.item_type)}</span>
            ${i.area ? `<span class="dl-badge area">${esc(i.area)}</span>` : ''}
            <span class="dl-entry-title">${esc(i.title)}</span>
          </div>
          <div class="dl-entry-body">${esc(i.body)}</div>
        </div>`).join('');
      return `
        <div class="dl-release">
          <div class="dl-release-head">
            <span class="dl-release-ver">${esc(v)}</span>
            <span class="dl-release-date">${date}</span>
          </div>
          ${entries}
        </div>`;
    }).join('');
  }

  // ---------------------------------------------------------------- roadmap

  async function loadRoadmap(el) {
    el.innerHTML = '<p class="dl-empty">Loading...</p>';
    let data;
    try { data = await readLedger('/api/dev-ledger/roadmap'); }
    catch (e) { el.innerHTML = '<p class="dl-empty">' + esc(e.message) + '</p>'; return; }

    const cols = [
      ['now', 'Now', 'In progress'],
      ['next', 'Next', 'Up next'],
      ['later', 'Later', 'On the horizon'],
    ];
    const total = cols.reduce((n, [k]) => n + ((data[k] || []).length), 0);
    if (!total) { el.innerHTML = '<p class="dl-empty">The roadmap is being drawn up. Check back soon.</p>'; return; }

    el.innerHTML = cols.map(([key, label, blurb]) => {
      const items = data[key] || [];
      const body = items.length
        ? items.map((i) => `
            <div class="dl-entry ${esc(i.item_type)}">
              <div class="dl-entry-head">
                <span class="dl-entry-title">${esc(i.title)}</span>
                ${i.vote_count ? `<span class="dl-meta">&#9650; ${i.vote_count}</span>` : ''}
              </div>
              <div class="dl-entry-body">${esc(i.body)}</div>
            </div>`).join('')
        : '<p class="dl-empty" style="font-size:0.85rem;">Nothing here yet.</p>';
      return `
        <div class="dl-col" data-stage="${key}">
          <div class="dl-col-head">${esc(label)}</div>
          ${body}
        </div>`;
    }).join('');
  }

  // ---------------------------------------------------------------- feedback

  const fb = { type: 'all', sort: 'top' };

  async function loadFeedback(el) {
    el.innerHTML = '<p class="dl-empty">Loading...</p>';
    const qs = new URLSearchParams({ sort: fb.sort });
    if (fb.type !== 'all') qs.set('type', fb.type);
    let items;
    try { items = await readLedger('/api/dev-ledger/feedback?' + qs.toString()); }
    catch (e) { el.innerHTML = '<p class="dl-empty">' + esc(e.message) + '</p>'; return; }
    if (!items.length) {
      el.innerHTML = '<p class="dl-empty">Nothing here yet. Be the first to post one.</p>';
      return;
    }
    el.innerHTML = items.map(renderCard).join('');
    el.querySelectorAll('.dl-vote').forEach((b) => b.addEventListener('click', onVote));
  }

  function renderCard(i) {
    const voted = i.viewer_has_voted ? ' voted' : '';
    return `
      <div class="dl-card">
        <button class="dl-vote${voted}" data-id="${i.id}" aria-label="Upvote">
          <span class="caret">&#9650;</span>
          <span class="count">${i.vote_count || 0}</span>
        </button>
        <div class="dl-card-body">
          <div class="dl-card-head">
            <span class="dl-badge ${esc(i.item_type)}">${esc(i.item_type)}</span>
            ${i.severity ? `<span class="dl-badge sev">${esc(i.severity)}</span>` : ''}
            ${i.area ? `<span class="dl-badge area">${esc(i.area)}</span>` : ''}
            <span class="dl-card-title">${esc(i.title)}</span>
          </div>
          <div class="dl-card-text">${esc(i.body)}</div>
          <div class="dl-card-foot">
            ${esc((i.status || '').replace('_', ' '))} &middot; ${submitter(i)} &middot; ${fmtDate(i.created_at)}
          </div>
        </div>
      </div>`;
  }

  async function onVote(e) {
    const btn = e.currentTarget;
    const id = btn.dataset.id;
    if (!isSignedIn() || !window.Auth) { promptSignIn(); return; }
    btn.disabled = true;
    try {
      const r = await window.Auth.authedFetch('/api/dev-ledger/vote/' + id, { method: 'POST' });
      if (r.status === 401) { promptSignIn(); return; }
      if (!r.ok) throw new Error('Vote failed.');
      const data = await r.json();
      btn.querySelector('.count').textContent = data.vote_count;
      btn.classList.toggle('voted', !!data.voted);
    } catch (_) {
      /* leave the count as-is on failure */
    } finally {
      btn.disabled = false;
    }
  }

  function wireFeedbackControls(listEl) {
    document.querySelectorAll('[data-fb-type]').forEach((b) => {
      b.addEventListener('click', () => {
        fb.type = b.dataset.fbType;
        document.querySelectorAll('[data-fb-type]').forEach((x) => x.classList.toggle('active', x === b));
        loadFeedback(listEl);
      });
    });
    document.querySelectorAll('[data-fb-sort]').forEach((b) => {
      b.addEventListener('click', () => {
        fb.sort = b.dataset.fbSort;
        document.querySelectorAll('[data-fb-sort]').forEach((x) => x.classList.toggle('active', x === b));
        loadFeedback(listEl);
      });
    });
    wireSubmit(listEl);
  }

  function wireSubmit(listEl) {
    const form = document.getElementById('dlSubmitForm');
    if (!form) return;
    const typeSel = document.getElementById('dlSubType');
    const sevWrap = document.getElementById('dlSubSeverityWrap');
    if (typeSel && sevWrap) {
      const sync = () => { sevWrap.style.display = typeSel.value === 'bug' ? '' : 'none'; };
      typeSel.addEventListener('change', sync);
      sync();
    }
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const note = document.getElementById('dlSubNote');
      const btn = form.querySelector('button[type=submit]');
      if (!isSignedIn() || !window.Auth) { promptSignIn(); return; }
      const payload = {
        item_type: typeSel.value,
        title: document.getElementById('dlSubTitle').value.trim(),
        body: document.getElementById('dlSubBody').value.trim(),
        area: (document.getElementById('dlSubArea').value || '').trim() || null,
        severity: typeSel.value === 'bug' ? (document.getElementById('dlSubSeverity').value || null) : null,
      };
      note.className = 'dl-note';
      note.textContent = '';
      btn.disabled = true;
      try {
        const r = await window.Auth.authedFetch('/api/dev-ledger/feedback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const body = await r.json().catch(() => ({}));
        if (r.status === 401) { promptSignIn(); return; }
        if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Submission failed.');
        note.className = 'dl-note ok';
        note.textContent = 'Thanks -- got it. We review submissions before they appear on the board.';
        document.getElementById('dlSubTitle').value = '';
        document.getElementById('dlSubBody').value = '';
        document.getElementById('dlSubArea').value = '';
      } catch (err) {
        note.className = 'dl-note err';
        note.textContent = err.message || 'Submission failed.';
      } finally {
        btn.disabled = false;
      }
    });
  }

  // ---------------------------------------------------------------- init

  document.addEventListener('DOMContentLoaded', () => {
    const v = document.getElementById('dlVersion');
    if (v) loadVersion(v);

    const cl = document.getElementById('dlChangelog');
    if (cl) loadChangelog(cl);

    const rm = document.getElementById('dlRoadmap');
    if (rm) loadRoadmap(rm);

    const fbList = document.getElementById('dlFeedback');
    if (fbList) { wireFeedbackControls(fbList); loadFeedback(fbList); }
  });
})();
