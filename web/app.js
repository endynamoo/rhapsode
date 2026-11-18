/* Rhapsode — local read-aloud
   Kokoro-82M in the backend, karaoke timing from phoneme alignments. */

'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const DEFAULT_SETTINGS = {
  voice: 'af_heart',
  speed: 1.0,
  theme: 'ink',
  font: 'sans',
  fontSize: 19,
  autoScroll: true,
  remoteAccess: false,
  translateToVoice: true,
  filmQuality: 'high',
  autoScenes: true,
  autoFilms: false,
};

const LANG_NAMES = {
  en: 'English', es: 'Spanish', fr: 'French', it: 'Italian',
  pt: 'Portuguese', hi: 'Hindi', ja: 'Japanese', zh: 'Chinese',
};

const SPEEDS = [0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0];
const PREFETCH = 3;
const AVG_CHARS_PER_SEC = 15.5;

const SAMPLE = `The Time Traveller (for so it will be convenient to speak of him) was expounding a recondite matter to us. His grey eyes shone and twinkled, and his usually pale face was flushed and animated. The fire burned brightly, and the soft radiance of the incandescent lights in the lilies of silver caught the bubbles that flashed and passed in our glasses.

Our chairs, being his patents, embraced and caressed us rather than submitted to be sat upon, and there was that luxurious after-dinner atmosphere when thought roams gracefully free of the trammels of precision. And he put it to us in this way — marking the points with a lean forefinger — as we sat and lazily admired his earnestness over this new paradox (as we thought it) and his fecundity.

"You must follow me carefully. I shall have to controvert one or two ideas that are almost universally accepted. The geometry, for instance, they taught you at school is founded on a misconception."

"Is not that rather a large thing to expect us to begin upon?" said Filby, an argumentative person with red hair.`;

const state = {
  settings: { ...DEFAULT_SETTINGS },
  docs: [],
  doc: null,
  units: [],
  index: 0,
  audioIndex: -1,
  words: [],
  wordIdx: -1,
  playing: false,
  loading: false,
  finished: false,
  voice: DEFAULT_SETTINGS.voice,
  speed: DEFAULT_SETTINGS.speed,
  autoScroll: true,
  clips: new Map(),
  pending: new Map(),
  durations: new Map(),
  gen: 0,
  raf: 0,
  saveTimer: 0,
  lastScroll: 0,
  docChars: [],
  totalChars: 1,
  activeUnitEl: null,
  activeWordEl: null,
  mediaTitle: null,
  view: 'text',
  page: 0,
  sidePanel: 'library',
  chapter: null,
  translated: false,
  spoken: '',
  targetLang: '',
  translateWarned: false,
  translationSpans: [],
  pageCaptionSpans: [],
  pageWordEl: null,
  pdfWords: null,
  pdfPage: -1,
  pdfUnitText: '',
  pdfUnitStart: -1,
  pdfLastPos: 0,
  pseudoKey: '',
  pseudoTimes: [],
  pseudoIdx: -1,
  cinema: {
    planKey: '',
    chapter: -1,
    scenes: [],
    index: -1,
    cache: new Map(),
    pending: new Map(),
    token: 0,
    sceneToken: 0,
    prefetchedKey: '',
    ready: new Set(),
    preparing: false,
    prepareToken: 0,
    jobs: new Map(),
    filmTimer: 0,
  },
};

const sceneReview = {
  chapter: 0,
  scenes: [],
  images: [],
  marked: new Set(),
  current: null,
  rect: null,
  busy: false,
  drag: null,
};

const audio = new Audio();
audio.preload = 'auto';
audio.preservesPitch = true;
audio.webkitPreservesPitch = true;
audio.style.display = 'none';
document.body.appendChild(audio);

/* ─────────────────────────── utils ─────────────────────────── */

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText || 'Request failed';
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  const type = res.headers.get('content-type') || '';
  return type.includes('application/json') ? res.json() : res.text();
}

function fmtTime(seconds) {
  if (!isFinite(seconds) || seconds < 0) seconds = 0;
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
  return `${m}:${String(r).padStart(2, '0')}`;
}

function readTime(words) {
  const mins = words / 190;
  if (mins < 1) return 'under a minute';
  if (mins < 60) return `${Math.round(mins)} min`;
  return `${Math.floor(mins / 60)}h ${Math.round(mins % 60)}m`;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function toast(message, kind = 'info', ms = 3600) {
  const el = document.createElement('div');
  el.className = `toast${kind === 'error' ? ' is-error' : ''}`;
  el.innerHTML = '<span class="toast-dot"></span><span class="toast-text"></span>';
  el.querySelector('.toast-text').textContent = message;
  $('#toasts').appendChild(el);
  if (ms > 0) {
    setTimeout(() => {
      el.classList.add('is-out');
      setTimeout(() => el.remove(), 200);
    }, ms);
  }
  return el;
}

/* ─────────────────────────── boot ─────────────────────────── */

document.addEventListener('DOMContentLoaded', init);

async function init() {
  bindUI();
  buildSpeedOptions();

  try {
    const saved = await api('/api/settings');
    state.settings = { ...DEFAULT_SETTINGS, ...saved };
  } catch (_) {}
  renderThemeOptions();
  applySettings();

  try {
    state.view = localStorage.getItem('murmur.view2') === 'cinema' ? 'cinema' : 'book';
  } catch (_) {}

  await Promise.all([loadVoices(), refreshLibrary()]);
  checkEngine();

  const raw = decodeURIComponent(location.hash.replace(/^#/, ''));
  if (raw) {
    const [id, query] = raw.split('?');
    const params = new URLSearchParams(query || '');
    try {
      await openDoc(id, { autoplay: params.get('play') === '1' });
    } catch (_) {}
  }

  connectEvents();
}

function connectEvents() {
  if (!('EventSource' in window)) return;
  try {
    const source = new EventSource('/api/events');
    source.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'quick' && message.id) {
          openDoc(message.id, { autoplay: true });
        }
      } catch (_) {}
    };
  } catch (_) {}
}

function bindUI() {
  $('#addBtn').addEventListener('click', openAdd);
  $('#emptyAdd').addEventListener('click', openAdd);
  $('#sampleBtn').addEventListener('click', addSample);
  $$('#sideTabs button').forEach((button) =>
    button.addEventListener('click', () => {
      if (!button.disabled) setSidePanel(button.dataset.panel);
    })
  );
  $('#themeBtn').addEventListener('click', toggleTheme);
  $('#engineState').addEventListener('click', () => checkEngine());
  $('#settingsBtn').addEventListener('click', openSettings);
  $('#searchBtn').addEventListener('click', openSearch);
  wireEngine();
  $('#focusBtn').addEventListener('click', toggleFocus);
  $('#focusExit').addEventListener('click', () => setFocus(false));
  $('#menuBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    document.body.classList.toggle('sidebar-open');
  });
  $('.main').addEventListener('click', () => {
    if (document.body.classList.contains('sidebar-open') && window.innerWidth <= 700) {
      document.body.classList.remove('sidebar-open');
    }
  });

  $('#playBtn').addEventListener('click', togglePlay);
  $('#prevBtn').addEventListener('click', prevSentence);
  $('#nextBtn').addEventListener('click', nextSentence);
  $('#gestureBtn').addEventListener('click', () => {
    $('#gestureBtn').hidden = true;
    playUnit(state.index, { restart: true });
  });

  $('#speedSel').addEventListener('change', (e) => setSpeed(parseFloat(e.target.value)));
  $('#voiceSel').addEventListener('change', (e) => setVoice(e.target.value));
  $('#autoBtn').addEventListener('click', () => {
    state.autoScroll = !state.autoScroll;
    state.settings.autoScroll = state.autoScroll;
    $('#autoBtn').classList.toggle('is-on', state.autoScroll);
    saveSettings({ autoScroll: state.autoScroll });
    if (state.autoScroll) scrollToUnit(state.index);
  });

  $('#progressTrack').addEventListener('click', seekToFraction);
  $('#chapterFrame').addEventListener('load', onChapterLoad);
  $('#cinemaMake').addEventListener('click', makeFilm);
  $('#cinemaReview').addEventListener('click', openSceneReview);
  $('#cinemaQueue').addEventListener('click', onFilmQueueClick);
  $('#sceneBack').addEventListener('click', closeSceneEditor);
  $('#sceneRegenWhole').addEventListener('click', () => regenerateScene('whole'));
  $('#sceneRegenArea').addEventListener('click', () => regenerateScene('region'));
  $('#sceneBatch').addEventListener('click', regenerateMarked);
  $('#sceneCanvas').addEventListener('pointerdown', startSceneRect);
  window.addEventListener('pointermove', moveSceneRect);
  window.addEventListener('pointerup', endSceneRect);
  $$('#sceneModal [data-close]').forEach((b) => b.addEventListener('click', closeModals));
  $('#sceneModal').addEventListener('mousedown', (e) => {
    if (e.target.id === 'sceneModal') closeModals();
  });

  $$('#viewToggle button').forEach((button) =>
    button.addEventListener('click', () => setView(button.dataset.view))
  );
  $('#pagePrev').addEventListener('click', () => goToPage(state.page - 1));
  $('#pageNext').addEventListener('click', () => goToPage(state.page + 1));

  $('#exportBtn').addEventListener('click', exportDoc);

  // add modal
  $$('#addModal [data-close]').forEach((b) => b.addEventListener('click', closeModals));
  $('#addModal').addEventListener('mousedown', (e) => {
    if (e.target.id === 'addModal') closeModals();
  });
  $$('.tab').forEach((tab) => tab.addEventListener('click', () => switchTab(tab.dataset.tab)));
  $('#pasteText').addEventListener('input', updatePasteCount);
  $('#pasteAdd').addEventListener('click', addPasted);
  $('#urlAdd').addEventListener('click', addUrl);
  $('#urlInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addUrl();
  });
  $('#fileInput').addEventListener('change', (e) => {
    if (e.target.files[0]) addFile(e.target.files[0]);
    e.target.value = '';
  });

  // settings modal
  $$('#settingsModal [data-close]').forEach((b) => b.addEventListener('click', closeModals));
  $('#settingsModal').addEventListener('mousedown', (e) => {
    if (e.target.id === 'settingsModal') closeModals();
  });

  // search modal
  $$('#searchModal [data-close]').forEach((b) => b.addEventListener('click', closeModals));
  $('#searchModal').addEventListener('mousedown', (e) => {
    if (e.target.id === 'searchModal') closeModals();
  });
  $('#searchInput').addEventListener('input', () => {
    clearTimeout(searchView.timer);
    searchView.timer = setTimeout(runSearch, 180);
  });
  $('#searchInput').addEventListener('keydown', onSearchKey);
  $$('.search-filters button').forEach((button) =>
    button.addEventListener('click', () => {
      searchView.recent = button.dataset.recent || '';
      $$('.search-filters button').forEach((other) =>
        other.classList.toggle('is-active', other === button)
      );
      if (searchView.query.trim()) runSearch();
    })
  );
  $('#searchIndexBtn').addEventListener('click', () => startReindex(false));
  $('#setVoice').addEventListener('change', (e) => {
    state.settings.voice = e.target.value;
    saveSettings({ voice: e.target.value });
  });
  $('#setFontSize').addEventListener('input', (e) => {
    const size = parseInt(e.target.value, 10);
    state.settings.fontSize = size;
    applySettings();
  });
  $('#setFontSize').addEventListener('change', (e) => {
    saveSettings({ fontSize: parseInt(e.target.value, 10) });
  });
  $('#setFontFamily').addEventListener('change', (e) => {
    state.settings.font = e.target.value;
    saveSettings({ font: e.target.value });
    applySettings();
  });
  $('#setThemeSelect').addEventListener('change', (e) => {
    state.settings.theme = e.target.value;
    saveSettings({ theme: e.target.value });
    applySettings();
  });
  $$('#setFilmQuality button').forEach((b) =>
    b.addEventListener('click', () => {
      const quality = b.dataset.filmQuality === 'fast' ? 'fast' : 'high';
      if (quality === state.settings.filmQuality) return;
      state.settings.filmQuality = quality;
      saveSettings({ filmQuality: quality });
      applySettings();
      resetCinemaQuality();
      toast(quality === 'high'
        ? 'Manga quality: High - 1080p FLUX.2 [klein], four steps'
        : 'Manga quality: Fast - 720p FLUX.2 [klein], two steps');
    })
  );
  $('#setPolish').addEventListener('click', async () => {
    const next = $('#setPolish').getAttribute('aria-checked') !== 'true';
    await saveSettings({ polishStills: next });
    applySettings();
    toast(next ? 'Polish pass on - each still is re-drawn once' : 'Polish pass off - stills are painted once');
  });
  $('#setAutoScenes').addEventListener('click', async () => {
    const next = $('#setAutoScenes').getAttribute('aria-checked') !== 'true';
    await saveSettings({ autoScenes: next });
    applySettings();
    toast(next
      ? 'Scenes on - stills are painted ahead while you read'
      : 'Scenes off - only the scene on screen is painted');
  });
  $('#setAutoFilms').addEventListener('click', async () => {
    const next = $('#setAutoFilms').getAttribute('aria-checked') !== 'true';
    await saveSettings({ autoFilms: next });
    // Turning it off stops new auto work; the running chapter keeps painting
    // and its own Cancel stays the way to stop it.
    if (!next) state.cinema.autoRun = null;
    applySettings();
    toast(next
      ? 'Chapter films on - the remaining chapters queue after the one you start'
      : 'Chapter films off - each chapter asks before it queues');
  });
  $('#setAutoScroll').addEventListener('click', () => {
    state.settings.autoScroll = !state.settings.autoScroll;
    state.autoScroll = state.settings.autoScroll;
    saveSettings({ autoScroll: state.autoScroll });
    applySettings();
  });
  $('#setRemote').addEventListener('click', async () => {
    const next = $('#setRemote').getAttribute('aria-checked') !== 'true';
    if (next) {
      const ok = window.confirm(
        'Remote access lets other devices on your network or Tailscale open Rhapsode with a private link.\n\nTurn it on?'
      );
      if (!ok) return;
    }
    await saveSettings({ remoteAccess: next });
    applySettings();
    loadNetwork();
    toast(next ? 'Remote access will be on after restarting Rhapsode' : 'Remote access will be off after restarting Rhapsode');
  });
  $('#setTranslate').addEventListener('click', async () => {
    const next = $('#setTranslate').getAttribute('aria-checked') !== 'true';
    await saveSettings({ translateToVoice: next });
    applySettings();
    state.clips.clear();
    state.pending.clear();
    state.durations.clear();
    state.gen++;
    toast(next ? 'Voices will translate text into their language' : 'Voices will read the original text');
    if (state.doc) playUnit(state.index, { restart: true });
  });
  $('#clearCacheBtn').addEventListener('click', async () => {
    try {
      const res = await api('/api/cache/prune', { method: 'POST' });
      toast(`Cache trimmed · ${(res.bytes / 1e6).toFixed(0)} MB in use`);
      $('#cacheInfo').textContent = cacheLabel(res);
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  // reader clicks
  $('#reader').addEventListener('click', (e) => {
    const unit = e.target.closest('.unit');
    if (!unit) return;
    const i = parseInt(unit.dataset.i, 10);
    if (Number.isNaN(i)) return;
    if (i === state.audioIndex && !audio.paused) return;
    playUnit(i, { restart: true });
  });

  // keyboard
  document.addEventListener('keydown', onKeyDown);
  setupMediaSession();

  // audio events
  audio.addEventListener('ended', onAudioEnded);
  audio.addEventListener('play', () => {
    state.playing = true;
    state.finished = false;
    updatePlayerUI();
  });
  audio.addEventListener('pause', () => {
    if (!audio.paused) return; // a new clip already took over
    if (!state.loading) {
      state.playing = false;
      updatePlayerUI();
      savePosition();
    }
  });
  audio.addEventListener('error', () => {
    if (!audio.src) return;
    setLoading(false);
    toast('Audio could not be loaded', 'error');
  });
  // Background tabs pause requestAnimationFrame, so keep progress honest.
  audio.addEventListener('timeupdate', () => {
    if (state.playing) tick();
  });

  // drag & drop anywhere
  let dragDepth = 0;
  window.addEventListener('dragenter', (e) => {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    dragDepth++;
    $('#dropOverlay').classList.add('is-on');
  });
  window.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) $('#dropOverlay').classList.remove('is-on');
  });
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => {
    e.preventDefault();
    dragDepth = 0;
    $('#dropOverlay').classList.remove('is-on');
    const file = e.dataTransfer?.files?.[0];
    if (file) addFile(file);
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) savePosition(true);
  });
  let pdfLayoutFrame = 0;
  window.addEventListener('resize', () => {
    if (pdfLayoutFrame) cancelAnimationFrame(pdfLayoutFrame);
    pdfLayoutFrame = requestAnimationFrame(() => {
      pdfLayoutFrame = 0;
      if (state.doc && state.doc.viewer === 'pdf' && state.view === 'page') {
        highlightPdfWord(state.wordIdx);
      }
    });
  });
  window.addEventListener('beforeunload', () => {
    if (!state.doc) return;
    try {
      fetch(`/api/docs/${state.doc.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ position: state.index }),
        keepalive: true,
      });
    } catch (_) {}
  });
}

/* ─────────────────────────── engine + voices ─────────────────────────── */

async function checkEngine() {
  const el = $('#engineState');
  try {
    const health = await api('/api/health');
    if (health.modelReady) {
      el.classList.remove('is-loading', 'is-error');
      const ocr = health.ocr ? ' · OCR' : '';
      el.querySelector('span').textContent = `Kokoro-82M${ocr}`;
      el.title = `${health.voices} voices · on-device`;
      $('#cacheInfo').textContent = cacheLabel(health.cache);
    } else if (health.modelError) {
      // Say what is wrong instead of loading forever: the pill used to spin on a
      // voice model that was never going to appear. Keep a slow poll going so a
      // bootstrap that finishes while the app is open heals it by itself.
      el.classList.remove('is-loading');
      el.classList.add('is-error');
      el.querySelector('span').textContent = 'Voice model missing';
      el.title = `${health.modelError} Click to retry.`;
      setTimeout(checkEngine, 5000);
    } else {
      el.classList.add('is-loading');
      el.querySelector('span').textContent = 'Loading voice model…';
      setTimeout(checkEngine, 1500);
    }
  } catch (_) {
    el.classList.add('is-error');
    el.querySelector('span').textContent = 'Engine offline';
  }
}

function cacheLabel(cache) {
  if (!cache) return '';
  const mb = (cache.bytes || 0) / 1e6;
  return `${cache.clips} clips cached · ${mb < 1 ? '0' : mb.toFixed(0)} MB`;
}

async function loadVoices() {
  try {
    const data = await api('/api/voices');
    fillVoiceSelect($('#voiceSel'), data.groups);
    fillVoiceSelect($('#setVoice'), data.groups);
    state.voice = state.settings.voice || data.default;
    syncVoiceSelects();
  } catch (_) {}
}

function fillVoiceSelect(select, groups) {
  select.innerHTML = '';
  for (const group of groups) {
    const og = document.createElement('optgroup');
    og.label = `${group.flag}  ${group.label}`;
    for (const v of group.voices) {
      const option = document.createElement('option');
      option.value = v.id;
      option.textContent = `${v.name}${v.recommended ? '  ★' : ''}`;
      option.title = `${v.gender === 'F' ? 'Female' : 'Male'} · grade ${v.grade}`;
      og.appendChild(option);
    }
    select.appendChild(og);
  }
}

function syncVoiceSelects() {
  $('#voiceSel').value = state.voice;
  $('#setVoice').value = state.settings.voice;
}

function buildSpeedOptions() {
  const select = $('#speedSel');
  select.innerHTML = '';
  for (const s of SPEEDS) {
    const option = document.createElement('option');
    option.value = String(s);
    option.textContent = `${s % 1 === 0 ? s.toFixed(1) : s}×`;
    select.appendChild(option);
  }
  select.value = String(state.speed);
}

/* ─────────────────────────── settings ─────────────────────────── */

function applySettings() {
  const s = state.settings;
  const theme = window.applyRhapsodeTheme ? window.applyRhapsodeTheme(s.theme) : null;
  if (theme) {
    state.settings.theme = theme.id;
    const current = $('#themeCurrent');
    if (current) current.textContent = `${theme.name} · ${theme.mode}`;
    const select = $('#setThemeSelect');
    if (select && select.value !== theme.id) select.value = theme.id;
    updateThemePreview(theme);
  }
  const stack = window.rhapsodeFontStack ? window.rhapsodeFontStack(s.font) : '';
  if (stack) document.documentElement.style.setProperty('--font-read', stack);
  else document.documentElement.style.removeProperty('--font-read');
  document.documentElement.style.setProperty('--read-size', `${s.fontSize}px`);
  state.autoScroll = s.autoScroll;
  $('#autoBtn').classList.toggle('is-on', state.autoScroll);
  $('#fontSizeLabel').textContent = s.fontSize;
  $('#setFontSize').value = s.fontSize;
  const fontSelect = $('#setFontFamily');
  if (fontSelect && fontSelect.options.length) fontSelect.value = s.font;
  $$('#setFilmQuality button').forEach((b) =>
    b.classList.toggle('is-active', b.dataset.filmQuality === (s.filmQuality || 'high'))
  );
  $('#setAutoScroll').setAttribute('aria-checked', String(s.autoScroll));
  $('#setRemote').setAttribute('aria-checked', String(!!s.remoteAccess));
  $('#setTranslate').setAttribute('aria-checked', String(s.translateToVoice !== false));
  $('#setPolish').setAttribute('aria-checked', String(s.polishStills !== false));
  $('#setAutoScenes').setAttribute('aria-checked', String(s.autoScenes !== false));
  $('#setAutoFilms').setAttribute('aria-checked', String(!!s.autoFilms));
  syncVoiceSelects();
  $('#speedSel').value = String(state.speed);
  if (state.doc && state.doc.viewer === 'epub' && state.view === 'page' && !$('#pageView').hidden) {
    const frame = $('#chapterFrame');
    frame.dataset.url = '';
    renderPage(state.page);
  }
  try {
    localStorage.setItem('murmur.theme', s.theme);
    localStorage.setItem('murmur.font', s.font);
  } catch (_) {}
}

function renderThemeOptions() {
  const select = $('#setThemeSelect');
  if (!select || !window.RHAPSODE_THEMES) return;
  select.innerHTML = '';
  // Two groups rather than one long list: picking a light theme is a different
  // intent from picking a dark one, and the count is 20.
  const groups = { dark: document.createElement('optgroup'), light: document.createElement('optgroup') };
  groups.dark.label = 'Dark';
  groups.light.label = 'Light';
  for (const theme of window.RHAPSODE_THEMES) {
    const option = document.createElement('option');
    option.value = theme.id;
    option.textContent = theme.name;
    if (theme.note) option.title = theme.note;
    (groups[theme.mode] || groups.dark).appendChild(option);
  }
  for (const group of Object.values(groups)) {
    if (group.children.length) select.appendChild(group);
  }
}

/* A select cannot show colours, so the selected palette sits beside it: page,
   panel and accent, which is what tells the 20 themes apart at a glance. */
function updateThemePreview(theme) {
  const preview = $('#themePreview');
  if (!preview || !theme) return;
  const chips = [theme.colors.bg, theme.colors.panel, theme.colors.accent];
  [...preview.children].forEach((chip, index) => {
    chip.style.background = chips[index] || theme.colors.accent;
  });
  preview.title = `${theme.name} — ${theme.mode}${theme.note ? `, ${theme.note}` : ''}`;
}

let fontsLoaded = false;

async function loadFonts() {
  const select = $('#setFontFamily');
  if (!select) return;
  if (fontsLoaded) {
    select.value = state.settings.font || 'sans';
    return;
  }
  const addOption = (value, label, parent) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    (parent || select).appendChild(option);
  };
  try {
    const data = await api('/api/fonts');
    fontsLoaded = true;
    select.innerHTML = '';
    addOption('sans', 'System sans');
    addOption('serif', 'System serif');
    const families = (data.fonts || []).filter(
      (name) => name && name !== 'sans' && name !== 'serif'
    );
    if (families.length) {
      const group = document.createElement('optgroup');
      group.label = 'Installed fonts';
      for (const name of families) addOption(name, name, group);
      select.appendChild(group);
    }
    const current = state.settings.font || 'sans';
    if (![...select.options].some((option) => option.value === current)) {
      addOption(current, current);
    }
    select.value = current;
  } catch (_) {
    select.innerHTML = '';
    addOption('sans', 'System sans');
    addOption('serif', 'System serif');
    select.value = state.settings.font || 'sans';
  }
}

async function saveSettings(patch) {
  try {
    state.settings = { ...state.settings, ...(await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })) };
  } catch (_) {}
}

function toggleTheme() {
  const mode = document.documentElement.dataset.mode === 'light' ? 'dark' : 'light';
  state.settings.theme = mode === 'light' ? 'paper' : 'ink';
  saveSettings({ theme: state.settings.theme });
  applySettings();
}

function bumpFont(delta) {
  const size = Math.min(28, Math.max(15, state.settings.fontSize + delta));
  state.settings.fontSize = size;
  applySettings();
  saveSettings({ fontSize: size });
}

/* ─────────────────────────── library ─────────────────────────── */

async function refreshLibrary() {
  try {
    const data = await api('/api/docs');
    state.docs = data.docs || [];
  } catch (_) {
    state.docs = [];
  }
  renderLibrary();
}

function renderLibrary() {
  const library = $('#library');
  library.innerHTML = '';
  $('#libCount').textContent = state.docs.length ? String(state.docs.length) : '';
  if (!state.docs.length) {
    const empty = document.createElement('div');
    empty.className = 'toc-empty';
    empty.textContent = 'No documents yet. Add one to start listening.';
    library.appendChild(empty);
  }
  for (const doc of state.docs) {
    const item = document.createElement('div');
    item.className = 'lib-item' + (state.doc && state.doc.id === doc.id ? ' is-active' : '');
    item.tabIndex = 0;
    item.dataset.id = doc.id;
    item.setAttribute('role', 'button');
    item.setAttribute('aria-current', state.doc && state.doc.id === doc.id ? 'true' : 'false');

    const title = document.createElement('div');
    title.className = 'lib-title';
    title.textContent = doc.title;

    const meta = document.createElement('div');
    meta.className = 'lib-meta';
    meta.textContent = `${doc.words.toLocaleString()} words · ${readTime(doc.words)}`;

    const bar = document.createElement('div');
    bar.className = 'lib-progress';
    const fill = document.createElement('i');
    const pct = doc.unitCount ? (doc.position / doc.unitCount) * 100 : 0;
    fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    bar.appendChild(fill);

    const del = document.createElement('button');
    del.className = 'lib-del';
    del.title = 'Remove';
    del.setAttribute('aria-label', `Remove ${doc.title}`);
    del.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      removeDoc(doc);
    });

    item.append(title, meta, bar, del);
    item.addEventListener('click', () => openDoc(doc.id));
    item.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openDoc(doc.id);
      }
    });
    library.appendChild(item);
  }
}

/* ─────────────────────────── contents ─────────────────────────── */

function setSidePanel(panel) {
  state.sidePanel = panel === 'contents' ? 'contents' : 'library';
  $('#libraryPanel').hidden = state.sidePanel !== 'library';
  $('#contentsPanel').hidden = state.sidePanel !== 'contents';
  $$('#sideTabs button').forEach((button) =>
    button.classList.toggle('is-active', button.dataset.panel === state.sidePanel)
  );
  if (state.sidePanel === 'contents') updateTocActive();
}

function renderContents() {
  const list = $('#contents');
  const toc = (state.doc && state.doc.toc) || [];
  const tab = $('#contentsTab');
  tab.disabled = toc.length === 0;
  list.innerHTML = '';

  if (!toc.length) {
    const empty = document.createElement('div');
    empty.className = 'toc-empty';
    empty.textContent = state.doc
      ? 'No chapters were detected in this document.'
      : 'Open a book or PDF to see its chapters here.';
    list.appendChild(empty);
    return;
  }

  toc.forEach((entry, i) => {
    const button = document.createElement('button');
    button.className = 'toc-item';
    button.dataset.index = String(i);
    button.style.paddingLeft = `${10 + Math.max(0, entry.level || 0) * 12}px`;
    button.textContent = entry.title || `Section ${i + 1}`;
    button.title = entry.title || '';
    button.addEventListener('click', () => jumpToToc(i));
    list.appendChild(button);
  });

  updateTocActive();
}

function updateTocActive() {
  const toc = (state.doc && state.doc.toc) || [];
  if (!toc.length) return;
  let active = 0;
  for (let i = 0; i < toc.length; i++) {
    if (toc[i].unit <= state.index) active = i;
  }
  $$('#contents .toc-item').forEach((el, i) => el.classList.toggle('is-active', i === active));
  const element = $(`#contents .toc-item[data-index="${active}"]`);
  if (element && state.sidePanel === 'contents') {
    element.scrollIntoView({ block: 'nearest' });
  }
}

function jumpToToc(index) {
  const entry = state.doc && state.doc.toc && state.doc.toc[index];
  if (!entry) return;
  playUnit(entry.unit, { restart: true });
  savePosition(true);
  document.body.classList.remove('sidebar-open');
}

async function removeDoc(doc) {
  if (!confirm(`Remove “${doc.title}” from your library?`)) return;
  try {
    await api(`/api/docs/${doc.id}`, { method: 'DELETE' });
    if (state.doc && state.doc.id === doc.id) {
      stopAudio();
      state.doc = null;
      state.units = [];
      $('#reader').innerHTML = '';
      $('#exportBtn').hidden = true;
      history.replaceState(null, '', location.pathname);
      showEmpty();
    }
    await refreshLibrary();
    toast('Removed from library');
  } catch (err) {
    toast(err.message, 'error');
  }
}

function showEmpty() {
  $('#empty').hidden = false;
  $('#pageView').hidden = true;
  $('#cinemaView').hidden = true;
  $('#readerScroll').hidden = false;
  $('#viewToggle').hidden = true;
  renderContents();
  setSidePanel('library');
  $('#docTitle').textContent = 'Library';
  $('#docMeta').textContent = 'Pick something to listen to';
  $('#nowTitle').textContent = 'Nothing playing';
  $('#nowSub').textContent = 'Add a document to begin';
  $('#clauseInfo').textContent = '—';
  $('#progressFill').style.width = '0%';
  $('#reader').classList.remove('is-playing');
  updatePlayerUI();
}

/* ─────────────────────────── documents ─────────────────────────── */

async function openDoc(id, { autoplay = false, position = null } = {}) {
  if (state.doc && state.doc.id === id && state.units.length) {
    // A search hit can point into the book that is already open.
    if (position != null) {
      state.index = Math.min(Math.max(0, position), state.units.length - 1);
      setCurrentUnit(state.index);
      updatePlayerUI();
      savePosition(true);
    }
    if (autoplay) togglePlay();
    return;
  }
  stopAudio();
  let doc;
  try {
    doc = await api(`/api/docs/${id}`);
  } catch (err) {
    toast(err.message, 'error');
    return;
  }

  state.gen++;
  state.doc = doc;
  state.units = doc.units.map((text) => ({ text, el: null, spans: [] }));
  state.docChars = doc.units.map((t) => t.length);
  state.totalChars = state.docChars.reduce((a, b) => a + b, 0) || 1;
  state.durations.clear();
  state.clips.clear();
  state.pending.clear();
  state.chapter = null;
  state.translated = false;
  state.spoken = '';
  state.translateWarned = false;
  state.cinema = {
    planKey: '',
    chapter: -1,
    scenes: [],
    index: -1,
    cache: new Map(),
    pending: new Map(),
    token: (state.cinema.token || 0) + 1,
    sceneToken: (state.cinema.sceneToken || 0) + 1,
    prefetchedKey: '',
    ready: new Set(),
    preparing: false,
    prepareToken: (state.cinema.prepareToken || 0) + 1,
    jobs: state.cinema.jobs || new Map(),
    filmTimer: state.cinema.filmTimer || 0,
  };
  state.pdfWords = null;
  state.pdfPage = -1;
  state.pdfUnitText = '';
  state.pdfUnitStart = -1;
  state.pdfLastPos = 0;
  hidePdfWord();
  const chapterFrame = $('#chapterFrame');
  chapterFrame.dataset.url = '';
  chapterFrame.dataset.chapter = '';
  state.index = Math.min(Math.max(0, position ?? doc.position ?? 0), state.units.length - 1);
  state.audioIndex = -1;
  state.words = [];
  state.finished = false;
  state.voice = doc.voice || state.settings.voice;
  state.speed = doc.speed || state.settings.speed;
  if (state.view !== 'cinema') {
    state.view = doc.viewer ? 'page' : 'text';
  }

  renderDoc();
  syncVoiceSelects();
  $('#speedSel').value = String(state.speed);

  $('#empty').hidden = true;
  $('#exportBtn').hidden = false;
  document.body.classList.remove('sidebar-open');
  $('#docTitle').textContent = doc.title;
  $('#docMeta').textContent = `${doc.words.toLocaleString()} words · about ${readTime(doc.words)}`;
  $('#nowTitle').textContent = doc.title;
  history.replaceState(null, '', `#${doc.id}`);

  setCurrentUnit(state.index);
  applyView();
  updatePlayerUI();
  renderContents();
  setSidePanel(doc.toc && doc.toc.length ? 'contents' : 'library');
  refreshLibrary();

  if (autoplay) playUnit(state.index, { restart: true });
}

function renderDoc() {
  const reader = $('#reader');
  reader.innerHTML = '';
  const frag = document.createDocumentFragment();

  state.units.forEach((unit, i) => {
    const p = document.createElement('p');
    p.className = 'unit';
    p.dataset.i = String(i);
    p.textContent = unit.text;
    unit.el = p;
    unit.spans = [];
    frag.appendChild(p);
  });

  reader.appendChild(frag);
}

// Word spans are only materialized for sentences that are actually spoken,
// which keeps very long books cheap to render.
function ensureSpans(unit) {
  if (!unit || !unit.el || unit.spans.length) return;
  const tokens = unit.text.match(/\S+\s*/g) || [];
  unit.el.textContent = '';
  const frag = document.createDocumentFragment();
  for (const token of tokens) {
    const parts = token.match(/^(\S+)([\s\S]*)$/);
    if (!parts) continue;
    const span = document.createElement('span');
    span.className = 'w';
    span.textContent = parts[1];
    frag.appendChild(span);
    unit.spans.push(span);
    if (parts[2]) frag.appendChild(document.createTextNode(parts[2]));
  }
  unit.el.appendChild(frag);
}

/* ─────────────────────────── audio pipeline ─────────────────────────── */

async function getClip(i) {
  if (state.clips.has(i)) return state.clips.get(i);
  if (state.pending.has(i)) return state.pending.get(i);

  const voice = state.voice;
  const promise = (async () => {
    const data = await api('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: state.units[i].text, voice }),
    });
    const clip = {
      url: data.url,
      words: data.words,
      duration: data.duration,
      spoken: data.spoken || state.units[i].text,
      translated: !!data.translated,
      targetLang: data.targetLang || '',
      error: data.translateError || null,
    };
    if (voice === state.voice) {
      state.clips.set(i, clip);
      state.durations.set(i, clip.duration);
    }
    state.pending.delete(i);
    return clip;
  })().catch((err) => {
    state.pending.delete(i);
    throw err;
  });

  state.pending.set(i, promise);
  return promise;
}

async function prefetch(from, gen) {
  for (let step = 1; step <= PREFETCH; step++) {
    const i = from + step;
    if (i >= state.units.length) break;
    if (gen !== state.gen) return;
    if (state.clips.has(i) || state.pending.has(i)) continue;
    try {
      await getClip(i);
    } catch (_) {}
  }
  pruneClips();
}

function pruneClips() {
  if (state.clips.size <= 24) return;
  for (const key of [...state.clips.keys()]) {
    if (Math.abs(key - state.index) > 10 && key !== state.audioIndex) {
      state.clips.delete(key);
    }
  }
}

function cinemaChapterKey(chapter) {
  return `${state.doc.id}|${chapter}`;
}

function resetCinemaQuality() {
  const c = state.cinema;
  c.planKey = '';
  c.chapter = -1;
  c.scenes = [];
  c.index = -1;
  c.cache.clear();
  c.pending.clear();
  c.ready.clear();
  c.prefetchedKey = '';
  c.token++;
  c.sceneToken++;
  c.prepareToken++;
  c.preparing = false;
  $('#cinemaLoading').hidden = true;

  if (!state.doc || state.view !== 'cinema') return;
  const index = state.index;
  if (state.playing || state.loading) {
    pause();
    playUnit(index);
  } else {
    updateCinema(index);
  }
}

async function prepareThenPlay(index) {
  if (!state.doc || state.cinema.preparing) return;
  const token = ++state.cinema.prepareToken;
  state.cinema.preparing = true;
  state.index = index;
  state.translated = false;
  state.spoken = '';
  state.targetLang = '';
  state.wordIdx = -1;
  setCurrentUnit(index);
  pause();
  setLoading(true);

  const chapter = cinemaChapterForUnit(index);
  const key = cinemaChapterKey(chapter);
  const loading = $('#cinemaLoading');
  const label = $('#cinemaLoadingText');
  const fill = $('#cinemaProgressFill');

  try {
    await ensureCinemaPlan(chapter, index);
    if (token !== state.cinema.prepareToken) return;
    const scene = sceneForUnit(state.cinema.scenes, index);

    await api('/api/film/prefetch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        doc: state.doc.id,
        chapter,
        index: scene ? scene.index : 0,
        count: 0,
      }),
    });

    loading.hidden = false;
    while (token === state.cinema.prepareToken) {
      const progress = await api(
        `/api/film/progress?doc=${encodeURIComponent(state.doc.id)}&chapter=${chapter}`
      );
      const finishedCount = (progress.done || 0) + (progress.failed || 0);
      const total = Math.max(1, progress.total || 1);
      label.textContent = `Preparing chapter — ${finishedCount}/${progress.total} scenes`;
      fill.style.width = `${Math.min(100, Math.round((finishedCount / total) * 100))}%`;
      if (finishedCount >= progress.total) {
        state.cinema.ready.add(key);
        loading.hidden = true;
        break;
      }
      await sleep(1500);
    }
    if (token !== state.cinema.prepareToken) return;

    setLoading(false);
    playUnit(index, { restart: true });
  } catch (err) {
    loading.hidden = true;
    setLoading(false);
    toast(err.message || 'Could not prepare the chapter', 'error');
  } finally {
    if (token === state.cinema.prepareToken) state.cinema.preparing = false;
  }
}

async function playUnit(i, { restart = false } = {}) {
  if (!state.doc || !state.units.length) return;
  if (i < 0) i = 0;
  if (i >= state.units.length) i = state.units.length - 1;

  const gen = ++state.gen;
  state.index = i;
  state.finished = false;
  state.translated = false;
  state.spoken = '';
  setLoading(true);
  audio.pause();
  cancelAnimationFrame(state.raf);
  clearWordHighlight();
  state.wordIdx = -1;
  setCurrentUnit(i);

  try {
    const clip = await getClip(i);
    if (gen !== state.gen) return;

    const canResume =
      !restart &&
      state.audioIndex === i &&
      audio.src &&
      audio.currentTime > 0.02 &&
      !audio.ended;

    if (!canResume) {
      audio.src = clip.url;
      audio.currentTime = 0;
      state.audioIndex = i;
      state.words = clip.words;
      state.wordIdx = -1;
      clearWordHighlight();
    }

    audio.playbackRate = state.speed;
    audio.preservesPitch = true;

    state.translated = clip.translated;
    state.spoken = clip.spoken;
    state.targetLang = clip.targetLang || '';
    resetPseudo();
    if (clip.error && !state.translateWarned) {
      toast(clip.error, 'error', 7000);
      state.translateWarned = true;
    }
    updatePageCaption(state.index);

    await audio.play();

    if (gen !== state.gen) return;
    setLoading(false);
    state.playing = true;
    $('#gestureBtn').hidden = true;
    updatePlayerUI();
    startTicker();
    prefetch(i, gen);
    savePosition();
  } catch (err) {
    if (gen !== state.gen) return;
    setLoading(false);
    state.playing = false;
    updatePlayerUI();
    if (err && err.name === 'NotAllowedError') {
      $('#gestureBtn').hidden = false;
      toast('Click “Listen” to start — the browser needs a tap first');
    } else if (err) {
      toast(err.message || 'Could not play that sentence', 'error');
    }
  }
}

function resume() {
  if (!audio.src) return playUnit(state.index, { restart: true });
  audio.playbackRate = state.speed;
  audio
    .play()
    .then(() => {
      state.playing = true;
      $('#gestureBtn').hidden = true;
      updatePlayerUI();
      startTicker();
      savePosition();
    })
    .catch(() => playUnit(state.index, { restart: true }));
}

function pause() {
  audio.pause();
  state.playing = false;
  cancelAnimationFrame(state.raf);
  updatePlayerUI();
  savePosition(true);
}

function togglePlay() {
  if (!state.doc) {
    openAdd();
    return;
  }
  $('#playBtn').classList.remove('needs-play');
  if (state.playing) pause();
  else if (state.audioIndex === state.index && audio.src && !audio.ended) resume();
  else playUnit(state.index, { restart: false });
}

function nextSentence() {
  if (!state.doc) return;
  if (state.index + 1 >= state.units.length) {
    pause();
    toast('End of document');
    return;
  }
  playUnit(state.index + 1, { restart: true });
}

function prevSentence() {
  if (!state.doc) return;
  if (state.view === 'cinema') {
    if (state.index === 0) {
      playUnit(0, { restart: true });
    } else {
      playUnit(state.index - 1, { restart: true });
    }
    return;
  }
  if (audio.currentTime > 2 || state.index === 0) {
    playUnit(state.index, { restart: true });
  } else {
    playUnit(state.index - 1, { restart: true });
  }
}

function onAudioEnded() {
  if (!state.doc) return;
  if (state.index + 1 < state.units.length) {
    playUnit(state.index + 1, { restart: true });
  } else {
    state.playing = false;
    state.finished = true;
    cancelAnimationFrame(state.raf);
    updatePlayerUI();
    clearWordHighlight();
    savePosition(true);
    toast('Finished reading');
  }
}

function stopAudio() {
  state.gen++;
  audio.pause();
  audio.removeAttribute('src');
  try {
    audio.load();
  } catch (_) {}
  cancelAnimationFrame(state.raf);
  state.playing = false;
  state.loading = false;
  state.audioIndex = -1;
  state.words = [];
  state.wordIdx = -1;
  state.translated = false;
  state.spoken = '';
  updateTranslationBar();
  resetPseudo();
  clearWordHighlight();
  setLoading(false);
  updatePlayerUI();
}

/* ─────────────────────────── playback UI ─────────────────────────── */

function startTicker() {
  cancelAnimationFrame(state.raf);
  const loop = () => {
    tick();
    if (state.playing) state.raf = requestAnimationFrame(loop);
  };
  state.raf = requestAnimationFrame(loop);
}

function tick() {
  if (state.audioIndex < 0 || !audio.src) return;
  const t = audio.currentTime;
  updateWordHighlight(t);
  updateProgress(t);
  maybeAutoScroll(t);
}

function updateWordHighlight(t) {
  const words = state.words;
  if (!words || !words.length) return;
  let lo = 0;
  let hi = words.length - 1;
  let idx = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].start <= t) {
      idx = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  if (idx < 0) idx = 0;
  const changed = idx !== state.wordIdx;
  state.wordIdx = idx;

  if (state.view === 'cinema') return;

  if (state.translated) {
    if (changed) highlightTranslationWord(idx);
    highlightRenderedWord(t);
    return;
  }

  if (!changed) return;

  if (state.view === 'page' && state.doc && state.doc.viewer === 'epub') {
    highlightChapterWord(idx);
    return;
  }
  if (state.view === 'page' && state.doc && state.doc.viewer) {
    highlightPageCaptionWord(idx);
    highlightPdfWord(idx);
    return;
  }

  const unit = state.units[state.index];
  if (!unit) return;
  ensureSpans(unit);
  if (!unit.spans.length) return;
  const span = unit.spans[Math.min(idx, unit.spans.length - 1)];
  if (state.activeWordEl && state.activeWordEl !== span) {
    state.activeWordEl.classList.remove('is-active');
  }
  if (span) {
    span.classList.add('is-active');
    state.activeWordEl = span;
  }
}

/* Translated audio has different words, so map its duration onto the original
   sentence's words by speech weight and keep a cursor moving on the page. */
function resetPseudo() {
  state.pseudoKey = '';
  state.pseudoTimes = [];
  state.pseudoIdx = -1;
}

function speechWeight(text) {
  const core = (text.match(/[\p{L}\p{N}]/gu) || []).length;
  const vowels = (text.match(/[aeiouyAEIOUY]/g) || []).length;
  let weight = 0.75 * Math.max(1, core) + 1.1 * vowels;
  const last = text.trim().slice(-1);
  const pauses = { ',': 0.35, ';': 0.6, ':': 0.55, '—': 0.6, '.': 0.9, '!': 0.9, '?': 0.9, '…': 1.1 };
  if (pauses[last]) weight += pauses[last];
  return Math.max(1, weight);
}

function pseudoTokens() {
  const unit = state.units[state.index];
  if (!unit) return null;

  if (state.view === 'page' && state.doc && state.doc.viewer === 'epub') {
    const chapter = state.chapter;
    if (!chapter) return null;
    const range = chapter.ranges[state.index - chapter.first];
    if (!range) return null;
    if (!range.tokens) {
      range.tokens = [];
      const regex = /\S+\s*/g;
      let match;
      while ((match = regex.exec(range.text))) {
        range.tokens.push({ start: match.index, end: match.index + match[0].length });
      }
    }
    return {
      book: true,
      texts: range.tokens.map((token) => range.text.slice(token.start, token.end)),
    };
  }

  if (!unit.spans.length) return null;
  return { book: false, texts: unit.spans.map((span) => span.textContent) };
}

function ensurePseudo(duration) {
  const source = pseudoTokens();
  if (!source || !source.texts.length) return null;

  const key = `${state.index}|${source.book ? 'b' : 't'}|${duration.toFixed(2)}|${source.texts.length}`;
  if (state.pseudoKey === key) return state.pseudoTimes;

  const start = 0.06;
  const end = Math.max(start + 0.1, duration - 0.12);
  const weights = source.texts.map(speechWeight);
  const total = weights.reduce((sum, value) => sum + value, 0) || 1;

  let accumulated = 0;
  state.pseudoTimes = weights.map((weight) => {
    const from = start + ((end - start) * accumulated) / total;
    accumulated += weight;
    const to = start + ((end - start) * accumulated) / total;
    return { start: from, end: to };
  });
  state.pseudoKey = key;
  state.pseudoIdx = -1;
  return state.pseudoTimes;
}

function highlightRenderedWord(t) {
  const duration = state.durations.get(state.audioIndex) || 0;
  if (!duration) return;
  const times = ensurePseudo(duration);
  if (!times || !times.length) return;

  let low = 0;
  let high = times.length - 1;
  let index = 0;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (times[mid].start <= t) {
      index = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  if (index === state.pseudoIdx) return;
  state.pseudoIdx = index;

  if (state.view === 'page' && state.doc && state.doc.viewer === 'epub') {
    highlightChapterWord(index);
    return;
  }
  if (state.view === 'page' && state.doc && state.doc.viewer) {
    highlightPageCaptionWord(index);
    highlightPdfWord(index);
    return;
  }

  const unit = state.units[state.index];
  if (!unit || !unit.spans.length) return;
  const span = unit.spans[Math.min(index, unit.spans.length - 1)];
  if (state.activeWordEl && state.activeWordEl !== span) {
    state.activeWordEl.classList.remove('is-active');
  }
  if (span) {
    span.classList.add('is-active');
    state.activeWordEl = span;
  }
}

function clearWordHighlight() {
  if (state.activeWordEl) {
    state.activeWordEl.classList.remove('is-active');
    state.activeWordEl = null;
  }
  if (state.pageWordEl) {
    state.pageWordEl.classList.remove('is-active');
    state.pageWordEl = null;
  }
  hidePdfWord();
  state.wordIdx = -1;
}

function updateProgress(t) {
  const duration = state.durations.get(state.audioIndex) || 0;
  const frac = duration > 0 ? Math.min(1, t / duration) : 0;

  let charsBefore = 0;
  for (let i = 0; i < state.index; i++) charsBefore += state.docChars[i];
  const unitChars = state.docChars[state.index] || 0;
  const overall = (charsBefore + frac * unitChars) / state.totalChars;
  $('#progressFill').style.width = `${Math.min(100, overall * 100).toFixed(2)}%`;

  let elapsed = 0;
  let total = 0;
  for (let i = 0; i < state.units.length; i++) {
    const known = state.durations.get(i);
    const est = state.docChars[i] / AVG_CHARS_PER_SEC;
    total += known || est;
    if (i < state.index) elapsed += known || est;
  }
  elapsed += t;
  const remaining = Math.max(0, total - elapsed);
  const sentence =
    state.translated && state.spoken
      ? state.spoken
      : state.units[state.index]?.text || '';
  const language = state.translated
    ? `${LANG_NAMES[state.targetLang] || 'Translated'} · `
    : '';
  $('#nowSub').textContent = `${language}${fmtTime(remaining)} left · ${sentence.slice(0, 90)}`;
}

function setCurrentUnit(i) {
  const unit = state.units[i];
  if (!unit) return;
  clearSearchMatch();
  if (state.activeUnitEl) state.activeUnitEl.classList.remove('is-current');
  ensureSpans(unit);
  unit.el.classList.add('is-current');
  state.activeUnitEl = unit.el;
  $('#clauseInfo').textContent = `${i + 1} / ${state.units.length}`;
  if (state.view === 'page' && state.doc && state.doc.viewer) {
    renderPageForIndex(i);
  } else {
    scrollToUnit(i);
  }
  updatePageCaption(i);
  updateTocActive();
  clearChapterHighlight();
  resetPseudo();
  if (state.view === 'cinema') updateCinema(i);
}

/* ─────────────────────────── page view ─────────────────────────── */

function applyView() {
  const hasDoc = !!state.doc;
  const hasViewer = !!(hasDoc && state.doc.viewer);
  let mode = state.view;
  if (!hasDoc) mode = 'text';
  else if (mode === 'book') mode = hasViewer ? 'page' : 'text';
  else if (mode === 'page' && !hasViewer) mode = 'text';

  const isText = mode === 'text';
  const isPage = mode === 'page';
  const isCinema = mode === 'cinema';

  $('#viewToggle').hidden = !hasDoc;
  $('#readerScroll').hidden = !isText;
  $('#pageView').hidden = !isPage;
  $('#cinemaView').hidden = !isCinema;
  $('#pageView').classList.toggle('is-epub', isPage && hasViewer && state.doc.viewer === 'epub');

  const active = isCinema ? 'cinema' : 'book';
  $$('#viewToggle button').forEach((button) =>
    button.classList.toggle('is-active', button.dataset.view === active)
  );

  resetPseudo();
  if (isCinema) {
    updateCinema(state.index);
  } else if (isPage) {
    renderPageForIndex(state.index);
  } else if (hasDoc) {
    requestAnimationFrame(() => scrollToUnit(state.index, 'auto'));
  }
  updatePageCaption(state.index);
}

function setView(view) {
  if (view === 'book') {
    state.view = state.doc && state.doc.viewer ? 'page' : 'text';
  } else if (view === 'cinema') {
    state.view = 'cinema';
  } else {
    state.view = 'text';
  }
  if (state.view !== 'cinema') {
    state.cinema.prepareToken++;
    state.cinema.preparing = false;
    $('#cinemaLoading').hidden = true;
  }
  try {
    localStorage.setItem(
      'murmur.view2',
      state.view === 'cinema' ? 'cinema' : 'book',
    );
  } catch (_) {}
  applyView();
}

function renderPageForIndex(i) {
  const pages = (state.doc && state.doc.unitPages) || [];
  renderPage(pages[i] != null ? pages[i] : 0);
}

function chapterFrameUrl(doc, n) {
  const computed = getComputedStyle(document.documentElement);
  const color = (name, fallback) => {
    const value = computed.getPropertyValue(name).trim();
    return /^#[0-9a-f]{6}$/i.test(value) ? value : fallback;
  };
  const params = new URLSearchParams({
    theme: document.documentElement.dataset.mode || 'dark',
    font: state.settings.font || 'sans',
    size: String(state.settings.fontSize),
    ink: color('--text', '#ece9f8'),
    accent: color('--accent', '#8b7bff'),
    hl: color('--hl', '#8b7bff'),
    hl_ink: color('--hl-ink', '#0d0724'),
  });
  return `/api/docs/${doc.id}/chapter/${n}?${params}`;
}

function renderPage(n) {
  const doc = state.doc;
  if (!doc || !doc.viewer) return;
  const total = Math.max(1, doc.pageCount || 1);
  n = Math.max(0, Math.min(n, total - 1));
  state.page = n;
  if (state.pdfPage !== n) {
    state.pdfPage = n;
    state.pdfWords = null;
    state.pdfUnitText = '';
    state.pdfUnitStart = -1;
    state.pdfLastPos = 0;
    hidePdfWord();
  }

  const image = $('#pageImage');
  const frame = $('#chapterFrame');

  if (doc.viewer === 'epub') {
    image.hidden = true;
    frame.hidden = false;
    const url = chapterFrameUrl(doc, n);
    if (frame.dataset.url !== url) {
      state.chapter = null;
      frame.dataset.url = url;
      frame.dataset.chapter = String(n);
      frame.src = url;
    }
    $('#pageLabel').textContent = `${n + 1} / ${total}`;
    $('#pageBar').hidden = total <= 1;
    $('#pagePrev').disabled = n <= 0;
    $('#pageNext').disabled = n >= total - 1;
    return;
  }

  frame.hidden = true;
  image.hidden = false;
  const url =
    doc.viewer === 'pdf'
      ? `/api/docs/${doc.id}/page/${n}?dpi=200`
      : `/api/docs/${doc.id}/image`;
  if (image.getAttribute('src') !== url) {
    $('#pageView').scrollTop = 0;
    image.classList.add('is-loading');
    image.onload = () => {
      image.classList.remove('is-loading');
      highlightPdfWord(state.wordIdx);
    };
    image.onerror = () => {
      image.classList.remove('is-loading');
      toast('Could not render that page', 'error');
    };
    image.src = url;
  }

  $('#pageLabel').textContent = `${n + 1} / ${total}`;
  $('#pageBar').hidden = total <= 1;
  $('#pagePrev').disabled = n <= 0;
  $('#pageNext').disabled = n >= total - 1;

  // Warm the next page so page turns feel instant.
  if (doc.viewer === 'pdf' && n + 1 < total) {
    const preload = new Image();
    preload.src = `/api/docs/${doc.id}/page/${n + 1}?dpi=200`;
  }
  if (doc.viewer === 'pdf') loadPdfWords(n);
}

function goToPage(n) {
  const doc = state.doc;
  if (!doc || !doc.viewer) return;
  const total = Math.max(1, doc.pageCount || 1);
  if (n < 0 || n >= total) return;

  renderPage(n);

  const pages = doc.unitPages || [];
  const first = pages.indexOf(n);
  if (first >= 0 && first !== state.index) {
    state.index = first;
    setCurrentUnit(first);
    savePosition();
    if (state.playing) playUnit(first, { restart: true });
  }
}

function updatePageCaption(i) {
  const caption = $('#pageCaption');
  if (!caption) return;
  if (!state.doc || !state.doc.viewer) {
    caption.textContent = '';
    caption.dataset.text = '';
    state.pageCaptionSpans = [];
    state.pageWordEl = null;
    hidePdfWord();
  } else {
    const text =
      state.translated && state.spoken && i === state.index
        ? state.spoken
        : (state.units[i] && state.units[i].text) || '';
    if (caption.dataset.text !== text) {
      caption.dataset.text = text;
      caption.textContent = '';
      state.pageCaptionSpans = [];
      state.pageWordEl = null;
      if (state.doc.viewer === 'epub') {
        caption.textContent = text.length > 240 ? `${text.slice(0, 240)}…` : text;
      } else {
        const tokens = text.match(/\S+\s*/g) || [];
        for (const token of tokens) {
          const parts = token.match(/^(\S+)([\s\S]*)$/);
          if (!parts) continue;
          const span = document.createElement('span');
          span.className = 'w';
          span.textContent = parts[1];
          caption.appendChild(span);
          state.pageCaptionSpans.push(span);
          if (parts[2]) caption.appendChild(document.createTextNode(parts[2]));
        }
      }
    }
    if (state.doc.viewer !== 'epub') {
      highlightPageCaptionWord(state.wordIdx);
      highlightPdfWord(state.wordIdx);
    }
  }
  updateCinemaCaption(i);
  updateTranslationBar();
}

function highlightPageCaptionWord(index) {
  const spans = state.pageCaptionSpans;
  if (!spans.length) return;
  if (index < 0) {
    if (state.pageWordEl) {
      state.pageWordEl.classList.remove('is-active');
      state.pageWordEl = null;
    }
    return;
  }
  const active = spans[Math.min(Math.max(0, index), spans.length - 1)];
  if (state.pageWordEl && state.pageWordEl !== active) {
    state.pageWordEl.classList.remove('is-active');
  }
  if (active) {
    active.classList.add('is-active');
    state.pageWordEl = active;
  }
}

function hidePdfWord() {
  const box = $('#pageWordBox');
  if (box) box.hidden = true;
}

async function loadPdfWords(page) {
  if (!state.doc || state.doc.viewer !== 'pdf') return;
  if (state.pdfWords && state.pdfWords.page === page) return;
  try {
    const data = await api(`/api/docs/${state.doc.id}/page/${page}/words`);
    if (!state.doc || state.doc.viewer !== 'pdf' || state.page !== page) return;
    state.pdfWords = data;
    state.pdfUnitText = '';
    state.pdfUnitStart = -1;
    state.pdfLastPos = 0;
    highlightPdfWord(state.wordIdx);
  } catch (_) {
    state.pdfWords = null;
    hidePdfWord();
  }
}

function normalizeWordToken(text) {
  return (text || '')
    .toLowerCase()
    .replace(/[\u00ad\u200b-\u200d\ufeff\ufffe]/g, '')
    .replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '');
}

function pdfUnitStart(unitText, words) {
  const tokens = (unitText.match(/\S+/g) || [])
    .map(normalizeWordToken)
    .filter(Boolean);
  if (!tokens.length || !words.length) return -1;
  const pageTokens = words.map((word) => normalizeWordToken(word.text));
  // Prefer the match closest to where reading left off, so repeated phrases
  // do not jump to the top of the page.
  const anchor = Math.max(0, state.pdfLastPos || 0);
  for (const size of [4, 3, 2, 1]) {
    if (tokens.length < size) continue;
    let best = -1;
    let bestDistance = Infinity;
    for (let start = 0; start + size <= pageTokens.length; start++) {
      let match = true;
      for (let offset = 0; offset < size; offset++) {
        if (pageTokens[start + offset] !== tokens[offset]) {
          match = false;
          break;
        }
      }
      if (!match) continue;
      const distance = Math.abs(start - anchor);
      if (distance < bestDistance) {
        best = start;
        bestDistance = distance;
      }
    }
    if (best >= 0) {
      state.pdfLastPos = best + tokens.length;
      return best;
    }
  }
  return -1;
}

function highlightPdfWord(index) {
  const box = $('#pageWordBox');
  const image = $('#pageImage');
  if (!box || !image) return;
  if (!state.doc || state.doc.viewer !== 'pdf' || state.view !== 'page' || image.hidden) {
    box.hidden = true;
    return;
  }
  if (!state.pdfWords || !state.pdfWords.words.length) {
    box.hidden = true;
    return;
  }
  if (index < 0) {
    box.hidden = true;
    return;
  }
  const unitText = (state.units[state.index] && state.units[state.index].text) || '';
  if (state.pdfUnitText !== unitText) {
    state.pdfUnitText = unitText;
    state.pdfUnitStart = pdfUnitStart(unitText, state.pdfWords.words);
  }
  if (state.pdfUnitStart < 0) {
    box.hidden = true;
    return;
  }
  const tokenCount = (unitText.match(/\S+/g) || []).length;
  const offset = Math.min(index, Math.max(0, tokenCount - 1));
  const word = state.pdfWords.words[state.pdfUnitStart + offset];
  if (!word) {
    box.hidden = true;
    return;
  }
  const stage = image.parentElement;
  const imageRect = image.getBoundingClientRect();
  const stageRect = stage.getBoundingClientRect();
  if (imageRect.width < 10 || imageRect.height < 10) {
    box.hidden = true;
    return;
  }
  box.style.left = `${imageRect.left - stageRect.left + word.x * imageRect.width}px`;
  box.style.top = `${imageRect.top - stageRect.top + word.y * imageRect.height}px`;
  box.style.width = `${Math.max(2, word.w * imageRect.width)}px`;
  box.style.height = `${Math.max(2, word.h * imageRect.height)}px`;
  box.hidden = false;
}

/* ─────────────────────────── cinema view ─────────────────────────── */

function cinemaChapterForUnit(index) {
  const pages = (state.doc && state.doc.unitPages) || [];
  return pages[index] != null ? pages[index] : 0;
}

function updateCinemaCaption(i) {
  const unit = state.units[i];
  const text =
    state.translated && state.spoken && i === state.index
      ? state.spoken
      : (unit && unit.text) || '';
  $('#cinemaCaption').textContent = text;
}

async function ensureCinemaPlan(chapter, unitIndex) {
  const key = `${state.doc.id}|${chapter}`;
  if (state.cinema.planKey === key) return state.cinema.scenes;
  const data = await api(`/api/film/plan?doc=${encodeURIComponent(state.doc.id)}&chapter=${chapter}`);
  state.cinema.planKey = key;
  state.cinema.chapter = chapter;
  state.cinema.scenes = data.scenes || [];
  state.cinema.index = -1;

  return state.cinema.scenes;
}

function sceneForUnit(scenes, unit) {
  for (const scene of scenes) {
    if (unit >= scene.first && unit <= scene.last) return scene;
  }
  return null;
}

async function updateCinema(unitIndex) {
  if (!state.doc || state.view !== 'cinema') return;
  const token = ++state.cinema.token;
  const chapter = cinemaChapterForUnit(unitIndex);
  let scenes;
  try {
    scenes = await ensureCinemaPlan(chapter, unitIndex);
  } catch (_) {
    return;
  }
  if (token !== state.cinema.token) return;

  const scene = sceneForUnit(scenes, unitIndex);
  if (!scene) return;
  $('#cinemaScene').textContent = `Scene ${scene.index + 1} / ${scenes.length}`;

  if (scene.index !== state.cinema.index) {
    state.cinema.index = scene.index;
    showCinemaScene(chapter, scene);
  }
  prefetchCinema(chapter, scenes, scene.index);
}

const CINEMA_PHASES = new Set(['planning', 'reference', 'loading', 'painting', 'cleanup', 'polishing']);

/* The label for one still comes from the server's activity object, never from a
   guess here: a cold model load must not read as painting. */
function sceneActivityLabel(act) {
  const sceneNo = Number.isInteger(act.scene) ? act.scene + 1 : null;
  const where = sceneNo ? `scene ${sceneNo}` : 'the scene';
  let text;
  switch (act.phase) {
    case 'loading':
      text = act.coldStart
        ? 'Loading the stills engine — first still this session'
        : 'Loading the stills engine';
      break;
    case 'planning': text = `Planning ${where}`; break;
    case 'reference': text = 'Painting the character reference'; break;
    case 'painting': text = `Painting ${where}`; break;
    case 'cleanup': text = `Cleaning up ${where}`; break;
    case 'polishing': text = `Polishing ${where}`; break;
    case 'placeholder': text = 'No stills engine on this machine — placeholder scene'; break;
    case 'failed': text = `Could not paint ${where}`; break;
    case 'queued': text = 'Waiting for the stills engine'; break;
    default: text = 'Waiting for the stills engine';
  }
  const queued = Math.max(0, (act.queued || 0) - (act.active || 0));
  if (queued && CINEMA_PHASES.has(act.phase)) text += ` · ${queued} queued`;
  return text;
}

function updateCinemaEngine(act) {
  const chip = $('#cinemaEngine');
  const bits = [act.model, act.profile].filter(Boolean);
  if (act.phase === 'placeholder') bits.push('placeholder');
  if (chip) {
    chip.textContent = bits.join(' · ');
    chip.hidden = !bits.length;
  }
}

function setCinemaBusy(busy) {
  const bar = $('.cinema-progress');
  if (!bar) return;
  bar.classList.toggle('is-busy', !!busy);
  const fill = $('#cinemaProgressFill');
  if (!fill) return;
  // An inline width left by the prepare pass would beat the CSS class.
  fill.style.width = busy ? '' : '0%';
}

function showCinemaScene(chapter, scene) {
  const key = `${chapter}:${scene.index}`;
  const loading = $('#cinemaLoading');
  const label = $('#cinemaLoadingText');
  const retry = $('#cinemaRetry');
  const image = $('#cinemaImage');

  const cached = state.cinema.cache.get(key);
  if (cached) {
    setCinemaImage(cached);
    loading.hidden = true;
    setCinemaBusy(false);
    return;
  }

  state.cinema.sceneToken = (state.cinema.sceneToken || 0) + 1;
  const token = state.cinema.sceneToken;
  image.classList.remove('is-ready');
  if (retry) retry.hidden = true;

  const attempt = async () => {
    if (token !== state.cinema.sceneToken) return;
    let data;
    try {
      data = await api('/api/film/scene', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ doc: state.doc.id, chapter, index: scene.index }),
      });
    } catch (err) {
      if (token !== state.cinema.sceneToken) return;
      loading.hidden = true;
      setCinemaBusy(false);
      toast(err.message || 'Could not paint that scene', 'error');
      return;
    }
    if (token !== state.cinema.sceneToken) return;

    const act = data.activity || {};
    updateCinemaEngine(act);

    if (data.ready) {
      state.cinema.cache.set(key, data.url);
      setCinemaImage(data.url);
      loading.hidden = true;
      setCinemaBusy(false);
      if (data.placeholder) {
        const chip = $('#cinemaEngine');
        if (chip) {
          chip.textContent = 'placeholder — no stills engine';
          chip.hidden = false;
        }
      }
      return;
    }

    loading.hidden = false;
    if (!state.cinema.preparing) label.textContent = sceneActivityLabel(act);
    setCinemaBusy(act.busy !== false);
    if (act.phase === 'failed') {
      setCinemaBusy(false);
      if (retry) {
        retry.hidden = false;
        retry.onclick = () => {
          state.cinema.sceneToken += 1;
          showCinemaScene(chapter, scene);
        };
      }
      toast(act.error || 'Could not paint that scene', 'error');
      return;
    }
    setTimeout(attempt, 1300);
  };

  loading.hidden = false;
  label.textContent = 'Waiting for the stills engine…';
  setCinemaBusy(true);
  attempt();
}

function setCinemaImage(url) {
  const image = $('#cinemaImage');
  image.classList.remove('is-ready', 'cinema-zoom');
  image.src = url;
  image.onload = () => {
    void image.offsetWidth;
    image.classList.add('is-ready', 'cinema-zoom');
  };
}

function prefetchCinema(chapter, scenes, index) {
  // Scenes off means exactly that: the still on screen is painted, the ones
  // after it wait until you arrive. Chapter films are a separate switch.
  if (state.settings.autoScenes === false) return;
  for (let offset = 1; offset <= 2; offset++) {
    const scene = scenes[index + offset];
    if (!scene) break;
    const key = `${chapter}:${scene.index}`;
    if (state.cinema.cache.has(key) || state.cinema.pending.has(key)) continue;
    const promise = api('/api/film/scene', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc: state.doc.id, chapter, index: scene.index }),
    })
      .then((data) => {
        state.cinema.cache.set(key, data.url);
      })
      .catch(() => {})
      .finally(() => state.cinema.pending.delete(key));
    state.cinema.pending.set(key, promise);
  }
}

function filmPhaseLabel(job) {
  if (job.state === 'queued' || job.phase === 'queued') return 'Queued';
  if (job.state === 'done') return 'Ready';
  if (job.state === 'error') return 'Failed';
  if (job.state === 'cancelled') return 'Cancelled';
  const phase = job.phase || '';
  if (phase === 'images') return `Painting ${job.done || 0}/${job.total || 0}`;
  if (phase === 'narration') return `Narrating ${job.done || 0}/${job.total || 0}`;
  if (phase === 'video') return `Encoding ${job.done || 0}/${job.total || 0}`;
  return phase || 'Working';
}

function renderFilmQueue() {
  const box = $('#cinemaQueue');
  const jobs = [...state.cinema.jobs.values()].sort(
    (a, b) => (a.created || 0) - (b.created || 0)
  );
  box.hidden = jobs.length === 0;
  box.innerHTML = '';
  for (const job of jobs) {
    const element = document.createElement('div');
    element.className = 'film-job';
    if (job.state === 'done') element.classList.add('is-done');
    if (job.state === 'error') element.classList.add('is-error');
    const percent = job.total
      ? Math.min(100, Math.round(((job.done || 0) / job.total) * 100))
      : (job.state === 'done' ? 100 : 0);

    const head = document.createElement('div');
    head.className = 'film-job-head';
    const title = document.createElement('span');
    title.className = 'film-job-title';
    title.textContent = `Chapter ${(job.chapter || 0) + 1}`;
    const state = document.createElement('span');
    state.className = 'film-job-state';
    state.textContent = filmPhaseLabel(job);
    head.append(title, state);

    const bar = document.createElement('div');
    bar.className = 'film-job-bar';
    const fill = document.createElement('i');
    fill.style.width = `${percent}%`;
    bar.appendChild(fill);

    const actions = document.createElement('div');
    actions.className = 'film-job-actions';
    if (job.state === 'done') {
      const link = document.createElement('a');
      link.href = `/api/film/${job.id}/video`;
      link.download = '';
      link.textContent = 'Download';
      actions.appendChild(link);
    } else if (job.state === 'error') {
      const message = document.createElement('span');
      message.className = 'film-job-state';
      message.textContent = job.error ? job.error.slice(0, 90) : 'Failed';
      actions.appendChild(message);
    } else if (job.state !== 'cancelled') {
      const cancel = document.createElement('button');
      cancel.dataset.cancel = job.id;
      cancel.textContent = 'Cancel';
      actions.appendChild(cancel);
    }
    if (job.state === 'done' || job.state === 'error' || job.state === 'cancelled') {
      const dismiss = document.createElement('button');
      dismiss.dataset.dismiss = job.id;
      dismiss.textContent = 'Dismiss';
      actions.appendChild(dismiss);
    }

    element.append(head, bar, actions);
    box.appendChild(element);
  }

  // One obvious way to stop auto work: only queued auto jobs are drained, the
  // running chapter keeps its own Cancel. It hides once there is nothing left to
  // cancel, so the row does not linger beside a chapter that is still painting.
  const autoActive = jobs.some(
    (job) => job.auto && !['done', 'error', 'cancelled'].includes(job.state)
  );
  const autoQueued = jobs.some((job) => job.auto && job.state === 'queued');
  if (autoQueued || (state.cinema.autoRun && autoActive)) {
    const foot = document.createElement('div');
    foot.className = 'film-job-actions';
    const stop = document.createElement('button');
    stop.dataset.stopAuto = '1';
    stop.textContent = 'Stop auto-generation';
    foot.appendChild(stop);
    box.appendChild(foot);
  }
}

function startFilmPolling() {
  if (state.cinema.filmTimer) return;
  state.cinema.filmTimer = window.setInterval(pollFilms, 1500);
}

async function pollFilms() {
  const active = [...state.cinema.jobs.values()].filter(
    (job) => !['done', 'error', 'cancelled'].includes(job.state)
  );
  if (!active.length) {
    window.clearInterval(state.cinema.filmTimer);
    state.cinema.filmTimer = 0;
    return;
  }
  await Promise.all(active.map(async (job) => {
    try {
      const status = await api(`/api/film/${job.id}`);
      const wasDone = job.state === 'done';
      Object.assign(job, status);
      if (status.state === 'done' && !wasDone) {
        toast(`Chapter ${job.chapter + 1} film ready`);
        continueAutoRun(job);
      } else if (status.state === 'error') {
        toast(status.error || `Chapter ${job.chapter + 1} film failed`, 'error', 6000);
      }
    } catch (err) {
      job.state = 'error';
      job.error = err.message;
    }
  }));
  renderFilmQueue();
}

async function onFilmQueueClick(event) {
  const stopAuto = event.target.closest('[data-stop-auto]');
  if (stopAuto) {
    state.cinema.autoRun = null;
    try {
      const result = await api('/api/film/stop-auto', { method: 'POST' });
      const stopped = result.stopped || 0;
      toast(stopped
        ? `Stopped ${stopped} queued chapter${stopped === 1 ? '' : 's'} — the one painting continues`
        : 'No queued chapters to stop — the current one continues');
    } catch (err) {
      toast(err.message || 'Could not stop auto-generation', 'error');
    }
    renderFilmQueue();
    return;
  }
  const cancel = event.target.closest('[data-cancel]');
  if (cancel) {
    const id = cancel.dataset.cancel;
    try {
      await api(`/api/film/${id}`, { method: 'DELETE' });
    } catch (_) {}
    const job = state.cinema.jobs.get(id);
    if (job) {
      job.state = 'cancelled';
      job.phase = 'cancelled';
    }
    renderFilmQueue();
    return;
  }
  const dismiss = event.target.closest('[data-dismiss]');
  if (dismiss) {
    state.cinema.jobs.delete(dismiss.dataset.dismiss);
    renderFilmQueue();
  }
}

async function openSceneReview() {
  if (!state.doc) return;
  const chapter = cinemaChapterForUnit(state.index);
  let plan;
  let images;
  try {
    [plan, images] = await Promise.all([
      api(`/api/film/plan?doc=${encodeURIComponent(state.doc.id)}&chapter=${chapter}`),
      api(`/api/film/images?doc=${encodeURIComponent(state.doc.id)}&chapter=${chapter}`),
    ]);
  } catch (err) {
    toast(err.message || 'Could not load scenes', 'error');
    return;
  }
  sceneReview.chapter = chapter;
  sceneReview.scenes = plan.scenes || [];
  sceneReview.images = images.images || [];
  sceneReview.marked.clear();
  sceneReview.current = null;
  sceneReview.rect = null;
  sceneReview.busy = false;
  sceneReview.drag = null;
  $('#sceneEditor').hidden = true;
  $('#sceneGrid').hidden = false;
  $('#sceneRect').hidden = true;
  $('#sceneRegenArea').disabled = true;
  $('#scenePrompt').value = '';
  renderSceneGrid();
  $('#sceneStatus').textContent = sceneReview.images.length
    ? 'Click a scene to inspect it. Drag a box to regenerate only that area.'
    : 'No scenes planned for this chapter.';
  $('#sceneModal').hidden = false;
}

function updateSceneBatch() {
  const count = sceneReview.marked.size;
  const button = $('#sceneBatch');
  button.textContent = count ? `Regenerate marked (${count})` : 'Regenerate marked';
  button.disabled = sceneReview.busy || count === 0;
}

function renderSceneGrid() {
  const grid = $('#sceneGrid');
  grid.innerHTML = '';
  for (const item of sceneReview.images) {
    const tile = document.createElement('div');
    tile.className = 'scene-tile';
    tile.dataset.index = String(item.index);
    if (!item.url) tile.classList.add('is-missing');
    if (sceneReview.marked.has(item.index)) tile.classList.add('is-marked');

    if (item.url) {
      const image = document.createElement('img');
      image.src = item.url;
      image.alt = `Scene ${item.index + 1}`;
      image.loading = 'lazy';
      tile.appendChild(image);
    } else {
      tile.textContent = 'Not painted';
    }

    const mark = document.createElement('label');
    mark.className = 'scene-check';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = sceneReview.marked.has(item.index);
    check.addEventListener('click', (event) => event.stopPropagation());
    check.addEventListener('change', () => {
      if (check.checked) sceneReview.marked.add(item.index);
      else sceneReview.marked.delete(item.index);
      tile.classList.toggle('is-marked', check.checked);
      updateSceneBatch();
    });
    mark.append(check, document.createTextNode(' Mark'));
    mark.addEventListener('click', (event) => event.stopPropagation());
    tile.appendChild(mark);

    const badge = document.createElement('span');
    badge.className = 'scene-index';
    badge.textContent = `Scene ${item.index + 1}`;
    tile.appendChild(badge);
    tile.addEventListener('click', () => openSceneEditor(item.index));
    grid.appendChild(tile);
  }
  updateSceneBatch();
}

function openSceneEditor(index) {
  const item = sceneReview.images.find((entry) => entry.index === index);
  sceneReview.current = index;
  sceneReview.rect = null;
  sceneReview.drag = null;
  $('#sceneGrid').hidden = true;
  $('#sceneEditor').hidden = false;
  $('#sceneEditorTitle').textContent =
    `Scene ${index + 1} / ${sceneReview.images.length}`;
  const image = $('#sceneEditImage');
  image.src = item && item.url ? item.url : '';
  image.alt = `Scene ${index + 1}`;
  $('#scenePrompt').value = '';
  $('#sceneRect').hidden = true;
  $('#sceneRegenArea').disabled = true;
  $('#sceneRegenWhole').disabled = sceneReview.busy;
  $('#sceneStatus').textContent = item && item.url
    ? 'Drag a box over the part to redraw, or regenerate the whole scene.'
    : 'This scene is not painted yet. Use Regenerate whole.';
}

function closeSceneEditor() {
  sceneReview.current = null;
  sceneReview.rect = null;
  sceneReview.drag = null;
  $('#sceneRect').hidden = true;
  $('#sceneEditor').hidden = true;
  $('#sceneGrid').hidden = false;
  $('#sceneStatus').textContent = 'Click a scene to inspect it. Drag a box to regenerate only that area.';
  updateSceneBatch();
}

function sceneImageBox() {
  const image = $('#sceneEditImage');
  const canvas = $('#sceneCanvas');
  if (!image.getAttribute('src')) return null;
  const imageRect = image.getBoundingClientRect();
  const canvasRect = canvas.getBoundingClientRect();
  if (imageRect.width < 10 || imageRect.height < 10) return null;
  return { imageRect, canvasRect };
}

function scenePoint(event) {
  const box = sceneImageBox();
  if (!box) return null;
  const x = (event.clientX - box.imageRect.left) / box.imageRect.width;
  const y = (event.clientY - box.imageRect.top) / box.imageRect.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  return { x, y };
}

function drawSceneRect() {
  const box = sceneImageBox();
  const overlay = $('#sceneRect');
  const rect = sceneReview.rect;
  if (!box || !rect) {
    overlay.hidden = true;
    return;
  }
  overlay.style.left = `${box.imageRect.left - box.canvasRect.left + rect.x * box.imageRect.width}px`;
  overlay.style.top = `${box.imageRect.top - box.canvasRect.top + rect.y * box.imageRect.height}px`;
  overlay.style.width = `${rect.w * box.imageRect.width}px`;
  overlay.style.height = `${rect.h * box.imageRect.height}px`;
  overlay.hidden = false;
}

function startSceneRect(event) {
  if (sceneReview.busy || sceneReview.current === null) return;
  const point = scenePoint(event);
  if (!point) return;
  event.preventDefault();
  sceneReview.rect = null;
  sceneReview.drag = { start: point };
  $('#sceneRect').hidden = true;
  $('#sceneRegenArea').disabled = true;
}

function moveSceneRect(event) {
  if (!sceneReview.drag) return;
  const point = scenePoint(event);
  if (!point) return;
  const start = sceneReview.drag.start;
  const rect = {
    x: Math.min(start.x, point.x),
    y: Math.min(start.y, point.y),
    w: Math.abs(point.x - start.x),
    h: Math.abs(point.y - start.y),
  };
  if (rect.w < 0.01 || rect.h < 0.01) return;
  sceneReview.rect = rect;
  drawSceneRect();
}

function endSceneRect() {
  if (!sceneReview.drag) return;
  sceneReview.drag = null;
  if (!sceneReview.rect || sceneReview.rect.w < 0.02 || sceneReview.rect.h < 0.02) {
    sceneReview.rect = null;
    $('#sceneRect').hidden = true;
  }
  $('#sceneRegenArea').disabled = sceneReview.busy || !sceneReview.rect;
}

function setSceneBusy(busy, message) {
  sceneReview.busy = busy;
  $('#sceneRegenWhole').disabled = busy || sceneReview.current === null;
  $('#sceneRegenArea').disabled = busy || !sceneReview.rect;
  $('#sceneBack').disabled = false;
  updateSceneBatch();
  if (message) $('#sceneStatus').textContent = message;
}

function applySceneImage(index, url) {
  const item = sceneReview.images.find((entry) => entry.index === index);
  if (item) item.url = url;
  const tile = document.querySelector(`.scene-tile[data-index="${index}"]`);
  if (tile) {
    tile.classList.remove('is-missing');
    const image = tile.querySelector('img');
    if (image) image.src = url;
    else renderSceneGrid();
  }
  if (sceneReview.current === index) $('#sceneEditImage').src = url;
}

function invalidateCinemaScene(chapter, index) {
  state.cinema.cache.delete(`${chapter}:${index}`);
  state.cinema.index = -1;
  if (state.view === 'cinema') {
    state.cinema.sceneToken++;
    updateCinema(state.index);
  }
}

async function regenerateScene(mode) {
  if (!state.doc || sceneReview.current === null || sceneReview.busy) return;
  const index = sceneReview.current;
  const payload = {
    doc: state.doc.id,
    chapter: sceneReview.chapter,
    index,
    mode,
    prompt: $('#scenePrompt').value.trim(),
  };
  if (mode === 'region') {
    if (!sceneReview.rect) return;
    payload.rect = [
      sceneReview.rect.x,
      sceneReview.rect.y,
      sceneReview.rect.w,
      sceneReview.rect.h,
    ];
  }
  setSceneBusy(
    true,
    mode === 'region' ? 'Regenerating selected area…' : 'Regenerating whole scene…',
  );
  try {
    const data = await api('/api/film/scene/regenerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    applySceneImage(index, data.url);
    sceneReview.rect = null;
    $('#sceneRect').hidden = true;
    invalidateCinemaScene(sceneReview.chapter, index);
    $('#sceneStatus').textContent = `Scene ${index + 1} regenerated.`;
  } catch (err) {
    toast(err.message || 'Regeneration failed', 'error', 6000);
    $('#sceneStatus').textContent = err.message || 'Regeneration failed';
  } finally {
    setSceneBusy(false);
  }
}

async function regenerateMarked() {
  if (!state.doc || sceneReview.busy || !sceneReview.marked.size) return;
  const indices = [...sceneReview.marked];
  sceneReview.busy = true;
  updateSceneBatch();
  let done = 0;
  for (let position = 0; position < indices.length; position++) {
    const index = indices[position];
    $('#sceneStatus').textContent =
      `Regenerating marked scene ${position + 1} of ${indices.length}…`;
    try {
      const data = await api('/api/film/scene/regenerate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          doc: state.doc.id,
          chapter: sceneReview.chapter,
          index,
          mode: 'whole',
          prompt: '',
        }),
      });
      applySceneImage(index, data.url);
      invalidateCinemaScene(sceneReview.chapter, index);
      sceneReview.marked.delete(index);
      done += 1;
    } catch (err) {
      toast(`Scene ${index + 1}: ${err.message || 'regeneration failed'}`, 'error', 5000);
    }
  }
  sceneReview.busy = false;
  renderSceneGrid();
  $('#sceneStatus').textContent = `Regenerated ${done} scene${done === 1 ? '' : 's'}.`;
}

async function makeFilm() {
  if (!state.doc) return;
  const chapter = cinemaChapterForUnit(state.index);
  let plan;
  try {
    plan = await api(`/api/film/plan?doc=${encodeURIComponent(state.doc.id)}&chapter=${chapter}`);
  } catch (err) {
    toast(err.message || 'Could not plan the chapter film', 'error');
    return;
  }
  const auto = !!state.settings.autoFilms;
  if (!auto) {
    const perImage = state.settings.filmQuality === 'fast' ? 35 : 120;
    const minutes = Math.max(1, Math.round(((plan.scenes || []).length * perImage) / 60));
    const quality = state.settings.filmQuality === 'fast' ? 'Fast' : 'High / manga · 1080p';
    if (!confirm(
      `Create a film for chapter ${chapter + 1}?\n\n` +
      `${(plan.scenes || []).length} key images · ${quality}\n` +
      `Estimated ${minutes} min for images, then encoding.`
    )) return;
  }
  try {
    await queueChapterFilm(chapter, plan, auto);
    toast(auto
      ? `Chapter ${chapter + 1} queued — the remaining chapters follow when this one finishes`
      : `Chapter ${chapter + 1} queued — you can keep reading or film another chapter`);
  } catch (err) {
    toast(err.message, 'error');
  }
}

/* One queue, in chapter order. `plan` is passed only to skip a second round-trip
   when the caller already has it; the server clamps the chapter and reuses a
   rendered film, so continuing through finished chapters costs one request each. */
async function queueChapterFilm(chapter, plan = null, auto = false) {
  if (!state.doc) return null;
  if (plan === null) {
    plan = await api(`/api/film/plan?doc=${encodeURIComponent(state.doc.id)}&chapter=${chapter}`);
  }
  const job = await api(`/api/docs/${state.doc.id}/film`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chapter, voice: state.voice, auto }),
  });
  state.cinema.jobs.set(job.id, { ...job, chapter });
  if (auto) {
    const last = Math.max(0, (plan.chapters || 1) - 1);
    state.cinema.autoRun = { docId: state.doc.id, next: chapter + 1, last };
  }
  renderFilmQueue();
  startFilmPolling();
  return job;
}

/* Auto mode continues only for the book still open and only while the setting
   is on: turning Auto Generate off stops new work without touching the job that
   is already painting. */
function continueAutoRun(job) {
  const run = state.cinema.autoRun;
  if (!run || !job.auto || !state.settings.autoFilms) return false;
  if (!state.doc || run.docId !== state.doc.id) { state.cinema.autoRun = null; return false; }
  if (run.next > run.last) {
    state.cinema.autoRun = null;
    toast('Auto Generate finished the remaining chapters');
    return false;
  }
  const busy = [...state.cinema.jobs.values()].some(
    (queued) => queued.chapter === run.next && !['done', 'error', 'cancelled'].includes(queued.state)
  );
  if (busy) return false;
  const chapter = run.next;
  run.next += 1;
  queueChapterFilm(chapter, null, true).catch((err) => {
    state.cinema.autoRun = null;
    toast(err.message || `Could not queue chapter ${chapter + 1}`, 'error');
  });
  return true;
}

function updateTranslationBar() {
  const bar = $('#translationBar');
  const show = !!(state.translated && state.spoken && state.doc && state.view !== 'cinema');
  bar.hidden = !show;
  if (!show) {
    state.translationSpans = [];
    return;
  }
  $('#translationLang').textContent = state.targetLang || 'tr';

  const text = $('#translationText');
  if (text.dataset.text !== state.spoken) {
    text.dataset.text = state.spoken;
    text.textContent = '';
    state.translationSpans = [];
    const tokens = state.spoken.match(/\S+\s*/g) || [];
    for (const token of tokens) {
      const parts = token.match(/^(\S+)([\s\S]*)$/);
      if (!parts) continue;
      const span = document.createElement('span');
      span.className = 'tw';
      span.textContent = parts[1];
      text.appendChild(span);
      state.translationSpans.push(span);
      if (parts[2]) text.appendChild(document.createTextNode(parts[2]));
    }
  }
  highlightTranslationWord(state.wordIdx >= 0 ? state.wordIdx : 0);
}

function highlightTranslationWord(index) {
  const spans = state.translationSpans;
  if (!spans.length) return;
  const active = spans[Math.min(Math.max(0, index), spans.length - 1)];
  for (const span of spans) {
    if (span !== active) span.classList.remove('is-active');
  }
  if (active) active.classList.add('is-active');
}

/* ─────────────────── word highlighting inside EPUB chapters ─────────────────── */

const BLOCK_TAGS = new Set([
  'P', 'DIV', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'LI', 'BLOCKQUOTE', 'SECTION',
  'ARTICLE', 'TR', 'FIGURE', 'FIGCAPTION', 'PRE', 'UL', 'OL', 'TABLE', 'HR',
]);

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function onChapterLoad() {
  const frame = $('#chapterFrame');
  if (!state.doc || state.doc.viewer !== 'epub' || frame.hidden) return;
  const chapter = parseInt(frame.dataset.chapter || '', 10);
  if (Number.isNaN(chapter)) return;

  const doc = frame.contentDocument;
  if (!doc || !doc.body) return;

  try {
    state.chapter = buildChapter(doc, chapter);
    if (state.chapter) {
      highlightChapterWord(state.wordIdx >= 0 ? state.wordIdx : 0, true);
    }
  } catch (err) {
    state.chapter = null;
  }
}

function buildChapter(doc, chapter) {
  const pages = state.doc.unitPages || [];
  const indices = [];
  for (let i = 0; i < pages.length; i++) {
    if (pages[i] === chapter) indices.push(i);
  }
  if (!indices.length) return null;

  // Flatten the chapter into text with a separator at block boundaries, so our
  // sentences (which were extracted with the same boundaries) can be located.
  const segments = [];
  let flat = '';

  const walk = (node) => {
    if (node.nodeType === 3) {
      const text = node.nodeValue;
      if (text) {
        segments.push({ start: flat.length, node });
        flat += text;
        // The extractor inserted a separator between every text node, so do
        // the same here; otherwise inline styling can split words differently.
        flat += ' ';
      }
      return;
    }
    if (node.nodeType !== 1) return;
    const tag = node.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'HEAD') return;
    if (tag === 'BR') {
      flat += '\n';
      return;
    }
    for (const child of node.childNodes) walk(child);
    if (BLOCK_TAGS.has(tag)) flat += '\n';
  };
  walk(doc.body);

  // Locate every sentence of this chapter, in reading order.
  const ranges = [];
  let cursor = 0;
  for (const index of indices) {
    const text = state.units[index].text;
    const words = text.split(/\s+/).filter(Boolean);
    let found = null;
    if (words.length) {
      try {
        const pattern = words.map(escapeRegExp).join('\\s+');
        const regex = new RegExp(pattern, 'g');
        regex.lastIndex = cursor;
        let match = regex.exec(flat);
        if (!match) {
          regex.lastIndex = 0;
          match = regex.exec(flat);
        }
        if (match) {
          found = { start: match.index, end: match.index + match[0].length, text: match[0] };
        }
      } catch (_) {
        found = null;
      }
    }
    ranges.push(found);
    if (found) cursor = found.end;
  }

  // Highlight styling lives inside the chapter document.
  const highlight = '#8b7bff';
  const ink = '#0d0724';
  const style = doc.createElement('style');
  style.textContent =
    `.mw{border-radius:2px}` +
    `.mw.speaking{background:${highlight};color:${ink};box-shadow:0 0 0 1px ${highlight}}`;
  doc.head.appendChild(style);

  // Wrap every word so highlighting is a class toggle.
  const spans = [];
  for (const segment of segments) {
    wrapSegment(doc, segment, spans);
  }
  spans.sort((a, b) => a.start - b.start);

  return { first: indices[0], ranges, spans, active: [], lastScroll: 0, lastUnit: -1 };
}

function wrapSegment(doc, segment, spans) {
  const node = segment.node;
  const text = node.nodeValue;
  if (!/\S/.test(text)) return;

  const fragment = doc.createDocumentFragment();
  const regex = /\S+\s*/g;
  let match;
  let last = 0;
  while ((match = regex.exec(text))) {
    if (match.index > last) {
      fragment.appendChild(doc.createTextNode(text.slice(last, match.index)));
    }
    const span = doc.createElement('span');
    span.className = 'mw';
    const parts = match[0].match(/^(\S+)([\s\S]*)$/);
    span.textContent = parts[1];
    fragment.appendChild(span);
    if (parts[2]) fragment.appendChild(doc.createTextNode(parts[2]));
    spans.push({
      el: span,
      start: segment.start + match.index,
      end: segment.start + match.index + parts[1].length,
    });
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    fragment.appendChild(doc.createTextNode(text.slice(last)));
  }
  if (node.parentNode) node.parentNode.replaceChild(fragment, node);
}

function highlightChapterWord(wordIndex, force = false) {
  const chapter = state.chapter;
  if (!chapter) return;

  const range = chapter.ranges[state.index - chapter.first];
  if (!range) return;

  if (!range.tokens) {
    range.tokens = [];
    const regex = /\S+\s*/g;
    let match;
    while ((match = regex.exec(range.text))) {
      range.tokens.push({ start: match.index, end: match.index + match[0].length });
    }
  }
  if (!range.tokens.length) return;

  const token = range.tokens[Math.min(Math.max(0, wordIndex), range.tokens.length - 1)];
  if (!token) return;

  const start = range.start + token.start;
  const end = range.start + token.end;

  if (!force && chapter.activeStart === start) return;
  chapter.activeStart = start;

  for (const element of chapter.active) element.classList.remove('speaking');
  chapter.active = [];

  // First span that ends after the token starts.
  let low = 0;
  let high = chapter.spans.length - 1;
  let first = chapter.spans.length;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (chapter.spans[mid].end > start) {
      first = mid;
      high = mid - 1;
    } else {
      low = mid + 1;
    }
  }

  for (let i = first; i < chapter.spans.length && chapter.spans[i].start < end; i++) {
    chapter.spans[i].el.classList.add('speaking');
    chapter.active.push(chapter.spans[i].el);
  }

  if (!chapter.active.length) return;
  const element = chapter.active[0];
  const doc = element.ownerDocument;
  const view = doc.documentElement;
  const rect = element.getBoundingClientRect();
  const outside = rect.top < 90 || rect.bottom > view.clientHeight - 150;
  const now = performance.now();
  if (outside && (chapter.lastUnit !== state.index || now - chapter.lastScroll > 600)) {
    chapter.lastUnit = state.index;
    chapter.lastScroll = now;
    element.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
}

function clearChapterHighlight() {
  if (!state.chapter) return;
  for (const element of state.chapter.active) element.classList.remove('speaking');
  state.chapter.active = [];
  state.chapter.activeStart = null;
}

function scrollToUnit(i, behavior = 'smooth') {
  const unit = state.units[i];
  if (!unit || !unit.el) return;
  const scroller = $('#readerScroll');
  const rect = unit.el.getBoundingClientRect();
  const bounds = scroller.getBoundingClientRect();
  const outside = rect.top < bounds.top + 90 || rect.bottom > bounds.bottom - 140;
  if (!outside) return;
  const target = scroller.scrollTop + rect.top - bounds.top - scroller.clientHeight * 0.34;
  scroller.scrollTo({ top: Math.max(0, target), behavior });
}

function maybeAutoScroll(t) {
  if (!state.autoScroll) return;
  const now = performance.now();
  if (now - state.lastScroll < 900) return;
  const el = state.activeWordEl;
  if (!el) return;
  const scroller = $('#readerScroll');
  const rect = el.getBoundingClientRect();
  const bounds = scroller.getBoundingClientRect();
  if (rect.top < bounds.top + 110 || rect.bottom > bounds.bottom - 150) {
    state.lastScroll = now;
    const target = scroller.scrollTop + rect.top - bounds.top - scroller.clientHeight * 0.42;
    scroller.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
  }
}

function setLoading(value) {
  state.loading = value;
  updatePlayerUI();
}

function updatePlayerUI() {
  const player = $('#player');
  player.classList.toggle('is-playing', state.playing);
  player.classList.toggle('is-loading', state.loading);
  player.classList.toggle('is-idle', !state.doc);
  $('#reader').classList.toggle('is-playing', state.playing);
  $('#playBtn').setAttribute('aria-label', state.playing ? 'Pause' : 'Play');
  $('#playBtn').title = state.playing ? 'Pause (space)' : 'Play (space)';
  updateMediaSession();
}

function setupMediaSession() {
  if (!('mediaSession' in navigator)) return;
  try {
    navigator.mediaSession.setActionHandler('play', () => togglePlay());
    navigator.mediaSession.setActionHandler('pause', () => pause());
    navigator.mediaSession.setActionHandler('previoustrack', () => prevSentence());
    navigator.mediaSession.setActionHandler('nexttrack', () => nextSentence());
    navigator.mediaSession.setActionHandler('seekbackward', () => {
      audio.currentTime = Math.max(0, audio.currentTime - 10);
    });
    navigator.mediaSession.setActionHandler('seekforward', () => {
      audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 10);
    });
  } catch (_) {}
}

function updateMediaSession() {
  if (!('mediaSession' in navigator)) return;
  try {
    if (state.doc && state.mediaTitle !== state.doc.id + state.voice) {
      state.mediaTitle = state.doc.id + state.voice;
      navigator.mediaSession.metadata = new MediaMetadata({
        title: state.doc.title,
        artist: 'Rhapsode',
        album: `${state.voice} · Kokoro-82M`,
      });
    }
    navigator.mediaSession.playbackState = state.playing ? 'playing' : 'paused';
  } catch (_) {}
}

function seekToFraction(e) {
  if (!state.doc) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  const targetChars = frac * state.totalChars;
  let acc = 0;
  let i = 0;
  for (; i < state.units.length; i++) {
    if (acc + state.docChars[i] >= targetChars) break;
    acc += state.docChars[i];
  }
  playUnit(Math.min(i, state.units.length - 1), { restart: true });
}

function setSpeed(speed) {
  state.speed = speed;
  audio.playbackRate = speed;
  $('#speedSel').value = String(speed);
  savePosition(true);
}

function cycleSpeed(direction) {
  const current = state.speed;
  const idx = SPEEDS.findIndex((s) => Math.abs(s - current) < 0.001);
  const next = Math.min(SPEEDS.length - 1, Math.max(0, (idx < 0 ? 2 : idx) + direction));
  setSpeed(SPEEDS[next]);
}

function setVoice(voice) {
  if (!voice || voice === state.voice) return;
  const wasPlaying = state.playing;
  state.voice = voice;
  state.clips.clear();
  state.pending.clear();
  state.durations.clear();
  state.gen++;
  audio.pause();
  audio.removeAttribute('src');
  state.audioIndex = -1;
  state.words = [];
  clearWordHighlight();
  if (state.doc && wasPlaying) {
    playUnit(state.index, { restart: true });
  } else if (state.doc) {
    setCurrentUnit(state.index);
    updatePlayerUI();
  }
  savePosition(true);
  toast('Voice changed');
}

function savePosition(immediate = false) {
  if (!state.doc) return;
  clearTimeout(state.saveTimer);
  const commit = async () => {
    try {
      await api(`/api/docs/${state.doc.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ position: state.index, voice: state.voice, speed: state.speed }),
      });
      state.doc.position = state.index;
      state.doc.voice = state.voice;
      state.doc.speed = state.speed;
      const doc = state.docs.find((d) => d.id === state.doc.id);
      if (doc) {
        doc.position = state.index;
        doc.voice = state.voice;
        doc.speed = state.speed;
        const fill = document.querySelector(`.lib-item[data-id="${doc.id}"] .lib-progress i`);
        if (fill && state.units.length) {
          fill.style.width = `${Math.min(100, (state.index / state.units.length) * 100)}%`;
        }
      }
    } catch (_) {}
  };
  if (immediate) commit();
  else state.saveTimer = setTimeout(commit, 700);
}

/* ─────────────────────────── search ─────────────────────────── */

const searchView = {
  seq: 0,
  timer: 0,
  poll: 0,
  query: '',
  recent: '',
  hits: [],
  data: null,
  status: null,
  backfilled: false,
  selected: -1,
};

function openSearch() {
  const modal = $('#searchModal');
  const input = $('#searchInput');
  modal.hidden = false;
  loadSearchStatus();
  setTimeout(() => {
    input.focus();
    input.select();
  }, 40);
  if (input.value.trim()) runSearch();
}

async function loadSearchStatus() {
  let status = null;
  try {
    status = await api('/api/search/status');
  } catch (_) {}
  searchView.status = status;
  renderSearchNote();
  if (!status) return;
  const indexing = status.indexing || {};
  if ((status.pending || 0) > 0 && !indexing.running) startReindex(true);
}

async function startReindex(silent = false) {
  clearTimeout(searchView.poll);
  try {
    const res = await api('/api/search/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (res && res.status) searchView.status = res.status;
    renderSearchNote();
    if (!silent) toast('Indexing your library in the background');
    scheduleSearchPoll();
  } catch (err) {
    if (!silent) toast(err.message, 'error');
  }
}

async function downloadSearchModel() {
  try {
    await api('/api/search/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ download: true }),
    });
    toast('Downloading the search model in the background');
    scheduleSearchPoll();
  } catch (err) {
    toast(err.message, 'error');
  }
}

function scheduleSearchPoll() {
  clearTimeout(searchView.poll);
  searchView.poll = setTimeout(pollSearch, 1200);
}

async function pollSearch() {
  if ($('#searchModal').hidden) return;
  let status = null;
  try {
    status = await api('/api/search/status');
  } catch (_) {
    return;
  }
  searchView.status = status;
  renderSearchNote();
  const indexing = status.indexing || {};
  const semantic = status.semantic || {};
  if (semantic.available && !searchView.backfilled) {
    // Books indexed before the model arrived still need their vectors.
    searchView.backfilled = true;
    startReindex(true);
    return;
  }
  const busy = indexing.running || (status.pending || 0) > 0 || semantic.state === 'downloading';
  if (busy && !indexing.lastError) {
    scheduleSearchPoll();
    return;
  }
  if (searchView.query.trim()) runSearch();
}

function renderSearchNote() {
  const note = $('#searchNote');
  note.textContent = '';
  const status = searchView.status;
  if (!status) {
    note.hidden = true;
    return;
  }
  const indexing = status.indexing || {};
  const semantic = status.semantic || {};
  const parts = [];

  if (indexing.running) {
    const done = indexing.done || 0;
    const total = indexing.total || 0;
    parts.push(total ? `Indexing your library… ${done} of ${total} books` : 'Indexing your library…');
  } else if (indexing.lastError) {
    const error = document.createElement('span');
    error.className = 'search-error';
    error.textContent = `Indexing stopped: ${indexing.lastError}`;
    parts.push(error);
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Try again';
    retry.addEventListener('click', () => startReindex(false));
    parts.push(retry);
  } else if ((status.pending || 0) > 0) {
    parts.push(`${status.pending} ${status.pending === 1 ? 'book is' : 'books are'} waiting to be indexed`);
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Index now';
    button.addEventListener('click', () => startReindex(false));
    parts.push(button);
  }

  if (semantic.state === 'downloading') {
    const progress = Math.round((semantic.progress || 0) * 100);
    parts.push(`Downloading the meaning model… ${progress}%`);
    const bar = document.createElement('span');
    bar.className = 'search-progress';
    const fill = document.createElement('i');
    fill.style.width = `${progress}%`;
    bar.appendChild(fill);
    parts.push(bar);
    scheduleSearchPoll();
  } else if (!semantic.available && semantic.state !== 'off') {
    parts.push(
      semantic.error
        ? semantic.error
        : 'Keyword search only — the meaning model is not downloaded (23 MB, Apache-2.0, CPU only).'
    );
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = semantic.state === 'error' ? 'Try again' : 'Download';
    button.addEventListener('click', downloadSearchModel);
    parts.push(button);
  }

  if (!parts.length) {
    note.hidden = true;
    return;
  }
  note.hidden = false;
  for (const part of parts) {
    if (typeof part === 'string') {
      const span = document.createElement('span');
      span.textContent = part;
      note.appendChild(span);
    } else {
      note.appendChild(part);
    }
  }
}

async function runSearch() {
  clearTimeout(searchView.timer);
  const query = $('#searchInput').value.trim();
  searchView.query = query;
  const seq = ++searchView.seq;
  if (!query) {
    searchView.hits = [];
    $('#searchResults').textContent = '';
    $('#searchState').textContent = 'Type to search every book in your library.';
    return;
  }
  $('#searchState').textContent = 'Searching…';
  try {
    const params = new URLSearchParams({ q: query, limit: '30' });
    if (searchView.recent) params.set('recent', searchView.recent);
    const data = await api(`/api/search?${params}`);
    if (seq !== searchView.seq) return;
    searchView.data = data;
    renderSearchResults(data);
  } catch (err) {
    if (seq !== searchView.seq) return;
    $('#searchResults').textContent = '';
    $('#searchState').textContent = err.message;
  }
}

function searchWindowLabel(recent) {
  if (recent === '7d') return 'this week';
  if (recent === '30d') return 'this month';
  return '';
}

function renderSearchResults(data) {
  const results = $('#searchResults');
  results.textContent = '';
  searchView.hits = data.hits || [];
  searchView.selected = -1;
  const windowLabel = searchWindowLabel(searchView.recent);
  const mode = data.mode === 'hybrid' ? 'words and meaning' : 'keywords only';
  const count = data.total || 0;
  if (count) {
    $('#searchState').textContent =
      `${count} ${count === 1 ? 'passage' : 'passages'} · ${data.tookMs} ms · ${mode}` +
      (windowLabel ? ` · read ${windowLabel}` : '');
  } else {
    $('#searchState').textContent =
      `Nothing found${windowLabel ? ` in books you read ${windowLabel}` : ''} — try fewer or different words.`;
  }
  if (!searchView.hits.length) {
    const empty = document.createElement('p');
    empty.className = 'search-empty';
    empty.textContent = data.mode === 'hybrid'
      ? 'Searched the words and the meaning of your query.'
      : 'Searched the words in your library. Meaning search is not available.';
    results.appendChild(empty);
    return;
  }
  const groups = new Map();
  for (const hit of searchView.hits) {
    if (!groups.has(hit.docId)) groups.set(hit.docId, { title: hit.title, hits: [] });
    groups.get(hit.docId).hits.push(hit);
  }
  for (const group of groups.values()) {
    const head = document.createElement('div');
    head.className = 'search-book';
    head.textContent = group.title;
    const tally = document.createElement('span');
    tally.textContent = `${group.hits.length} ${group.hits.length === 1 ? 'passage' : 'passages'}`;
    head.appendChild(tally);
    results.appendChild(head);
    for (const hit of group.hits) results.appendChild(searchHitButton(hit));
  }
}

function searchLocation(hit) {
  const parts = [];
  if (hit.chapterTitle) parts.push(hit.chapterTitle);
  if (hit.page) parts.push(`page ${hit.page + 1}`);
  parts.push(`sentence ${hit.unit + 1}${hit.unitCount ? ` of ${hit.unitCount}` : ''}`);
  return parts.join(' · ');
}

function searchHitButton(hit) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'search-hit';
  button.dataset.chunk = String(hit.chunkId);
  button.setAttribute('aria-label', `${hit.title}: ${hit.snippet ? hit.snippet.text : ''}`);

  const snippet = document.createElement('span');
  const text = (hit.snippet && hit.snippet.text) || '';
  let cursor = 0;
  for (const range of (hit.snippet && hit.snippet.highlights) || []) {
    const start = Math.max(0, Math.min(text.length, range[0]));
    const stop = Math.max(start, Math.min(text.length, range[1]));
    if (start > cursor) snippet.appendChild(document.createTextNode(text.slice(cursor, start)));
    const mark = document.createElement('mark');
    mark.textContent = text.slice(start, stop);
    snippet.appendChild(mark);
    cursor = stop;
  }
  if (cursor < text.length) snippet.appendChild(document.createTextNode(text.slice(cursor)));

  const where = document.createElement('span');
  where.className = 'search-where';
  where.textContent = searchLocation(hit);
  button.append(snippet, where);
  button.addEventListener('click', () => openSearchHit(hit));
  return button;
}

async function openSearchHit(hit) {
  const jump = hit.jump || {};
  closeModals();
  await openDoc(jump.docId || hit.docId, { position: jump.unit != null ? jump.unit : hit.unit });
  requestAnimationFrame(() =>
    flashSearchMatch(jump.unit != null ? jump.unit : hit.unit, jump.terms || [])
  );
}

function flashSearchMatch(unitIndex, terms) {
  if (state.view !== 'text') return; // the page view already turned to the page
  const unit = state.units[unitIndex];
  if (!unit || !unit.el) return;
  ensureSpans(unit);
  const wanted = (terms || []).map((term) => term.toLowerCase());
  if (!wanted.length) return;
  for (const span of unit.spans) {
    const word = span.textContent.replace(/^[^\w]+|[^\w]+$/g, '').toLowerCase();
    if (word && wanted.includes(word)) span.classList.add('is-match');
  }
}

function clearSearchMatch() {
  $$('.w.is-match').forEach((span) => span.classList.remove('is-match'));
}

function onSearchKey(event) {
  if (event.key === 'Escape') {
    closeModals();
    return;
  }
  const hits = $$('#searchResults .search-hit');
  if (event.key === 'Enter') {
    if (!hits.length) {
      runSearch();
      return;
    }
    const inside = hits.includes(document.activeElement);
    const target = inside ? document.activeElement : hits[Math.max(0, searchView.selected)];
    if (target) {
      event.preventDefault();
      target.click();
    }
    return;
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    if (!hits.length) return;
    event.preventDefault();
    const current = hits.indexOf(document.activeElement);
    const next = event.key === 'ArrowDown'
      ? (current < 0 ? 0 : Math.min(hits.length - 1, current + 1))
      : (current <= 0 ? 0 : current - 1);
    searchView.selected = next;
    hits[next].focus();
  }
}

/* ─────────────────────────── add flows ─────────────────────────── */

function openAdd() {
  $('#addModal').hidden = false;
  switchTab('paste');
  setTimeout(() => $('#pasteText').focus(), 40);
}

function openSettings() {
  $('#settingsModal').hidden = false;
  loadNetwork();
  loadFonts();
  refreshEngine();
}

async function refreshEngine() {
  let state;
  try {
    state = await api('/api/engine');
  } catch (_) {
    $('#engineSummary').textContent = 'Engine state unavailable.';
    return;
  }
  $('#engineSummary').textContent = state.summary || state.profile || '';
  $$('#setEngineProfile button').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.engineProfile === state.profile);
    button.setAttribute('aria-checked', String(button.dataset.engineProfile === state.profile));
  });
  const painter = state.painter || {};
  const job = painter.last_job || {};
  const bits = [];
  bits.push(painter.running ? 'stills engine running' : 'stills engine idle');
  try {
    const act = await api('/api/film/activity');
    if (act && act.phase && act.phase !== 'idle') bits.push(sceneActivityLabel(act));
  } catch (_) {
    /* the profile summary above is still worth showing */
  }
  if (job.seconds) bits.push(`last still ${job.seconds}s (${job.width}x${job.height}, ${job.steps} steps)`);
  if (job.peak_gib) bits.push(`${job.peak_gib} GiB peak`);
  if (job.free_gib) bits.push(`${job.free_gib} GiB VRAM free after`);
  const ram = painter.ram || {};
  if (ram.free_gib) {
    bits.push(`${ram.free_gib} GiB RAM free of ${ram.total_gib}`);
    if (ram.free_gib * 1024 < (ram.floor_mib || 0)) bits.push('below the RAM floor - painting will refuse');
  }
  $('#engineStatus').textContent = bits.join(' · ');
}

async function setEngineProfile(name) {
  try {
    await api('/api/engine/profile', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ profile: name }),
    });
    toast(`Engine switched to ${name}`);
  } catch (err) {
    toast(`Could not switch engine: ${String(err && err.message || err)}`, 'error');
  }
  refreshEngine();
}

function wireEngine() {
  $$('#setEngineProfile button').forEach((button) =>
    button.addEventListener('click', () => setEngineProfile(button.dataset.engineProfile)));
  $('#restartEngine')?.addEventListener('click', async () => {
    try {
      await api('/api/engine/restart', { method: 'POST' });
      toast('Engine restarted - the next still loads the active profile');
    } catch (err) {
      toast(`Restart failed: ${err.message}`, 'error');
    }
    refreshEngine();
  });
}

async function loadNetwork() {
  const status = $('#remoteStatus');
  const links = $('#remoteLinks');
  let info;
  try {
    info = await api('/api/network');
  } catch (_) {
    status.textContent = 'Network status unavailable';
    return;
  }

  const wantsRemote = !!info.remoteAccess;
  if (info.enabled && wantsRemote) {
    status.textContent = `Available on the network · port ${info.port}`;
  } else if (!info.enabled && wantsRemote) {
    status.textContent = 'Restart Rhapsode to turn on remote access';
  } else if (info.enabled && !wantsRemote) {
    status.textContent = 'Restart Rhapsode to turn off remote access';
  } else {
    status.textContent = 'Off — this Mac only';
  }
  $('#setRemote').setAttribute('aria-checked', String(wantsRemote));

  links.innerHTML = '';
  if (!info.enabled || !wantsRemote) {
    links.hidden = true;
    return;
  }

  const entries = (info.urls || []).filter((entry) => entry.label !== 'This Mac');
  if (!entries.length) {
    links.hidden = true;
    return;
  }

  for (const entry of entries) {
    const row = document.createElement('div');
    row.className = 'remote-link';
    const label = document.createElement('span');
    label.className = 'remote-label';
    label.textContent = entry.label;
    const code = document.createElement('code');
    code.textContent = entry.url;
    code.title = entry.url;
    const copy = document.createElement('button');
    copy.className = 'chip';
    copy.textContent = 'Copy';
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(entry.url);
        toast('Link copied');
      } catch (_) {
        toast('Could not copy the link', 'error');
      }
    });
    row.append(label, code, copy);
    links.appendChild(row);
  }

  const note = document.createElement('p');
  note.className = 'remote-note';
  const tailscaleOff = info.tailscale && !['Running', 'unknown'].includes(info.tailscale);
  note.textContent = tailscaleOff
    ? 'Tailscale is stopped on this Mac. Start it, then reopen Settings to get the tailnet links.'
    : 'Open these once on another device; the token is remembered. The key lives in ~/.murmur/access_token.';
  links.appendChild(note);
  links.hidden = false;
}

function closeModals() {
  $('#addModal').hidden = true;
  $('#settingsModal').hidden = true;
  $('#sceneModal').hidden = true;
  $('#searchModal').hidden = true;
  clearTimeout(searchView.poll);
  sceneReview.drag = null;
  sceneReview.rect = null;
}

function switchTab(name) {
  $$('.tab').forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle('is-active', active);
    t.setAttribute('aria-selected', String(active));
  });
  $$('.panel').forEach((p) => (p.hidden = p.dataset.panel !== name));
  if (name === 'url') setTimeout(() => $('#urlInput').focus(), 40);
}

function updatePasteCount() {
  const text = $('#pasteText').value.trim();
  const words = (text.match(/[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff]+/g) || []).length;
  $('#pasteCount').textContent = `${words.toLocaleString()} words`;
  $('#pasteAdd').disabled = words < 3;
}

async function addPasted() {
  const text = $('#pasteText').value.trim();
  if (text.length < 10) return;
  const title = $('#pasteTitle').value.trim();
  $('#pasteAdd').disabled = true;
  try {
    const doc = await api('/api/docs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, text }),
    });
    $('#pasteText').value = '';
    $('#pasteTitle').value = '';
    updatePasteCount();
    closeModals();
    await refreshLibrary();
    await openDoc(doc.id, { autoplay: true });
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    $('#pasteAdd').disabled = false;
  }
}

async function addUrl() {
  const url = $('#urlInput').value.trim();
  if (!url) return;
  const button = $('#urlAdd');
  button.disabled = true;
  button.textContent = 'Fetching…';
  try {
    const doc = await api('/api/docs/url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    $('#urlInput').value = '';
    closeModals();
    await refreshLibrary();
    await openDoc(doc.id, { autoplay: true });
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = 'Fetch & read';
  }
}

async function addFile(file) {
  const allowed = /\.(pdf|epub|txt|md|markdown|docx|rtf|html?|png|jpe?g|tiff?|bmp|webp|heic|heif)$/i;
  if (!allowed.test(file.name)) {
    toast('Unsupported file type', 'error');
    return;
  }
  closeModals();
  const pending = toast(`Reading ${file.name}…`, 'info', 0);
  try {
    const form = new FormData();
    form.append('file', file);
    const doc = await api('/api/docs/upload', { method: 'POST', body: form });
    pending.remove();
    if (doc.ocr && doc.ocr.ocrPages) {
      if (doc.ocr.images) {
        toast('OCR read the image');
      } else {
        const pages = doc.ocr.ocrPages;
        const note = doc.ocr.truncated ? ' · page limit reached' : '';
        toast(`OCR read ${pages} page${pages === 1 ? '' : 's'}${note}`);
      }
    }
    await refreshLibrary();
    await openDoc(doc.id, { autoplay: true });
  } catch (err) {
    pending.remove();
    toast(err.message, 'error', 5000);
  }
}

async function addSample() {
  try {
    const doc = await api('/api/docs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'The Time Machine — H. G. Wells', text: SAMPLE }),
    });
    await refreshLibrary();
    await openDoc(doc.id, { autoplay: true });
  } catch (err) {
    toast(err.message, 'error');
  }
}

/* ─────────────────────────── export ─────────────────────────── */

async function exportDoc() {
  if (!state.doc) return;
  try {
    const job = await api(`/api/docs/${state.doc.id}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice: state.voice }),
    });
    const el = toast(`Exporting 0 of ${job.total} sentences…`, 'info', 0);
    const label = el.querySelector('.toast-text');
    const timer = setInterval(async () => {
      try {
        const status = await api(`/api/export/${job.id}`);
        if (status.state === 'running' || status.state === 'queued') {
          label.textContent = `Exporting ${status.done} of ${status.total} sentences…`;
        } else if (status.state === 'done') {
          clearInterval(timer);
          el.remove();
          toast('Export ready — downloading');
          download(`/api/export/${job.id}/file`);
        } else if (status.state === 'error') {
          clearInterval(timer);
          el.remove();
          toast(status.error || 'Export failed', 'error');
        }
      } catch (_) {
        clearInterval(timer);
      }
    }, 900);
  } catch (err) {
    toast(err.message, 'error');
  }
}

function download(url) {
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ─────────────────────────── misc ─────────────────────────── */

function setFocus(on) {
  document.body.classList.toggle('focus', on);
  const button = $('#focusBtn');
  if (button) {
    button.title = on ? 'Show UI (F)' : 'Hide UI (F)';
    button.setAttribute('aria-pressed', String(on));
  }
  if (on) toast('UI hidden — press F or Esc to show it', 'info', 2200);
}

function toggleFocus() {
  setFocus(!document.body.classList.contains('focus'));
}

function onKeyDown(e) {
  const tag = (e.target.tagName || '').toLowerCase();
  const typing = ['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable;

  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
    e.preventDefault();
    openSearch();
    return;
  }
  if (e.key === 'Escape') {
    closeModals();
    document.body.classList.remove('sidebar-open');
    if (document.body.classList.contains('focus')) setFocus(false);
    return;
  }
  if (e.metaKey && e.key.toLowerCase() === 'n') {
    e.preventDefault();
    openAdd();
    return;
  }
  if (e.metaKey && e.key === ',') {
    e.preventDefault();
    openSettings();
    return;
  }
  if (typing) return;
  if ((tag === 'button' || tag === 'a') && (e.key === ' ' || e.key === 'Enter')) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  switch (e.key) {
    case ' ':
      e.preventDefault();
      togglePlay();
      break;
    case 'ArrowRight':
      e.preventDefault();
      nextSentence();
      break;
    case 'ArrowLeft':
      e.preventDefault();
      prevSentence();
      break;
    case ']':
      cycleSpeed(1);
      break;
    case '[':
      cycleSpeed(-1);
      break;
    case '+':
    case '=':
      bumpFont(1);
      break;
    case '-':
      bumpFont(-1);
      break;
    case 'f':
    case 'F':
      toggleFocus();
      break;
    case 'v':
    case 'V': {
      const views = ['book', 'cinema'];
      const active = state.view === 'cinema' ? 'cinema' : 'book';
      const at = views.indexOf(active);
      setView(views[(at + 1) % views.length]);
      break;
    }
    case 'c':
    case 'C':
      if (state.doc && state.doc.toc && state.doc.toc.length) {
        document.body.classList.add('sidebar-open');
        setSidePanel('contents');
      }
      break;
    default:
      break;
  }
}

// Small handle for debugging and local scripting.
window.murmur = { state, audio, openDoc, playUnit, setSpeed, setVoice, pause };
