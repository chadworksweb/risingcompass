// Audience Resonance - per-song section (the song-page display row).
// Sticky-left: count, compass-charge vs listeners, story-mode ternary, verdict
// filter. Scrolling-right: story cards. Linked brushing: hover a card to light
// its dot. Drop-in: renderSongSection(mountEl, { song, resonances }).

import { renderTernary } from './ternary.js';

// Verdict palette, distinct from the 5 charge-tier colors.
const VERDICT_COLOR = { true: '#00d4aa', camouflage: '#9a8cff', adjacent: '#8a93a8' };
const FLAG_LABEL = { flagged: 'contested', in_review: 'in review', corrected: 'corrected' };

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function dominant(r) {
  const m = Math.max(r.true, r.camouflage, r.adjacent);
  if (m === r.true) return 'true';
  if (m === r.camouflage) return 'camouflage';
  return 'adjacent';
}

function meanOf(resonances) {
  const n = resonances.length || 1;
  const s = resonances.reduce((acc, r) => {
    acc.t += r.true; acc.c += r.camouflage; acc.a += r.adjacent; return acc;
  }, { t: 0, c: 0, a: 0 });
  return { true: s.t / n, camouflage: s.c / n, adjacent: s.a / n };
}

function propBar(t, c, a) {
  return '<div class="ar-prop-bar">'
    + `<span style="width:${t}%;background:${VERDICT_COLOR.true}"></span>`
    + `<span style="width:${c}%;background:${VERDICT_COLOR.camouflage}"></span>`
    + `<span style="width:${a}%;background:${VERDICT_COLOR.adjacent}"></span>`
    + '</div>';
}

function legend(t, c, a) {
  return '<div class="ar-prop-legend">'
    + `<span style="color:${VERDICT_COLOR.true}">True ${Math.round(t)}</span> &middot; `
    + `<span style="color:${VERDICT_COLOR.camouflage}">Camouflage ${Math.round(c)}</span> &middot; `
    + `<span style="color:${VERDICT_COLOR.adjacent}">Adjacent ${Math.round(a)}</span></div>`;
}

function flagBadge(r) {
  if (!r.flag || r.flag === 'none') return '';
  return `<span class="ar-flag ar-flag-${r.flag}">${FLAG_LABEL[r.flag] || r.flag}</span>`;
}

export function renderSongSection(mount, { song, resonances }) {
  if (!mount || !song) return;
  mount.textContent = '';
  mount.classList.add('ar-song-row');

  // ----- left column -----
  const left = document.createElement('div');
  left.className = 'ar-song-left';
  const mean = meanOf(resonances);
  const chargeStr = (song.charge > 0 ? '+' : '') + song.charge;
  left.innerHTML =
    `<div class="ar-song-head">
       <div class="ar-song-title">${esc(song.title)}</div>
       <div class="ar-song-artist">${esc(song.artist)}</div>
       <div class="ar-song-charge" style="color:${song.color}">Compass: ${esc(song.tier_label)} (${chargeStr})</div>
     </div>
     <div class="ar-count-wrap"><span class="ar-count">${resonances.length}</span> <span class="ar-count-label">Resonances</span></div>
     <div class="ar-song-tern" id="ar-song-tern"></div>
     <div class="ar-rollup">
       <div class="ar-rollup-label">What listeners felt, on average</div>
       ${propBar(mean.true, mean.camouflage, mean.adjacent)}
       ${legend(mean.true, mean.camouflage, mean.adjacent)}
     </div>
     <div class="era-tabs ar-verdict-tabs" id="ar-verdict-tabs"></div>`;
  mount.appendChild(left);

  // ----- right column (story cards) -----
  const right = document.createElement('div');
  right.className = 'ar-song-right';
  mount.appendChild(right);

  const cardById = new Map();
  for (const r of resonances) {
    const card = document.createElement('article');
    card.className = 'ar-story-card';
    card.innerHTML =
      `<div class="ar-story-top"><span class="ar-story-user">@${esc(r.username)}</span>${flagBadge(r)}</div>
       <p class="ar-story-text">${esc(r.story)}</p>
       ${propBar(r.true, r.camouflage, r.adjacent)}
       ${legend(r.true, r.camouflage, r.adjacent)}`;
    right.appendChild(card);
    cardById.set(r.id, card);
  }

  // ----- story-mode ternary + linked brushing + verdict filter -----
  const ternMount = left.querySelector('#ar-song-tern');
  const tabsEl = left.querySelector('#ar-verdict-tabs');
  let dotById = new Map();
  let filter = 'all';

  function renderTern() {
    const points = resonances.map((r) => ({
      t: r.true / 100, c: r.camouflage / 100, a: r.adjacent / 100,
      color: VERDICT_COLOR[dominant(r)],
      r: 7,
      id: r.id,
      tooltipHtml: `<strong>@${esc(r.username)}</strong><br><span class="traj-tooltip-sub">T ${r.true} &nbsp; C ${r.camouflage} &nbsp; A ${r.adjacent}</span>`,
      dim: filter !== 'all' && dominant(r) !== filter,
    }));
    dotById = renderTernary(ternMount, {
      points,
      caption: 'Each dot is one person; color is the verdict that dominated their story.',
    }).dotById;
  }

  // hover a card -> light its dot
  for (const r of resonances) {
    const card = cardById.get(r.id);
    card.addEventListener('mouseenter', () => { const d = dotById.get(r.id); if (d) d.classList.add('is-hot'); });
    card.addEventListener('mouseleave', () => { const d = dotById.get(r.id); if (d) d.classList.remove('is-hot'); });
  }

  const tabs = [
    { key: 'all', title: 'All', tagline: `${resonances.length}` },
    { key: 'true', title: 'True', tagline: 'the song did it' },
    { key: 'camouflage', title: 'Camouflage', tagline: 'counterfeit' },
    { key: 'adjacent', title: 'Adjacent', tagline: 'their life' },
  ];
  function renderTabs() {
    tabsEl.textContent = '';
    for (const tab of tabs) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'era-tab' + (tab.key === filter ? ' active' : '');
      btn.innerHTML = `<span class="era-tab-title">${tab.title}</span><span class="era-tab-tagline">${tab.tagline}</span>`;
      btn.addEventListener('click', () => { filter = tab.key; apply(); });
      tabsEl.appendChild(btn);
    }
  }
  function apply() {
    renderTabs();
    renderTern();
    for (const r of resonances) {
      const show = filter === 'all' || dominant(r) === filter;
      cardById.get(r.id).style.display = show ? '' : 'none';
    }
  }
  apply();
}
