// The Settings panel: appearance (theme, font size, reading font), voice and
// speed, stills and auto-generate, engine profile, remote access.
//
// Extracted from app.js unchanged. Everything it needs arrives through `deps`
// rather than being reached for in the module scope, so the seam is visible: a
// function that needs something new has to ask for it here.
export function createSettingsView(deps) {
  const { $, $$, api, state, toast, syncVoiceSelects, renderPage, sceneActivityLabel } = deps;

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
    $('#setPolishFaces').setAttribute('aria-checked', String(s.polishFaces === true));
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
    let act = null;
    try {
      act = await api('/api/film/activity');
    } catch (_) {
      /* the profile summary above is still worth showing */
    }
    // A worker that is still loading its weights is not "idle": the panel used to
    // read "stills engine idle · Loading the stills engine" at the same time.
    const starting = painter.running !== true && act && act.phase && act.phase !== 'idle';
    bits.push(painter.running ? 'stills engine running'
      : starting ? 'stills engine starting' : 'stills engine idle');
    if (act && act.phase && act.phase !== 'idle') {
      // For a placeholder the reason is the useful part: it names the cause and
      // the fix, and Settings is where someone goes looking for it.
      bits.push(act.phase === 'placeholder'
        ? act.detail || sceneActivityLabel(act)
        : sceneActivityLabel(act));
    }
    if (job.seconds) bits.push(`last still ${job.seconds}s (${job.width}x${job.height}, ${job.steps} steps)`);
    if (job.peak_gib) bits.push(`${job.peak_gib} GiB peak`);
    if (job.free_gib) bits.push(`${job.free_gib} GiB VRAM free after`);
    const ram = painter.ram || {};
    if (ram.free_gib) {
      bits.push(`${ram.free_gib} GiB RAM free of ${ram.total_gib}`);
      // The floor is a cold-load guard: fluxgen refuses to *load* the transformer
      // below it. While the worker is resident it holds the model, so free RAM is
      // naturally under the floor and painting is working exactly as intended --
      // saying "painting will refuse" there contradicted the scene on screen.
      if (!painter.running && ram.free_gib * 1024 < (ram.floor_mib || 0)) {
        bits.push('below the RAM floor - the next cold load will refuse');
      }
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

  return {
    applySettings,
    renderThemeOptions,
    updateThemePreview,
    loadFonts,
    saveSettings,
    toggleTheme,
    bumpFont,
    openSettings,
    refreshEngine,
    setEngineProfile,
    wireEngine,
    loadNetwork,
  };
}
