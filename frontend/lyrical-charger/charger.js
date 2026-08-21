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
const resultEffects = $('#result-listener-effects');
const resultEffectsBody = $('#result-listener-effects-body');
const resultSocietal = $('#result-societal');
const resultSocietalBody = $('#result-societal-body');
const resultConsensus = $('#result-consensus');
const resultContamination = $('#result-contamination');
const resultDogma = $('#result-dogma');
const resultMisread = $('#result-misread');
const resultContest = $('#result-contest');
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

// Both delegate to the shared /js/rc-turnstile.js helper. The names and the
// EMPTY-STRING return are kept because ~14 call sites below depend on them;
// RCTurnstile.token() returns null, which would serialise as JSON null instead.
const TS_MOUNT = 'turnstile-mount';

function getTurnstileToken() {
  return window.RCTurnstile.token(TS_MOUNT) || '';
}

function resetTurnstile() {
  window.RCTurnstile.reset(TS_MOUNT);
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
// back to the charger the user was on -- never out to the homepage. The
// song-entry link routes through openSupportView().
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
    window.RCTurnstile.configure(cfg.turnstile_site_key);
    // ONE widget for the whole page. Both tabs read the same token via
    // getTurnstileToken(), and only one submission is ever in flight, so a
    // second widget would just be another thing to keep reset.
    await window.RCTurnstile.mount(TS_MOUNT);
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

// LEIT clutter control: soft "are you sure?" gate when the backend judged the
// paste not to look like a commercially released song. The user can confirm
// (resubmit with confirm_commercial=true, which queues it for human audit) or
// cancel. Creative/Curio Charger are shown as coming soon (no live routes yet).
function closeCommercialWarning() {
  const modal = document.getElementById('commercial-warning-modal');
  if (modal) { modal.classList.remove('open'); modal.setAttribute('aria-hidden', 'true'); }
}

function showCommercialWarning(data) {
  const modal = document.getElementById('commercial-warning-modal');
  if (!modal) {
    // Graceful fallback if the modal markup is absent.
    const msg = (data && data.commercial_reason) || 'This may not be a commercially released song.';
    if (window.confirm(msg + '\n\nContinue anyway? Flagged submissions are reviewed.')) submitLyrics(true);
    return;
  }
  const reasonEl = document.getElementById('cw-reason');
  if (reasonEl) reasonEl.textContent = (data && data.commercial_reason) || "This doesn't look like a commercially released song.";
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
}

(function wireCommercialWarning() {
  const cont = document.getElementById('cw-continue');
  const cancel = document.getElementById('cw-cancel');
  if (cont) cont.addEventListener('click', () => { closeCommercialWarning(); submitLyrics(true); });
  if (cancel) cancel.addEventListener('click', closeCommercialWarning);
})();

// Real-progress phase map. The single-song charge is now an async job
// (POST /calibrate-lyrics/start -> poll /status/{token}); the worker reports its
// TRUE phase as it runs, so these percentages track actual backend work instead
// of a timer. Percentages are monotonic and the worker stamps phases in order,
// so the bar only ever moves forward. (Cosmetic-timer fallback `startProgress`
// is still used by the synchronous Search flow.)
const PHASE_PROGRESS = {
  queued:      { pct: 8,  label: 'Queued',                  detail: 'Starting the calibrator...', sub: null },
  identity:    { pct: 22, label: 'Reading lyrics',          detail: 'Checking they match the song...', sub: null },
  calibrating: { pct: 48, label: 'Calibrating',             detail: 'Building the case against the rubric...', sub: null },
  listener:    { pct: 70, label: 'Generating your reading', detail: 'What it may do to a listener...', sub: 0 },
  ether:       { pct: 82, label: 'Generating your reading', detail: 'Naming what it is...', sub: 0 },
  societal:    { pct: 92, label: 'Generating your reading', detail: 'And what it does at the scale of a society...', sub: 1 },
  done:        { pct: 100, label: 'Complete',               detail: 'Calibration ready.', sub: 2 },
};

function driveProgressPhase(phase) {
  const p = PHASE_PROGRESS[phase];
  if (!p) return;
  setProgress(p.pct, p.label, p.detail);
  if (p.sub !== null && p.sub !== undefined) setSubstepActive(p.sub);
}

// Poll the calibrate job until it finishes, driving the real bar from `phase`.
// Resolves with the LyricsCalibrateOut result; rejects with a coded Error
// ('job_lost' | 'timeout' | a server error message) the caller maps to UI.
async function pollCalibrateJob(token, headers) {
  const deadline = Date.now() + 5 * 60 * 1000; // safety ceiling
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1200));
    let resp;
    try {
      resp = await fetch(`${API_BASE}/calibrate-lyrics/status/${token}`, { headers });
    } catch (_) {
      continue; // transient network blip -- keep polling
    }
    if (resp.status === 404) throw new Error('job_lost');
    if (!resp.ok) continue; // e.g. a poll-rate 429 -- back off and retry
    const data = await resp.json();
    if (data.phase) driveProgressPhase(data.phase);
    if (data.status === 'done') return data.result;
    if (data.status === 'error') throw new Error(data.error || 'Calibration failed. Try again.');
  }
  throw new Error('timeout');
}

// Render a finished LyricsCalibrateOut (the worker's result payload) -- identical
// branch handling to the old synchronous response.
async function handleCalibrationResult(data) {
  if (!data || data.status === 'error') {
    stopProgress();
    showError('Could not calibrate these lyrics. Try different lyrics or check formatting.');
    showScreen('screen-entry');
    btnSubmit.disabled = false;
    return;
  }

  if (data.status === 'lyrics_mismatch' || data.status === 'lyrics_diverge_from_prior') {
    stopProgress();
    showError(data.block_reason || 'These lyrics don\'t appear to match this song.');
    showScreen('screen-entry');
    btnSubmit.disabled = false;
    return;
  }

  if (data.status === 'run_capped') {
    stopProgress();
    showCappedCard(data);
    showScreen('screen-entry');
    btnSubmit.disabled = false;
    return;
  }

  if (data.status === 'not_commercial_warning') {
    // LEIT clutter control: soft warning -- this didn't look like a commercially
    // released song. Let the user confirm and push through (which queues it for
    // human audit) or route to Creative/Curio.
    stopProgress();
    showCommercialWarning(data);
    showScreen('screen-entry');
    btnSubmit.disabled = false;
    return;
  }

  if (data.status === 'saved_view_on_page') {
    stopProgress();
    showSavedCard(data);
    showScreen('screen-entry');
    btnSubmit.disabled = false;
    return;
  }

  completeProgress();
  await new Promise((r) => setTimeout(r, 600)); // brief beat on 100%
  renderResults(data);
  showScreen('screen-results');
}

async function submitLyrics(confirmCommercial = false) {
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
  resetProgress();
  setProgress(6, 'Submitting', 'Sending to the calibrator...');

  try {
    const headers = await calibrateHeaders();

    // 1) Synchronous submit: validation, bot-check, and credit pre-flight all
    //    resolve here (clean HTTP errors), then a job token comes back.
    const resp = await fetch(`${API_BASE}/calibrate-lyrics/start`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        lyrics, title, artist,
        artists: artistEntries.map(e => ({ name: e.name, role: e.role })),
        hp_website: getHpValue(),
        turnstile_token: getTurnstileToken(),
        confirm_commercial: confirmCommercial,
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

    const { job_token } = await resp.json();
    if (!job_token) {
      stopProgress();
      showError('Calibration failed. Try again.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    // 2) Poll the job, driving the REAL bar from the worker's phase.
    let result;
    try {
      result = await pollCalibrateJob(job_token, headers);
    } catch (e) {
      stopProgress();
      const msg = e && e.message;
      if (msg === 'job_lost') showError('We lost track of that calibration. Please try again.');
      else if (msg === 'timeout') showError('That took longer than expected. Please try again.');
      else showError(msg || 'Calibration failed. Try again.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    await handleCalibrationResult(result);
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

    if (data.status === 'run_capped') {
      stopProgress();
      showCappedCard(data);
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    if (data.status === 'error') {
      stopProgress();
      showError('Could not calibrate this song. Try a different one.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    if (data.status === 'saved_view_on_page') {
      stopProgress();
      showSavedCard(data);
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
    renderProseExcerpt(resultEffects, resultEffectsBody, data.listener_effects_prose);
    renderProseExcerpt(resultSocietal, resultSocietalBody, data.societal_effects_prose);
  } else {
    renderProse(resultEffects, resultEffectsBody, data.listener_effects_prose);
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
  // A HELD reading has no song page yet, and "Did we get it wrong?" would send
  // the reader to a queue when a re-read is one click away. The contest lane
  // replaces both while the reading is held.
  renderContestLane(data);
  if (data && data.held) {
    resultMisread.innerHTML = '';
  } else {
    resultMisread.innerHTML = `
      ${detailsLink}
      <a href="/misread-submission.html?${misreadParams.toString()}" class="misread-link">Did we get it wrong?</a>
    `;
  }
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

// Card shape (Square 1:1 / Instagram 3:4). Default 1:1, re-renders the preview on change.
let cardRatio = 'square';
async function renderChargeCard() {
  if (!lastResult || !window.RCChargeCard) return;
  await window.RCChargeCard.render(lastResult, chargeCardCanvas, { ratio: cardRatio });
}
const ccRatioWrap = document.getElementById('cc-ratio');
if (ccRatioWrap) {
  ccRatioWrap.querySelectorAll('.cc-ratio-opt').forEach((opt) => {
    opt.addEventListener('click', async () => {
      cardRatio = opt.dataset.ratio === 'portrait' ? 'portrait' : 'square';
      ccRatioWrap.querySelectorAll('.cc-ratio-opt').forEach((o) => o.classList.toggle('is-active', o === opt));
      try { await renderChargeCard(); } catch (_) {}
    });
  });
}

if (btnShare) {
  btnShare.addEventListener('click', async () => {
    if (!lastResult || !window.RCChargeCard) return;
    btnShare.disabled = true;
    try {
      await renderChargeCard();
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
  errorMessage.classList.remove('saved');
  errorMessage.textContent = '';
}

// Recovery card: the calibration completed and the song was saved, but a
// post-save step failed before the full result could be shown. We don't error
// out -- we tell the user it's done and offer a link to the saved reading
// (their choice to click; not an auto-redirect).
function showSavedCard(data) {
  errorMessage.textContent = '';
  errorMessage.classList.add('saved');
  errorMessage.appendChild(document.createTextNode(
    'Your song was completed and saved.'));
  if (data && data.song_slug) {
    errorMessage.appendChild(document.createElement('br'));
    const a = document.createElement('a');
    a.href = '/songs/' + data.song_slug;
    a.textContent = 'View your reading ->';
    errorMessage.appendChild(a);
  } else {
    errorMessage.appendChild(document.createTextNode(
      ' Find it in the Library or via search.'));
  }
  errorMessage.classList.remove('hidden');
}

// Run-capped card: the song has hit its public run limit. Its reading is
// settled -- no new public run is made. Inform (not an error) and link to the
// song page so the user can see the current calibration.
function showCappedCard(data) {
  const cap = data && data.run_cap ? data.run_cap : 10;
  const count = data && data.run_count ? data.run_count : cap;
  errorMessage.textContent = '';
  errorMessage.classList.add('saved');
  errorMessage.appendChild(document.createTextNode(
    `This song has reached its ${cap}-run limit (calibrated ${count} times). ` +
    `Its reading is settled, so it's no longer open to public runs.`));
  if (data && data.song_slug) {
    errorMessage.appendChild(document.createElement('br'));
    const a = document.createElement('a');
    a.href = '/songs/' + data.song_slug;
    a.textContent = 'View its calibration ->';
    errorMessage.appendChild(a);
  }
  errorMessage.classList.remove('hidden');
}

// ============================================================
// Contest lane (prepublish)
//
// A reading arrives HELD: delivered to the reader, not yet in the Library.
// They can take it, say it missed something and get exactly one re-read, or
// send it to a person. Saying nothing is fine too, and the commonest case by
// far: the server publishes it on a timer.
//
// The whole block is inert unless the server says `held`, so with the
// prepublish flag off this file changes nothing about how a reading renders.
// ============================================================

let heldToken = null;      // the reader's handle on the held reading
let heldIsReRead = false;  // true once they have spent their one re-read
let contestAxes = null;    // fetched once, from the server's closed set

async function loadContestAxes() {
  // The axes live on the server because the guard validates against them
  // there. Fetching rather than hardcoding means the list a reader picks from
  // can never drift out of step with the list that gets accepted.
  if (contestAxes) return contestAxes;
  try {
    const headers = await calibrateHeaders();
    const resp = await fetch(`${API_BASE}/contest/axes`, { headers });
    if (!resp.ok) return null;
    const data = await resp.json();
    contestAxes = data.axes || null;
  } catch (_) {
    contestAxes = null;
  }
  return contestAxes;
}

// The lane is new and it changes when a reading reaches the library, so it says
// so on every screen of it -- held, form, and re-read alike. One constant rather
// than three copies, so it cannot go missing from a state nobody looked at.
const CONTEST_BETA = '<span class="contest-beta">Beta</span>';

function renderHeldPrompt() {
  resultContest.innerHTML = `
    <div class="contest-held">
      <p class="contest-held-note">${CONTEST_BETA}This reading is not saved yet.</p>
      <div class="contest-held-actions">
        <button type="button" class="btn btn-primary" id="contest-accept">Looks right</button>
        <button type="button" class="btn btn-secondary" id="contest-open">Something's off</button>
      </div>
    </div>
  `;
  resultContest.classList.remove('hidden');
  $('#contest-accept').addEventListener('click', acceptReading);
  $('#contest-open').addEventListener('click', openContestForm);
}

async function openContestForm() {
  const axes = await loadContestAxes();
  if (!axes) {
    renderContestError('That form could not load. You can still send this to review.');
    return;
  }
  const options = axes.map((a) => `
    <label class="contest-axis">
      <input type="radio" name="contest-axis" value="${escapeAttr(a.key)}">
      <span>${esc(a.label)}</span>
    </label>
  `).join('');

  resultContest.innerHTML = `
    <div class="contest-form">
      <p class="contest-form-title">${CONTEST_BETA}What did the reading miss?</p>
      <div class="contest-axes">${options}</div>

      <label class="contest-label" for="contest-note">Quote the line it missed it on</label>
      <textarea id="contest-note" class="contest-note" rows="3"
        placeholder="Say what the line means, and quote enough of it that we can find it."></textarea>
      <p class="contest-help">Leave the tier out of it. Point at the words and the rubric will do the rest.</p>

      <label class="contest-label" for="contest-lyrics">Paste the lyrics again</label>
      <textarea id="contest-lyrics" class="contest-lyrics" rows="6"
        placeholder="The same lyrics you pasted before."></textarea>
      <p class="contest-help">We cleared them the moment your reading landed, and we never store them, so we need them once more to read it again.</p>

      <div class="contest-form-actions">
        <button type="button" class="btn btn-primary" id="contest-submit">Read it again</button>
        <button type="button" class="btn btn-secondary" id="contest-cancel">Never mind</button>
      </div>
      <div class="contest-error hidden" id="contest-error"></div>
    </div>
  `;
  resultContest.classList.remove('hidden');
  $('#contest-submit').addEventListener('click', submitContest);
  $('#contest-cancel').addEventListener('click', renderHeldPrompt);
  phCapture('contest_opened', {});
}

function renderContestError(message) {
  const box = $('#contest-error');
  if (box) {
    box.textContent = message;
    box.classList.remove('hidden');
    return;
  }
  resultContest.innerHTML = `<div class="contest-error">${esc(message)}</div>`;
  resultContest.classList.remove('hidden');
}

async function submitContest() {
  const axis = (document.querySelector('input[name="contest-axis"]:checked') || {}).value;
  const note = ($('#contest-note')?.value || '').trim();
  const lyrics = ($('#contest-lyrics')?.value || '').trim();

  if (!axis) return renderContestError('Pick what the reading got wrong.');
  if (!note) return renderContestError('Tell us what it missed, and quote the line.');
  if (!lyrics) return renderContestError('Paste the lyrics again so it can be read.');

  const submitBtn = $('#contest-submit');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Reading it again...';
  }

  try {
    const headers = await calibrateHeaders();
    const resp = await fetch(`${API_BASE}/contest`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ job_token: heldToken, axis, note, lyrics }),
    });
    const data = await resp.json().catch(() => ({}));

    if (!resp.ok) {
      // The guard's rejections are the useful ones: each says what to send
      // instead, so surface it as written rather than flattening it.
      const detail = data.detail || {};
      const message = detail.message
        || (typeof detail === 'string' ? detail : 'That could not be sent. Try again.');
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Read it again';
      }
      return renderContestError(message);
    }

    phCapture('contest_reread', { tier_moved: !!data.tier_moved });
    heldIsReRead = true;
    renderResults(Object.assign({}, data.result, {
      held: true,
      job_token: heldToken,
      _reReadOf: lastResult ? lastResult.tier : null,
      _tierMoved: !!data.tier_moved,
    }));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (_) {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Read it again';
    }
    renderContestError('That could not be sent. Check your connection and try again.');
  }
}

function isSignedIn() {
  try {
    return !!(window.Auth && window.Auth.isSignedIn());
  } catch (_) {
    return false;
  }
}

function renderReReadPrompt(tierMoved) {
  const moved = tierMoved
    ? 'Here it is again, and it landed somewhere else this time.'
    : 'Here it is again. It read the same way twice.';
  // The reply address is asked for ONLY when signed out. A signed-in reader's
  // account address is fetched server-side, so putting the field in front of
  // them would be asking for something already known.
  const emailField = isSignedIn() ? '' : `
      <label class="contest-label" for="contest-email">Email, if you want an answer</label>
      <input type="email" id="contest-email" class="contest-email"
        autocomplete="email" placeholder="Leave it blank and it still gets read.">
  `;
  resultContest.innerHTML = `
    <div class="contest-held">
      <p class="contest-held-note">${CONTEST_BETA}${esc(moved)}</p>
      ${emailField}
      <div class="contest-held-actions">
        <button type="button" class="btn btn-primary" id="contest-accept">Take this one</button>
        <button type="button" class="btn btn-secondary" id="contest-decline">Still wrong, send it to review</button>
      </div>
    </div>
  `;
  resultContest.classList.remove('hidden');
  $('#contest-accept').addEventListener('click', acceptReading);
  $('#contest-decline').addEventListener('click', declineReading);
}

async function acceptReading() {
  const btn = $('#contest-accept');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
  try {
    const headers = await calibrateHeaders();
    const resp = await fetch(`${API_BASE}/accept`, {
      method: 'POST', headers,
      body: JSON.stringify({ job_token: heldToken }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error('accept failed');

    phCapture('contest_accepted', { was_reread: heldIsReRead });
    heldToken = null;
    const link = data.song_slug
      ? `<a href="/songs/${encodeURIComponent(data.song_slug)}" class="details-cta">Read the full reading -&gt;</a>`
      : '';
    resultContest.innerHTML = `<div class="contest-done"><p>Saved.</p>${link}</div>`;
  } catch (_) {
    // It is on a 30-minute timer regardless, so nothing is lost here.
    resultContest.innerHTML =
      '<div class="contest-done"><p>That did not go through, but your reading is safe and will save itself shortly.</p></div>';
  }
}

async function declineReading() {
  const btn = $('#contest-decline');
  const email = ($('#contest-email')?.value || '').trim();
  if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }
  try {
    const headers = await calibrateHeaders();
    const resp = await fetch(`${API_BASE}/decline`, {
      method: 'POST', headers,
      body: JSON.stringify({ job_token: heldToken, email: email || null }),
    });
    if (!resp.ok) throw new Error('decline failed');
    phCapture('contest_declined', { gave_email: !!email });
    heldToken = null;
    const answer = email
      ? ' We have your address if there is anything to tell you.'
      : '';
    resultContest.innerHTML =
      `<div class="contest-done"><p>Sent to review. A person reads these, and this one will not go into the library in the meantime.${answer}</p></div>`;
  } catch (_) {
    if (btn) { btn.disabled = false; btn.textContent = 'Still wrong, send it to review'; }
    renderContestError('That could not be sent. Try again in a moment.');
  }
}

function renderContestLane(data) {
  // Not held means it published on delivery, which is every reading while the
  // prepublish flag is off. Leave the block empty and hidden.
  if (!data || !data.held || !data.job_token) {
    heldToken = null;
    heldIsReRead = false;
    resultContest.classList.add('hidden');
    resultContest.innerHTML = '';
    return;
  }
  heldToken = data.job_token;
  if (data._reReadOf !== undefined) {
    heldIsReRead = true;
    renderReReadPrompt(data._tierMoved);
  } else {
    heldIsReRead = false;
    renderHeldPrompt();
  }
}
