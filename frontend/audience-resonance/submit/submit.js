// Share a Resonance - multi-step submission wizard.
// Step pattern reimplemented (vanilla JS) from the chadlewine booking-inquiry
// form: step state, per-step validation, progress, back/next, carried state.
// Flow: pick the song -> write unguarded -> reveal the slice on commit ->
// accept or flag -> choose username + consent.
//
// LIVE: the song picker searches the real corpus (/api/songs), the slice runs
// server-side (POST /slice -> poll), and the submission persists via /submit
// (the server-computed verdict is stored by token; the client never sends
// proportions). While the slicer ships dark the server returns a neutral
// verdict, so the reveal falls back to a clearly-labeled local preview.
//
// ?demo / ?seed runs fully offline against the seed fixture (preview slice, no
// network), for design review.

import { fetchSeedSongs } from '/audience-resonance/data.js';

const VERDICT_COLOR = { true: '#00d4aa', camouflage: '#9a8cff', adjacent: '#8a93a8' };
const STEP_LABELS = ['Song', 'Story', 'Reveal', 'Verdict', 'Consent'];
const MIN_STORY = 40;

const DEMO = (() => { const p = new URLSearchParams(location.search); return p.has('demo') || p.has('seed'); })();

let seedSongs = [];
const state = {
  step: 0, song: null, story: '',
  slice: null, slicePreview: true, sliceToken: null,
  decision: null, flagReason: '', username: '', consent: null,
  submitError: '',
};

const root = document.getElementById('ar-wizard');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function api() { return (typeof window !== 'undefined' && window.API) ? window.API : null; }

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
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
    + `<span style="color:${VERDICT_COLOR.true}">True ${t}</span> &middot; `
    + `<span style="color:${VERDICT_COLOR.camouflage}">Camouflage ${c}</span> &middot; `
    + `<span style="color:${VERDICT_COLOR.adjacent}">Adjacent ${a}</span></div>`;
}

// Local preview slicer (keyword heuristic). Only ever a preview -- the real
// verdict is computed server-side and persisted by token.
function previewSlice(story) {
  const s = ' ' + story.toLowerCase() + ' ';
  let t = 34, c = 33, a = 33;
  const ADJ = ['funeral', 'wedding', 'summer', 'drove', 'road trip', 'remember', 'kitchen', 'my dad', 'my mom', 'my sister', 'my brother', 'grandmother', 'first dance', 'reminds me', 'the day', 'her laugh'];
  const CAM = ['profound', 'empty', 'resentment', 'pumped', 'flattered', 'righteous', 'filth', 'stew', 'hit of', 'felt like church', 'costume'];
  const TRU = ['saved', 'survive', 'alive', 'honest', 'truth', 'elevated', 'lifted', 'kept going', 'out of that', 'changed me', 'steadied', 'light on'];
  for (const w of ADJ) if (s.includes(w)) a += 9;
  for (const w of CAM) if (s.includes(w)) c += 9;
  for (const w of TRU) if (s.includes(w)) t += 9;
  const sum = t + c + a;
  const T = Math.round((t / sum) * 100);
  const C = Math.round((c / sum) * 100);
  return { true: T, camouflage: C, adjacent: 100 - T - C, attribution: [] };
}

function dominant(sl) {
  const m = Math.max(sl.true, sl.camouflage, sl.adjacent);
  if (m === sl.true) return { key: 'true', label: 'True', gloss: 'the song did the work' };
  if (m === sl.camouflage) return { key: 'camouflage', label: 'Camouflage', gloss: 'a lift in disguise' };
  return { key: 'adjacent', label: 'Adjacent', gloss: 'your life did the work, the song was nearby' };
}

// ----- live data -----
async function searchSongs(term) {
  if (DEMO) {
    const q = term.trim().toLowerCase();
    return seedSongs
      .filter((s) => !q || s.title.toLowerCase().includes(q) || s.artist.toLowerCase().includes(q))
      .slice(0, 8);
  }
  if (term.trim().length < 2 || !api()) return [];
  try {
    const r = await api().get(`/api/songs?q=${encodeURIComponent(term.trim())}&limit=8`);
    return (r.results || []).map((s) => ({
      id: s.id, title: s.title, artist: s.artist, slug: s.slug,
      color: s.tier_hex, tier_label: s.tier_label, charge: s.charge_value,
    }));
  } catch (_) { return []; }
}

// Fire the server slice and poll to completion. Returns { token, slice, live }.
// live=true means a real verdict came back; live=false means dark/pending/error
// (caller falls back to the labeled preview). Never throws.
async function runSlice(songId, story) {
  if (DEMO || !api()) return { token: null, slice: null, live: false };
  let token = null;
  try {
    const start = await api().post('/api/audience-resonance/slice', { song_id: songId, story });
    token = start.slice_token;
    for (let i = 0; i < 18; i++) {
      const st = await api().get(`/api/audience-resonance/slice/${token}`);
      if (st.status === 'done') {
        const sl = st.slice || {};
        if (sl.status === 'done') {
          return { token, live: true, slice: {
            true: sl.prop_true, camouflage: sl.prop_camouflage, adjacent: sl.prop_adjacent,
            attribution: sl.slice_attribution || [],
          } };
        }
        return { token, slice: null, live: false };  // pending (slicer dark)
      }
      if (st.status === 'error') return { token, slice: null, live: false };
      await sleep(700);
    }
  } catch (_) { /* fall through to preview */ }
  return { token, slice: null, live: false };
}

async function doSubmit() {
  if (DEMO || !api()) { state.step += 1; render(); return; }
  try {
    await api().post('/api/audience-resonance/submit', {
      song_id: state.song.id,
      username: state.username.trim(),
      story: state.story.trim(),
      consent: state.consent,
      slice_token: state.sliceToken || null,
      flagged: state.decision === 'flag',
      flag_reason: state.decision === 'flag' ? state.flagReason.trim() : null,
    }, { auth: true });
    state.step += 1;
    render();
  } catch (e) {
    if (e && e.status === 503) {
      state.submitError = 'Audience Resonance is in preview. Submissions open at launch.';
    } else {
      state.submitError = (e && e.message) || 'Something went wrong. Please try again.';
    }
    render();
  }
}

function valid() {
  if (state.step === 0) return !!state.song;
  if (state.step === 1) return state.story.trim().length >= MIN_STORY;
  if (state.step === 2) return true;
  if (state.step === 3) return state.decision === 'accept' || (state.decision === 'flag' && state.flagReason.trim().length >= 10);
  if (state.step === 4) return !!state.consent && state.username.trim().length >= 1;
  return true;
}

function progressHtml() {
  return '<div class="ar-steps">' + STEP_LABELS.map((label, i) => {
    const cls = i === state.step ? 'active' : (i < state.step ? 'done' : '');
    return `<div class="ar-step-dot ${cls}">${label}</div>`;
  }).join('') + '</div>';
}

function revealHtml() {
  const sl = state.slice;
  const dom = dominant(sl);
  let work;
  if (!state.slicePreview && Array.isArray(sl.attribution) && sl.attribution.length) {
    work = '<div class="ar-shows-work"><strong>Where that came from:</strong><ul class="ar-attr-list">'
      + sl.attribution.map((e) => `<li><em>"${esc(e.quote)}"</em> reads as ${esc(e.reads_as || dom.key)}${e.note ? `: ${esc(e.note)}` : ''}</li>`).join('')
      + '</ul></div>';
  } else {
    const firstLine = state.story.trim().split(/(?<=[.!?])\s+/)[0] || state.story.trim().slice(0, 80);
    work = `<div class="ar-shows-work"><strong>Where that came from:</strong> the line <em>"${esc(firstLine)}"</em> reads as ${dom.label.toLowerCase()}. <span style="color:var(--rc-text-dim)">(Preview only; the live slicer reads every line on the server.)</span></div>`;
  }
  return `<h3 class="ar-step-h">How your story read</h3>
    <p class="ar-step-sub">This is how your words read, not a ruling on you.</p>
    ${propBar(sl.true, sl.camouflage, sl.adjacent)}
    ${legend(sl.true, sl.camouflage, sl.adjacent)}
    <div class="ar-reveal-verdict">Mostly <strong style="color:${VERDICT_COLOR[dom.key]}">${dom.label}</strong>:${dom.gloss}.</div>
    ${work}`;
}

function stepHtml() {
  if (state.step === 0) {
    return `<h3 class="ar-step-h">Which song?</h3>
      <p class="ar-step-sub">Pick the one this is about. Your resonance attaches to it.</p>
      <input type="text" id="song-q" class="ar-input" placeholder="Search by title or artist" autocomplete="off">
      <div id="song-results"></div>`;
  }
  if (state.step === 1) {
    return `<h3 class="ar-step-h">What did it do to you?</h3>
      <p class="ar-step-sub">Write it the way it actually happened. Nothing reads your words until you commit.</p>
      <textarea id="story" class="ar-textarea" placeholder="The first time I heard it...">${esc(state.story)}</textarea>
      <div class="ar-prop-legend" id="story-count"></div>`;
  }
  if (state.step === 2) return revealHtml();
  if (state.step === 3) {
    const flagOpen = state.decision === 'flag';
    return `<h3 class="ar-step-h">Does that read true to you?</h3>
      <p class="ar-step-sub">You always get the last word before anything is public.</p>
      <div class="ar-decisions">
        <button type="button" class="ar-consent-opt ${state.decision === 'accept' ? 'sel' : ''}" data-dec="accept">That reads true.</button>
        <button type="button" class="ar-consent-opt ${flagOpen ? 'sel' : ''}" data-dec="flag">No, you misread my story.</button>
      </div>
      <div id="flag-reason-wrap" style="${flagOpen ? '' : 'display:none'}">
        <textarea id="flag-reason" class="ar-textarea" style="min-height:90px;margin-top:0.6rem" placeholder="In a sentence, what did we get wrong? A person reviews this before it posts.">${esc(state.flagReason)}</textarea>
      </div>`;
  }
  if (state.step === 4) {
    return `<h3 class="ar-step-h">Who is it from, and what happens to it?</h3>
      <p class="ar-step-sub">Your call. You can change or delete it at any time.</p>
      <input type="text" id="username" class="ar-input" maxlength="120" placeholder="A username (not your real name)" value="${esc(state.username)}">
      <label class="ar-consent-opt ${state.consent === 'publish' ? 'sel' : ''}" data-consent="publish"><strong>Publish</strong>:it appears under your username.</label>
      <label class="ar-consent-opt ${state.consent === 'private' ? 'sel' : ''}" data-consent="private"><strong>Keep private</strong>:out of public view, kept anonymously for the research.</label>
      <p class="ar-door">Either way, your story is read. If you keep it private it stays out of public view but is kept, anonymously, as part of the research. You can delete it permanently at any time, and when you do it is gone everywhere, with no trace.</p>
      ${state.submitError ? `<p class="ar-door" style="color:${VERDICT_COLOR.camouflage}">${esc(state.submitError)}</p>` : ''}`;
  }
  // done
  const pub = state.consent === 'publish';
  return `<h3 class="ar-step-h">Recorded.</h3>
    <p class="ar-step-sub">${state.decision === 'flag'
      ? 'A person will review your note before anything posts.'
      : pub ? 'Your resonance is recorded under your username.' : 'Your resonance is kept privately for the research.'}</p>
    <p class="ar-door">Thank you for adding your resonance.</p>`;
}

function navHtml() {
  if (state.step >= STEP_LABELS.length) return '';
  const last = state.step === STEP_LABELS.length - 1;
  const back = state.step > 0 ? '<button type="button" class="ar-btn-ghost ar-btn" id="back">Back</button>' : '<span></span>';
  const nextLabel = last ? 'Submit' : (state.step === 2 ? 'Continue' : 'Next');
  return `<div class="ar-wizard-nav">${back}<button type="button" class="ar-btn" id="next" ${valid() ? '' : 'disabled'}>${nextLabel}</button></div>`;
}

function render() {
  const done = state.step >= STEP_LABELS.length;
  root.innerHTML = (done ? '' : progressHtml()) + `<div class="ar-wizard-body">${stepHtml()}</div>` + navHtml();
  wire();
}

function wire() {
  // Step 0: live song search (debounced; latest query wins)
  const q = root.querySelector('#song-q');
  if (q) {
    const results = root.querySelector('#song-results');
    let seq = 0;
    let timer = null;
    const paint = async () => {
      const mine = ++seq;
      const list = await searchSongs(q.value);
      if (mine !== seq) return;  // a newer query superseded this one
      results.innerHTML = list.map((s) => `
        <div class="ar-song-pick ${state.song && state.song.id === s.id ? 'sel' : ''}" data-id="${s.id}">
          <span class="dot" style="background:${s.color}"></span>
          <span class="meta"><span class="t">${esc(s.title)}</span> <span class="a">${esc(s.artist)} &middot; ${esc(s.tier_label)}</span></span>
        </div>`).join('');
      results.querySelectorAll('.ar-song-pick').forEach((el) => {
        el.addEventListener('click', () => {
          state.song = list.find((s) => s.id === parseInt(el.dataset.id, 10));
          render();
        });
      });
    };
    q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(paint, 180); });
    paint();
  }

  // Step 1: story
  const story = root.querySelector('#story');
  if (story) {
    const count = root.querySelector('#story-count');
    const upd = () => {
      state.story = story.value;
      const n = state.story.trim().length;
      count.textContent = n < MIN_STORY ? `${MIN_STORY - n} more characters` : `${n} characters`;
      const next = root.querySelector('#next');
      if (next) next.disabled = !valid();
    };
    story.addEventListener('input', upd);
    upd();
  }

  // Step 3: decision
  root.querySelectorAll('[data-dec]').forEach((el) => {
    el.addEventListener('click', () => { state.decision = el.dataset.dec; render(); });
  });
  const fr = root.querySelector('#flag-reason');
  if (fr) {
    fr.addEventListener('input', () => {
      state.flagReason = fr.value;
      const next = root.querySelector('#next');
      if (next) next.disabled = !valid();
    });
  }

  // Step 4: username + consent
  const uname = root.querySelector('#username');
  if (uname) {
    uname.addEventListener('input', () => {
      state.username = uname.value;
      const next = root.querySelector('#next');
      if (next) next.disabled = !valid();
    });
  }
  root.querySelectorAll('[data-consent]').forEach((el) => {
    el.addEventListener('click', () => { state.consent = el.dataset.consent; render(); });
  });

  // nav
  const back = root.querySelector('#back');
  if (back) back.addEventListener('click', () => { state.step = Math.max(0, state.step - 1); state.submitError = ''; render(); });
  const next = root.querySelector('#next');
  if (next) next.addEventListener('click', async () => {
    if (!valid()) return;
    if (state.step === 1) {
      // Commit -> reveal on the slice. Live: server slice; else labeled preview.
      next.disabled = true;
      next.textContent = 'Reading your story...';
      const res = await runSlice(state.song.id, state.story);
      state.sliceToken = res.token;
      if (res.live && res.slice) { state.slice = res.slice; state.slicePreview = false; }
      else { state.slice = previewSlice(state.story); state.slicePreview = true; }
      state.step += 1;
      render();
      return;
    }
    if (state.step === STEP_LABELS.length - 1) { await doSubmit(); return; }
    state.step += 1;
    render();
  });
}

function renderComingSoon() {
  root.innerHTML = `
    <div class="ar-coming-soon">
      <h3 class="ar-step-h">Audience Resonance is opening soon</h3>
      <p class="ar-step-sub">You will be able to share what a song actually did to you, and see how it read, here. Sharing is not open yet.</p>
      <p class="ar-door">In the meantime, explore the <a href="/audience-resonance/" class="accent-link">Audience Resonance map</a>.</p>
    </div>`;
}

async function boot() {
  const params = new URLSearchParams(location.search);

  // Dark-launch lock (Album Charger pattern): when submissions are closed, show
  // a coming-soon panel instead of the wizard, so the entry reads as locked
  // rather than letting someone fill it all in and hit a wall. ?demo previews
  // the wizard regardless.
  if (!DEMO && api()) {
    try {
      const cfg = await api().get('/api/audience-resonance/config');
      if (cfg && cfg.submissions_open === false) { renderComingSoon(); return; }
    } catch (_) { /* config unreachable -> fall through; /submit still 503s */ }
  }

  // Best-effort username prefill from a signed-in handle (only if auth.js is on
  // the page); otherwise the user types one.
  try { if (window.Auth && window.Auth.getMe) { const me = await window.Auth.getMe(); if (me && me.handle) state.username = me.handle; } } catch (_) {}

  // Song-bound entry (from a song page's "Share a Resonance" link): pre-select
  // the song from ?slug and start at the story step.
  if (!DEMO && params.has('slug') && api()) {
    try {
      const d = await api().get(`/api/songs/${encodeURIComponent(params.get('slug'))}`);
      if (d && d.song_id != null) {
        state.song = {
          id: d.song_id, title: d.title, artist: d.artist, slug: params.get('slug'),
          color: d.tier_hex, tier_label: d.tier_label, charge: d.charge_value,
        };
        state.step = 1;
      }
    } catch (_) { /* fall back to manual song search */ }
  }

  // Demo shortcut: ?demo jumps to the reveal with a sample (offline preview).
  if (DEMO) {
    seedSongs = await fetchSeedSongs();
    if (params.has('demo')) {
      state.song = seedSongs.find((s) => s.id === 2001) || seedSongs[0];
      state.story = 'I was 19 and ready to disappear. This song did not glorify any of that. It found me at the bottom and somehow made the bottom feel survivable, like someone had been there and left a light on. I came out of that winter because of it.';
      state.slice = previewSlice(state.story);
      state.slicePreview = true;
      state.step = 2;
    }
  }

  render();
}

boot();
