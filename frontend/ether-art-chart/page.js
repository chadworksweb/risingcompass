/* === The Ether Art Chart — product page renderer ===
 *
 * Step 9 scope: today's view first. Annual rollups + topic constellation
 * land in step 10 — the page already has the section scaffolds for them.
 *
 * This is the "live mirror" of the homepage card per scope §150 — same
 * data source, fuller treatment: deadpan line in display register, all
 * topic chips per row (not just the dominant one), tier dot + signed
 * charge for the day's emotional weight.
 */
(() => {
  const TIER_LABELS = {
    violet: 'Ascended',
    blue: 'Elevated',
    green: 'Decent',
    orange: 'Degraded',
    red: 'Corrupted',
  };

  const dateFmt = new Intl.DateTimeFormat('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  });

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function chipsHtml(topics) {
    if (!topics || !topics.length) return '';
    const rendered = topics.map((t, i) => {
      const cls = i === 0 ? 'eac-chip eac-chip--dominant' : 'eac-chip';
      const label = String(t).replace(/-/g, ' ');
      return `<span class="${cls}">${escapeHtml(label)}</span>`;
    });
    return `<div class="eac-chips">${rendered.join('')}</div>`;
  }

  function chargeText(item) {
    const tier = item.rubric_color;
    if (!tier) return '';
    const label = TIER_LABELS[tier] || tier;
    const tickClass = ['violet','blue','green','orange','red'].includes(tier)
      ? `eac-tier-${tier}`
      : 'eac-tier-green';
    const score = item.charge_value == null
      ? ''
      : ` ${item.charge_value > 0 ? '+' + item.charge_value : item.charge_value}`;
    return `<span class="eac-charge"><span class="eac-tier-tick ${tickClass}"></span>${escapeHtml(label)}${escapeHtml(score)}</span>`;
  }

  function rowHtml(item) {
    const songHref = item.song_slug ? `/songs/${encodeURIComponent(item.song_slug)}/` : null;

    if (!item.deadpan_line) {
      const titleHtml = songHref
        ? `<a href="${songHref}">${escapeHtml(item.title)}</a>`
        : escapeHtml(item.title);
      return `
        <li class="eac-row eac-row--untagged">
          <span class="eac-pos">${item.position}</span>
          <div class="eac-text">
            <p class="eac-deadpan">${titleHtml}<span class="eac-untagged-pill">untagged</span></p>
            <div class="eac-meta">
              <span class="eac-artist">${escapeHtml(item.artist)}</span>
              ${chargeText(item) ? `<span class="eac-meta-sep">·</span>${chargeText(item)}` : ''}
            </div>
          </div>
        </li>`;
    }

    const deadpan = songHref
      ? `<a href="${songHref}">${escapeHtml(item.deadpan_line)}</a>`
      : escapeHtml(item.deadpan_line);

    const auditNote = (!item.topics || !item.topics.length)
      ? `<span class="eac-meta-sep">·</span><span class="eac-audit-note">no taxonomy match</span>`
      : '';

    return `
      <li class="eac-row">
        <span class="eac-pos">${item.position}</span>
        <div class="eac-text">
          <p class="eac-deadpan">${deadpan}</p>
          <div class="eac-meta">
            <span class="eac-artist">${escapeHtml(item.artist)}</span>
            ${chargeText(item) ? `<span class="eac-meta-sep">·</span>${chargeText(item)}` : ''}
            ${auditNote}
          </div>
          ${chipsHtml(item.topics)}
        </div>
      </li>`;
  }

  async function renderToday() {
    const list = document.getElementById('today-list');
    const meta = document.getElementById('today-meta');
    const status = document.getElementById('today-status');
    if (!list) return;

    try {
      const data = await API.getEtherToday();
      if (!data || !data.items || !data.items.length) {
        list.innerHTML = `<li class="eac-loading">No reading available yet.</li>`;
        if (meta) meta.textContent = '';
        return;
      }

      if (meta) {
        const d = data.date ? new Date(data.date + 'T00:00:00') : null;
        meta.textContent = d ? dateFmt.format(d) : '';
      }

      const total = data.items.length;
      const tagged = data.items.filter((i) => i.deadpan_line).length;
      if (status) {
        if (tagged < total) {
          status.hidden = false;
          status.textContent =
            `${tagged} of ${total} tagged — the rest will fill in as new compass songs come through, or when the deferred backfill runs.`;
        } else {
          status.hidden = true;
        }
      }

      list.innerHTML = data.items.map(rowHtml).join('');
    } catch (err) {
      console.error('Failed to load /today:', err);
      list.innerHTML = `<li class="eac-loading">Could not load today&rsquo;s ether.</li>`;
      if (status) status.hidden = true;
    }
  }

  document.addEventListener('DOMContentLoaded', renderToday);
})();
