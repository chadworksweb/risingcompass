// Audience Resonance - corpus map page.
// Renders the corpus ternary from the LIVE API (each dot = a song), with an
// All / Disagreements filter (reuses the homepage .era-tabs pattern). Falls
// back to the synthetic seed as a labeled demo while the corpus is empty
// (see data.js).

import { renderTernary } from './ternary.js';
import { fetchCorpus } from './data.js';

// Charge tier colors, harvested from the homepage trajectory charts
// (frontend/js/app.js COLOR_HEX). A corrupted song is a red dot.
export const COLOR_HEX = {
  violet: '#aa54ff', blue: '#3388ff', green: '#33cc55', orange: '#ffbb33', red: '#ff3333',
};

// Dot radius encodes n. A 1-resonance song cannot masquerade as a busy one.
function dotRadius(n) { return Math.max(5, Math.min(18, 4 + n * 1.6)); }

// Disagreement: the compass scored the song one way, the listeners felt another.
// Low charge that genuinely elevated, or high charge that did not do the work.
function isDisagreement(charge, meanTrue) {
  if (charge < 0 && meanTrue >= 50) return true;
  if (charge > 0 && meanTrue < 35) return true;
  return false;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function tipHtml(ru) {
  const m = ru.mean;
  return `<strong>${esc(ru.song.title)}</strong> ${esc(ru.song.artist)}`
    + `<br><span style="color:${ru.song.color}">${esc(ru.song.tier_label)}</span> &middot; n=${ru.n}`
    + `<br><span class="traj-tooltip-sub">T ${Math.round(m.true)} &nbsp; C ${Math.round(m.camouflage)} &nbsp; A ${Math.round(m.adjacent)}</span>`;
}

function renderTabs(container, tabs, current, onSelect) {
  container.textContent = '';
  for (const tab of tabs) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'era-tab' + (tab.key === current ? ' active' : '');
    btn.innerHTML = `<span class="era-tab-title">${tab.title}</span><span class="era-tab-tagline">${tab.tagline}</span>`;
    btn.addEventListener('click', () => onSelect(tab.key));
    container.appendChild(btn);
  }
}

async function boot() {
  const { rollups, totalResonances, isDemo } = await fetchCorpus();

  const mount = document.getElementById('ar-ternary-mount');
  const tabsEl = document.getElementById('ar-filter-tabs');
  const noteEl = document.getElementById('ar-filter-note');

  const disCount = rollups.filter((ru) => isDisagreement(ru.song.charge, ru.mean.true)).length;
  let filter = 'all';
  const demoSuffix = isDemo ? ' (demo data)' : '';

  // Dark-launch watermark: a clear, unmissable DEMO DATA mark over the chart so
  // no one mistakes the seeded preview for real listener data.
  if (isDemo && mount) {
    const card = mount.closest('.card') || mount.parentElement;
    if (card && !card.querySelector('.ar-watermark')) {
      if (!card.style.position) card.style.position = 'relative';
      const wm = document.createElement('div');
      wm.className = 'ar-watermark';
      wm.setAttribute('aria-hidden', 'true');
      wm.textContent = 'DEMO DATA';
      card.appendChild(wm);
    }
  }

  const CAPTION = 'Each dot is a song, placed by what its resonances did: the closer to a corner, the more of that verdict. Size shows how many resonances; color is the compass charge.';

  function buildPoints() {
    return rollups.map((ru) => ({
      t: ru.mean.true / 100,
      c: ru.mean.camouflage / 100,
      a: ru.mean.adjacent / 100,
      color: ru.song.color,
      r: dotRadius(ru.n),
      // Demo dots are fictional songs with no real page -- not clickable. Live
      // dots link to the real song page.
      href: (!isDemo && ru.song.slug) ? `/songs/${ru.song.slug}` : null,
      tooltipHtml: tipHtml(ru),
      dim: filter === 'dis' && !isDisagreement(ru.song.charge, ru.mean.true),
    }));
  }

  function updateNote() {
    noteEl.textContent = (filter === 'all'
      ? `${rollups.length} songs, ${totalResonances} resonances.`
      : `${disCount} of ${rollups.length} songs disagree: the compass scored them one way, the listeners felt another.`)
      + demoSuffix;
  }

  function rerender() {
    renderTernary(mount, {
      points: buildPoints(),
      caption: CAPTION,
      onPointClick: (p) => { if (p.href) window.location.href = p.href; },
    });
    updateNote();
  }

  const tabs = [
    { key: 'all', title: 'All songs', tagline: 'the whole corpus' },
    { key: 'dis', title: 'Disagreements', tagline: 'charge and listeners diverge' },
  ];
  function selectFilter(k) {
    filter = k;
    renderTabs(tabsEl, tabs, filter, selectFilter);
    rerender();
  }
  renderTabs(tabsEl, tabs, filter, selectFilter);
  rerender();

  console.log(`[AR] corpus: ${rollups.length} songs, ${totalResonances} resonances, ${disCount} disagreements${isDemo ? ' [demo seed]' : ' [live]'}`);
}

boot();
