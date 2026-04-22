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

  async function init() {
    // Clean URL: /songs/<slug>. Fall back to ?slug=<slug> for legacy links.
    const params = new URLSearchParams(window.location.search);
    let slug = params.get('slug');
    if (!slug) {
      const m = window.location.pathname.match(/^\/songs\/([^/]+)\/?$/);
      if (m && m[1] !== 'song.html') slug = decodeURIComponent(m[1]);
    }
    if (!slug) return;

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
    } catch (err) {
      console.error('Failed to load song:', err);
      document.getElementById('song-title').textContent = '';
      document.getElementById('not-found').hidden = false;
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

  function placeNeedleDot(value) {
    // Map -100..+100 to x=20..300 across the SVG axis.
    const clamped = Math.max(-100, Math.min(100, value || 0));
    const x = 20 + ((clamped + 100) / 200) * 280;
    const dot = document.getElementById('vibe-needle-dot');
    const lbl = document.getElementById('vibe-needle-label');
    if (dot) dot.setAttribute('cx', x);
    if (lbl) {
      lbl.setAttribute('x', x);
      lbl.textContent = (clamped > 0 ? '+' : '') + clamped;
    }
  }

  function applyVibeState(state) {
    placeNeedleDot(state.value);
    document.getElementById('vibe-down-count').textContent = state.year_split.down;
    document.getElementById('vibe-up-count').textContent = state.year_split.up;
    document.getElementById('vibe-year-note').textContent = `Pushes in ${state.current_year}`;

    const upBtn = document.getElementById('vibe-push-up');
    const downBtn = document.getElementById('vibe-push-down');
    const status = document.getElementById('vibe-status');

    if (state.eligible_to_push) {
      upBtn.disabled = false;
      downBtn.disabled = false;
      status.hidden = true;
    } else {
      upBtn.disabled = true;
      downBtn.disabled = true;
      status.className = 'vibe-status';
      status.textContent = `You've already pushed this song in ${state.current_year}. Eligibility refreshes January 1.`;
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
    applyVibeState(state);

    const handler = async (e) => {
      const direction = parseInt(e.currentTarget.dataset.direction, 10);
      const status = document.getElementById('vibe-status');
      document.getElementById('vibe-push-up').disabled = true;
      document.getElementById('vibe-push-down').disabled = true;
      status.className = 'vibe-status vibe-status--pending';
      status.innerHTML = '<span class="vibe-status-spinner" aria-hidden="true"></span>Pushing\u2026';
      status.hidden = false;
      try {
        const updated = await ArtistsAPI.pushVibe(song.song_source, song.song_id, direction, deviceId);
        applyVibeState(updated);
        status.className = 'vibe-status success';
        status.textContent = `Push recorded. The needle moved to ${updated.value > 0 ? '+' : ''}${updated.value}.`;
        status.hidden = false;
      } catch (err) {
        status.className = 'vibe-status error';
        status.textContent = err.message || 'Push failed.';
        status.hidden = false;
        // Re-fetch in case server already counted us.
        try {
          const fresh = await ArtistsAPI.getVibeState(song.song_source, song.song_id, deviceId);
          applyVibeState(fresh);
        } catch {}
      }
    };
    document.getElementById('vibe-push-up').addEventListener('click', handler);
    document.getElementById('vibe-push-down').addEventListener('click', handler);
  }

  function renderCalibrationRuns(runs, consensus) {
    const section = document.getElementById('section-runs');
    const intro = document.getElementById('runs-intro');
    const consensusEl = document.getElementById('runs-consensus');
    const list = document.getElementById('runs-list');
    if (!section || !intro || !consensusEl || !list) return;
    if (!runs.length) return;
    section.hidden = false;

    const activeRuns = runs.filter(r => !r.superseded);
    const supersededCount = runs.length - activeRuns.length;
    const count = activeRuns.length;
    let introText = count === 1
      ? 'This song has been calibrated once. Each submission re-runs the agent and logs the reading — over time, repeated calibrations refine the compass.'
      : `This song has been calibrated ${count} times. Each reading re-runs the agent and adds to the consensus — the canonical classification drifts toward the confidence-weighted mean as runs accumulate.`;
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

  function renderFlagCounts(song, counts) {
    const section = document.getElementById('section-flags');
    if (!section) return;

    const misread = Number(counts.misread) || 0;
    const satirical = Number(counts.satirical) || 0;
    const total = misread + satirical;

    const intro = total === 0
      ? 'No flags filed on this song yet. Disagree? You can be the first to file one.'
      : `The public has filed ${total} flag${total === 1 ? '' : 's'} on this song. We show every one — the compass aims to be a mirror, not a megaphone.`;
    document.getElementById('flags-intro').textContent = intro;

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

  function renderSong(song) {
    const isUncalibrated = !!song.uncalibrated || song.charge_value == null;
    const color = COLOR_HEX[song.rubric_color] || '#999';
    const tierLabel = CHARGE_LABELS[song.rubric_color] || '';
    const chargeDisplay = song.charge_value != null
      ? (song.charge_value > 0 ? '+' : '') + song.charge_value
      : 'N/A';

    // Page meta
    document.getElementById('page-title').textContent =
      `What Might "${song.title}" Do to the Listener? — The Rising Compass`;
    document.getElementById('meta-description').content = isUncalibrated
      ? `${song.title} by ${song.artist}: currently uncalibrated. Previous calibrations and reasoning shown below.`
      : song.charge_summary || `${song.title} by ${song.artist}: ${tierLabel} (${chargeDisplay}). Lyrical effects classified by The Rising Compass.`;
    document.getElementById('og-title').content =
      `"${song.title}" by ${song.artist} — Lyrical Effects Label`;
    document.getElementById('og-description').content = isUncalibrated
      ? `"${song.title}" by ${song.artist} is currently uncalibrated.`
      : song.charge_summary || `Classified as ${tierLabel} (${chargeDisplay}) by The Rising Compass.`;

    // Hero
    document.getElementById('song-title').textContent = song.title;
    const artistEl = document.getElementById('song-artist');
    if (song.artist_slug) {
      artistEl.innerHTML = `<a href="/artists/artist.html?slug=${encodeURIComponent(song.artist_slug)}" class="accent-link">${escapeHtml(song.artist)}</a>`;
    } else {
      artistEl.textContent = song.artist || '';
    }

    // Tier badge
    const badge = document.getElementById('song-tier-badge');
    if (isUncalibrated) {
      badge.innerHTML = `<span class="badge-tier badge-tier--uncalibrated">Uncalibrated</span>`;
    } else {
      badge.innerHTML = `
        <span class="badge-charge" style="color:${color}">${chargeDisplay}</span>
        <span class="badge-tier" style="background:${color}20;color:${color}">${escapeHtml(tierLabel)}</span>
      `;
    }

    // Section 2: Song-specific summary
    const summarySection = document.getElementById('section-summary');
    summarySection.hidden = false;
    document.getElementById('summary-text').textContent = isUncalibrated
      ? 'This song is currently uncalibrated. See the history section below for the reasoning behind the most recent reset.'
      : song.charge_summary || `This song is classified as ${tierLabel}.`;

    // Section 3: Effects — per-song prose if available, else tier-generic fallback.
    const effectsSection = document.getElementById('section-effects');
    effectsSection.hidden = !isUncalibrated ? false : true;
    if (!isUncalibrated) {
      const proseHtml = song.effects_prose
        ? song.effects_prose
            .split(/\n{2,}/)
            .map(p => p.trim())
            .filter(Boolean)
            .map(p => `<p>${escapeHtml(p)}</p>`)
            .join('')
        : `<p>${TIER_EFFECTS[song.rubric_color] || ''}</p>`;
      document.getElementById('effects-prose').innerHTML = proseHtml;
    }

    // Section 3: Contamination
    const contamSection = document.getElementById('section-contamination');
    contamSection.hidden = isUncalibrated;
    if (isUncalibrated) {
      // Skip contamination rendering for uncalibrated songs
    } else if (song.contaminated) {
      document.getElementById('contam-heading').textContent = 'Contamination Warning';
      document.getElementById('contam-answer').textContent =
        song.contamination_note || 'This song contains contaminated content that undermines its higher-tier substance.';
      contamSection.style.borderLeftColor = 'var(--rc-orange)';
    } else {
      document.getElementById('contam-heading').textContent = 'Contamination Status';
      document.getElementById('contam-answer').textContent = 'No contamination detected. The lyrical content is consistent with its classification tier.';
    }

    // Section 3b: Dogma Reference — only surfaces when the tag fired.
    const dogmaSection = document.getElementById('section-dogma');
    if (!isUncalibrated && song.dogma_referenced) {
      dogmaSection.hidden = false;
      document.getElementById('dogma-answer').textContent =
        song.dogma_note || 'This song references a specific doctrinal framework.';
    } else {
      dogmaSection.hidden = true;
    }

    // Section 4: Details table
    const detailsSection = document.getElementById('section-details');
    detailsSection.hidden = false;
    const table = document.getElementById('details-table');
    const rows = isUncalibrated
      ? [
          ['Tier', '<span style="color:#888">Uncalibrated</span>'],
          ['Charge', '<span style="color:#888">—</span>'],
          ['Classified By', 'The Rising Compass'],
          ['Method', '58-tenet rubric v1'],
        ]
      : [
          ['Tier', `<span style="color:${color}">${escapeHtml(tierLabel)}</span>`],
          ['Charge', `<span style="color:${color}">${chargeDisplay}</span>`],
          ['Classified By', 'The Rising Compass'],
          ['Method', '58-tenet rubric v1'],
        ];
    if (song.release_title) {
      rows.push(['Release', escapeHtml(song.release_title)]);
    }
    table.innerHTML = rows.map(([k, v]) =>
      `<tr><td class="detail-key">${k}</td><td class="detail-value">${v}</td></tr>`
    ).join('');

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
        { '@type': 'PropertyValue', propertyID: 'RisingCompassTier', name: 'Rising Compass Classification', value: tierLabel },
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
        classifiedBy: {
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
