/* ============================================================
   Lyrical Charger — Application Logic
   ============================================================ */

// --- Config ---
const IS_LOCAL = ['localhost', '127.0.0.1'].includes(window.location.hostname);
const API_HOST = IS_LOCAL
  ? `http://${window.location.hostname}:8000`
  : 'https://api.risingcompass.net';
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
const inputArtist = $('#input-artist');
const lyricsInput = $('#lyrics-input');

const consentCheck = $('#consent-check');
const btnSubmit = $('#btn-submit');
const errorMessage = $('#error-message');

const procBarFill = $('#proc-bar-fill');
const procPct = $('#proc-pct');
const procStage = $('#proc-stage');
const procDetail = $('#proc-detail');

const resultIdentity = $('#result-identity');
const resultClassification = $('#result-classification');
const resultSummary = $('#result-summary');
const resultContamination = $('#result-contamination');
const btnAgain = $('#btn-again');

// --- State ---
let activeTab = 'paste';

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
    hideError();
    validateSubmit();
  });
});

// ============================================================
// Validation
// ============================================================
function validateSubmit() {
  let valid = false;

  if (activeTab === 'paste') {
    const title = inputTitle.value.trim();
    const artist = inputArtist.value.trim();
    const lyrics = lyricsInput.value.trim();
    const hasTitle = title.length >= 1;
    const hasArtist = artist.length >= 1;
    const hasLyrics = lyrics.length >= 20;
    const consented = consentCheck.checked;
    valid = hasTitle && hasArtist && hasLyrics && consented;
  }
  // Search tab has no submit — it's stubbed

  btnSubmit.disabled = !valid;
}

lyricsInput.addEventListener('input', validateSubmit);
inputTitle.addEventListener('input', validateSubmit);
inputArtist.addEventListener('input', validateSubmit);
consentCheck.addEventListener('change', validateSubmit);

// ============================================================
// Progress Bar
// ============================================================
const STAGES = [
  { pct: 8,  label: 'Validating input',           detail: 'Checking lyrics structure...' },
  { pct: 18, label: 'Reading lyrics',              detail: 'Parsing line by line...' },
  { pct: 30, label: 'Loading rubric',              detail: '58 tenets across 5 tiers...' },
  { pct: 50, label: 'Classifying',                 detail: 'Building case from zero...' },
  { pct: 70, label: 'Evaluating contamination',    detail: 'Checking for hidden payloads...' },
  { pct: 82, label: 'Calculating charge',          detail: 'Mapping position within tier...' },
  { pct: 90, label: 'Generating summary',          detail: 'Writing charge summary...' },
];

let progressTimer = null;
let currentStage = 0;

function resetProgress() {
  currentStage = 0;
  procBarFill.style.width = '0%';
  procPct.textContent = '0%';
  procStage.textContent = 'Preparing...';
  procDetail.textContent = '';
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

  // Advance through stages on a schedule
  // Early stages go fast, classification stage lingers
  function advanceStage() {
    if (currentStage >= STAGES.length) return;

    const stage = STAGES[currentStage];
    setProgress(stage.pct, stage.label, stage.detail);
    currentStage++;

    // Delay increases as we get deeper — classification is the long wait
    const delay = currentStage <= 2 ? 600 : currentStage <= 4 ? 2500 : 3000;
    progressTimer = setTimeout(advanceStage, delay);
  }

  advanceStage();
}

function completeProgress() {
  if (progressTimer) clearTimeout(progressTimer);
  setProgress(100, 'Complete', 'Classification ready.');
}

function stopProgress() {
  if (progressTimer) clearTimeout(progressTimer);
}

// ============================================================
// Submit
// ============================================================
btnSubmit.addEventListener('click', submitLyrics);

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
  const artist = inputArtist.value.trim();

  if (!title || !artist) {
    showError('Song title and artist are required.');
    btnSubmit.disabled = false;
    return;
  }

  showScreen('screen-processing');
  startProgress();

  try {
    const headers = { 'Content-Type': 'application/json' };
    if (API_KEY) headers['X-Api-Key'] = API_KEY;

    const resp = await fetch(`${API_BASE}/classify-lyrics`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ lyrics, title, artist }),
    });

    if (resp.status === 429) {
      stopProgress();
      showError("You've submitted several readings recently. Try again in an hour.");
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    if (!resp.ok) {
      stopProgress();
      const data = await resp.json().catch(() => ({}));
      showError(data.detail || 'Classification failed. Try again.');
      showScreen('screen-entry');
      btnSubmit.disabled = false;
      return;
    }

    const data = await resp.json();

    if (data.status === 'error') {
      stopProgress();
      showError('Could not classify these lyrics. Try different lyrics or check formatting.');
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

  // Classification badge + charge
  const color = TIER_COLORS[data.tier] || 'green';
  const label = data.tier_label || TIER_LABELS[data.tier] || 'Decent';
  const charge = data.charge != null ? data.charge : 0;
  const sign = charge >= 0 ? '+' : '';
  const chargeClass = getChargeClass(charge);

  resultClassification.innerHTML = `
    <div class="tier-badge">
      <span class="tier-badge-dot" style="background: var(--tier-${color})"></span>
      <span class="tier-badge-label">${label}</span>
    </div>
    <div class="charge-display ${chargeClass}">${sign}${charge}</div>
  `;

  // Summary
  if (data.charge_summary) {
    resultSummary.innerHTML = `<p class="summary-text">${esc(data.charge_summary)}</p>`;
    resultSummary.style.display = '';
  } else {
    resultSummary.style.display = 'none';
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
}

// ============================================================
// Classify Another
// ============================================================
btnAgain.addEventListener('click', () => {
  lyricsInput.value = '';
  inputTitle.value = '';
  inputArtist.value = '';
  consentCheck.checked = false;
  btnSubmit.disabled = true;
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
