/* === Song Effects Label — Page Logic === */

(() => {
  'use strict';

  const CHARGE_LABELS = {
    violet: 'Ascended', blue: 'Elevated', green: 'Decent',
    orange: 'Degraded', red: 'Corrupted',
  };
  const COLOR_HEX = {
    violet: '#aa54ff', blue: '#3388ff', green: '#33cc55',
    orange: '#ffbb33', red: '#ff3333',
  };

  const TIER_EFFECTS = {
    violet: 'This song operates at the highest lyrical frequency. Its words may invoke collective healing, spiritual sovereignty, or transcendent perspective. Listeners may experience elevated awareness, compassion, or a sense of connection to something larger than themselves.',
    blue: 'This song carries elevated lyrical charge. Its words process honest internal work, questioning, or genuine growth. Listeners may experience self-reflection, emotional clarity, or motivation toward personal development.',
    green: 'This song carries neutral lyrical charge. Its words are pleasant or fun without transformative intent. Listeners may experience enjoyment, relaxation, or casual engagement without significant emotional shift.',
    orange: 'This song carries degraded lyrical charge. Its words reinforce ego-driven patterns: materialism, avoidance, or self-destructive framing. Listeners may experience reinforced insecurity, comparison, or emotional numbness.',
    red: 'This song carries corrupted lyrical charge. Its words transmit objectification, substance glorification, violence, or contempt. Listeners may experience desensitization, aggression reinforcement, or diminished empathy.',
  };

  function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function announce(msg) {
    const el = document.getElementById('sr-announce');
    if (el) el.textContent = msg;
  }

  // Terminal state — clear the dashboard and show a single clean panel. kind:
  // 'notfound' (no such song / no slug) or 'error' (backend/network failure).
  function showZeroState(kind) {
    document.querySelectorAll('.song-main > .song-section, .song-main > .song-row')
      .forEach(el => { el.hidden = true; });
    const nf = document.getElementById('not-found');
    const title = document.getElementById('not-found-title');
    const msg = document.getElementById('not-found-msg');
    const charger = document.getElementById('not-found-charger');
    if (!nf) return;
    if (kind === 'error') {
      title.textContent = 'Couldn’t load this song';
      msg.textContent = 'Something went wrong reaching the compass. This is usually temporary — refresh in a moment.';
      if (charger) charger.hidden = true;
    } else {
      title.textContent = 'Song not found';
      msg.textContent = 'We haven’t calibrated this song yet, or the link is off. Search the catalog, or send us the lyrics and the compass will read them.';
      if (charger) charger.hidden = false;
    }
    nf.hidden = false;
    document.title = `${title.textContent} — The Rising Compass`;
    announce(title.textContent);
  }

  async function init() {
    // Clean URL: /songs/<slug>. Fall back to ?slug=<slug> for legacy links.
    const params = new URLSearchParams(window.location.search);
    let slug = params.get('slug');
    if (!slug) {
      const m = window.location.pathname.match(/^\/songs\/([^/]+)\/?$/);
      if (m && m[1] !== 'song.html') slug = decodeURIComponent(m[1]);
    }
    if (!slug) { showZeroState('notfound'); return; }

    try {
      const song = await ArtistsAPI.getSong(slug);
      renderSong(song);
      announce(`Loaded effects label for ${song.title}`);
      // Flag counts load independently — failures here shouldn't block the page.
      ArtistsAPI.getSongFlagCounts(slug)
        .then(counts => renderFlagCounts(song, counts))
        .catch(err => console.warn('Flag counts unavailable:', err));
      // Calibration Log (recalibrations + resets + pre-publish corrections) is independent too.
      ArtistsAPI.getSongHistory(slug)
        .then(data => renderRecalibrationHistory(
          data.recalibrations || [],
          data.resets || [],
          data.pre_publish_corrections || [],
        ))
        .catch(err => console.warn('Calibration Log unavailable:', err));
      // Calibration runs (corpus + consensus) are also independent.
      ArtistsAPI.getSongCalibrationRuns(slug)
        .then(data => renderCalibrationRuns(data.runs || [], data.consensus))
        .catch(err => console.warn('Calibration runs unavailable:', err));
      // Audience Vibe layer (independent — no song.song_source means no needle).
      if (song.song_source && song.song_id) {
        initAudienceVibe(song);
      }
      // Artist Verified — claim CTA always renders; published-block fetch
      // requires the polymorphic id pair.
      renderArtistClaimCta(song);
      if (song.song_source && song.song_id) {
        ArtistsAPI.getArtistVerifiedBlock(song.song_source, song.song_id)
          .then(block => { if (block) renderArtistVerifiedBlock(block); })
          .catch(err => console.warn('Artist verified block unavailable:', err));
      }
      // Lobby comments -- only mountable when we have a polymorphic (source, id)
      // pair. Songs without a compass/library mapping have no stable id to
      // anchor a thread to, so the section stays hidden.
      if (song.song_source && song.song_id && typeof Comments !== 'undefined') {
        const cmtEl = document.getElementById('song-comments');
        if (cmtEl) {
          cmtEl.hidden = false;
          Comments.mount(cmtEl, {
            targetType: 'song',
            targetSource: song.song_source,
            targetId: song.song_id,
          });
        }
      }
    } catch (err) {
      console.error('Failed to load song:', err);
      // getSong throws Error('API error: 404') for a genuine miss; anything
      // else (network, 5xx, timeout) is a load failure, not a missing song.
      const isMiss = /\b404\b/.test((err && err.message) || '');
      showZeroState(isMiss ? 'notfound' : 'error');
    }
  }

  function formatRecalDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  }

  function renderRecalibrationHistory(recalibrations, resets, corrections) {
    const stamp = document.getElementById('recal-stamp');
    const stampBtn = document.getElementById('recal-stamp-btn');
    const section = document.getElementById('section-history');
    const list = document.getElementById('recal-list');
    if (!stamp || !stampBtn || !section || !list) return;

    resets = resets || [];
    corrections = corrections || [];
    const combined = [
      ...recalibrations.map(r => ({ ...r, _kind: 'recal', _at: r.applied_at })),
      ...resets.map(r => ({ ...r, _kind: 'reset', _at: r.reset_at })),
      ...corrections.map(r => ({ ...r, _kind: 'correction', _at: r.occurred_at })),
    ].sort((a, b) => (b._at || '').localeCompare(a._at || ''));

    if (!combined.length) return;

    const latest = combined[0];
    const dateStr = formatRecalDate(latest._at);
    const count = combined.length;
    const hasReset = resets.length > 0;
    const onlyCorrections = corrections.length > 0 && recalibrations.length === 0 && !hasReset;
    let label;
    if (onlyCorrections) label = 'Corrected';
    else if (hasReset && recalibrations.length === 0) label = 'Reset';
    else label = 'Recalibrated';
    const stampText = count === 1
      ? `${label} ${dateStr}`
      : `${hasReset ? 'Revised' : 'Recalibrated'} ${count} times — most recently ${dateStr}`;

    stampBtn.textContent = stampText + ' \u2014 read the story';
    stamp.hidden = false;

    list.innerHTML = combined.map(r => {
      if (r._kind === 'correction') {
        const beforeColor = r.before && r.before.tier_hex ? r.before.tier_hex : '#888';
        const afterColor = r.after && r.after.tier_hex ? r.after.tier_hex : '#888';
        const beforeChg = r.before && r.before.charge != null ? (r.before.charge > 0 ? '+' : '') + r.before.charge : 'N/A';
        const afterChg = r.after && r.after.charge != null ? (r.after.charge > 0 ? '+' : '') + r.after.charge : 'N/A';
        const beforeLabel = r.before && r.before.tier_label ? r.before.tier_label : '—';
        const afterLabel = r.after && r.after.tier_label ? r.after.tier_label : '—';
        const beforeContam = r.before && r.before.contaminated
          ? '<span class="recal-entry-contam">Contaminated</span>' : '';
        const afterContam = r.after && r.after.contaminated
          ? '<span class="recal-entry-contam">Contaminated</span>' : '';
        const tagsHtml = r.tags
          ? `<div class="recal-entry-tags">${r.tags.split(',').map(t => `<span class="recal-entry-tag">${escapeHtml(t.trim())}</span>`).join('')}</div>`
          : '';
        return `
          <li class="recal-entry recal-entry--correction">
            <div class="recal-entry-head">
              <span class="recal-entry-date">${escapeHtml(formatRecalDate(r.occurred_at))}</span>
              <span class="recal-entry-type">Pre-publish Correction</span>
            </div>
            <div class="recal-entry-shift">
              <span style="color:${beforeColor}">${beforeChg} ${escapeHtml(beforeLabel)}</span>
              ${beforeContam}
              <span class="arrow">&rarr;</span>
              <span style="color:${afterColor}">${afterChg} ${escapeHtml(afterLabel)}</span>
              ${afterContam}
            </div>
            <p class="recal-entry-summary">${escapeHtml(r.human_rationale || '')}</p>
            ${tagsHtml}
          </li>
        `;
      }
      if (r._kind === 'reset') {
        const beforeColor = r.before && r.before.tier_hex ? r.before.tier_hex : '#888';
        const beforeChg = r.before && r.before.charge != null ? (r.before.charge > 0 ? '+' : '') + r.before.charge : 'N/A';
        const beforeLabel = r.before && r.before.tier_label ? r.before.tier_label : '—';
        return `
          <li class="recal-entry recal-entry--reset">
            <div class="recal-entry-head">
              <span class="recal-entry-date">${escapeHtml(formatRecalDate(r.reset_at))}</span>
              <span class="recal-entry-type">Reset</span>
            </div>
            <div class="recal-entry-shift">
              <span style="color:${beforeColor}">${beforeChg} ${escapeHtml(beforeLabel)}</span>
              <span class="arrow">&rarr;</span>
              <span style="color:#888">Uncalibrated</span>
            </div>
            <p class="recal-entry-summary">${escapeHtml(r.reason || '')}</p>
          </li>
        `;
      }
      const beforeColor = r.before && r.before.tier_hex ? r.before.tier_hex : '#888';
      const afterColor = r.after && r.after.tier_hex ? r.after.tier_hex : '#888';
      const beforeChg = r.before && r.before.charge != null ? (r.before.charge > 0 ? '+' : '') + r.before.charge : 'N/A';
      const afterChg = r.after && r.after.charge != null ? (r.after.charge > 0 ? '+' : '') + r.after.charge : 'N/A';
      const beforeLabel = r.before && r.before.tier_label ? r.before.tier_label : '—';
      const afterLabel = r.after && r.after.tier_label ? r.after.tier_label : '—';
      const pipelineLabel = ({
        'manual': 'Manual',
        'rubric_update': 'Rubric Update',
        'satirical_flag': 'Satirical Flag',
        'vibe_gap': 'Vibe Gap',
        'consensus_drift': 'Consensus Drift',
      })[r.pipeline] || r.pipeline || 'Recalibration';
      const lensSuffix = r.lens === 'satire' ? ' · Satire Lens' : '';
      const typeLabel = pipelineLabel + lensSuffix;

      let snapshot = '';
      if (r.flag_count_snapshot) {
        const fc = r.flag_count_snapshot;
        snapshot = `<div class="recal-entry-snapshot">At time of recalibration: ${fc.misread || 0} misread reports, ${fc.satirical || 0} satirical flags.</div>`;
      }

      let rubricChange = '';
      if (r.rubric_change_note) {
        rubricChange = `<div class="recal-entry-rubric-change"><strong>Rubric change:</strong> ${escapeHtml(r.rubric_change_note)}</div>`;
      }

      return `
        <li class="recal-entry">
          <div class="recal-entry-head">
            <span class="recal-entry-date">${escapeHtml(formatRecalDate(r.applied_at))}</span>
            <span class="recal-entry-type">${escapeHtml(typeLabel)}</span>
          </div>
          <div class="recal-entry-shift">
            <span style="color:${beforeColor}">${beforeChg} ${escapeHtml(beforeLabel)}</span>
            <span class="arrow">&rarr;</span>
            <span style="color:${afterColor}">${afterChg} ${escapeHtml(afterLabel)}</span>
          </div>
          <p class="recal-entry-summary">${escapeHtml(r.public_summary || '')}</p>
          ${rubricChange}
          ${snapshot}
        </li>
      `;
    }).join('');

    stampBtn.addEventListener('click', () => {
      section.hidden = !section.hidden;
      if (!section.hidden) {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  }

  // --- Audience Vibe ---

  function getDeviceId() {
    let id = localStorage.getItem('rc-device-id');
    if (!id) {
      id = (crypto && crypto.randomUUID) ? crypto.randomUUID() : ('dev-' + Date.now() + '-' + Math.random().toString(36).slice(2));
      localStorage.setItem('rc-device-id', id);
    }
    return id;
  }

  // Audience vibe = a 21-point window centred on the compass score.
  // Each push moves the audience needle ±1 from compass. The bar represents
  // offset = audience - compass, clamped visually to [-10, +10] (each unit
  // = 5% of bar width). +10 sits on the LEFT (more Ascended direction).
  const AUDIENCE_WINDOW = 10;
  const PCT_PER_UNIT = 50 / AUDIENCE_WINDOW;

  function offsetToPct(offset) {
    const clamped = Math.max(-AUDIENCE_WINDOW, Math.min(AUDIENCE_WINDOW, offset || 0));
    return 50 - clamped * PCT_PER_UNIT;
  }

  function vibeValueToColor(value) {
    if (value >= 60) return 'violet';
    if (value >= 20) return 'blue';
    if (value >= -20) return 'green';
    if (value >= -60) return 'orange';
    return 'red';
  }

  function audienceWindowGradient(compass) {
    // The full charge spectrum has only five pure-color stops: +100 violet,
    // +50 blue, 0 green (centre of Decent), -50 orange, -100 red. Between
    // them the gradient blends. The bar shows a 20-unit window from
    // compass+10 (left, 0%) to compass-10 (right, 100%); each charge unit
    // = 5% of bar width. Stops outside 0-100% are valid — CSS uses them
    // for interpolation across the visible slice.
    if (compass == null || isNaN(compass)) return null;
    const SPECTRUM_STOPS = [
      { charge: 100, color: 'violet' },
      { charge: 50, color: 'blue' },
      { charge: 0, color: 'green' },
      { charge: -50, color: 'orange' },
      { charge: -100, color: 'red' },
    ];
    const parts = SPECTRUM_STOPS.map(s => {
      const pct = ((compass + 10) - s.charge) * 5;
      return `var(--rc-${s.color}) ${pct.toFixed(2)}%`;
    });
    return `linear-gradient(to right, ${parts.join(', ')})`;
  }

  // Module-level state so the frame can recompute on either side updating.
  // compassChargeValue is the song's lyric-reading charge (e.g. +79).
  // audienceCurrentValue is the absolute audience needle from the backend
  // (the offset = audienceCurrentValue - compassChargeValue).
  let compassChargeValue = null;
  let audienceCurrentValue = null;

  function currentOffset() {
    if (compassChargeValue == null || audienceCurrentValue == null) return null;
    return audienceCurrentValue - compassChargeValue;
  }

  function updateGapBand() {
    const band = document.getElementById('audience-vibe-gap');
    if (!band) return;
    const offset = currentOffset();
    if (offset == null) {
      band.classList.add('hidden');
      return;
    }
    band.classList.remove('hidden');
    const compassPct = 50;
    const audiencePct = offsetToPct(offset);
    band.style.left = Math.min(compassPct, audiencePct) + '%';
    band.style.width = Math.abs(compassPct - audiencePct) + '%';
  }

  function placeAudienceMarker(value) {
    // Audience needle = the moving tick + tooltip above the bar. Position is
    // determined by the offset from compass; label displays the absolute
    // score the song would be at if recalibrated to the audience's vibe
    // (push counts live below in the year split).
    audienceCurrentValue = value;
    const offset = compassChargeValue != null ? value - compassChargeValue : value;
    const pct = offsetToPct(offset);
    const marker = document.getElementById('audience-vibe-marker-audience');
    const scoreEl = document.getElementById('audience-vibe-audience-score');
    const tick = document.getElementById('audience-vibe-tick');
    if (marker) marker.style.left = pct + '%';
    if (scoreEl) scoreEl.textContent = (value > 0 ? '+' : '') + value;
    if (tick) {
      tick.classList.remove('hidden');
      tick.style.left = pct + '%';
    }
    updateGapBand();
  }

  function setCompassReference(chargeValue, rubricColor) {
    // Compass = the resting glowing dot + label, anchored at the centre of
    // the 21-point window (50%). The label always reads the absolute charge
    // (e.g. +79); position is fixed regardless of value.
    compassChargeValue = chargeValue;
    const point = document.getElementById('charge-point');
    const marker = document.getElementById('audience-vibe-marker-compass');
    const scoreEl = document.getElementById('audience-vibe-compass-score');
    if (chargeValue == null || isNaN(chargeValue)) {
      if (marker) marker.classList.add('hidden');
      if (point) point.style.display = 'none';
      updateGapBand();
      return;
    }
    // Tint the hero (compass) panel border with the exact spectrum color for
    // this song's integer charge -- not the flat tier color.
    const hero = document.querySelector('.song-section--hero');
    if (hero && typeof Charge !== 'undefined') {
      // Set the same CSS vars the SSR HTML bakes in -- idempotent, so no
      // recolor "pop" when JS runs after the fetch.
      hero.style.setProperty('--charge-color', Charge.spectrumHex(chargeValue));
      hero.style.setProperty('--charge-glow', Charge.spectrumRgba(chargeValue, 0.45));
    }
    if (typeof Charge !== 'undefined') {
      // degree=90 → 50% (centre). Colour comes from the song's rubric tier.
      Charge.setLevel(rubricColor || vibeValueToColor(chargeValue), 0, 0, 90);
    }
    // Replace the default rainbow gradient with one scoped to the window.
    const grad = document.querySelector('#audience-vibe-spectrum-container .charge-gradient');
    const bg = audienceWindowGradient(chargeValue);
    if (grad && bg) grad.style.background = bg;
    if (point) point.style.display = '';
    if (marker) {
      marker.classList.remove('hidden');
      marker.style.left = '50%';
    }
    if (scoreEl) scoreEl.textContent = (chargeValue > 0 ? '+' : '') + chargeValue;
    // Audience marker may already exist; refresh its computed offset.
    if (audienceCurrentValue != null) placeAudienceMarker(audienceCurrentValue);
    updateGapBand();
  }

  function setVoteButtonsDisabled(disabled) {
    ['audience-vibe-push-up', 'audience-vibe-push-agree', 'audience-vibe-push-down']
      .forEach(id => { const b = document.getElementById(id); if (b) b.disabled = disabled; });
  }

  function applyVibeState(state) {
    placeAudienceMarker(state.value);
    // The headline verdict — the score the audience thinks the song should be.
    const headline = document.getElementById('audience-vibe-headline-score');
    if (headline) {
      headline.textContent = (state.value > 0 ? '+' : '') + state.value;
      headline.style.color = COLOR_HEX[vibeValueToColor(state.value)] || '';
    }
    const split = state.year_split || {};
    document.getElementById('audience-vibe-up-count').textContent = split.up || 0;
    const agreeEl = document.getElementById('audience-vibe-agree-count');
    if (agreeEl) agreeEl.textContent = split.agree || 0;
    document.getElementById('audience-vibe-down-count').textContent = split.down || 0;
    document.getElementById('audience-vibe-year-note').textContent = `Votes in ${state.current_year}`;

    const status = document.getElementById('audience-vibe-status');

    if (state.eligible_to_push) {
      setVoteButtonsDisabled(false);
      status.hidden = true;
    } else {
      setVoteButtonsDisabled(true);
      status.className = 'audience-vibe-status';
      status.textContent = `You've already voted on this song in ${state.current_year}. Eligibility refreshes January 1.`;
      status.hidden = false;
    }
  }

  async function initAudienceVibe(song) {
    const section = document.getElementById('section-vibe');
    if (!section) return;
    const deviceId = getDeviceId();
    let state;
    try {
      state = await ArtistsAPI.getVibeState(song.song_source, song.song_id, deviceId);
    } catch (err) {
      console.warn('Vibe state unavailable:', err);
      return;
    }
    section.hidden = false;
    // Drop the loading state — frame becomes interactive, spinner hides.
    const frame = document.getElementById('audience-vibe-spectrum-frame');
    const loadingRow = document.getElementById('audience-vibe-loading-row');
    if (frame) frame.classList.remove('is-loading');
    if (loadingRow) loadingRow.hidden = true;
    if (typeof Charge !== 'undefined') Charge.render('audience-vibe-spectrum-container');
    // Inject a gap band (sits behind) and an audience tick (sits on top)
    // into the gradient. The glowing dot stays as the compass reference;
    // the tick moves with the audience vibe; the band visualises the gap.
    const grad = document.querySelector('#audience-vibe-spectrum-container .charge-gradient');
    if (grad) {
      grad.style.position = grad.style.position || 'relative';
      if (!document.getElementById('audience-vibe-gap')) {
        const band = document.createElement('div');
        band.id = 'audience-vibe-gap';
        band.className = 'audience-vibe-gap hidden';
        grad.appendChild(band);
      }
      if (!document.getElementById('audience-vibe-tick')) {
        const tick = document.createElement('div');
        tick.id = 'audience-vibe-tick';
        tick.className = 'audience-vibe-tick hidden';
        grad.appendChild(tick);
      }
    }
    renderAmphitheater(document.getElementById('audience-amphitheater'), song.rubric_color);
    setCompassReference(song.charge_value, song.rubric_color);
    applyVibeState(state);

    const handler = async (e) => {
      const direction = parseInt(e.currentTarget.dataset.direction, 10);
      const status = document.getElementById('audience-vibe-status');
      setVoteButtonsDisabled(true);
      status.className = 'audience-vibe-status audience-vibe-status--pending';
      status.innerHTML = '<span class="audience-vibe-status-spinner" aria-hidden="true"></span>Recording your vote\u2026';
      status.hidden = false;
      try {
        const updated = await ArtistsAPI.pushVibe(song.song_source, song.song_id, direction, deviceId);
        applyVibeState(updated);
        status.className = 'audience-vibe-status success';
        const score = `${updated.value > 0 ? '+' : ''}${updated.value}`;
        status.textContent = direction === 0
          ? `Vote recorded. The audience reads the song at ${score}.`
          : `Vote recorded. The audience now reads the song at ${score}.`;
        status.hidden = false;
      } catch (err) {
        status.className = 'audience-vibe-status error';
        status.textContent = err.message || 'Vote failed.';
        status.hidden = false;
        // Re-fetch in case server already counted us.
        try {
          const fresh = await ArtistsAPI.getVibeState(song.song_source, song.song_id, deviceId);
          applyVibeState(fresh);
        } catch {}
      }
    };
    ['audience-vibe-push-up', 'audience-vibe-push-agree', 'audience-vibe-push-down']
      .forEach(id => { const b = document.getElementById(id); if (b) b.addEventListener('click', handler); });
  }

  // Amphitheater motif \u2014 curved tiers of seat dots standing in for the crowd.
  // Loose accent + symbolism (not a data viz): the same procedural-SVG approach
  // the tenets organ uses, bent into amphitheater seating. Seats tint along the
  // charge spectrum so the arc echoes the bar beneath it; the song's own tier
  // colour gets a brighter highlight band near the centre.
  function renderAmphitheater(svg, rubricColor) {
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const W = 600, H = 150;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const cx = W / 2;
    const cy = H + 46;            // arc centre sits below the frame -> shallow curve
    const TIERS = 5;
    const baseR = 92;
    const tierGap = 13;
    const SPECTRUM = ['#aa54ff', '#3388ff', '#33cc55', '#ffbb33', '#ff3333'];
    const highlight = COLOR_HEX[rubricColor] || 'var(--rc-accent)';
    // Spread seats across a ~150deg fan, denser rows further back.
    const startA = Math.PI * (1 - 5 / 6);   // 30deg
    const endA = Math.PI * (5 / 6);         // 150deg
    for (let t = 0; t < TIERS; t++) {
      const r = baseR + t * tierGap;
      const seats = 22 + t * 4;
      const group = document.createElementNS(SVG_NS, 'g');
      group.setAttribute('opacity', String(0.22 + t * 0.07));
      for (let s = 0; s < seats; s++) {
        const a = startA + (endA - startA) * (s / (seats - 1));
        const x = cx + r * Math.cos(a);
        const y = cy - r * Math.sin(a);
        const dot = document.createElementNS(SVG_NS, 'circle');
        dot.setAttribute('cx', x.toFixed(1));
        dot.setAttribute('cy', y.toFixed(1));
        dot.setAttribute('r', '2.4');
        // Colour by horizontal position to mirror the charge spectrum band.
        const frac = (x / W);
        const idx = Math.max(0, Math.min(SPECTRUM.length - 1, Math.round(frac * (SPECTRUM.length - 1))));
        // Centre-front seats glow in the song's tier colour.
        const central = Math.abs(frac - 0.5) < 0.16 && t >= TIERS - 2;
        dot.setAttribute('fill', central ? highlight : SPECTRUM[idx]);
        group.appendChild(dot);
      }
      svg.appendChild(group);
    }
  }

  function renderCalibrationRuns(runs, consensus) {
    const section = document.getElementById('section-runs');
    const intro = document.getElementById('runs-intro');
    const consensusEl = document.getElementById('runs-consensus');
    const list = document.getElementById('runs-list');
    if (!section || !intro || !consensusEl || !list) return;
    intro.classList.remove('is-loading');
    if (!runs.length) {
      intro.textContent = 'No calibration runs yet.';
      return;
    }
    section.hidden = false;

    const activeRuns = runs.filter(r => !r.superseded);
    const supersededCount = runs.length - activeRuns.length;
    const count = activeRuns.length;
    let introText = count === 1
      ? 'This song has been calibrated once. Each submission re-runs the agent and logs the reading — over time, repeated calibrations refine the compass.'
      : `This song has been calibrated ${count} times. Each reading re-runs the agent and adds to the consensus — the canonical calibration drifts toward the confidence-weighted mean as runs accumulate.`;
    if (supersededCount > 0) {
      introText += ` ${supersededCount} earlier reading${supersededCount === 1 ? ' is' : 's are'} superseded — shown below but excluded from the consensus because the rubric changed.`;
    }
    intro.textContent = introText;

    if (consensus && count >= 2) {
      const cColor = COLOR_HEX[consensus.rubric_color] || '#999';
      const cLabel = CHARGE_LABELS[consensus.rubric_color] || '';
      const cCharge = consensus.charge_value;
      const cSign = cCharge >= 0 ? '+' : '';
      consensusEl.innerHTML = `
        <div class="runs-consensus-row">
          <span class="runs-consensus-label">Consensus across ${consensus.run_count} runs</span>
          <span class="runs-consensus-tier" style="color:${cColor}">${escapeHtml(cLabel)} ${cSign}${cCharge}</span>
        </div>
      `;
    } else {
      consensusEl.innerHTML = '';
    }

    list.innerHTML = runs.map((r, i) => {
      const color = r.tier_hex || '#888';
      const label = r.tier_label || '—';
      const charge = r.charge_value != null ? (r.charge_value > 0 ? '+' : '') + r.charge_value : '—';
      const conf = r.confidence != null ? (r.confidence * 100).toFixed(0) + '%' : null;
      const trigger = triggerLabel(r.triggered_by);
      const date = formatRecalDate(r.run_at);
      const runLabel = `Run ${runs.length - i}`;
      const contamNote = r.contaminated && r.contamination_note ? r.contamination_note : null;
      const superseded = r.superseded;
      const supersededTag = superseded
        ? `<span class="runs-entry-superseded" title="${escapeHtml(r.superseded_reason || '')}">SUPERSEDED</span>`
        : '';
      return `
        <li class="runs-entry${superseded ? ' runs-entry--superseded' : ''}">
          <div class="runs-entry-head">
            <span class="runs-entry-num">${escapeHtml(runLabel)}</span>
            <span class="runs-entry-date">${escapeHtml(date)}</span>
            <span class="runs-entry-trigger">${escapeHtml(trigger)}</span>
            ${supersededTag}
          </div>
          <div class="runs-entry-reading">
            <span style="color:${color};font-weight:700">${escapeHtml(label)}</span>
            <span style="color:${color}" class="runs-entry-charge">${charge}</span>
            ${conf ? `<span class="runs-entry-conf">confidence ${conf}</span>` : ''}
          </div>
          ${r.charge_summary ? `<p class="runs-entry-summary">${escapeHtml(r.charge_summary)}</p>` : ''}
          ${contamNote ? `<p class="runs-entry-contam"><strong>Contamination:</strong> ${escapeHtml(contamNote)}</p>` : ''}
        </li>
      `;
    }).join('');
  }

  function triggerLabel(t) {
    if (!t) return 'Agent run';
    const map = {
      'lyrical_charger': 'Lyrical Charger',
      'lyrical_charger_search': 'Lyrical Charger',
      'cl_stream': 'CL Stream',
      'compass_manual': 'Compass (manual)',
      'compass_daily': 'Daily reading',
      'seed': 'Initial calibration',
    };
    return map[t] || t;
  }

  function youtubeId(url) {
    if (!url) return null;
    const m = url.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{6,})/);
    return m ? m[1] : null;
  }

  function vimeoId(url) {
    if (!url) return null;
    const m = url.match(/vimeo\.com\/(?:video\/)?(\d+)/);
    return m ? m[1] : null;
  }

  function isDirectAudio(url) {
    if (!url) return false;
    return /\.(mp3|wav|ogg|m4a|aac|flac)(\?.*)?$/i.test(url);
  }

  function renderArtistClaimCta(song) {
    const link = document.getElementById('artist-claim-link');
    const section = document.getElementById('section-artist-claim');
    if (!link || !section) return;
    const params = new URLSearchParams({
      title: song.title || '',
      artist: song.artist || '',
      song_source: song.song_source || '',
      song_id: String(song.song_id || ''),
      color: song.rubric_color || 'green',
    });
    link.href = `/artist-claim.html?${params.toString()}`;
    section.hidden = false;
  }

  function renderArtistVerifiedBlock(block) {
    const section = document.getElementById('section-artist-verified');
    const attribution = document.getElementById('av-attribution');
    const content = document.getElementById('av-block-content');
    const infoBtn = document.getElementById('av-info-btn');
    const popover = document.getElementById('av-info-popover');
    if (!section || !content || !attribution) return;

    const artistName = block.artist_name || 'the artist';
    const artistLink = block.artist_slug
      ? `<a href="/artists/${encodeURIComponent(block.artist_slug)}" class="accent-link">${escapeHtml(artistName)}</a>`
      : escapeHtml(artistName);
    attribution.innerHTML = `Captured directly from <strong>${artistLink}</strong>.`;

    let html = '';
    if (block.block_text) {
      html += block.block_text
        .split(/\n{2,}/)
        .map(p => p.trim())
        .filter(Boolean)
        .map(p => `<p>${escapeHtml(p)}</p>`)
        .join('');
    }

    let mediaHtml = '';
    if (block.video_url) {
      const yt = youtubeId(block.video_url);
      const vm = vimeoId(block.video_url);
      if (yt) {
        mediaHtml += `<div class="av-video-embed"><iframe src="https://www.youtube.com/embed/${encodeURIComponent(yt)}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>`;
      } else if (vm) {
        mediaHtml += `<div class="av-video-embed"><iframe src="https://player.vimeo.com/video/${encodeURIComponent(vm)}" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe></div>`;
      } else {
        mediaHtml += `<a class="av-video-link" href="${escapeHtml(block.video_url)}" target="_blank" rel="noopener">Watch the artist's video &rarr;</a>`;
      }
    }
    if (block.audio_url) {
      if (isDirectAudio(block.audio_url)) {
        mediaHtml += `<audio class="av-audio-player" controls preload="none" src="${escapeHtml(block.audio_url)}">Your browser does not support audio playback.</audio>`;
      } else {
        mediaHtml += `<a class="av-audio-link" href="${escapeHtml(block.audio_url)}" target="_blank" rel="noopener">Listen to the artist &rarr;</a>`;
      }
    }
    if (mediaHtml) html += `<div class="av-media-wrap">${mediaHtml}</div>`;

    content.innerHTML = html;
    section.hidden = false;

    if (infoBtn && popover && !infoBtn.dataset.bound) {
      infoBtn.dataset.bound = '1';
      infoBtn.addEventListener('click', () => {
        popover.hidden = !popover.hidden;
      });
    }

    // Once a verified block is published, the inbound 'are you the artist?'
    // CTA is redundant on this song — hide it.
    const claimSection = document.getElementById('section-artist-claim');
    if (claimSection) claimSection.hidden = true;
  }

  function renderFlagCounts(song, counts) {
    const section = document.getElementById('section-flags');
    if (!section) return;

    const misread = Number(counts.misread) || 0;
    const satirical = Number(counts.satirical) || 0;
    const total = misread + satirical;

    const intro = total === 0
      ? 'No flags filed on this song yet. Disagree? You can be the first to file one.'
      : `The public has filed ${total} flag${total === 1 ? '' : 's'} on this song. We show every one — the compass aims to be a mirror, not a megaphone.`;
    const introEl = document.getElementById('flags-intro');
    introEl.classList.remove('is-loading');
    introEl.textContent = intro;

    const countsEl = document.getElementById('flag-counts');
    countsEl.innerHTML = `
      <div class="flag-count misread">
        <span class="flag-count-num">${misread}</span>
        <span class="flag-count-label">Misread Reports</span>
      </div>
      <div class="flag-count satirical">
        <span class="flag-count-num">${satirical}</span>
        <span class="flag-count-label">Satirical Flags</span>
      </div>
    `;

    const link = document.getElementById('flag-link');
    if (link) {
      const params = new URLSearchParams({
        title: song.title || '',
        artist: song.artist || '',
        color: song.rubric_color || 'green',
      });
      if (song.charge_summary) params.set('cs', song.charge_summary);
      link.href = `/misread-submission.html?${params.toString()}`;
    }

    section.hidden = false;
  }

  // A small floating status toast for the charge-card download. Lazily created,
  // reused across clicks. Plain text only (we control every message).
  function showCardToast(message, opts) {
    opts = opts || {};
    let toast = document.getElementById('charge-card-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'charge-card-toast';
      toast.className = 'charge-card-toast';
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
      document.body.appendChild(toast);
    }
    toast.classList.toggle('charge-card-toast--error', !!opts.error);
    toast.innerHTML = '';
    if (opts.spinner) {
      const sp = document.createElement('span');
      sp.className = 'charge-card-toast-spinner';
      sp.setAttribute('aria-hidden', 'true');
      toast.appendChild(sp);
    }
    const txt = document.createElement('span');
    txt.textContent = message;
    toast.appendChild(txt);
    // Force reflow so the transition runs even on a reused node.
    void toast.offsetWidth;
    toast.classList.add('is-visible');
    clearTimeout(toast._hideTimer);
    if (opts.autohideMs) {
      toast._hideTimer = setTimeout(() => toast.classList.remove('is-visible'), opts.autohideMs);
    }
  }

  // Wire the "Get Charge Card" button. Renders the rc-charge-card via the
  // shared charge card generator (window.RCChargeCard) from this song's
  // calibration -- compass-branded (no Lyrical Charger verbiage) -- and
  // downloads it as a PNG, with a smooth status toast on desktop + mobile.
  function wireChargeCard(song, isUncalibrated, color, tierLabel) {
    const btn = document.getElementById('charge-card-btn');
    if (!btn) return;
    if (isUncalibrated || !window.RCChargeCard) {
      btn.hidden = true;
      return;
    }
    btn.hidden = false;

    // Map the song-detail shape onto the card's expected data shape. The card
    // keys off `tier` (rubric_color) and `charge` (charge_value).
    const cardData = {
      tier: song.rubric_color,
      tier_label: song.tier_label || tierLabel,
      charge: song.charge_value,
      charge_summary: song.charge_summary || '',
      deadpan_line: song.deadpan_line || '',
      title: song.title || '',
      artist: song.artist || '',
      topics: Array.isArray(song.topics) ? song.topics : [],
    };

    btn.onclick = async () => {
      const canvas = document.getElementById('charge-card-canvas');
      if (!canvas) return;
      btn.disabled = true;
      showCardToast('Downloading now…', { spinner: true });
      const startedAt = Date.now();
      try {
        const cardOpts = { brand: 'compass' };
        await window.RCChargeCard.render(cardData, canvas, cardOpts);
        await window.RCChargeCard.shareOrDownload(canvas, cardData, true, cardOpts);
        // Hold the "Downloading now" state briefly so it doesn't flash past on
        // fast machines, then confirm.
        const elapsed = Date.now() - startedAt;
        if (elapsed < 650) await new Promise(r => setTimeout(r, 650 - elapsed));
        showCardToast('Check your downloads', { autohideMs: 4000 });
        announce('Charge card downloaded — check your downloads');
      } catch (_) {
        showCardToast('Couldn’t make the card — try again', { error: true, autohideMs: 4000 });
      }
      btn.disabled = false;
    };
  }

  function renderSong(song) {
    const isUncalibrated = !!song.uncalibrated || song.charge_value == null;
    // Released, but its lyrics are genuinely unobtainable, so it carries no
    // reading by design (distinct from a reset/uncalibrated song awaiting a
    // re-read). Labeled honestly rather than shown as a generic "uncalibrated".
    const isLyricsUnavailable = !!song.lyrics_unavailable;
    const color = COLOR_HEX[song.rubric_color] || '#999';
    const tierLabel = CHARGE_LABELS[song.rubric_color] || '';
    const chargeDisplay = song.charge_value != null
      ? (song.charge_value > 0 ? '+' : '') + song.charge_value
      : 'N/A';

    // SEO/GEO tagline — the song/artist string that should saturate the page.
    // Embedded in <title>, meta, og:*, JSON-LD, and every section H2 so the
    // page reads as the natural-language query a listener or LLM would search:
    // 'what does listening to "X" by Y do to the listener'.
    const tagline = song.artist
      ? `"${song.title}" by ${song.artist}`
      : `"${song.title}"`;

    // Page meta — GEO framing: the page answers "what is X about?".
    const pageTitle = `What is ${tagline} about? — The Rising Compass`;
    document.title = pageTitle;
    const pageTitleEl = document.getElementById('page-title');
    if (pageTitleEl) pageTitleEl.textContent = pageTitle;

    const meaningLead = `This page answers what ${tagline} is about — the meaning behind the lyrics`;
    const summaryLine = isLyricsUnavailable
      ? `${meaningLead}. This song is released but its lyrics are not available to read, so The Rising Compass carries no reading for it.`
      : isUncalibrated
      ? `${meaningLead}. Currently uncalibrated by The Rising Compass; previous reasoning shown below.`
      : song.charge_summary
        ? `${meaningLead}: ${song.charge_summary}`
        : `${meaningLead}, calibrated ${tierLabel} (${chargeDisplay}) by The Rising Compass on a 58-tenet rubric.`;
    document.getElementById('meta-description').content = summaryLine;
    document.getElementById('og-title').content = pageTitle;
    document.getElementById('og-description').content = summaryLine;

    // GEO H1 — the natural-language question, above the summary.
    const questionEl = document.getElementById('song-question');
    if (questionEl) questionEl.textContent = `What is ${tagline} about?`;

    // Canonical + og:url — use the slug-based URL so social shares and
    // crawlers don't dedupe to /songs/song.html.
    if (song.slug) {
      const canonicalUrl = `https://risingcompass.net/songs/${song.slug}`;
      const ogUrlEl = document.getElementById('og-url');
      if (ogUrlEl) ogUrlEl.content = canonicalUrl;
      const canonicalEl = document.getElementById('canonical-link');
      if (canonicalEl) canonicalEl.href = canonicalUrl;
    }

    // Section headings — every H2 inherits the tagline so the page is dense
    // with the song/artist string for search + generative engines.
    const setH2 = (sectionId, text) => {
      const h2 = document.querySelector(`#${sectionId} h2`);
      if (h2) h2.textContent = text;
    };
    setH2('section-summary', `Summary of ${tagline}`);
    setH2('section-listener-effects', `What Might Listening to ${tagline} Do to the Listener?`);
    setH2('section-societal-effects', `What Might Listening to ${tagline} Do to a Society?`);
    setH2('section-history', `Calibration Log for ${tagline}`);
    setH2('section-vibe', `Audience Vibe on ${tagline}`);
    setH2('section-runs', `Calibration Runs for ${tagline}`);
    setH2('section-flags', `Flag Activity on ${tagline}`);
    setH2('section-about', `How Is ${tagline} Calibrated?`);
    setH2('section-artist-claim', `Are You the Artist of ${tagline}?`);

    // Hero
    document.getElementById('song-title').textContent = song.title;
    const artistEl = document.getElementById('song-artist');
    if (song.artist_slug) {
      artistEl.innerHTML = `<a href="/artists/${encodeURIComponent(song.artist_slug)}" class="accent-link">${escapeHtml(song.artist)}</a>`;
    } else {
      artistEl.textContent = song.artist || '';
    }

    // Origin chart — the chart this song first surfaced on (Build 7). Present
    // only for chart-born songs; a pure Lyrical Charger / terminal birth has no
    // chart origin, so the line stays hidden.
    const originEl = document.getElementById('song-origin-chart');
    if (originEl) {
      if (song.origin_chart_label) {
        originEl.textContent = `First surfaced on ${song.origin_chart_label}`;
        originEl.hidden = false;
      } else {
        originEl.hidden = true;
      }
    }

    // Tier badge + compass gauge. Calibrated songs get the gauge (which already
    // shows the score + tier), so the text badge is suppressed; uncalibrated
    // songs keep the simple "Uncalibrated" pill and no gauge.
    const badge = document.getElementById('song-tier-badge');
    const compassWrap = document.getElementById('song-compass-container');
    if (isUncalibrated) {
      const badgeLabel = isLyricsUnavailable ? 'Lyrics unavailable' : 'Uncalibrated';
      badge.innerHTML = `<span class="badge-tier badge-tier--uncalibrated">${badgeLabel}</span>`;
      if (compassWrap) compassWrap.hidden = true;
    } else {
      badge.innerHTML = '';
      badge.hidden = true;
      if (compassWrap && typeof Compass !== 'undefined') {
        compassWrap.hidden = false;
        Compass.render('song-compass-container');
        // score -> degree: score = (90 - degree)*100/90  =>  degree = 90 - score*0.9
        const degree = 90 - (Number(song.charge_value) || 0) * 0.9;
        Compass.setDegree(degree, song.rubric_color || vibeValueToColor(song.charge_value));
      } else {
        // Fallback if the gauge script is unavailable.
        badge.hidden = false;
        badge.innerHTML = `
          <span class="badge-charge" style="color:${color}">${chargeDisplay}</span>
          <span class="badge-tier" style="background:${color}20;color:${color}">${escapeHtml(tierLabel)}</span>
        `;
      }
    }

    // rc-charge-card — same charge card the Lyrical Charger offers after a
    // reading, made available on every calibrated song's page. Hidden for
    // uncalibrated songs (no charge to render).
    wireChargeCard(song, isUncalibrated, color, tierLabel);

    // Section 2: Song-specific summary
    const summarySection = document.getElementById('section-summary');
    summarySection.hidden = false;
    const summaryText = document.getElementById('summary-text');
    summaryText.classList.remove('is-loading');
    summaryText.textContent = isLyricsUnavailable
      ? `${tagline} is released, but its lyrics are not available to read on any source, so The Rising Compass carries no reading for it. If the lyrics surface, it will be calibrated like any other song.`
      : isUncalibrated
      ? `${tagline} is currently uncalibrated. See the history section below for the reasoning behind the most recent reset.`
      : song.charge_summary || `${tagline} is calibrated as ${tierLabel} by The Rising Compass.`;

    // Section 3: Effects — per-song prose if available, else tier-generic fallback.
    const effectsSection = document.getElementById('section-listener-effects');
    effectsSection.hidden = !isUncalibrated ? false : true;
    if (!isUncalibrated) {
      const proseHtml = song.listener_effects_prose
        ? song.listener_effects_prose
            .split(/\n{2,}/)
            .map(p => p.trim())
            .filter(Boolean)
            .map(p => `<p>${escapeHtml(p)}</p>`)
            .join('')
        : `<p>${TIER_EFFECTS[song.rubric_color] || ''}</p>`;
      const effectsEl = document.getElementById('listener-effects-prose');
      effectsEl.classList.remove('is-loading');
      effectsEl.innerHTML = proseHtml;
    }

    // Section 3b: Societal Effects — always shown alongside the listener read
    // for calibrated songs. A placeholder stands in when no song-specific
    // society-scale prose exists yet (no tier-generic fallback by design — a
    // generic per-tier read would defeat the point of a society diagnosis).
    const societalSection = document.getElementById('section-societal-effects');
    const societalEl = document.getElementById('societal-effects-prose');
    societalSection.hidden = isUncalibrated;
    if (!isUncalibrated) {
      societalEl.classList.remove('is-loading');
      if (song.societal_effects_prose) {
        societalEl.classList.remove('prose--placeholder');
        societalEl.innerHTML = song.societal_effects_prose
          .split(/\n{2,}/)
          .map(p => p.trim())
          .filter(Boolean)
          .map(p => `<p>${escapeHtml(p)}</p>`)
          .join('');
      } else {
        societalEl.classList.add('prose--placeholder');
        societalEl.innerHTML = `<p>A society-scale reading for ${escapeHtml(tagline)} hasn't been generated yet. When it is, it will appear here — what running this song's program across a whole culture would tend to do.</p>`;
      }
    }

    // Contamination — now a hazard badge on the compass (hero), mirroring the
    // homepage panel. Shown only for calibrated, contaminated songs; the
    // contamination note becomes the tooltip.
    const contamBadge = document.getElementById('song-contam-badge');
    if (contamBadge) {
      if (!isUncalibrated && song.contaminated) {
        const tip = document.getElementById('song-contam-tooltip');
        if (tip) {
          tip.innerHTML = `<strong>Contaminated.</strong> ${escapeHtml(
            song.contamination_note ||
            `${tagline} carries content that undercuts its higher-tier substance.`
          )}`;
        }
        contamBadge.hidden = false;
      } else {
        contamBadge.hidden = true;
      }
    }

    // Dogma hazard-style badge on the hero — parallel to the contamination
    // badge. The fuller Dogma Reference section below still carries the note.
    const dogmaBadge = document.getElementById('song-dogma-badge');
    if (dogmaBadge) {
      if (!isUncalibrated && song.dogma_referenced) {
        const dtip = document.getElementById('song-dogma-tooltip');
        if (dtip) {
          dtip.innerHTML = `<strong>Dogma referenced.</strong> ${escapeHtml(
            song.dogma_note ||
            `${tagline} invokes a specific doctrinal framework. This tag is metadata only — it does not affect the charge.`
          )}`;
        }
        dogmaBadge.hidden = false;
      } else {
        dogmaBadge.hidden = true;
      }
    }

    // Translated — neutral provenance badge (metadata only, never affects the
    // charge). Set manually; stacks below the dogma badge when both show.
    const translatedBadge = document.getElementById('song-translated-badge');
    if (translatedBadge) {
      if (!isUncalibrated && song.translated) {
        const ttip = document.getElementById('song-translated-tooltip');
        if (ttip) {
          ttip.innerHTML = `<strong>Translated.</strong> ${escapeHtml(
            `Calibrated from a translation of the original non-English lyrics. This tag is metadata only — it does not affect the charge.`
          )}`;
        }
        translatedBadge.classList.toggle('stacked', !isUncalibrated && !!song.dogma_referenced);
        translatedBadge.hidden = false;
      } else {
        translatedBadge.hidden = true;
      }
    }

    // Medley - neutral provenance badge (metadata only, never affects the
    // charge). Marks a calibration that reads a curated multi-song medley as
    // one arc. Stacks below dogma and translated when those also show.
    const medleyBadge = document.getElementById('song-medley-badge');
    if (medleyBadge) {
      if (!isUncalibrated && song.medley) {
        const ttip = document.getElementById('song-medley-tooltip');
        if (ttip) {
          ttip.innerHTML = `<strong>Medley.</strong> ${escapeHtml(
            `This reading calibrates a curated multi-song medley as a single arc, not one authored song. Metadata only, it does not affect the charge.`
          )}`;
        }
        const above = (!isUncalibrated && !!song.dogma_referenced ? 1 : 0) + (!isUncalibrated && !!song.translated ? 1 : 0);
        medleyBadge.classList.toggle('stacked', above === 1);
        medleyBadge.classList.toggle('stacked2', above === 2);
        medleyBadge.hidden = false;
      } else {
        medleyBadge.hidden = true;
      }
    }

    // Section 3b: Dogma Reference — only surfaces when the tag fired.
    const dogmaSection = document.getElementById('section-dogma');
    if (!isUncalibrated && song.dogma_referenced) {
      dogmaSection.hidden = false;
      setH2('section-dogma', `Dogma Reference in ${tagline}`);
      document.getElementById('dogma-answer').textContent =
        song.dogma_note || `${tagline} references a specific doctrinal framework.`;
    } else {
      dogmaSection.hidden = true;
    }

    // (Calibration Details section removed — the compass gauge in the hero
    // now carries tier + charge.)

    // Section 5: About
    document.getElementById('section-about').hidden = false;

    // JSON-LD
    injectJsonLd(song);
  }

  function injectJsonLd(song) {
    const el = document.getElementById('json-ld');
    if (!el) return;

    const color = song.rubric_color;
    const tierLabel = CHARGE_LABELS[color] || '';

    const jsonLd = {
      '@context': [
        'https://schema.org',
        {
          rc: 'https://risingcompass.net/schema/',
          lyricalCharge: 'rc:LyricalCharge',
          chargeTier: 'rc:ChargeTier',
          chargeValue: 'rc:chargeValue',
          chargeSummary: 'rc:chargeSummary',
          contaminated: 'rc:contaminated',
          contaminationNote: 'rc:contaminationNote',
        },
      ],
      '@type': 'MusicRecording',
      name: song.title,
      url: `https://risingcompass.net/songs/${song.slug}`,
      byArtist: {
        '@type': 'MusicGroup',
        name: song.artist,
      },
      review: {
        '@type': 'Rating',
        author: {
          '@type': 'Organization',
          name: 'Rising Compass',
          url: 'https://risingcompass.net',
        },
        ratingValue: song.charge_value,
        bestRating: 100,
        worstRating: -100,
        ratingExplanation: song.charge_summary || '',
      },
      additionalProperty: [
        { '@type': 'PropertyValue', propertyID: 'RisingCompassTier', name: 'Rising Compass Calibration', value: tierLabel },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassTierColor', name: 'Rising Compass Tier Color', value: color },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassCharge', name: 'Rising Compass Charge Value', value: song.charge_value, minValue: -100, maxValue: 100, unitText: 'charge' },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassSummary', name: 'Rising Compass Charge Summary', value: song.charge_summary || '' },
        { '@type': 'PropertyValue', propertyID: 'RisingCompassContaminated', name: 'Rising Compass Contamination Flag', value: song.contaminated },
      ],
      lyricalCharge: {
        '@type': 'rc:LyricalCharge',
        chargeTier: tierLabel,
        tierColor: color,
        chargeValue: song.charge_value,
        chargeSummary: song.charge_summary || '',
        contaminated: song.contaminated,
        contaminationNote: song.contamination_note || null,
        calibratedBy: {
          '@type': 'Organization',
          name: 'Rising Compass',
          url: 'https://risingcompass.net',
        },
      },
    };

    el.textContent = JSON.stringify(jsonLd);
  }

  init();
})();
