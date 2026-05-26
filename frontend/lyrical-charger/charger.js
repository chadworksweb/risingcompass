/* ============================================================
   Lyrical Charger — Application Logic
   ============================================================ */

// --- Config ---
const IS_LOCAL = ['localhost', '127.0.0.1'].includes(window.location.hostname);
const API_HOST = '';  // same-origin relative; dev_server proxies /api -> :8000 locally
const API_BASE = `${API_HOST}/api/analyzer`;
const API_KEY = IS_LOCAL
  ? '09bcf6d7b84be7f50292fd35465fe745404ad0fb0780b35c7a5747b5c202a662'
  : '6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b';

// --- Tier color map ---
const TIER_COLORS = {
  violet: 'violet',
  blue: 'blue',
  green: 'green',
  orange: 'orange',
  red: 'red',
};

const TIER_LABELS = {
  violet: 'Ascended',
  blue: 'Elevated',
  green: 'Decent',
  orange: 'Degraded',
  red: 'Corrupted',
};

// --- DOM refs ---
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const screenEntry = $('#screen-entry');
const screenProcessing = $('#screen-processing');
const screenResults = $('#screen-results');

const tabs = $$('.tab');
const tabPaste = $('#tab-paste');
const tabSearch = $('#tab-search');

const inputTitle = $('#input-title');
const artistsContainer = $('#artists-input');
const artistAddBtn = $('#artist-add-btn');
const artistParseHint = $('#artist-parse-hint');
const artistParseBtn = $('#artist-parse-btn');
const lyricsInput = $('#lyrics-input');

const searchQuery = $('#search-query');
const searchArtist = $('#search-artist');
const btnSearch = $('#btn-search');
const searchStatus = $('#search-status');
const searchResults = $('#search-results');

const consentCheck = $('#consent-check');
const btnSubmit = $('#btn-submit');
const errorMessage = $('#error-message');

const procBarFill = $('#proc-bar-fill');
const procPct = $('#proc-pct');
const procStage = $('#proc-stage');
const procDetail = $('#proc-detail');
const procSubsteps = $('#proc-substeps');

const resultIdentity = $('#result-identity');
const resultCalibration = $('#result-calibration');
const resultSummary = $('#result-summary');
const resultEffects = $('#result-effects');
const resultEffectsBody = $('#result-effects-body');
const resultSocietal = $('#result-societal');
const resultSocietalBody = $('#result-societal-body');
const resultConsensus = $('#result-consensus');
const resultContamination = $('#result-contamination');
const resultMisread = $('#result-misread');
const btnAgain = $('#btn-again');

// --- State ---
let activeTab = 'paste';
let selectedTrack = null;  // { track_id, title, artist }
let turnstileWidgetId = null;

// --- Auth (optional) ---
// When a user is signed in (Clerk via /js/auth.js), we attach their bearer
// token to calibrate calls so the reading is saved to their account under
// "Songs you've calibrated." Anonymous readings still work -- the token is
// simply absent. Auth.init() is fired early but never blocks the page.
if (window.Auth) { window.Auth.init().catch(() => {}); }

async function calibrateHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  if (API_KEY) headers['X-Api-Key'] = API_KEY;
  try {
    if (window.Auth && window.Auth.isSignedIn()) {
      const token = await window.Auth.getToken();
      if (token) headers['Authorization'] = `Bearer ${token}`;
    }
  } catch { /* token fetch is best-effort; fall back to anonymous */ }
  return headers;
}

// --- Bot protection helpers ---
function getHpValue() {
  return ($('#hp-website')?.value || '').trim();
}

function getTurnstileToken() {
  if (!window.turnstile || turnstileWidgetId === null) return '';
  try {
    return window.turnstile.getResponse(turnstileWidgetId) || '';
  } catch {
    return '';
  }
}

function resetTurnstile() {
  if (window.turnstile && turnstileWidgetId !== null) {
    try { window.turnstile.reset(turnstileWidgetId); } catch {}
  }
}

// ============================================================
// Multi-Artist Input
// ============================================================
const FEATURE_MARKERS = [' featuring ', ' feat. ', ' feat ', ' ft. ', ' ft '];
const PRIMARY_SPLIT_RE = /\s*(?:,|&)\s*|\s+[xX]\s+/;

function renderArtistRow(index, entry) {
  const row = document.createElement('div');
  row.className = 'artist-row';
  row.dataset.index = String(index);
  row.innerHTML = `
    <input type="text" class="artist-name" placeholder="Artist" maxlength="200" value="${entry?.name ? escapeAttr(entry.name) : ''}">
    <div class="artist-role">
      <label><input type="radio" name="role-${index}" value="primary"${entry?.role !== 'featured' ? ' checked' : ''}> Primary</label>
      <label><input type="radio" name="role-${index}" value="featured"${entry?.role === 'featured' ? ' checked' : ''}> Featured</label>
    </div>
    <button type="button" class="artist-remove" aria-label="Remove artist">×</button>
  `;
  row.querySelector('.artist-name').addEventListener('input', () => {
    checkArtistParseHint();
    validateSubmit();
  });
  row.querySelectorAll('input[type=radio]').forEach(r => r.addEventListener('change', validateSubmit));
  row.querySelector('.artist-remove').addEventListener('click', () => {
    row.remove();
    refreshArtistRowsUI();
    validateSubmit();
  });
  return row;
}

function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function refreshArtistRowsUI() {
  const rows = artistsContainer.querySelectorAll('.artist-row');
  rows.forEach((row, i) => {
    row.dataset.index = String(i);
    // Single row: hide role pickers + remove button for cleanliness
    const roleWrap = row.querySelector('.artist-role');
    const removeBtn = row.querySelector('.artist-remove');
    if (rows.length === 1) {
      roleWrap.classList.add('hidden');
      removeBtn.classList.add('hidden');
    } else {
      roleWrap.classList.remove('hidden');
      removeBtn.classList.remove('hidden');
    }
  });
}

function addArtistRow(entry) {
  const rows = artistsContainer.querySelectorAll('.artist-row');
  const row = renderArtistRow(rows.length, entry);
  artistsContainer.appendChild(row);
  refreshArtistRowsUI();
  return row;
}

function collectArtists() {
  const rows = artistsContainer.querySelectorAll('.artist-row');
  const out = [];
  rows.forEach((row, i) => {
    const name = row.querySelector('.artist-name').value.trim();
    if (!name) return;
    const roleInput = row.querySelector('input[name^="role-"]:checked');
    const role = roleInput ? roleInput.value : 'primary';
    out.push({ name, role, position: i });
  });
  return out;
}

function formatArtistString(entries) {
  if (!entries || !entries.length) return '';
  const primaries = entries.filter(e => e.role === 'primary').map(e => e.name);
  const features = entries.filter(e => e.role === 'featured').map(e => e.name);
  let s = primaries.join(' & ');
  if (features.length) s += ' feat. ' + features.join(' & ');
  return s || entries.map(e => e.name).join(' & ');
}

function parseArtistString(raw) {
  if (!raw) return [];
  const text = raw.trim();
  if (!text) return [];
  let primaryPart = text;
  let featuredPart = '';
  const lowered = text.toLowerCase();
  for (const marker of FEATURE_MARKERS) {
    const idx = lowered.indexOf(marker);
    if (idx >= 0) {
      primaryPart = text.slice(0, idx);
      featuredPart = text.slice(idx + marker.length);
      break;
    }
  }
  const primaries = primaryPart.split(PRIMARY_SPLIT_RE).map(s => s.trim()).filter(Boolean);
  const features = featuredPart ? featuredPart.split(PRIMARY_SPLIT_RE).map(s => s.trim()).filter(Boolean) : [];
  let pos = 0;
  const out = [];
  primaries.forEach(n => out.push({ name: n, role: 'primary', position: pos++ }));
  features.forEach(n => out.push({ name: n, role: 'featured', position: pos++ }));
  return out;
}

function checkArtistParseHint() {
  const rows = artistsContainer.querySelectorAll('.artist-row');
  if (rows.length !== 1) {
    artistParseHint.classList.add('hidden');
    return;
  }
  const raw = rows[0].querySelector('.artist-name').value;
  const entries = parseArtistString(raw);
  if (entries.length >= 2) {
    artistParseHint.classList.remove('hidden');
  } else {
    artistParseHint.classList.add('hidden');
  }
}

function doParseArtistString() {
  const rows = artistsContainer.querySelectorAll('.artist-row');
  if (!rows.length) return;
  const raw = rows[0].querySelector('.artist-name').value;
  const entries = parseArtistString(raw);
  if (entries.length < 2) return;
  artistsContainer.innerHTML = '';
  entries.forEach(e => addArtistRow(e));
  artistParseHint.classList.add('hidden');
  validateSubmit();
}

function clearArtistRows() {
  artistsContainer.innerHTML = '';
  addArtistRow();
}

// Initialize with one empty row + wire buttons
addArtistRow();
artistAddBtn.addEventListener('click', () => addArtistRow());
artistParseBtn.addEventListener('click', doParseArtistString);

// --- Page-view beacon ---
function firePageView() {
  try {
    const headers = { 'Content-Type': 'application/json' };
    if (API_KEY) headers['X-Api-Key'] = API_KEY;
    fetch(`${API_BASE}/page-view`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ path: window.location.pathname, title: document.title }),
      keepalive: true,
    }).catch(() => {});
  } catch {}
}
firePageView();

// --- Availability gate ---
// Fetches /api/analyzer/availability on load. When LC is disabled, hides
// the entry/processing/results screens, shows the unavailable screen with
// the admin-supplied message and the subscribe form.
async function checkAvailability() {
  try {
    const headers = {};
    if (API_KEY) headers['X-Api-Key'] = API_KEY;
    const resp = await fetch(`${API_BASE}/availability`, { headers });
    if (!resp.ok) return;  // best-effort; let the user try and surface the 503 later
    const data = await resp.json();
    if (data.available) return;

    const msg = $('#unavail-message');
    if (msg && data.message) msg.textContent = data.message;

    showScreen('screen-unavailable');
    wireSubscribeForm();
  } catch {
    // Network error — let the page render normally so the user can try.
  }
}

function wireSubscribeForm() {
  const form = $('#unavail-subscribe-form');
  const status = $('#unavail-status');
  const submit = $('#unavail-submit');
  if (!form || form.dataset.wired) return;
  form.dataset.wired = '1';

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = $('#unavail-email').value.trim();
    const hp = $('#unavail-hp')?.value || '';
    if (!email) return;

    submit.disabled = true;
    status.className = 'unavail-status';
    status.textContent = 'Sending...';

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (API_KEY) headers['X-Api-Key'] = API_KEY;
      const resp = await fetch(`${API_BASE}/subscribe`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ email, hp_website: hp, turnstile_token: getTurnstileToken() }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = (data && data.detail) || `HTTP ${resp.status}`;
        throw new Error(typeof detail === 'string' ? detail : 'Subscribe failed.');
      }
      status.className = 'unavail-status success';
      status.textContent = data.message || 'Thanks. We\'ll email you when LC is back.';
      $('#unavail-email').value = '';
    } catch (err) {
      status.className = 'unavail-status error';
      status.textContent = err.message || 'Could not subscribe. Try again in a moment.';
    } finally {
      submit.disabled = false;
    }
  });
}

checkAvailability();

// ============================================================
// Donate widget (Stripe Checkout — same account as chadlewine)
// ============================================================
function initDonateWidget() {
  const widget = $('#donate-widget');
  if (!widget || widget.dataset.wired) return;
  widget.dataset.wired = '1';

  const presets = $$('#donate-widget .donate-preset');
  const customInput = $('#donate-custom-input');
  const consent = $('#donate-consent');
  const submit = $('#donate-submit');
  const status = $('#donate-status');
  const terms = $('#donate-terms');
  const termsTrigger = $('#donate-terms-trigger');

  let activeAmount = 5;
  let usingCustom = false;

  function refreshSubmit() {
    const amt = effectiveAmount();
    submit.textContent = `Donate $${amt.toFixed(amt % 1 === 0 ? 0 : 2)}`;
    submit.disabled = !(consent.checked && amt >= 1);
  }

  function effectiveAmount() {
    if (usingCustom) {
      const n = parseFloat(customInput.value);
      return isFinite(n) ? n : 0;
    }
    return activeAmount;
  }

  presets.forEach((btn) => {
    btn.addEventListener('click', () => {
      activeAmount = parseFloat(btn.dataset.amount);
      usingCustom = false;
      customInput.value = '';
      presets.forEach((b) => b.classList.remove('donate-preset--active'));
      btn.classList.add('donate-preset--active');
      refreshSubmit();
    });
  });

  customInput.addEventListener('input', () => {
    if (customInput.value !== '') {
      usingCustom = true;
      presets.forEach((b) => b.classList.remove('donate-preset--active'));
    } else {
      usingCustom = false;
      // Restore last preset selection
      const def = $$('#donate-widget .donate-preset')[0];
      if (def) def.classList.add('donate-preset--active');
      activeAmount = 5;
    }
    refreshSubmit();
  });

  consent.addEventListener('change', refreshSubmit);

  if (termsTrigger) {
    termsTrigger.addEventListener('click', () => {
      terms.classList.toggle('hidden');
    });
  }

  submit.addEventListener('click', async () => {
    const amt = effectiveAmount();
    if (amt < 1 || !consent.checked) return;

    submit.disabled = true;
    const prevText = submit.textContent;
    submit.textContent = 'Redirecting...';
    status.className = 'donate-status';
    status.textContent = '';

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (API_KEY) headers['X-Api-Key'] = API_KEY;
      const origin = window.location.origin;
      const path = window.location.pathname;
      const resp = await fetch(`${API_HOST}/api/donate`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          amount: amt,
          source: 'lyrical_charger',
          success_url: `${origin}${path}?donated={CHECKOUT_SESSION_ID}`,
          cancel_url: `${origin}${path}?donate_cancelled=1`,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        const detail = err.detail || `HTTP ${resp.status}`;
        throw new Error(typeof detail === 'string' ? detail : 'Stripe error');
      }
      const data = await resp.json();
      if (!data.url) throw new Error('No checkout URL returned.');
      // Stripe replaces {CHECKOUT_SESSION_ID} with the real id on the success_url
      window.location.href = data.url;
    } catch (err) {
      status.className = 'donate-status error';
      status.textContent = err.message || 'Could not start checkout. Try again.';
      submit.disabled = false;
      submit.textContent = prevText;
    }
  });

  refreshSubmit();
}

initDonateWidget();

// Entry-screen "Support this tool" link: bring up the donate widget.
// When LC is online, the unavailable screen is hidden but the widget
// inside it is still in the DOM — we toggle to it on click and let
// the user dismiss with the back/refresh.
const entryDonateLink = document.getElementById('entry-donate-link');
if (entryDonateLink) {
  entryDonateLink.addEventListener('click', (e) => {
    e.preventDefault();
    const msg = document.getElementById('unavail-message');
    const headline = document.querySelector('.unavail-headline');
    const subscribeCard = document.querySelector('#screen-unavailable .unavail-card');
    if (headline) headline.textContent = 'Support Lyrical Charger.';
    if (msg) msg.textContent = "Thanks for keeping this tool alive and free.";
    if (subscribeCard) subscribeCard.style.display = 'none';
    showScreen('screen-unavailable');
  });
}

// Donation return: ?donated=cs_test_... means Stripe redirected here
// after a successful checkout. Show the thanks screen.
function handleDonationReturn() {
  const params = new URLSearchParams(window.location.search);
  if (params.has('donated')) {
    showScreen('screen-thanks');
    const continueBtn = document.getElementById('thanks-continue');
    if (continueBtn) {
      continueBtn.addEventListener('click', () => {
        const url = window.location.origin + window.location.pathname;
        window.location.href = url;
      });
    }
  }
}
handleDonationReturn();

async function initBotProtection() {
  try {
    const headers = {};
    if (API_KEY) headers['X-Api-Key'] = API_KEY;
    const resp = await fetch(`${API_BASE}/config`, { headers });
    if (!resp.ok) return;
    const cfg = await resp.json();
    if (!cfg.turnstile_site_key) return;

    // Inject Turnstile script
    const script = document.createElement('script');
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onTurnstileLoad';
    script.async = true;
    script.defer = true;
    window.onTurnstileLoad = () => {
      const mount = $('#turnstile-mount');
      if (!mount) return;
      mount.classList.remove('hidden');
      turnstileWidgetId = window.turnstile.render(mount, {
        sitekey: cfg.turnstile_site_key,
        theme: 'dark',
        size: 'flexible',
      });
    };
    document.head.appendChild(script);
  } catch {
    // Bot protection is best-effort; swallow errors so the page still works.
  }
}
initBotProtection();

// ============================================================
// Screen Management
// ============================================================
function showScreen(id) {
  $$('.screen').forEach((s) => s.classList.remove('active'));
  $(`#${id}`).classList.add('active');
  window.scrollTo(0, 0);
}

// ============================================================
// Tab Switching
// ============================================================
tabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    const target = tab.dataset.tab;
    if (target === activeTab) return;
    activeTab = target;
    tabs.forEach((t) => t.classList.toggle('active', t.dataset.tab === target));
    tabPaste.classList.toggle('active', target === 'paste');
    tabSearch.classList.toggle('active', target === 'search');
    selectedTrack = null;
    hideError();
    validateSubmit();
    if (target === 'search') validateSearch();
  });
});

// ============================================================
// Validation
// ============================================================
function validateSubmit() {
  let valid = false;

  if (activeTab === 'paste') {
    const title = inputTitle.value.trim();
    const artists = collectArtists();
    const lyrics = lyricsInput.value.trim();
    const hasTitle = title.length >= 1;
    const hasArtist = artists.length >= 1;
    const hasLyrics = lyrics.length >= 20;
    const consented = consentCheck.checked;
    valid = hasTitle && hasArtist && hasLyrics && consented;
  } else if (activeTab === 'search') {
    valid = selectedTrack !== null && consentCheck.checked;
  }

  btnSubmit.disabled = !valid;
}

function validateSearch() {
  btnSearch.disabled = searchQuery.value.trim().length < 1;
}

lyricsInput.addEventListener('input', validateSubmit);
inputTitle.addEventListener('input', validateSubmit);
consentCheck.addEventListener('change', validateSubmit);
searchQuery.addEventListener('input', validateSearch);
searchQuery.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !btnSearch.disabled) doSearch(); });

// ============================================================
// Progress Bar
// ============================================================
const STAGES = [
  { pct: 8,  label: 'Validating input',           detail: 'Checking lyrics structure...' },
  { pct: 18, label: 'Reading lyrics',              detail: 'Parsing line by line...' },
  { pct: 30, label: 'Loading rubric',              detail: '58 tenets across 5 tiers...' },
  { pct: 50, label: 'Calibrating',                 detail: 'Building case from zero...' },
  { pct: 68, label: 'Evaluating contamination',    detail: 'Checking for hidden messages...' },
  { pct: 80, label: 'Calculating charge',          detail: 'Mapping position within tier...' },
];

// Tail phase. After the rubric pass the backend makes two more Opus calls
// (per-listener prose, then per-society prose) and then saves -- this is the
// slow stretch that used to leave the bar frozen at 90%. Each sub-step creeps
// the bar and lights up its row in #proc-substeps so the wait reads as motion.
// The bar holds at the last sub-step until the response lands (completeProgress
// jumps it to 100); if a call runs long, the user sees a live "what it's doing"
// row rather than a stuck percentage.
const SUBSTAGES = [
  { pct: 87, step: 0, label: 'Writing your reading' },
  { pct: 93, step: 1, label: 'Writing the societal reading' },
  { pct: 97, step: 2, label: 'Finalizing' },
];

let progressTimer = null;
let currentStage = 0;

function resetSubsteps() {
  if (!procSubsteps) return;
  procSubsteps.classList.add('hidden');
  procSubsteps.querySelectorAll('li').forEach((li) => li.classList.remove('is-active', 'is-done'));
}

function setSubstepActive(step) {
  if (!procSubsteps) return;
  procSubsteps.classList.remove('hidden');
  procSubsteps.querySelectorAll('li').forEach((li, idx) => {
    li.classList.toggle('is-done', idx < step);
    li.classList.toggle('is-active', idx === step);
  });
}

function resetProgress() {
  currentStage = 0;
  procBarFill.style.width = '0%';
  procPct.textContent = '0%';
  procStage.textContent = 'Preparing...';
  procDetail.textContent = '';
  resetSubsteps();
}

function setProgress(pct, label, detail) {
  procBarFill.style.width = `${pct}%`;
  procPct.textContent = `${pct}%`;
  procStage.textContent = label;
  procDetail.textContent = detail || '';
}

function startProgress() {
  resetProgress();
  currentStage = 0;

  // Main stages: early ones go fast, calibration lingers.
  function advanceMain() {
    if (currentStage >= STAGES.length) { advanceSub(0); return; }
    const stage = STAGES[currentStage];
    setProgress(stage.pct, stage.label, stage.detail);
    currentStage++;
    const delay = currentStage <= 2 ? 600 : 2500;
    progressTimer = setTimeout(advanceMain, delay);
  }

  // Tail sub-steps for the prose generation. Hold on the last one; the real
  // response (completeProgress) is what finishes the bar.
  function advanceSub(i) {
    if (i >= SUBSTAGES.length) return;
    const sub = SUBSTAGES[i];
    setProgress(sub.pct, 'Generating your reading', '');
    setSubstepActive(sub.step);
    progressTimer = setTimeout(() => advanceSub(i + 1), 5000);
  }

  advanceMain();
}

function completeProgress() {
  if (progressTimer) clearTimeout(progressTimer);
  if (procSubsteps) {
    procSubsteps.querySelectorAll('li').forEach((li) => {
      li.classList.remove('is-active');
      li.classList.add('is-done');
    });
  }
  setProgress(100, 'Complete', 'Calibration ready.');
}

function stopProgress() {
  if (progressTimer) clearTimeout(progressTimer);
}

// ============================================================
// Submit
// ============================================================
btnSubmit.addEventListener('click', handleSubmit);

function handleSubmit() {
  if (activeTab === 'search' && selectedTrack) {
    submitSearch();
  } else {
    submitLyrics();
  }
}

// Mirrors backend detect_prose_like. Returns reason string if prose-like.
function detectProseLike(text) {
  const lines = text.split(/\r?\n/).map(l => l.replace(/\s+$/, '')).filter(l => l.trim());
  if (!lines.length) return null;
  const words = text.match(/\S+/g) || [];
  if (words.length < 40) return null;
  const longest = Math.max(...lines.map(l => l.length));
  if (longest > 300) return 'One or more lines are very long (over 300 characters). Song lyrics are usually broken into short lines.';
  const avg = lines.reduce((s, l) => s + l.length, 0) / lines.length;
  if (avg > 100) return 'The average line is very long. Song lyrics usually break every few words, not in paragraph-like chunks.';
  const ratio = lines.length / Math.max(words.length, 1);
  if (ratio < 0.05) return 'Line breaks are sparse — this reads more like prose than lyrics.';
  const lowered = lines.map(l => l.toLowerCase().trim());
  const unique = new Set(lowered);
  const dupes = lowered.length - unique.size;
  if (dupes === 0 && words.length > 250 && avg > 60) return 'Nothing repeats here. Song lyrics typically have a refrain or repeated lines. This reads like prose.';
  return null;
}

async function submitLyrics() {
  hideError();
  btnSubmit.disabled = true;

  const lyrics = lyricsInput.value.trim();
  if (lyrics.length < 20) {
    showError('Paste at least a few lines of lyrics for an accurate reading.');
    btnSubmit.disabled = false;
    return;
  }

  const title = inputTitle.value.trim();
  const artistEntries = collectArtists();
  const artist = formatArtistString(artistEntries);

  if (!title || !artist) {
    showError('Song title and artist are required.');
    btnSubmit.disabled = false;
    return;
  }

  showScreen('screen-processing');
  startProgress();

  try {
    const headers = await calibrateHeaders();

    const resp = await fetch(`${API_BASE}/calibrate-lyrics`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        lyrics, title, artist,
        artists: artistEntries.map(e => ({ name: e.name, role: e.role })),
        hp_website: getHpValue(),
        turnstile_token: getTurnstileToken(),
      }),
    });

    // Turnstile tokens are single-use — always reset so the next submission has a fresh one.
    resetTurnstile();

    if (resp.status === 429) {
      stopProgress();
      showError("You've hit the free daily limit (20 readings). Try again tomorrow.");
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    if (resp.status === 503) {
      // LC was flipped to disabled while this page was open.
      stopProgress();
      const data = await resp.json().catch(() => ({}));
      const msg = data?.detail?.message || 'Lyrical Charger is temporarily offline.';
      const u = $('#unavail-message');
      if (u) u.textContent = msg;
      showScreen('screen-unavailable');
      wireSubscribeForm();
      btnSubmit.disabled = false;
      return;
    }

    if (!resp.ok) {
      stopProgress();
      const data = await resp.json().catch(() => ({}));
      showError(data.detail || 'Calibration failed. Try again.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    const data = await resp.json();

    if (data.status === 'lyrics_mismatch' || data.status === 'lyrics_diverge_from_prior') {
      stopProgress();
      showError(data.block_reason || 'These lyrics don\'t appear to match this song.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    if (data.status === 'error') {
      stopProgress();
      showError('Could not calibrate these lyrics. Try different lyrics or check formatting.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    completeProgress();
    // Brief pause on 100% before showing results
    await new Promise((r) => setTimeout(r, 600));

    renderResults(data);
    showScreen('screen-results');
  } catch (e) {
    stopProgress();
    resetTurnstile();
    showError('Connection error. Check your internet and try again.');
    showScreen('screen-entry');
    btnSubmit.disabled = false;
  }
}

// ============================================================
// Search Flow
// ============================================================
btnSearch.addEventListener('click', doSearch);

async function doSearch() {
  hideError();
  btnSearch.disabled = true;
  searchResults.innerHTML = '';
  searchStatus.classList.remove('hidden');
  searchStatus.textContent = 'Searching...';
  selectedTrack = null;
  validateSubmit();

  try {
    const headers = { 'Content-Type': 'application/json' };
    if (API_KEY) headers['X-Api-Key'] = API_KEY;

    const resp = await fetch(`${API_BASE}/search-songs`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        query: searchQuery.value.trim(),
        artist: searchArtist.value.trim(),
      }),
    });

    if (resp.status === 429) {
      searchStatus.textContent = 'Too many searches. Try again later.';
      btnSearch.disabled = false;
      return;
    }

    if (!resp.ok) {
      searchStatus.textContent = 'Search failed. Try again.';
      btnSearch.disabled = false;
      return;
    }

    const data = await resp.json();

    if (data.message && (!data.results || data.results.length === 0)) {
      searchStatus.textContent = data.message;
      btnSearch.disabled = false;
      return;
    }

    searchStatus.classList.add('hidden');
    renderSearchResults(data.results);
  } catch (e) {
    searchStatus.textContent = 'Connection error. Check your internet.';
  }

  btnSearch.disabled = false;
}

function renderSearchResults(results) {
  searchResults.innerHTML = results.map(r => `
    <div class="search-result-item" data-track-id="${r.track_id}" data-title="${esc(r.title)}" data-artist="${esc(r.artist)}">
      <div class="search-result-info">
        <div class="search-result-title">${esc(r.title)}</div>
        <div class="search-result-artist">${esc(r.artist)}</div>
        ${r.album ? `<div class="search-result-album">${esc(r.album)}</div>` : ''}
      </div>
      <div class="search-result-action">Calibrate</div>
    </div>
  `).join('');

  // Click handlers
  searchResults.querySelectorAll('.search-result-item').forEach(item => {
    item.addEventListener('click', () => selectTrack(item));
  });
}

function selectTrack(item) {
  // Deselect previous
  searchResults.querySelectorAll('.search-result-item').forEach(el => {
    el.style.borderColor = '';
    el.style.background = '';
  });
  // Select this one
  item.style.borderColor = 'var(--rc-accent)';
  item.style.background = 'rgba(0, 212, 170, 0.08)';

  selectedTrack = {
    track_id: parseInt(item.dataset.trackId),
    title: item.dataset.title,
    artist: item.dataset.artist,
  };
  validateSubmit();
}

async function submitSearch() {
  hideError();
  btnSubmit.disabled = true;

  showScreen('screen-processing');
  startProgress();

  try {
    const headers = await calibrateHeaders();

    const resp = await fetch(`${API_BASE}/calibrate-search`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        ...selectedTrack,
        hp_website: getHpValue(),
        turnstile_token: getTurnstileToken(),
      }),
    });

    // Turnstile tokens are single-use — always reset so the next submission has a fresh one.
    resetTurnstile();

    if (resp.status === 429) {
      stopProgress();
      showError("You've hit the free daily limit (20 readings). Try again tomorrow.");
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    if (resp.status === 404) {
      stopProgress();
      showError('Lyrics not available for this track. Try pasting lyrics directly.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    if (!resp.ok) {
      stopProgress();
      const data = await resp.json().catch(() => ({}));
      showError(data.detail || 'Calibration failed. Try again.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    const data = await resp.json();

    if (data.status === 'error') {
      stopProgress();
      showError('Could not calibrate this song. Try a different one.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    completeProgress();
    await new Promise((r) => setTimeout(r, 600));

    renderResults(data);
    showScreen('screen-results');
  } catch (e) {
    stopProgress();
    resetTurnstile();
    showError('Connection error. Check your internet and try again.');
    showScreen('screen-entry');
    btnSubmit.disabled = false;
  }
}

// ============================================================
// Results Rendering
// ============================================================
function renderResults(data) {
  // Identity
  if (data.title && data.title !== 'Untitled') {
    let html = `<div class="result-song-title">${esc(data.title)}</div>`;
    if (data.artist && data.artist !== 'Unknown') {
      html += `<div class="result-song-artist">${esc(data.artist)}</div>`;
    }
    resultIdentity.innerHTML = html;
    resultIdentity.style.display = '';
  } else {
    resultIdentity.style.display = 'none';
  }

  // Calibration badge + charge
  const color = TIER_COLORS[data.tier] || 'green';
  const label = data.tier_label || TIER_LABELS[data.tier] || 'Decent';
  const charge = data.charge != null ? data.charge : 0;
  const sign = charge >= 0 ? '+' : '';
  const chargeClass = getChargeClass(charge);

  resultCalibration.innerHTML = `
    <span class="result-compass-badge" aria-hidden="true">
      <svg viewBox="0 0 32 32" width="48" height="48">
        <rect width="32" height="32" rx="6" fill="#0a0a14"/>
        <path d="M 5,20 A 11,11 0 0,1 7.6,13.1" fill="none" stroke="#9933ff" stroke-width="6" stroke-linecap="butt"/>
        <path d="M 7.6,13.1 A 11,11 0 0,1 12.6,9.6" fill="none" stroke="#3388ff" stroke-width="6" stroke-linecap="butt"/>
        <path d="M 12.6,9.6 A 11,11 0 0,1 19.4,9.6" fill="none" stroke="#33cc55" stroke-width="6" stroke-linecap="butt"/>
        <path d="M 19.4,9.6 A 11,11 0 0,1 24.4,13.1" fill="none" stroke="#ffbb33" stroke-width="6" stroke-linecap="butt"/>
        <path d="M 24.4,13.1 A 11,11 0 0,1 27,20" fill="none" stroke="#ff3333" stroke-width="6" stroke-linecap="butt"/>
        <polygon points="16,10 14.2,20 17.8,20" fill="#eeeef4"/>
        <circle cx="16" cy="20" r="3" fill="#00d4aa"/>
      </svg>
    </span>
    <div class="charge-display ${chargeClass}">${sign}${charge}</div>
    <span class="tier-badge-label">${label}</span>
  `;

  // Summary
  if (data.charge_summary) {
    resultSummary.innerHTML = `<p class="summary-text">${esc(data.charge_summary)}</p>`;
    resultSummary.style.display = '';
  } else {
    resultSummary.style.display = 'none';
  }

  // Per-listener + per-society prose. On LC we tease an excerpt only -- the
  // full reading lives on the song detail page (CTA below). If there's no
  // song_slug to route to, fall back to the full prose so the user isn't
  // left with a truncated teaser and nowhere to read the rest.
  if (data.song_slug) {
    renderProseExcerpt(resultEffects, resultEffectsBody, data.effects_prose);
    renderProseExcerpt(resultSocietal, resultSocietalBody, data.societal_effects_prose);
  } else {
    renderProse(resultEffects, resultEffectsBody, data.effects_prose);
    renderProse(resultSocietal, resultSocietalBody, data.societal_effects_prose);
  }

  // Consensus across prior runs
  if (data.consensus && data.consensus.run_count >= 2) {
    const c = data.consensus;
    const cColor = TIER_COLORS[c.rubric_color] || 'green';
    const cLabel = TIER_LABELS[c.rubric_color] || '';
    const cCharge = c.charge_value;
    const cSign = cCharge >= 0 ? '+' : '';
    const agreement = (c.rubric_color === data.tier) ? 'agreement' : 'divergence';
    resultConsensus.innerHTML = `
      <div class="consensus-head">
        <span class="consensus-label">Consensus across ${c.run_count} runs</span>
        <span class="consensus-${agreement}">${agreement === 'agreement' ? 'Agrees' : 'Diverges'}</span>
      </div>
      <div class="consensus-body">
        <div class="consensus-tier">
          <span class="tier-badge-dot" style="background: var(--tier-${cColor})"></span>
          <span>${cLabel}</span>
          <span class="consensus-charge">${cSign}${cCharge}</span>
        </div>
      </div>
      <p class="consensus-explain">Each reading refines the compass. This song has been calibrated ${c.run_count} times; the weighted average (by confidence) is what the canonical record tracks.</p>
    `;
    resultConsensus.classList.remove('hidden');
  } else {
    resultConsensus.classList.add('hidden');
  }

  // Contamination
  if (data.contaminated && data.contamination_note) {
    resultContamination.innerHTML = `
      <div class="contam-label">Contamination Detected</div>
      <div class="contam-note">${esc(data.contamination_note)}</div>
    `;
    resultContamination.classList.remove('hidden');
  } else {
    resultContamination.classList.add('hidden');
  }

  // Misread link + view-details link
  const misreadParams = new URLSearchParams();
  misreadParams.set('title', data.title || '');
  misreadParams.set('artist', data.artist || '');
  misreadParams.set('color', data.tier || '');
  if (data.charge_summary) misreadParams.set('cs', data.charge_summary);
  const detailsLink = data.song_slug
    ? `<a href="/songs/${encodeURIComponent(data.song_slug)}" class="details-cta">Read the full reading -&gt;</a>`
    : '';
  resultMisread.innerHTML = `
    ${detailsLink}
    <a href="/misread-submission.html?${misreadParams.toString()}" class="misread-link">Did we get it wrong?</a>
  `;
}

// ============================================================
// Calibrate Another
// ============================================================
btnAgain.addEventListener('click', () => {
  lyricsInput.value = '';
  inputTitle.value = '';
  clearArtistRows();
  searchQuery.value = '';
  searchArtist.value = '';
  searchResults.innerHTML = '';
  searchStatus.classList.add('hidden');
  selectedTrack = null;
  consentCheck.checked = false;
  btnSubmit.disabled = true;
  btnSearch.disabled = true;
  resetTurnstile();
  hideError();
  showScreen('screen-entry');
});

// ============================================================
// Helpers
// ============================================================
function esc(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// Render blank-line-separated prose into <p> blocks, or hide the section
// when the backend returned nothing (generation failed soft).
function renderProse(section, body, prose) {
  if (!section || !body) return;
  const text = (prose || '').trim();
  if (!text) {
    section.classList.add('hidden');
    body.innerHTML = '';
    return;
  }
  const paras = text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  body.innerHTML = paras.map((p) => `<p>${esc(p)}</p>`).join('');
  section.classList.remove('hidden');
}

// LC teases the prose: first paragraph capped at a word boundary, marked as
// an excerpt. The full multi-paragraph reading lives on the song detail page.
const PROSE_EXCERPT_CHARS = 220;

function proseExcerpt(prose) {
  const text = (prose || '').trim();
  if (!text) return null;
  const firstPara = text.split(/\n\s*\n/)[0].trim();
  const truncated = firstPara.length > PROSE_EXCERPT_CHARS;
  if (!truncated) return { text: firstPara, truncated: false };
  const slice = firstPara.slice(0, PROSE_EXCERPT_CHARS);
  const cut = slice.slice(0, slice.lastIndexOf(' ')).replace(/[\s,;:.!?-]+$/, '');
  return { text: `${cut}...`, truncated: true };
}

function renderProseExcerpt(section, body, prose) {
  if (!section || !body) return;
  const ex = proseExcerpt(prose);
  if (!ex) {
    section.classList.add('hidden');
    body.innerHTML = '';
    return;
  }
  const tag = ex.truncated ? '<span class="prose-excerpt-tag">Excerpt</span>' : '';
  body.innerHTML = `<p>${esc(ex.text)}${tag}</p>`;
  section.classList.remove('hidden');
}

function getChargeClass(charge) {
  if (charge == null) return '';
  if (charge >= 75) return 'charge-high-positive';
  if (charge >= 25) return 'charge-positive';
  if (charge >= -24) return 'charge-neutral';
  if (charge >= -74) return 'charge-negative';
  return 'charge-high-negative';
}

function showError(msg) {
  errorMessage.textContent = msg;
  errorMessage.classList.remove('hidden');
}

function hideError() {
  errorMessage.classList.add('hidden');
  errorMessage.textContent = '';
}
