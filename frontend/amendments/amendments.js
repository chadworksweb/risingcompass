/* === Amendments — fetch + render the public record === */

(() => {
  'use strict';

  // ─── API config (mirrors the Tenets / Lyrical Charger pattern) ──
  const IS_LOCAL = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const API_HOST = IS_LOCAL
    ? `http://${window.location.hostname}:8000`
    : 'https://api.risingcompass.net';
  const API_KEY = IS_LOCAL
    ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
    : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

  async function fetchLog() {
    let lastErr;
    for (let i = 0; i < 3; i++) {
      try {
        const resp = await fetch(`${API_HOST}/api/amendments`, {
          headers: { 'X-Api-Key': API_KEY },
          signal: AbortSignal.timeout(8000),
        });
        if (resp.ok) return resp.json();
        if (resp.status >= 400 && resp.status < 500) throw new Error(`API error ${resp.status}`);
        lastErr = new Error(`API error ${resp.status}`);
      } catch (err) {
        lastErr = err;
      }
      if (i < 2) await new Promise((r) => setTimeout(r, 400 * (i + 1)));
    }
    throw lastErr;
  }

  // Status → CSS var name (also used in card side-stripe)
  const STATUS_COLOR_VAR = {
    proposed: '--am-proposed',
    deliberating: '--am-deliberating',
    ratified: '--am-ratified',
    covered: '--am-covered',
    rejected: '--am-rejected',
  };

  // ─── helpers ────────────────────────────────────────────────────

  const html = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === 'class') node.className = v;
      else if (k === 'style') node.setAttribute('style', v);
      else if (k.startsWith('data-') || k === 'id' || k === 'role' || k === 'aria-label' || k === 'target' || k === 'rel') {
        node.setAttribute(k, v);
      } else {
        node[k] = v;
      }
    }
    for (const c of [].concat(children)) {
      if (c != null) node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return node;
  };

  const statusColor = (key) => `var(${STATUS_COLOR_VAR[key] || '--am-rule'})`;

  // ─── venue CTA ──────────────────────────────────────────────────

  function renderVenueCta(root, venue) {
    root.replaceChildren();
    if (!venue || !venue.url) return;
    const label = venue.platform === 'discord' ? 'Join the Discord' : 'Open the venue';

    if (venue.coming_soon) {
      const btn = html('button', {
        type: 'button',
        class: 'am-cta',
        'aria-haspopup': 'dialog',
        'aria-controls': 'venue-modal',
      }, label);
      btn.addEventListener('click', openVenueModal);
      root.appendChild(btn);
    } else {
      root.appendChild(html('a', {
        class: 'am-cta',
        href: venue.url,
        target: '_blank',
        rel: 'noopener',
      }, label));
    }

    if (venue.note) {
      const noteWrap = html('p', { class: 'am-cta-note' }, venue.note);
      root.parentElement.appendChild(noteWrap);
    }
  }

  // ─── venue modal ────────────────────────────────────────────────

  let venueModalLastFocus = null;

  function openVenueModal() {
    const modal = document.getElementById('venue-modal');
    if (!modal) return;
    venueModalLastFocus = document.activeElement;
    modal.hidden = false;
    document.body.classList.add('am-modal-open');
    document.addEventListener('keydown', onVenueModalKey);
    const closeBtn = modal.querySelector('.am-modal-close');
    if (closeBtn) closeBtn.focus();
  }

  function closeVenueModal() {
    const modal = document.getElementById('venue-modal');
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove('am-modal-open');
    document.removeEventListener('keydown', onVenueModalKey);
    if (venueModalLastFocus && typeof venueModalLastFocus.focus === 'function') {
      venueModalLastFocus.focus();
    }
  }

  function onVenueModalKey(e) {
    if (e.key === 'Escape') closeVenueModal();
  }

  document.addEventListener('click', (e) => {
    const t = e.target;
    if (t && t.matches && t.matches('[data-modal-close]')) closeVenueModal();
  });

  // ─── thresholds ─────────────────────────────────────────────────

  function renderThresholds(root, t) {
    root.replaceChildren();
    if (!t) return;
    const items = [
      { value: t.co_sponsors_required, label: 'Co-sponsors required' },
      { value: `${t.deliberation_window_days} days`, label: 'Deliberation window' },
      { value: `${t.ratification_supermajority_pct}%`, label: 'Supermajority to ratify' },
    ];
    items.forEach(({ value, label }) => {
      const box = html('div', { class: 'am-thresh' });
      box.appendChild(html('p', { class: 'am-thresh-value' }, String(value)));
      box.appendChild(html('p', { class: 'am-thresh-label' }, label));
      root.appendChild(box);
    });
  }

  // ─── amendment card ─────────────────────────────────────────────

  function renderCard(am, thresholds) {
    const color = statusColor(am.status);
    const card = html('article', {
      class: 'am-card',
      id: `amend-${am.id}`,
      style: `--status-color: ${color};`,
    });

    const idLine = html('p', { class: 'am-card-id' }, `${am.id} · ${am.status.toUpperCase()}`);
    card.appendChild(idLine);

    card.appendChild(html('h3', { class: 'am-card-title' }, am.title || '(untitled)'));

    // Meta row
    const meta = html('div', { class: 'am-card-meta' });
    if (am.proposed_at) meta.appendChild(html('span', {}, [html('b', {}, 'Proposed '), am.proposed_at]));
    if (am.cites_tenet) {
      const a = html('a', { href: `/tenets/#tenet-${am.cites_tenet}`, target: '_blank', rel: 'noopener' }, am.cites_tenet);
      meta.appendChild(html('span', {}, [html('b', {}, 'Cites '), a]));
    }
    if (am.proposer) meta.appendChild(html('span', {}, [html('b', {}, 'Proposer '), am.proposer]));
    if (am.ratified_at) meta.appendChild(html('span', {}, [html('b', {}, 'Ratified '), am.ratified_at]));
    if (am.rejected_at && am.status === 'rejected') meta.appendChild(html('span', {}, [html('b', {}, 'Rejected '), am.rejected_at]));
    card.appendChild(meta);

    // Co-sponsor progress (only relevant when proposed)
    if (am.status === 'proposed' && thresholds && thresholds.co_sponsors_required) {
      const required = thresholds.co_sponsors_required;
      const have = am.co_sponsors || 0;
      const pct = Math.min(100, Math.round((have / required) * 100));
      const prog = html('div', { class: 'am-progress' });
      const bar = html('div', { class: 'am-progress-bar' });
      bar.appendChild(html('div', { class: 'am-progress-fill', style: `width: ${pct}%;` }));
      prog.appendChild(bar);
      prog.appendChild(html('p', { class: 'am-progress-label' },
        `${have} of ${required} co-sponsors`));
      card.appendChild(prog);
    }

    // Proposed change
    if (am.proposed_change) {
      const sec = html('div', { class: 'am-card-section' });
      sec.appendChild(html('h4', {}, 'Proposed change'));
      sec.appendChild(html('p', {}, am.proposed_change));
      card.appendChild(sec);
    }

    // Novelty + soundness (only on active proposals)
    if (['proposed', 'deliberating'].includes(am.status)) {
      if (am.novelty_argument) {
        const sec = html('div', { class: 'am-card-section' });
        sec.appendChild(html('h4', {}, 'Novelty'));
        sec.appendChild(html('p', {}, am.novelty_argument));
        card.appendChild(sec);
      }
      if (am.soundness_argument) {
        const sec = html('div', { class: 'am-card-section' });
        sec.appendChild(html('h4', {}, 'Soundness'));
        sec.appendChild(html('p', {}, am.soundness_argument));
        card.appendChild(sec);
      }
    }

    // Covered-by explanation
    if (am.status === 'covered' && am.rejection_reason) {
      const sec = html('div', { class: 'am-card-section' });
      sec.appendChild(html('h4', {}, am.covered_by ? `Already covered by ${am.covered_by}` : 'Already covered'));
      sec.appendChild(html('p', {}, am.rejection_reason));
      card.appendChild(sec);
    }

    // Rejection reason
    if (am.status === 'rejected' && am.rejection_reason) {
      const sec = html('div', { class: 'am-card-section' });
      sec.appendChild(html('h4', {}, 'Rejection reason'));
      sec.appendChild(html('p', {}, am.rejection_reason));
      card.appendChild(sec);
    }

    // Discussion link
    if (am.discussion_url) {
      card.appendChild(html('a', {
        class: 'am-card-cta',
        href: am.discussion_url,
        target: '_blank',
        rel: 'noopener',
      }, 'View discussion →'));
    }

    return card;
  }

  // ─── groups (one per status) ────────────────────────────────────

  function renderGroups(root, data) {
    root.replaceChildren();
    const thresholds = data.thresholds || {};
    const byStatus = {};
    (data.amendments || []).forEach((am) => {
      (byStatus[am.status] = byStatus[am.status] || []).push(am);
    });

    (data.statuses || []).forEach((status) => {
      const color = statusColor(status.key);
      const group = html('section', {
        class: 'am-group',
        id: `group-${status.key}`,
        style: `--status-color: ${color};`,
      });

      const header = html('div', { class: 'am-group-header' });
      header.appendChild(html('h2', { class: 'am-group-label' }, status.label.toUpperCase()));
      const entries = byStatus[status.key] || [];
      header.appendChild(html('p', { class: 'am-group-count' }, `${entries.length} · ${status.key}`));
      group.appendChild(header);

      group.appendChild(html('p', { class: 'am-group-description' }, status.description));

      if (entries.length === 0) {
        group.appendChild(html('p', { class: 'am-empty' }, 'None on the record yet.'));
      } else {
        // Newest first by proposed_at (string-sortable YYYY-MM-DD)
        entries.sort((a, b) => (b.proposed_at || '').localeCompare(a.proposed_at || ''));
        entries.forEach((am) => group.appendChild(renderCard(am, thresholds)));
      }

      root.appendChild(group);
    });
  }

  // ─── meta ───────────────────────────────────────────────────────

  function renderFooterMeta(data) {
    const v = document.getElementById('schema-version');
    const u = document.getElementById('updated-at');
    if (v) v.textContent = `v${data.schema_version}`;
    if (u) u.textContent = data.updated_at || '—';
  }

  // ─── boot ───────────────────────────────────────────────────────

  async function boot() {
    const cta = document.getElementById('venue-cta');
    const thresholds = document.getElementById('thresholds');
    const sections = document.getElementById('sections');
    const toggleRow = document.getElementById('examples-toggle-row');
    const toggleBtn = document.getElementById('examples-toggle');
    const banner = document.getElementById('examples-banner');

    try {
      const data = await fetchLog();
      renderVenueCta(cta, data.venue);
      renderThresholds(thresholds, data.thresholds);
      renderFooterMeta(data);

      const realCount = (data.amendments || []).length;
      const exampleCount = (data.example_amendments || []).length;
      let showingExamples = false;

      const renderForMode = () => {
        const list = showingExamples ? data.example_amendments : data.amendments;
        renderGroups(sections, { ...data, amendments: list });
        if (banner) banner.hidden = !showingExamples;
        if (toggleBtn) {
          toggleBtn.textContent = showingExamples ? 'Hide example proposals' : 'Show example proposals';
          toggleBtn.setAttribute('aria-pressed', String(showingExamples));
        }
      };

      // Default render: real amendments only.
      renderForMode();

      // Show the toggle only when the record is empty AND we have examples to show.
      if (toggleRow && toggleBtn && exampleCount > 0 && realCount === 0) {
        toggleRow.hidden = false;
        toggleBtn.addEventListener('click', () => {
          showingExamples = !showingExamples;
          renderForMode();
        });
      }
    } catch (err) {
      console.error('Failed to load amendment log:', err);
      const msg = html('p', { class: 'am-empty' }, 'The amendment log is momentarily out of reach. Please reload.');
      sections.appendChild(msg);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
