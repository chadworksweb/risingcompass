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
const resultDogma = $('#result-dogma');
const resultMisread = $('#result-misread');
const btnAgain = $('#btn-again');
const btnShare = $('#btn-share');
const chargeCardModal = $('#charge-card-modal');
const chargeCardCanvas = $('#charge-card-canvas');
const btnShareNative = $('#btn-share-native');
const btnShareDownload = $('#btn-share-download');
const btnShareClose = $('#btn-share-close');

// --- State ---
let activeTab = 'paste';
let selectedTrack = null;  // { track_id, title, artist }
let turnstileWidgetId = null;
let lastResult = null;     // last calibration payload, for the charge card

// --- Auth (optional) ---
// When a user is signed in (Clerk via /js/auth.js), we attach their bearer
// token to calibrate calls so the reading is saved to their account under
// "Songs you've calibrated." Anonymous readings still work -- the token is
// simply absent. Auth.init() is fired early but never blocks the page.
if (window.Auth) { window.Auth.init().catch(() => {}); }

// PostHog event helper. No-op unless the lib actually loaded (it is gated off
// on localhost, for admin sessions, and for opted-out devices in the
// analytics partial), so these calls are always safe.
function phCapture(event, props) {
  try {
    if (window.posthog && window.posthog.__loaded) window.posthog.capture(event, props || {});
  } catch (_) {}
}

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
// Donate widget (Stripe Checkout — dedicated Rising Compass account)
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

// "Support this tool" is a real page VIEW (URL ?support), not a hashless
// modal. Opening it pushes a history entry so the browser Back button pops
// back to the charger the user was on -- never out to the homepage. Both
// the song-entry and album-entry links route through openSupportView().
// The view reuses the donate widget that lives inside #screen-unavailable.
let supportReturnScreen = 'screen-entry';

function renderSupportView(headlineText) {
  const headline = document.querySelector('.unavail-headline');
  const msg = document.getElementById('unavail-message');
  const subscribeCard = document.querySelector('#screen-unavailable .unavail-card');
  if (headline) headline.textContent = headlineText || 'Support Lyrical Charger.';
  if (msg) msg.textContent = 'Thanks for keeping this tool alive and free.';
  if (subscribeCard) subscribeCard.style.display = 'none';
  showScreen('screen-unavailable');
}

function openSupportView(headlineText, returnScreen) {
  supportReturnScreen = returnScreen || 'screen-entry';
  renderSupportView(headlineText);
  // Add a history entry only if we aren't already on the support URL, so a
  // repeat click doesn't stack duplicate states. Back from here pops to the
  // charger (handled by the popstate listener below).
  if (!new URLSearchParams(window.location.search).has('support')) {
    history.pushState(
      { view: 'support', ret: supportReturnScreen, headline: headlineText || '' },
      '',
      window.location.pathname + '?support',
    );
  }
}

function closeSupportView() {
  // Restore the subscribe card we hid so a later genuine "unavailable" state
  // still shows it, then return to the charger screen we came from.
  const subscribeCard = document.querySelector('#screen-unavailable .unavail-card');
  if (subscribeCard) subscribeCard.style.display = '';
  showScreen(supportReturnScreen || 'screen-entry');
}

window.addEventListener('popstate', (e) => {
  const onSupport = new URLSearchParams(window.location.search).has('support');
  const unavail = document.getElementById('screen-unavailable');
  const showingUnavail = unavail && unavail.classList.contains('active');
  if (onSupport && !showingUnavail) {
    // Forward/restore navigation back onto the support view.
    if (e.state && e.state.ret) supportReturnScreen = e.state.ret;
    renderSupportView(e.state && e.state.headline);
  } else if (!onSupport && showingUnavail) {
    // Back pressed out of the support view -> return to the charger.
    closeSupportView();
  }
});

const entryDonateLink = document.getElementById('entry-donate-link');
if (entryDonateLink) {
  entryDonateLink.addEventListener('click', (e) => {
    e.preventDefault();
    openSupportView('Support Lyrical Charger.', 'screen-entry');
  });
}

// Deep link / refresh on ?support: drop a clean charger entry behind the
// support view (replaceState to the bare path, then openSupportView pushes
// ?support) so Back still lands on the charger rather than the prior site.
if (new URLSearchParams(window.location.search).has('support')) {
  history.replaceState({}, '', window.location.pathname);
  openSupportView('Support Lyrical Charger.', 'screen-entry');
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
      phCapture('paywall_hit', { surface: 'charger_single', reason: 'daily_limit', signed_in: !!(window.Auth && window.Auth.isSignedIn()) });
      showError("You've hit the daily limit. Sign in and pick up a credit pack to keep going, or try again tomorrow.");
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    if (resp.status === 402) {
      // Credit-gated path (M3). Signed-in user without enough credits.
      stopProgress();
      phCapture('paywall_hit', { surface: 'charger_single', reason: 'out_of_credits', signed_in: true });
      showError("Out of credits. Pick up a credit pack or subscribe from your Account page to keep charging songs.");
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      try { window.location.assign('/account/?reason=out_of_credits&returnTo=' + encodeURIComponent(window.location.pathname)); } catch (_) {}
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
      const d = data.detail;
      if (d && typeof d === 'object') {
        // Structured rejection (e.g. lyrics_rejected) carries an appeal link.
        showError(d.reason || d.message || 'Calibration failed. Try again.', d.appeal_url);
      } else {
        showError(d || 'Calibration failed. Try again.');
      }
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
      showError("You've hit the daily limit. Sign in and pick up a credit pack to keep going, or try again tomorrow.");
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    if (resp.status === 402) {
      stopProgress();
      showError("Out of credits. Pick up a credit pack or subscribe from your Account page to keep charging songs.");
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      try { window.location.assign('/account/?reason=out_of_credits&returnTo=' + encodeURIComponent(window.location.pathname)); } catch (_) {}
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
      const d = data.detail;
      if (d && typeof d === 'object') {
        // Structured rejection (e.g. lyrics_rejected) carries an appeal link.
        showError(d.reason || d.message || 'Calibration failed. Try again.', d.appeal_url);
      } else {
        showError(d || 'Calibration failed. Try again.');
      }
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
  lastResult = data;
  phCapture('song_charged', {
    tier: data.tier,
    charge: data.charge,
    contaminated: !!data.contaminated,
    has_consensus: !!(data.consensus && data.consensus.run_count >= 2),
    signed_in: !!(window.Auth && window.Auth.isSignedIn()),
  });
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
        <path d="M 5,20 A 11,11 0 0,1 5.84,15.79" fill="none" stroke="#9933ff" stroke-width="6" stroke-linecap="butt"/>
        <path d="M 5.84,15.79 A 11,11 0 0,1 11.79,9.84" fill="none" stroke="#3388ff" stroke-width="6" stroke-linecap="butt"/>
        <path d="M 11.79,9.84 A 11,11 0 0,1 20.21,9.84" fill="none" stroke="#33cc55" stroke-width="6" stroke-linecap="butt"/>
        <path d="M 20.21,9.84 A 11,11 0 0,1 26.16,15.79" fill="none" stroke="#ffbb33" stroke-width="6" stroke-linecap="butt"/>
        <path d="M 26.16,15.79 A 11,11 0 0,1 27,20" fill="none" stroke="#ff3333" stroke-width="6" stroke-linecap="butt"/>
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

  // Dogma reference — parallel tag to contamination.
  if (data.dogma_referenced && data.dogma_note) {
    resultDogma.innerHTML = `
      <div class="dogma-label">&#x1F4DC; Dogma Reference</div>
      <div class="dogma-note">${esc(data.dogma_note)}</div>
    `;
    resultDogma.classList.remove('hidden');
  } else {
    resultDogma.classList.add('hidden');
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
// Charge card (renders the last reading to a downloadable/shareable image)
// ============================================================
function closeChargeCardModal() {
  if (!chargeCardModal) return;
  chargeCardModal.classList.remove('open');
  chargeCardModal.setAttribute('aria-hidden', 'true');
}

if (btnShare) {
  btnShare.addEventListener('click', async () => {
    if (!lastResult || !window.RCChargeCard) return;
    btnShare.disabled = true;
    try {
      await window.RCChargeCard.render(lastResult, chargeCardCanvas);
      if (btnShareNative) {
        const canNative = !!(navigator.canShare && window.File);
        btnShareNative.style.display = canNative ? '' : 'none';
      }
      chargeCardModal.classList.add('open');
      chargeCardModal.setAttribute('aria-hidden', 'false');
    } catch (_) {
      /* render failure: leave the modal closed */
    } finally {
      btnShare.disabled = false;
    }
  });
}

if (btnShareClose) btnShareClose.addEventListener('click', closeChargeCardModal);
if (chargeCardModal) {
  chargeCardModal.addEventListener('click', (e) => { if (e.target === chargeCardModal) closeChargeCardModal(); });
}
if (btnShareNative) {
  btnShareNative.addEventListener('click', () => {
    if (window.RCChargeCard && lastResult) window.RCChargeCard.shareOrDownload(chargeCardCanvas, lastResult, false);
  });
}
if (btnShareDownload) {
  btnShareDownload.addEventListener('click', () => {
    if (window.RCChargeCard && lastResult) window.RCChargeCard.shareOrDownload(chargeCardCanvas, lastResult, true);
  });
}

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

function showError(msg, appealUrl) {
  errorMessage.textContent = '';
  errorMessage.appendChild(document.createTextNode(msg));
  if (appealUrl) {
    errorMessage.appendChild(document.createTextNode(' '));
    const a = document.createElement('a');
    a.href = appealUrl;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = 'Appeal this';
    errorMessage.appendChild(a);
  }
  errorMessage.classList.remove('hidden');
}

function hideError() {
  errorMessage.classList.add('hidden');
  errorMessage.textContent = '';
}

/* ============================================================
   ALBUM CHARGER
   A second top-level mode alongside the single-song charger. Reuses the
   shared helpers above (showScreen, parse/formatArtistString, esc, escapeAttr,
   getChargeClass, renderProse, calibrateHeaders, getHpValue,
   getTurnstileToken, resetTurnstile, the progress bar, TIER_* maps). Owns its
   own form state, artist rows, track builder, validation, submit, and results.
   ============================================================ */
(function initAlbumCharger() {
  // --- Top-level mode tabs (Song Charger / Album Charger) ---
  const topTabBar = $('#top-tab-bar');
  const topTabs = $$('.top-tab');
  // Album Charger kill switch (resolved from /config below). Fail-closed:
  // until config resolves the Album tab + bar stay hidden, so a closed album
  // never flashes. Song Charger is always available.
  let albumChargerEnabled = false;

  // Album entry refs
  const albumTitleInput = $('#album-input-title');
  const albumArtistsContainer = $('#album-artists-input');
  const albumArtistAddBtn = $('#album-artist-add-btn');
  const albumArtistParseHint = $('#album-artist-parse-hint');
  const albumArtistParseBtn = $('#album-artist-parse-btn');
  const albumReleaseType = $('#album-release-type');
  const albumReleaseDate = $('#album-release-date');
  const albumReleaseYear = $('#album-release-year');
  const albumTracksContainer = $('#album-tracks');
  const albumAddTrackBtn = $('#album-add-track-btn');
  const albumTrackLimitNote = $('#album-track-limit-note');
  const MAX_ALBUM_TRACKS = 15;  // backend enforces this too; over 15 -> inquiry form
  const albumConsent = $('#album-consent-check');
  const albumBtnSubmit = $('#album-btn-submit');
  const albumErrorMessage = $('#album-error-message');

  // Album search refs
  const albumSubTabs = $$('.album-tab');
  const albumTabManual = $('#album-tab-manual');
  const albumTabSearch = $('#album-tab-search');
  const albumSearchQuery = $('#album-search-query');
  const albumSearchArtist = $('#album-search-artist');
  const albumBtnSearch = $('#album-btn-search');
  const albumSearchStatus = $('#album-search-status');
  const albumSearchResults = $('#album-search-results');
  const albumSearchGated = $('#album-search-gated');

  // Album results refs
  const albumResultIdentity = $('#album-result-identity');
  const albumResultCalibration = $('#album-result-calibration');
  const albumResultMeta = $('#album-result-meta');
  const albumResultSummary = $('#album-result-summary');
  const albumResultArc = $('#album-result-arc');
  const albumResultArcBody = $('#album-result-arc-body');
  const albumResultSocietal = $('#album-result-societal');
  const albumResultSocietalBody = $('#album-result-societal-body');
  const albumResultTracks = $('#album-result-tracks');
  const albumResultArtistLink = $('#album-result-artist-link');
  const albumResultCoverPick = $('#album-result-cover-pick');
  const albumBtnAgain = $('#album-btn-again');
  const albumDonateLink = $('#album-donate-link');

  if (!topTabBar || !albumTitleInput) return;  // album DOM absent; bail safely

  let albumActiveSubTab = 'manual';
  let albumSearchEnabled = false;

  const COMPASS_BADGE_SVG = `
    <svg viewBox="0 0 32 32" width="48" height="48">
      <rect width="32" height="32" rx="6" fill="#0a0a14"/>
      <path d="M 5,20 A 11,11 0 0,1 5.84,15.79" fill="none" stroke="#9933ff" stroke-width="6" stroke-linecap="butt"/>
      <path d="M 5.84,15.79 A 11,11 0 0,1 11.79,9.84" fill="none" stroke="#3388ff" stroke-width="6" stroke-linecap="butt"/>
      <path d="M 11.79,9.84 A 11,11 0 0,1 20.21,9.84" fill="none" stroke="#33cc55" stroke-width="6" stroke-linecap="butt"/>
      <path d="M 20.21,9.84 A 11,11 0 0,1 26.16,15.79" fill="none" stroke="#ffbb33" stroke-width="6" stroke-linecap="butt"/>
      <path d="M 26.16,15.79 A 11,11 0 0,1 27,20" fill="none" stroke="#ff3333" stroke-width="6" stroke-linecap="butt"/>
      <polygon points="16,10 14.2,20 17.8,20" fill="#eeeef4"/>
      <circle cx="16" cy="20" r="3" fill="#00d4aa"/>
    </svg>`;

  function showAlbumError(msg) {
    albumErrorMessage.textContent = msg;
    albumErrorMessage.classList.remove('hidden');
  }
  function hideAlbumError() {
    albumErrorMessage.classList.add('hidden');
    albumErrorMessage.textContent = '';
  }

  // ----- Top-tab chrome: hide the mode switcher during processing /
  // unavailable / thanks so the user can't swap modes mid-calibration.
  // Wrap showScreen (classic script: rebinding the hoisted declaration is
  // safe and every caller resolves the name at call time).
  const CHROME_HIDDEN_SCREENS = new Set([
    'screen-processing', 'screen-unavailable', 'screen-thanks',
  ]);
  const _baseShowScreen = showScreen;
  showScreen = function (id) {
    _baseShowScreen(id);
    // Hide the mode-tab bar on chrome-hidden screens, OR whenever the Album
    // Charger is closed (then only the Song Charger exists -- no bar at all).
    if (topTabBar) topTabBar.classList.toggle('hidden', !albumChargerEnabled || CHROME_HIDDEN_SCREENS.has(id));
  };

  // Apply the Album Charger kill switch: show/hide the Album top-tab + bar.
  function applyAlbumChargerGate() {
    topTabs.forEach((t) => {
      if (t.dataset.charger === 'album') t.classList.toggle('hidden', !albumChargerEnabled);
    });
    if (!albumChargerEnabled) {
      if (topTabBar) topTabBar.classList.add('hidden');
      setMode('song');  // never leave the user stranded on a closed album tab
    } else if (topTabBar) {
      topTabBar.classList.remove('hidden');  // config resolved open while on entry
    }
  }

  function setMode(mode) {
    topTabs.forEach((t) => {
      const on = t.dataset.charger === mode;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    if (mode === 'album') {
      hideError();
      showScreen('screen-album-entry');
    } else {
      hideAlbumError();
      showScreen('screen-entry');
    }
  }

  topTabs.forEach((t) => {
    t.addEventListener('click', () => setMode(t.dataset.charger));
  });

  // ----- Album multi-artist rows (own container; mirrors the song rows) -----
  function renderAlbumArtistRow(index, entry) {
    const row = document.createElement('div');
    row.className = 'artist-row';
    row.dataset.index = String(index);
    row.innerHTML = `
      <input type="text" class="artist-name" placeholder="Artist" maxlength="200" value="${entry?.name ? escapeAttr(entry.name) : ''}">
      <div class="artist-role">
        <label><input type="radio" name="album-role-${index}" value="primary"${entry?.role !== 'featured' ? ' checked' : ''}> Primary</label>
        <label><input type="radio" name="album-role-${index}" value="featured"${entry?.role === 'featured' ? ' checked' : ''}> Featured</label>
      </div>
      <button type="button" class="artist-remove" aria-label="Remove artist">&times;</button>
    `;
    row.querySelector('.artist-name').addEventListener('input', () => {
      checkAlbumArtistParseHint();
      validateAlbumSubmit();
    });
    row.querySelectorAll('input[type=radio]').forEach((r) => r.addEventListener('change', validateAlbumSubmit));
    row.querySelector('.artist-remove').addEventListener('click', () => {
      row.remove();
      refreshAlbumArtistRowsUI();
      validateAlbumSubmit();
    });
    return row;
  }

  function refreshAlbumArtistRowsUI() {
    const rows = albumArtistsContainer.querySelectorAll('.artist-row');
    rows.forEach((row, i) => {
      row.dataset.index = String(i);
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

  function addAlbumArtistRow(entry) {
    const rows = albumArtistsContainer.querySelectorAll('.artist-row');
    const row = renderAlbumArtistRow(rows.length, entry);
    albumArtistsContainer.appendChild(row);
    refreshAlbumArtistRowsUI();
    return row;
  }

  function collectAlbumArtists() {
    const rows = albumArtistsContainer.querySelectorAll('.artist-row');
    const out = [];
    rows.forEach((row, i) => {
      const name = row.querySelector('.artist-name').value.trim();
      if (!name) return;
      const roleInput = row.querySelector('input[type=radio]:checked');
      const role = roleInput ? roleInput.value : 'primary';
      out.push({ name, role, position: i });
    });
    return out;
  }

  function checkAlbumArtistParseHint() {
    const rows = albumArtistsContainer.querySelectorAll('.artist-row');
    if (rows.length !== 1) { albumArtistParseHint.classList.add('hidden'); return; }
    const entries = parseArtistString(rows[0].querySelector('.artist-name').value);
    albumArtistParseHint.classList.toggle('hidden', entries.length < 2);
  }

  function doParseAlbumArtistString() {
    const rows = albumArtistsContainer.querySelectorAll('.artist-row');
    if (!rows.length) return;
    const entries = parseArtistString(rows[0].querySelector('.artist-name').value);
    if (entries.length < 2) return;
    albumArtistsContainer.innerHTML = '';
    entries.forEach((e) => addAlbumArtistRow(e));
    albumArtistParseHint.classList.add('hidden');
    validateAlbumSubmit();
  }

  function clearAlbumArtistRows() {
    albumArtistsContainer.innerHTML = '';
    addAlbumArtistRow();
  }

  // ----- Track builder -----
  function renderTrackRow(num, data) {
    const row = document.createElement('div');
    row.className = 'album-track';
    if (data?.track_id != null) row.dataset.trackId = String(data.track_id);
    row.innerHTML = `
      <div class="album-track-head">
        <span class="album-track-num">${num}</span>
        <input type="text" class="album-track-title" placeholder="Track title" maxlength="200" spellcheck="false" value="${data?.title ? escapeAttr(data.title) : ''}">
        <button type="button" class="artist-remove album-track-remove" aria-label="Remove track">&times;</button>
      </div>
      <textarea class="album-track-lyrics" placeholder="Paste this track's lyrics..." rows="4" spellcheck="false">${data?.lyrics ? esc(data.lyrics) : ''}</textarea>
      <div class="album-track-feats"></div>
      <button type="button" class="album-track-feat-add">+ Add featured artist</button>
    `;
    row.querySelector('.album-track-title').addEventListener('input', validateAlbumSubmit);
    row.querySelector('.album-track-lyrics').addEventListener('input', validateAlbumSubmit);
    row.querySelector('.album-track-remove').addEventListener('click', () => {
      row.remove();
      renumberTracks();
      validateAlbumSubmit();
    });
    row.querySelector('.album-track-feat-add').addEventListener('click', () => addTrackFeatured(row));
    // Seed any prefilled featured artists (e.g. loaded from album search later).
    (data?.featured || []).forEach((name) => addTrackFeatured(row, name));
    return row;
  }

  // A per-track featured artist: just a name (role is always "featured").
  // These are layered on top of the album-level artists for that one track.
  function addTrackFeatured(row, name) {
    const feats = row.querySelector('.album-track-feats');
    const featRow = document.createElement('div');
    featRow.className = 'album-track-feat-row';
    featRow.innerHTML = `
      <input type="text" class="album-track-feat-name" placeholder="Featured artist" maxlength="200" spellcheck="false" value="${name ? escapeAttr(name) : ''}">
      <button type="button" class="artist-remove album-track-feat-remove" aria-label="Remove featured artist">&times;</button>
    `;
    featRow.querySelector('.album-track-feat-remove').addEventListener('click', () => featRow.remove());
    feats.appendChild(featRow);
    featRow.querySelector('.album-track-feat-name').focus();
  }

  function renumberTracks() {
    const rows = albumTracksContainer.querySelectorAll('.album-track');
    rows.forEach((row, i) => {
      row.querySelector('.album-track-num').textContent = String(i + 1);
      const removeBtn = row.querySelector('.album-track-remove');
      removeBtn.classList.toggle('hidden', rows.length === 1);
    });
    updateTrackLimitUI();
  }

  // Cap at MAX_ALBUM_TRACKS. At the cap, disable "+ Add track" and surface the
  // inquiry link (longer albums route to the general inquiry form).
  function updateTrackLimitUI() {
    const count = albumTracksContainer.querySelectorAll('.album-track').length;
    const atMax = count >= MAX_ALBUM_TRACKS;
    if (albumAddTrackBtn) {
      albumAddTrackBtn.disabled = atMax;
      albumAddTrackBtn.style.opacity = atMax ? '0.4' : '';
      albumAddTrackBtn.style.cursor = atMax ? 'not-allowed' : '';
    }
    if (albumTrackLimitNote) albumTrackLimitNote.classList.toggle('hidden', !atMax);
  }

  function addTrackRow(data) {
    if (albumTracksContainer.querySelectorAll('.album-track').length >= MAX_ALBUM_TRACKS) return;
    const num = albumTracksContainer.querySelectorAll('.album-track').length + 1;
    albumTracksContainer.appendChild(renderTrackRow(num, data));
    renumberTracks();
  }

  function clearTracks(initialCount) {
    albumTracksContainer.innerHTML = '';
    const n = initialCount || 1;
    for (let i = 0; i < n; i++) addTrackRow();
  }

  // Gather tracks. A row counts if it has a title AND (lyrics >= 20 chars OR a
  // resolved Musixmatch track_id). Returns {title, lyrics?, track_id?, track_number}.
  function collectTracks() {
    const rows = albumTracksContainer.querySelectorAll('.album-track');
    const out = [];
    rows.forEach((row, i) => {
      const title = row.querySelector('.album-track-title').value.trim();
      const lyrics = row.querySelector('.album-track-lyrics').value.trim();
      const trackId = row.dataset.trackId != null ? parseInt(row.dataset.trackId, 10) : null;
      if (!title) return;
      const hasLyrics = lyrics.length >= 20;
      const hasTrackId = trackId != null && albumSearchEnabled;
      if (!hasLyrics && !hasTrackId) return;
      const t = { title, track_number: i + 1 };
      if (hasLyrics) t.lyrics = lyrics;
      if (hasTrackId) t.track_id = trackId;
      const featured = [...row.querySelectorAll('.album-track-feat-name')]
        .map((inp) => inp.value.trim())
        .filter(Boolean);
      if (featured.length) t.featured = featured;
      out.push(t);
    });
    return out;
  }

  // ----- Validation -----
  function validateAlbumSubmit() {
    const title = albumTitleInput.value.trim();
    const artists = collectAlbumArtists();
    const tracks = collectTracks();
    const valid = title.length >= 1 && artists.length >= 1 && tracks.length >= 1 && albumConsent.checked;
    albumBtnSubmit.disabled = !valid;
  }

  // ----- Album sub-tabs (Build Manually / Search Album) -----
  albumSubTabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.albumTab;
      if (target === albumActiveSubTab) return;
      albumActiveSubTab = target;
      albumSubTabs.forEach((t) => t.classList.toggle('active', t.dataset.albumTab === target));
      albumTabManual.classList.toggle('active', target === 'manual');
      albumTabSearch.classList.toggle('active', target === 'search');
      hideAlbumError();
    });
  });

  // ----- Album search (Musixmatch-gated) -----
  function applyAlbumSearchGate() {
    if (albumSearchEnabled) {
      albumSearchGated.classList.add('hidden');
      albumBtnSearch.disabled = albumSearchQuery.value.trim().length < 1;
    } else {
      albumSearchGated.classList.remove('hidden');
      albumSearchGated.textContent =
        'Album search is coming soon. For now, switch to Build Manually and paste the lyrics for each track.';
      albumBtnSearch.disabled = true;
    }
  }

  async function doAlbumSearch() {
    if (!albumSearchEnabled) return;
    hideAlbumError();
    albumBtnSearch.disabled = true;
    albumSearchResults.innerHTML = '';
    albumSearchStatus.classList.remove('hidden');
    albumSearchStatus.textContent = 'Searching...';
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (API_KEY) headers['X-Api-Key'] = API_KEY;
      const resp = await fetch(`${API_BASE}/album/search`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ query: albumSearchQuery.value.trim(), artist: albumSearchArtist.value.trim() }),
      });
      if (!resp.ok) { albumSearchStatus.textContent = 'Search failed. Try again.'; albumBtnSearch.disabled = false; return; }
      const data = await resp.json();
      if (data.message && (!data.results || !data.results.length)) {
        albumSearchStatus.textContent = data.message; albumBtnSearch.disabled = false; return;
      }
      albumSearchStatus.classList.add('hidden');
      renderAlbumSearchResults(data.results);
    } catch (e) {
      albumSearchStatus.textContent = 'Connection error. Check your internet.';
    }
    albumBtnSearch.disabled = false;
  }

  function renderAlbumSearchResults(results) {
    albumSearchResults.innerHTML = results.map((r) => `
      <div class="search-result-item" data-album-id="${r.album_id}" data-title="${escapeAttr(r.title)}" data-artist="${escapeAttr(r.artist)}" data-release-date="${r.release_date ? escapeAttr(r.release_date) : ''}" data-release-year="${r.release_year != null ? r.release_year : ''}">
        <div class="search-result-info">
          <div class="search-result-title">${esc(r.title)}</div>
          <div class="search-result-artist">${esc(r.artist)}</div>
          ${r.release_year ? `<div class="search-result-album">${esc(String(r.release_year))}${r.track_count ? ` &middot; ${r.track_count} tracks` : ''}</div>` : ''}
        </div>
        <div class="search-result-action">Load tracks</div>
      </div>
    `).join('');
    albumSearchResults.querySelectorAll('.search-result-item').forEach((item) => {
      item.addEventListener('click', () => loadAlbumFromSearch(item));
    });
  }

  // Selecting a search result prefills the manual builder (album title, artist,
  // and a track row per track with its Musixmatch track_id). The single submit
  // path then sends track_id rows; the server fetches their lyrics.
  async function loadAlbumFromSearch(item) {
    const albumId = parseInt(item.dataset.albumId, 10);
    albumSearchStatus.classList.remove('hidden');
    albumSearchStatus.textContent = 'Loading tracklist...';
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (API_KEY) headers['X-Api-Key'] = API_KEY;
      const resp = await fetch(`${API_BASE}/album/search-tracks`, {
        method: 'POST', headers, body: JSON.stringify({ album_id: albumId }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.tracks || !data.tracks.length) {
        albumSearchStatus.textContent = (data && data.message) || 'Could not load that tracklist.';
        return;
      }
      albumTitleInput.value = item.dataset.title || '';
      // Trusted release date from the album lookup. Use a full yyyy-mm-dd when
      // present; otherwise fall back to the year only.
      const rd = item.dataset.releaseDate || '';
      if (/^\d{4}-\d{2}-\d{2}$/.test(rd)) {
        albumReleaseDate.value = rd;
        albumReleaseYear.value = rd.slice(0, 4);
      } else if (item.dataset.releaseYear) {
        albumReleaseYear.value = item.dataset.releaseYear;
      }
      clearAlbumArtistRows();
      albumArtistsContainer.querySelector('.artist-name').value = item.dataset.artist || '';
      albumTracksContainer.innerHTML = '';
      data.tracks.forEach((t) => addTrackRow({ title: t.title, track_id: t.track_id }));
      renumberTracks();
      albumSearchStatus.classList.add('hidden');
      // Jump back to the builder so the user sees the loaded tracks.
      albumSubTabs.forEach((t) => t.classList.toggle('active', t.dataset.albumTab === 'manual'));
      albumTabManual.classList.add('active');
      albumTabSearch.classList.remove('active');
      albumActiveSubTab = 'manual';
      validateAlbumSubmit();
    } catch (e) {
      albumSearchStatus.textContent = 'Connection error loading tracklist.';
    }
  }

  // ----- Submit -----
  async function submitAlbum() {
    hideAlbumError();
    albumBtnSubmit.disabled = true;

    const albumTitle = albumTitleInput.value.trim();
    const artistEntries = collectAlbumArtists();
    const artist = formatArtistString(artistEntries);
    const tracks = collectTracks();

    if (!albumTitle || !artist) {
      showAlbumError('Album title and at least one artist are required.');
      albumBtnSubmit.disabled = false;
      return;
    }
    if (!tracks.length) {
      showAlbumError('Add at least one track with pasted lyrics.');
      albumBtnSubmit.disabled = false;
      return;
    }

    const releaseDate = albumReleaseDate.value.trim();  // yyyy-mm-dd or ''
    const yearRaw = albumReleaseYear.value.trim();
    const releaseYear = yearRaw ? parseInt(yearRaw, 10) : null;

    showScreen('screen-processing');
    if (typeof resetProgress === 'function') resetProgress();
    if (typeof setProgress === 'function') setProgress(4, 'Queuing album', 'Preparing tracks...');

    try {
      const headers = await calibrateHeaders();
      const body = {
        album_title: albumTitle,
        artist,
        artists: artistEntries.map((e) => ({ name: e.name, role: e.role })),
        release_type: albumReleaseType.value || 'album',
        tracks,
        hp_website: getHpValue(),
        turnstile_token: getTurnstileToken(),
      };
      // Prefer a full release date; fall back to the year when that's all the
      // user gave. The backend derives the year from the date when present.
      if (/^\d{4}-\d{2}-\d{2}$/.test(releaseDate)) body.release_date = releaseDate;
      if (releaseYear && releaseYear >= 1900 && releaseYear <= 2100) body.release_year = releaseYear;

      // Submit returns a job token immediately; the heavy calibration runs in
      // the background and we poll its status. Keeps every request short so no
      // proxy timeout matters and the work survives the user closing the tab.
      const resp = await fetch(`${API_BASE}/album/calibrate`, {
        method: 'POST', headers, body: JSON.stringify(body),
      });
      resetTurnstile();

      if (resp.status === 402) {
        phCapture('paywall_hit', { surface: 'charger_album', reason: 'out_of_credits', signed_in: true });
        showError("Out of credits for an album of this length. Pick up a credit pack or subscribe from your Account page to keep charging albums.");
        try { window.location.assign('/account/?reason=out_of_credits&returnTo=' + encodeURIComponent(window.location.pathname)); } catch (_) {}
        return;
      }
      if (resp.status === 429) {
        phCapture('paywall_hit', { surface: 'charger_album', reason: 'daily_limit', signed_in: !!(window.Auth && window.Auth.isSignedIn()) });
        showAlbumError("You've hit the free daily album limit. Try again tomorrow.");
        showScreen('screen-album-entry');
        albumBtnSubmit.disabled = false;
        return;
      }
      if (resp.status === 503) {
        const data = await resp.json().catch(() => ({}));
        const msg = data?.detail?.message || 'The Charger is temporarily offline.';
        const u = $('#unavail-message');
        if (u) u.textContent = msg;
        showScreen('screen-unavailable');
        wireSubscribeForm();
        albumBtnSubmit.disabled = false;
        return;
      }
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        showAlbumError((data && typeof data.detail === 'string' && data.detail) || 'Album calibration failed. Try again.');
        showScreen('screen-album-entry');
        albumBtnSubmit.disabled = false;
        return;
      }

      const data = await resp.json();
      if (!data.job_token) {
        showAlbumError('Could not start the album charge. Try again.');
        showScreen('screen-album-entry');
        albumBtnSubmit.disabled = false;
        return;
      }
      albumPollStart = Date.now();
      pollAlbumStatus(data.job_token);
    } catch (e) {
      resetTurnstile();
      showAlbumError('Connection error. Check your internet and try again.');
      showScreen('screen-album-entry');
      albumBtnSubmit.disabled = false;
    }
  }

  // ----- Poll the background job, driving the bar from real progress -----
  const ALBUM_POLL_MS = 3000;
  const ALBUM_MAX_WAIT_MS = 12 * 60 * 1000;  // give up after this; album may still finish server-side
  let albumPollTimer = null;
  let albumPollStart = 0;

  function stopAlbumPoll() {
    if (albumPollTimer) { clearTimeout(albumPollTimer); albumPollTimer = null; }
  }

  function albumProgressFromStatus(s) {
    const phase = s.phase || s.status;
    if (phase === 'calibrating') {
      const total = s.total_tracks || 1;
      const done = s.calibrated_tracks || 0;
      const frac = total ? Math.min(1, done / total) : 0;
      return { pct: Math.round(10 + frac * 68), label: 'Calibrating tracks', detail: `${done} of ${total} tracks read` };
    }
    if (phase === 'synthesizing') return { pct: 85, label: 'Synthesizing the album', detail: 'Reading the album whole...' };
    if (phase === 'writing') return { pct: 93, label: 'Finalizing', detail: 'Attaching to the artist...' };
    return { pct: 6, label: 'Queuing album', detail: 'Preparing tracks...' };
  }

  function scheduleNextAlbumPoll(token) {
    if (Date.now() - albumPollStart > ALBUM_MAX_WAIT_MS) {
      stopAlbumPoll();
      showAlbumError('This is taking longer than expected. The album may still finish — check the artist page in a few minutes.');
      showScreen('screen-album-entry');
      albumBtnSubmit.disabled = false;
      return;
    }
    albumPollTimer = setTimeout(() => pollAlbumStatus(token), ALBUM_POLL_MS);
  }

  async function pollAlbumStatus(token) {
    try {
      const headers = {};
      if (API_KEY) headers['X-Api-Key'] = API_KEY;
      const resp = await fetch(`${API_BASE}/album/status/${encodeURIComponent(token)}`, { headers });
      if (!resp.ok) { scheduleNextAlbumPoll(token); return; }  // transient; keep polling
      const s = await resp.json();

      if (s.status === 'done') {
        stopAlbumPoll();
        const result = s.result || {};
        if (result.status === 'no_tracks') {
          showAlbumError(result.block_reason || 'None of the tracks could be calibrated.');
          showScreen('screen-album-entry');
          albumBtnSubmit.disabled = false;
          return;
        }
        // album_charged for anon only; signed-in album charges are captured
        // server-side (album worker) with the Clerk distinct_id so they merge
        // to the identified person and survive the tab closing mid-poll.
        if (!(window.Auth && window.Auth.isSignedIn())) {
          phCapture('album_charged', {
            tier: result.tier,
            charge: result.charge,
            track_count: result.track_count,
            calibrated_count: result.calibrated_count,
            contamination_count: result.contamination_count,
            release_type: result.release_type,
            signed_in: false,
          });
        }
        completeProgress();
        await new Promise((r) => setTimeout(r, 500));
        renderAlbumResults(result, token);
        showScreen('screen-album-results');
        return;
      }
      if (s.status === 'error') {
        stopAlbumPoll();
        showAlbumError(s.error || 'Album calibration failed. Try again.');
        showScreen('screen-album-entry');
        albumBtnSubmit.disabled = false;
        return;
      }

      const p = albumProgressFromStatus(s);
      if (typeof setProgress === 'function') setProgress(p.pct, p.label, p.detail);
      scheduleNextAlbumPoll(token);
    } catch (e) {
      scheduleNextAlbumPoll(token);  // network blip; keep trying until max wait
    }
  }

  // ----- Results -----
  function renderAlbumResults(data, token) {
    stopAlbumPoll();

    // Identity
    let idHtml = `<div class="result-song-title">${esc(data.album_title || 'Album')}</div>`;
    if (data.artist) idHtml += `<div class="result-song-artist">${esc(data.artist)}</div>`;
    albumResultIdentity.innerHTML = idHtml;

    // Album charge badge
    const color = TIER_COLORS[data.tier] || 'green';
    const label = data.tier_label || TIER_LABELS[data.tier] || 'Decent';
    const charge = data.charge != null ? data.charge : 0;
    const sign = charge >= 0 ? '+' : '';
    albumResultCalibration.innerHTML = `
      <span class="result-compass-badge" aria-hidden="true">${COMPASS_BADGE_SVG}</span>
      <div class="charge-display ${getChargeClass(charge)}">${sign}${charge}</div>
      <span class="tier-badge-label">${esc(label)}</span>
    `;

    // Meta line
    const typeLabel = { album: 'Album', ep: 'EP', single: 'Single' }[data.release_type] || 'Album';
    const metaBits = [typeLabel];
    metaBits.push(`${data.calibrated_count} of ${data.track_count} tracks charged`);
    if (data.contamination_count > 0) metaBits.push(`${data.contamination_count} contaminated`);
    albumResultMeta.innerHTML = metaBits.map((b) => `<span class="album-meta-pill">${esc(b)}</span>`).join('');

    // Summary
    if (data.charge_summary) {
      albumResultSummary.innerHTML = `<p class="summary-text">${esc(data.charge_summary)}</p>`;
      albumResultSummary.style.display = '';
    } else {
      albumResultSummary.style.display = 'none';
    }

    // Arc + societal prose (full, not teased -- there's no album detail page yet)
    renderProse(albumResultArc, albumResultArcBody, data.arc_prose);
    renderProse(albumResultSocietal, albumResultSocietalBody, data.societal_prose);

    // Per-track breakdown
    const tracks = (data.tracks || []).slice();
    albumResultTracks.innerHTML = `
      <h3 class="album-tracklist-title">Tracks</h3>
      <ul class="album-track-list">
        ${tracks.map((t) => renderTrackResult(t)).join('')}
      </ul>`;

    // Links: prefer the new release page when we have its slug, plus the
    // artist page.
    const links = [];
    if (data.artist_slug && data.release_slug) {
      links.push(`<a href="/artists/${encodeURIComponent(data.artist_slug)}/${encodeURIComponent(data.release_slug)}" class="details-cta">See this release -&gt;</a>`);
    }
    if (data.artist_slug) {
      links.push(`<a href="/artists/${encodeURIComponent(data.artist_slug)}" class="details-cta">See ${esc(data.artist)} on the artist page -&gt;</a>`);
    }
    albumResultArtistLink.innerHTML = links.join('<br>');

    // Cover-art match picker (ambiguous matches only; auto-matched albums
    // already have their cover attached + an admin verify email sent).
    renderCoverPick(data, token);
  }

  function renderCoverPick(data, token) {
    if (!albumResultCoverPick) return;
    if (!data.mb_needs_pick || !(data.mb_candidates && data.mb_candidates.length) || !token) {
      albumResultCoverPick.classList.add('hidden');
      albumResultCoverPick.innerHTML = '';
      return;
    }
    const cards = data.mb_candidates.map((c, i) => {
      const meta = [c.primary_type, (c.first_release_date || '').slice(0, 4)]
        .filter(Boolean).join(' &middot; ');
      const thumb = c.thumb_url
        ? `<img class="cover-pick-thumb" src="${encodeURI(c.thumb_url)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`
        : `<span class="cover-pick-thumb cover-pick-thumb--none"></span>`;
      return `
        <button type="button" class="cover-pick-card" data-mbid="${esc(c.musicbrainz_id)}">
          ${thumb}
          <span class="cover-pick-info">
            <span class="cover-pick-title">${esc(c.title)}</span>
            ${meta ? `<span class="cover-pick-meta">${meta}</span>` : ''}
          </span>
        </button>`;
    }).join('');
    albumResultCoverPick.innerHTML = `
      <p class="cover-pick-prompt">Which release is this? Pick one to add its cover art (or skip).</p>
      <div class="cover-pick-grid">${cards}</div>
      <p class="cover-pick-status" id="cover-pick-status" hidden></p>
      <button type="button" class="btn btn-text" id="cover-pick-skip">None of these</button>`;
    albumResultCoverPick.classList.remove('hidden');

    const statusEl = albumResultCoverPick.querySelector('#cover-pick-status');
    const finish = (msg) => {
      albumResultCoverPick.querySelector('.cover-pick-grid').style.display = 'none';
      const skip = albumResultCoverPick.querySelector('#cover-pick-skip');
      if (skip) skip.style.display = 'none';
      albumResultCoverPick.querySelector('.cover-pick-prompt').textContent = msg;
    };
    albumResultCoverPick.querySelectorAll('.cover-pick-card').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const mbid = btn.getAttribute('data-mbid');
        if (statusEl) { statusEl.hidden = false; statusEl.textContent = 'Adding cover art...'; }
        try {
          const headers = await calibrateHeaders();
          const resp = await fetch(`${API_BASE}/album/choose-release/${encodeURIComponent(token)}`, {
            method: 'POST', headers, body: JSON.stringify({ musicbrainz_id: mbid }),
          });
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          finish('Cover art added. Thanks -- our team will double-check the match.');
        } catch (e) {
          if (statusEl) statusEl.textContent = 'Could not add cover art. Please try again.';
        }
      });
    });
    const skipBtn = albumResultCoverPick.querySelector('#cover-pick-skip');
    if (skipBtn) skipBtn.addEventListener('click', () => finish('No cover art added.'));
  }

  function renderTrackResult(t) {
    const num = t.track_number != null ? t.track_number : '';
    if (t.status !== 'scored') {
      return `
        <li class="album-track-result is-skipped">
          <span class="atr-num">${esc(String(num))}</span>
          <span class="atr-title">${esc(t.title)}</span>
          <span class="atr-skip">${esc(t.skip_reason || 'Skipped')}</span>
        </li>`;
    }
    const color = TIER_COLORS[t.tier] || 'green';
    const charge = t.charge != null ? t.charge : 0;
    const sign = charge >= 0 ? '+' : '';
    const titleHtml = t.song_slug
      ? `<a class="atr-title" href="/songs/${encodeURIComponent(t.song_slug)}">${esc(t.title)}</a>`
      : `<span class="atr-title">${esc(t.title)}</span>`;
    const contam = t.contaminated ? '<span class="atr-contam" title="Contaminated">!</span>' : '';
    const dogma = t.dogma_referenced ? '<span class="atr-dogma" title="Dogma referenced">&#x1F4DC;</span>' : '';
    return `
      <li class="album-track-result">
        <span class="atr-num">${esc(String(num))}</span>
        <span class="atr-dot" style="background: var(--rc-${color})"></span>
        ${titleHtml}
        ${contam}
        ${dogma}
        <span class="atr-charge ${getChargeClass(charge)}">${sign}${charge}</span>
      </li>`;
  }

  // ----- Wiring -----
  addAlbumArtistRow();
  clearTracks(4);  // start with 4 empty track rows
  albumArtistAddBtn.addEventListener('click', () => addAlbumArtistRow());
  albumArtistParseBtn.addEventListener('click', doParseAlbumArtistString);
  albumAddTrackBtn.addEventListener('click', () => { addTrackRow(); validateAlbumSubmit(); });
  albumTitleInput.addEventListener('input', validateAlbumSubmit);
  albumConsent.addEventListener('change', validateAlbumSubmit);
  albumBtnSubmit.addEventListener('click', submitAlbum);
  albumBtnSearch.addEventListener('click', doAlbumSearch);
  albumSearchQuery.addEventListener('input', () => { if (albumSearchEnabled) albumBtnSearch.disabled = albumSearchQuery.value.trim().length < 1; });
  albumSearchQuery.addEventListener('keydown', (e) => { if (e.key === 'Enter' && albumSearchEnabled && !albumBtnSearch.disabled) doAlbumSearch(); });

  if (albumDonateLink) {
    albumDonateLink.addEventListener('click', (e) => {
      e.preventDefault();
      openSupportView('Support the Charger.', 'screen-album-entry');
    });
  }

  albumBtnAgain.addEventListener('click', () => {
    stopAlbumPoll();
    albumTitleInput.value = '';
    albumReleaseDate.value = '';
    albumReleaseYear.value = '';
    albumReleaseType.value = 'album';
    clearAlbumArtistRows();
    clearTracks(4);
    albumSearchQuery.value = '';
    albumSearchArtist.value = '';
    albumSearchResults.innerHTML = '';
    albumSearchStatus.classList.add('hidden');
    albumConsent.checked = false;
    albumBtnSubmit.disabled = true;
    resetTurnstile();
    hideAlbumError();
    showScreen('screen-album-entry');
  });

  // Resolve the Musixmatch gate for the Search Album sub-tab AND the Album
  // Charger kill switch for the whole Album top-tab.
  (async function resolveAlbumGates() {
    try {
      const headers = {};
      if (API_KEY) headers['X-Api-Key'] = API_KEY;
      const resp = await fetch(`${API_BASE}/config`, { headers });
      if (resp.ok) {
        const cfg = await resp.json();
        albumSearchEnabled = !!cfg.album_search_enabled;
        albumChargerEnabled = !!cfg.album_charger_enabled;
      }
    } catch { /* default: both gated (fail closed) */ }
    applyAlbumChargerGate();
    applyAlbumSearchGate();
  })();

  validateAlbumSubmit();
})();
