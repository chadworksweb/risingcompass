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

  function artistHtml(name, slug, className) {
    const inner = escapeHtml(name);
    if (slug) {
      return `<a class="${className} artist-link" href="/artists/${encodeURIComponent(slug)}" onclick="event.stopPropagation();">${inner}</a>`;
    }
    return `<span class="${className}">${inner}</span>`;
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
    const songHref = item.song_slug ? `/songs/${encodeURIComponent(item.song_slug)}` : null;

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
              ${artistHtml(item.artist, item.artist_slug, 'eac-artist')}
              ${chargeText(item) ? `<span class="eac-meta-sep">·</span>${chargeText(item)}` : ''}
            </div>
          </div>
        </li>`;
    }

    const titleHtml = songHref
      ? `<a href="${songHref}">${escapeHtml(item.title)}</a>`
      : escapeHtml(item.title);

    const auditNote = (!item.topics || !item.topics.length)
      ? `<span class="eac-meta-sep">·</span><span class="eac-audit-note">no taxonomy match</span>`
      : '';

    return `
      <li class="eac-row">
        <span class="eac-pos">${item.position}</span>
        <div class="eac-text">
          <p class="eac-deadpan">${escapeHtml(item.deadpan_line)}</p>
          <div class="eac-meta">
            <span class="eac-meta-title">${titleHtml}</span>
            <span class="eac-meta-sep">·</span>
            ${artistHtml(item.artist, item.artist_slug, 'eac-artist')}
            ${chargeText(item) ? `<span class="eac-meta-sep">·</span>${chargeText(item)}` : ''}
            ${auditNote}
          </div>
          ${chipsHtml(item.topics)}
        </div>
      </li>`;
  }

  function computeTodayDistribution(items) {
    const counts = {};
    const songsByTopic = {};
    for (const item of items || []) {
      if (!item.topics || !item.topics.length) continue;
      for (const t of item.topics) {
        counts[t] = (counts[t] || 0) + 1;
        if (!songsByTopic[t]) songsByTopic[t] = [];
        songsByTopic[t].push({
          deadpan: item.deadpan_line || item.title,
          artist: item.artist,
          artist_slug: item.artist_slug,
          song_slug: item.song_slug,
          position: item.position,
        });
      }
    }
    // Sort songs within each topic by chart position (1 first).
    for (const t of Object.keys(songsByTopic)) {
      songsByTopic[t].sort((a, b) => (a.position || 999) - (b.position || 999));
    }
    return Object.entries(counts)
      .map(([topic, count]) => ({ topic, count, songs: songsByTopic[topic] }))
      .sort((a, b) => b.count - a.count || a.topic.localeCompare(b.topic));
  }

  function adaptYearDistribution(distribution) {
    // The /year endpoint now returns top_songs per topic — server picks
    // the highest position-weighted carriers (dominant or not). Just
    // reshape into the {deadpan, artist, song_slug, position} shape the
    // tooltip renderer expects.
    if (!distribution || !distribution.length) return distribution || [];
    return distribution.map((d) => ({
      ...d,
      songs: (d.top_songs || []).map((s) => ({
        deadpan: s.deadpan_line || s.title,
        artist: s.artist,
        artist_slug: s.artist_slug,
        song_slug: s.song_slug,
        position: s.position,
      })),
    }));
  }

  async function renderToday() {
    const list = document.getElementById('today-list');
    const meta = document.getElementById('today-meta');
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

      list.innerHTML = data.items.map(rowHtml).join('');

      // Today's constellation — derive distribution from the items
      // we just rendered. Mirrors the year endpoint's
      // topic_distribution shape so buildConstellation works as-is.
      const todayMeta = document.getElementById('today-topic-meta');
      if (todayMeta) {
        const d = data.date ? new Date(data.date + 'T00:00:00') : null;
        todayMeta.textContent = d ? dateFmt.format(d) : '';
      }
      buildConstellation(computeTodayDistribution(data.items), 'constellation-today');
    } catch (err) {
      console.error('Failed to load /today:', err);
      list.innerHTML = `<li class="eac-loading">Could not load today&rsquo;s ether.</li>`;
      buildConstellation([], 'constellation-today');
    }
  }

  // --- Annual Rollups ----------------------------------------------------

  const periodFmt = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric' });

  function fmtPeriod(start, end) {
    if (!start || !end) return '';
    const s = new Date(start + 'T00:00:00');
    const e = new Date(end + 'T00:00:00');
    return `${periodFmt.format(s)} – ${periodFmt.format(e)}`;
  }

  function rollupRowHtml(item) {
    const songHref = item.song_slug ? `/songs/${encodeURIComponent(item.song_slug)}` : null;
    const titleHtml = songHref
      ? `<a href="${songHref}">${escapeHtml(item.title)}</a>`
      : escapeHtml(item.title);
    const dominant = item.dominant_topic
      ? `<div class="eac-chips"><span class="eac-chip eac-chip--dominant">${escapeHtml(String(item.dominant_topic).replace(/-/g,' '))}</span></div>`
      : '';
    return `
      <li class="eac-row">
        <span class="eac-pos">${item.position}</span>
        <div class="eac-text">
          <p class="eac-deadpan">${escapeHtml(item.deadpan_line || item.title)}</p>
          <div class="eac-meta">
            <span class="eac-meta-title">${titleHtml}</span>
            <span class="eac-meta-sep">·</span>
            ${artistHtml(item.artist, item.artist_slug, 'eac-artist')}
          </div>
          ${dominant}
        </div>
      </li>`;
  }

  function rankedRowHtml(t, isDominant) {
    const cls = isDominant ? 'eac-topic-row eac-topic-row--dominant' : 'eac-topic-row';
    const pct = `${(t.percent * 100).toFixed(1)}%`;
    return `
      <li class="${cls}">
        <span class="eac-topic-name">${escapeHtml(String(t.topic).replace(/-/g,' '))}</span>
        <span class="eac-topic-count">${t.count}</span>
        <span class="eac-topic-pct">${pct}</span>
      </li>`;
  }

  // ---- Force-directed constellation -----------------------------------
  // Build pack §8 — vanilla JS, no library. Tuning constants are
  // starting points; expect to hand-tune as real data arrives.

  const VIEW_W = 960;
  const VIEW_H = 440;
  const CENTER = { x: VIEW_W / 2, y: VIEW_H / 2 };
  const MAX_R = Math.min(VIEW_W, VIEW_H) * 0.42;
  const MIN_R = 22;

  const ATTRACTION_K = 0.04;
  const REPULSION_K  = 600;
  const DAMPING      = 0.85;
  const SETTLE_KE    = 0.05;
  const SETTLE_TICKS = 60;
  const MAX_TICKS    = 600;

  // Per-instance raf handles, keyed by id-prefix. Both today's and the
  // selected-year constellation can run simultaneously; their loops
  // are independent.
  const _constellationRafs = {};

  function stopConstellationLoop(prefix) {
    if (_constellationRafs[prefix]) {
      cancelAnimationFrame(_constellationRafs[prefix]);
      _constellationRafs[prefix] = null;
    }
  }

  function wireConstellationHover(starsG, prefix) {
    const svg = starsG.parentNode;
    const wrap = svg && svg.parentNode;
    const tooltip = document.getElementById(`${prefix}-tooltip`);

    function nodeFromEvent(ev) {
      const target = ev.target;
      if (!target || !target.getAttribute) return null;
      const idx = target.getAttribute('data-idx');
      if (idx == null) return null;
      const all = starsG.__nodes || [];
      return all[+idx] || null;
    }

    function setHotNode(node) {
      const all = starsG.__nodes || [];
      for (const m of all) {
        if (!m.dom) continue;
        const isHot = m === node;
        m.dom.halo.classList.toggle('is-hot', isHot);
        m.dom.core.classList.toggle('is-hot', isHot);
      }
    }

    function clearHot() {
      const all = starsG.__nodes || [];
      for (const m of all) {
        if (!m.dom) continue;
        m.dom.halo.classList.remove('is-hot');
        m.dom.core.classList.remove('is-hot');
      }
    }

    function setTooltipForNode(node) {
      if (!tooltip) return;
      const pct = (node.percent * 100).toFixed(1);
      const header =
        `<div class="ttp-stat">${node.count} song${node.count === 1 ? '' : 's'}`
        + `<span class="ttp-sep">·</span>${pct}%</div>`;

      const songs = (node.songs || []).slice(0, 12);
      let songList = '';
      if (songs.length) {
        const items = songs.map((s) => {
          const pos = s.position ? `<span class="ttp-pos">${s.position}</span>` : '';
          return `<li>${pos}<span class="ttp-deadpan">${escapeHtml(s.deadpan)}</span>`
            + `${artistHtml(s.artist, s.artist_slug, 'ttp-artist')}</li>`;
        });
        songList = `<ul class="ttp-songs">${items.join('')}</ul>`;
        const more = (node.songs || []).length - songs.length;
        if (more > 0) songList += `<div class="ttp-more">+${more} more</div>`;
      }

      tooltip.innerHTML = header + songList;
      tooltip.hidden = false;
    }

    function positionTooltipAt(clientX, clientY) {
      if (!tooltip || !wrap) return;
      const rect = wrap.getBoundingClientRect();
      const x = Math.max(8, Math.min(rect.width - 8, clientX - rect.left));
      const y = Math.max(8, Math.min(rect.height - 8, clientY - rect.top));
      tooltip.style.left = `${x}px`;
      tooltip.style.top = `${y}px`;
    }

    starsG.addEventListener('pointerover', (ev) => {
      const node = nodeFromEvent(ev);
      if (!node) return;
      setHotNode(node);
      setTooltipForNode(node);
      positionTooltipAt(ev.clientX, ev.clientY);
    });

    starsG.addEventListener('pointermove', (ev) => {
      if (tooltip && !tooltip.hidden) positionTooltipAt(ev.clientX, ev.clientY);
      const node = nodeFromEvent(ev);
      if (node) {
        setHotNode(node);
        setTooltipForNode(node);
      }
    });

    if (wrap) {
      wrap.addEventListener('pointerleave', () => {
        clearHot();
        if (tooltip) tooltip.hidden = true;
      });
    }
  }


  function buildConstellation(distribution, prefix) {
    const starsG = document.getElementById(`${prefix}-stars`);
    const labelsG = document.getElementById(`${prefix}-labels`);
    const empty = document.getElementById(`${prefix}-empty`);
    if (!starsG || !labelsG || !empty) return;

    stopConstellationLoop(prefix);
    starsG.innerHTML = '';
    labelsG.innerHTML = '';

    if (!distribution || !distribution.length) {
      empty.hidden = false;
      starsG.__nodes = [];
      return;
    }
    empty.hidden = true;

    const total = distribution.reduce((s, d) => s + d.count, 0) || 1;

    // Build node objects.
    const maxCount = Math.max(...distribution.map((d) => d.count));
    const nodes = distribution.map((d, i) => {
      const freqPercentile = d.count / maxCount; // 0..1, dominant=1
      const targetR = MIN_R + (1 - freqPercentile) * (MAX_R - MIN_R);
      const angle = (i / distribution.length) * Math.PI * 2;
      // Initial position: scattered near target radius with jitter
      const r0 = targetR * (0.6 + Math.random() * 0.6);
      const a0 = angle + (Math.random() - 0.5) * 0.6;
      return {
        topic: d.topic,
        count: d.count,
        percent: d.count / total,
        songs: d.songs || [],
        targetR,
        x: CENTER.x + Math.cos(a0) * r0,
        y: CENTER.y + Math.sin(a0) * r0,
        vx: 0,
        vy: 0,
        dominant: i === 0,
        // Visual encoding from frequency:
        coreR: 2.5 + freqPercentile * 4.5,         // 2.5–7
        haloR: 14 + freqPercentile * 26,           // 14–40
        opacity: 0.4 + freqPercentile * 0.6,       // 0.4–1.0
      };
    });

    // Render initial DOM. We update transform on each tick.
    nodes.forEach((n, idx) => {
      const haloCls = n.dominant ? 'star-halo star-halo--gold' : 'star-halo';
      const coreCls = n.dominant ? 'star-core star-core--gold' : 'star-core';
      const labelCls = n.dominant ? 'star-label star-label--gold' : 'star-label';
      const display = String(n.topic).replace(/-/g, ' ');
      const labelSize = 11 + (n.count / maxCount) * 4;

      const halo = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      halo.setAttribute('class', haloCls);
      halo.setAttribute('r', n.haloR);
      halo.setAttribute('opacity', n.opacity * 0.9);
      starsG.appendChild(halo);

      const core = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      core.setAttribute('class', coreCls);
      core.setAttribute('r', n.coreR);
      core.setAttribute('opacity', Math.min(n.opacity + 0.15, 1));
      starsG.appendChild(core);

      const hit = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      hit.setAttribute('class', 'star-hit');
      hit.setAttribute('r', Math.max(n.haloR, 22));
      hit.setAttribute('data-idx', idx);
      starsG.appendChild(hit);

      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('class', labelCls);
      label.setAttribute('font-size', labelSize.toFixed(1));
      label.setAttribute('opacity', Math.max(n.opacity, 0.55));
      label.textContent = display;
      labelsG.appendChild(label);

      n.dom = { halo, core, hit, label, labelSize };
    });

    // Hover interaction. Stash the live nodes array on the group so the
    // listeners (attached once) read whatever the latest rebuild wrote.
    starsG.__nodes = nodes;
    if (!starsG.__hoverWired) {
      wireConstellationHover(starsG, prefix);
      starsG.__hoverWired = true;
    }

    // Sim loop.
    let ticks = 0;
    let _settledTicks = 0;
    function tick() {
      ticks++;

      // Attraction toward target radius.
      for (const n of nodes) {
        const dx = n.x - CENTER.x;
        const dy = n.y - CENTER.y;
        const r = Math.hypot(dx, dy) || 0.0001;
        const radialErr = r - n.targetR;
        const ux = dx / r;
        const uy = dy / r;
        n.vx -= ATTRACTION_K * radialErr * ux;
        n.vy -= ATTRACTION_K * radialErr * uy;
      }

      // Pairwise repulsion.
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          let d = Math.hypot(dx, dy);
          if (d < 0.5) d = 0.5;
          const f = REPULSION_K / (d * d);
          const ux = dx / d;
          const uy = dy / d;
          a.vx += ux * f;
          a.vy += uy * f;
          b.vx -= ux * f;
          b.vy -= uy * f;
        }
      }

      // Integrate + damp.
      let ke = 0;
      for (const n of nodes) {
        n.vx *= DAMPING;
        n.vy *= DAMPING;
        n.x += n.vx;
        n.y += n.vy;
        // Clamp to viewport with margin so labels don't clip.
        const margin = 28;
        if (n.x < margin) { n.x = margin; n.vx *= -0.4; }
        if (n.x > VIEW_W - margin) { n.x = VIEW_W - margin; n.vx *= -0.4; }
        if (n.y < margin) { n.y = margin; n.vy *= -0.4; }
        if (n.y > VIEW_H - margin) { n.y = VIEW_H - margin; n.vy *= -0.4; }
        ke += n.vx * n.vx + n.vy * n.vy;
      }

      // Paint.
      for (const n of nodes) {
        const { halo, core, hit, label, labelSize } = n.dom;
        halo.setAttribute('cx', n.x);
        halo.setAttribute('cy', n.y);
        core.setAttribute('cx', n.x);
        core.setAttribute('cy', n.y);
        hit.setAttribute('cx', n.x);
        hit.setAttribute('cy', n.y);
        // Label sits below the star, with offset proportional to halo size.
        label.setAttribute('x', n.x);
        label.setAttribute('y', n.y + n.haloR * 0.55 + labelSize + 2);
      }

      if (ke / nodes.length < SETTLE_KE) _settledTicks++; else _settledTicks = 0;
      if (_settledTicks >= SETTLE_TICKS || ticks >= MAX_TICKS) {
        _constellationRafs[prefix] = null;
        return;
      }
      _constellationRafs[prefix] = requestAnimationFrame(tick);
    }
    _constellationRafs[prefix] = requestAnimationFrame(tick);
  }

  async function loadYear(year) {
    const list = document.getElementById('year-list');
    const meta = document.getElementById('rollup-meta');
    const status = document.getElementById('rollup-status');
    const ranked = document.getElementById('topic-ranked');
    if (!list || !meta || !ranked) return;

    list.innerHTML = `<li class="eac-loading">Loading ${year}…</li>`;
    ranked.innerHTML = `<li class="eac-loading">—</li>`;

    try {
      const data = await API.getEtherYear(year);
      const period = fmtPeriod(data.period_start, data.period_end);
      const totalSongs = data.total_compass_songs_in_period || 0;
      const auditCount = data.audit_flagged_count || 0;
      meta.textContent =
        `${period} · ${totalSongs} calibrated song${totalSongs === 1 ? '' : 's'}`
        + (auditCount ? ` · ${auditCount} audit-flagged` : '');

      // Status line — coverage honesty.
      const hasTopTwenty = data.top_20_deadpan && data.top_20_deadpan.length > 0;
      if (status) {
        if (totalSongs > 0 && !hasTopTwenty) {
          status.hidden = false;
          status.textContent =
            'No tagged songs in this year yet — every row is awaiting the deferred backfill or a fresh calibrator run.';
        } else {
          status.hidden = true;
        }
      }

      // Top 20 deadpan.
      if (hasTopTwenty) {
        list.innerHTML = data.top_20_deadpan.map(rollupRowHtml).join('');
      } else {
        list.innerHTML = `<li class="eac-loading">—</li>`;
      }

      // Ranked topic list.
      if (data.topic_distribution && data.topic_distribution.length) {
        ranked.innerHTML = data.topic_distribution
          .map((t, i) => rankedRowHtml(t, i === 0))
          .join('');
      } else {
        ranked.innerHTML = `<li class="eac-loading">No topics yet for ${year}.</li>`;
      }

      // Constellation.
      buildConstellation(
        adaptYearDistribution(data.topic_distribution || []),
        'constellation-annual',
      );
    } catch (err) {
      console.error(`Failed to load /year/${year}:`, err);
      meta.textContent = '';
      list.innerHTML = `<li class="eac-loading">Could not load ${year}.</li>`;
      ranked.innerHTML = `<li class="eac-loading">—</li>`;
      if (status) status.hidden = true;
      buildConstellation([], 'constellation-annual');
    }
  }

  async function initRollups() {
    const select = document.getElementById('year-select');
    if (!select) return;

    let years;
    try {
      const out = await API.getEtherYears();
      years = (out && out.years) || [];
    } catch (err) {
      console.error('Failed to load /years:', err);
      return;
    }

    if (!years.length) {
      const meta = document.getElementById('rollup-meta');
      if (meta) meta.textContent = 'No yearly data yet.';
      return;
    }

    // Newest first in the dropdown.
    const sorted = [...years].sort((a, b) => b - a);
    select.innerHTML = sorted.map((y) => `<option value="${y}">${y}</option>`).join('');
    const initial = sorted[0];
    select.value = String(initial);

    select.addEventListener('change', () => {
      const y = parseInt(select.value, 10);
      if (!isNaN(y)) loadYear(y);
    });

    loadYear(initial);
  }

  document.addEventListener('DOMContentLoaded', () => {
    renderToday();
    initRollups();
  });
})();
