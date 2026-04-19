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
    const params = new URLSearchParams(window.location.search);
    const slug = params.get('slug');
    if (!slug) return;

    try {
      const song = await ArtistsAPI.getSong(slug);
      renderSong(song);
      announce(`Loaded effects label for ${song.title}`);
    } catch (err) {
      console.error('Failed to load song:', err);
      document.getElementById('song-title').textContent = '';
      document.getElementById('not-found').hidden = false;
    }
  }

  function renderSong(song) {
    const color = COLOR_HEX[song.rubric_color] || '#999';
    const tierLabel = CHARGE_LABELS[song.rubric_color] || '';
    const chargeDisplay = song.charge_value != null
      ? (song.charge_value > 0 ? '+' : '') + song.charge_value
      : 'N/A';

    // Page meta
    document.getElementById('page-title').textContent =
      `What Might "${song.title}" Do to the Listener? — The Rising Compass`;
    document.getElementById('meta-description').content =
      song.charge_summary || `${song.title} by ${song.artist}: ${tierLabel} (${chargeDisplay}). Lyrical effects classified by The Rising Compass.`;
    document.getElementById('og-title').content =
      `"${song.title}" by ${song.artist} — Lyrical Effects Label`;
    document.getElementById('og-description').content =
      song.charge_summary || `Classified as ${tierLabel} (${chargeDisplay}) by The Rising Compass.`;

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
    badge.innerHTML = `
      <span class="badge-charge" style="color:${color}">${chargeDisplay}</span>
      <span class="badge-tier" style="background:${color}20;color:${color}">${escapeHtml(tierLabel)}</span>
    `;

    // Section 2: Song-specific summary
    const summarySection = document.getElementById('section-summary');
    summarySection.hidden = false;
    document.getElementById('summary-text').textContent =
      song.charge_summary || `This song is classified as ${tierLabel}.`;

    // Section 3: Effects (currently a tier-generic description)
    const effectsSection = document.getElementById('section-effects');
    effectsSection.hidden = false;
    document.getElementById('effects-prose').innerHTML =
      `<p>${TIER_EFFECTS[song.rubric_color] || ''}</p>`;

    // Section 3: Contamination
    const contamSection = document.getElementById('section-contamination');
    contamSection.hidden = false;
    if (song.contaminated) {
      document.getElementById('contam-heading').textContent = 'Contamination Warning';
      document.getElementById('contam-answer').textContent =
        song.contamination_note || 'This song contains contaminated content that undermines its higher-tier substance.';
      contamSection.style.borderLeftColor = 'var(--rc-orange)';
    } else {
      document.getElementById('contam-heading').textContent = 'Contamination Status';
      document.getElementById('contam-answer').textContent = 'No contamination detected. The lyrical content is consistent with its classification tier.';
    }

    // Section 4: Details table
    const detailsSection = document.getElementById('section-details');
    detailsSection.hidden = false;
    const table = document.getElementById('details-table');
    const rows = [
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
