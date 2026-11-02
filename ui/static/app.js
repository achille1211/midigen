/* midigen-ui front end: controls, canvas piano roll, Web Audio playback. No dependencies. */
(() => {
  "use strict";

  // ---------------------------------------------------------------- helpers
  const $ = (id) => document.getElementById(id);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const noteName = (p) => NOTE_NAMES[p % 12] + (Math.floor(p / 12) - 1);
  const MODE_LABEL = (m) => m.replace(/_/g, " ");
  const MODE_PLAIN = (m) => (state.modeLabels && state.modeLabels[m] ? MODE_LABEL(m) + " (" + state.modeLabels[m] + ")" : MODE_LABEL(m));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const COLORS = ["#f472b6", "#60a5fa", "#a78bfa", "#34d399", "#fbbf24", "#22d3ee", "#fb923c", "#f87171", "#a3e635", "#e879f9"];
  const ROLE_COLOR = { drums: "#f472b6", bass: "#60a5fa", chords: "#a78bfa", pad: "#34d399", arp: "#22d3ee", lead: "#fbbf24" };
  const GM_DRUMS = {
    35: "Kick 2", 36: "Kick", 37: "Rim", 38: "Snare", 39: "Clap", 40: "Snare 2", 41: "Tom lo", 42: "Hat closed",
    43: "Tom lo 2", 44: "Hat pedal", 45: "Tom mid", 46: "Hat open", 47: "Tom mid 2", 48: "Tom hi 2", 49: "Crash",
    50: "Tom hi", 51: "Ride", 52: "China", 53: "Ride bell", 54: "Tamb", 55: "Splash", 56: "Cowbell", 57: "Crash 2",
    59: "Ride 2", 69: "Cabasa", 70: "Shaker", 75: "Claves", 76: "Block hi", 77: "Block lo",
  };
  const GM_FAMILIES = ["Piano", "Chrom. perc", "Organ", "Guitar", "Bass", "Strings", "Ensemble", "Brass", "Reed",
    "Pipe", "Synth lead", "Synth pad", "Synth FX", "Ethnic", "Percussive", "SFX"];
  const GM_NAMES = { 0: "Acoustic piano", 4: "Electric piano", 11: "Vibraphone", 12: "Marimba", 14: "Tubular bells",
    16: "Organ", 25: "Steel guitar", 27: "Clean guitar", 30: "Distortion gtr", 32: "Acoustic bass", 33: "Finger bass",
    34: "Pick bass", 38: "Synth bass", 45: "Pizzicato", 46: "Harp", 48: "Strings", 52: "Choir", 61: "Brass",
    65: "Alto sax", 73: "Flute", 80: "Square lead", 81: "Saw lead", 89: "Warm pad", 90: "Polysynth", 91: "Space pad" };
  const programName = (p) => GM_NAMES[p] || GM_FAMILIES[Math.floor(p / 8)] + " #" + p;

  const state = {
    genres: [], modes: [], keys: [], limits: null, presets: [], examples: [], modeLabels: {}, preset: null,
    song: null, // API response for the take on screen
    ppb: 24,    // pixels per beat
    muted: new Set(), soloed: new Set(),
    session: null,          // session summary {id, takes:[meta], take_count, max_takes}
    takes: {},              // take id -> full take payload (cache)
    locks: new Set(),       // locked section indices of the take on screen
    usage: null,            // {generations_used, generations_limit, generations_left, resets_in_s}
    cmp: { on: false, a: null, b: null, data: null },
    busy: false,
  };

  function setStatus(msg, kind) {
    const el = $("status");
    el.textContent = msg || "";
    el.className = "status" + (kind ? " " + kind : "");
  }

  async function api(path, opts) {
    let res;
    try { res = await fetch(path, opts); } catch (e) { const ne = new Error("Can't reach the music server. Check that it is still running, then try again."); ne.code = "network"; throw ne; }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) {
      const err = new Error((data && data.error && data.error.message) || ("The server had a problem (error " + res.status + "). Please try again."));
      err.field = data && data.error && data.error.field;
      err.code = data && data.error && data.error.code;
      err.status = res.status;
      err.info = data && data.error;
      throw err;
    }
    return data;
  }

  // ---------------------------------------------------------------- controls
  const els = {
    form: $("genForm"), genre: $("genre"), key: $("key"), mode: $("mode"), tempo: $("tempo"), tempoRange: $("tempoRange"),
    bars: $("bars"), barsRange: $("barsRange"), seed: $("seed"), genBtn: $("genBtn"), desc: $("genreDesc"),
    tempoHint: $("tempoHint"), prompt: $("prompt"), promptBtn: $("promptBtn"), presets: $("presets"),
  };

  function option(value, label) { const o = document.createElement("option"); o.value = value; o.textContent = label; return o; }

  function currentGenre() { return state.genres.find((g) => g.name === els.genre.value); }

  function applyGenreDefaults() {
    const g = currentGenre();
    if (!g) return;
    els.desc.innerHTML = esc(g.plain || g.description) + (g.plain && g.plain !== g.description ? '<span class="tech">Technical: ' + esc(g.description) + "</span>" : "");
    // keys: auto + genre favourites + all
    els.key.replaceChildren(option("", "Auto (" + g.keys.join("/") + ")"));
    const fav = document.createElement("optgroup"); fav.label = "Typical for " + g.display;
    g.keys.forEach((k) => fav.appendChild(option(k, k)));
    const all = document.createElement("optgroup"); all.label = "All keys";
    state.keys.filter((k) => !g.keys.includes(k)).forEach((k) => all.appendChild(option(k, k)));
    els.key.append(fav, all);
    els.key.value = "";
    // modes: genre modes first
    els.mode.replaceChildren();
    const gm = document.createElement("optgroup"); gm.label = "Typical";
    g.modes.forEach((m) => gm.appendChild(option(m, MODE_PLAIN(m))));
    const om = document.createElement("optgroup"); om.label = "Other";
    state.modes.filter((m) => !g.modes.includes(m)).forEach((m) => om.appendChild(option(m, MODE_PLAIN(m))));
    els.mode.append(gm, om);
    els.mode.value = g.defaults.mode;
    // tempo
    els.tempoRange.min = g.tempo.min; els.tempoRange.max = g.tempo.max;
    els.tempo.value = els.tempoRange.value = g.tempo.default;
    els.tempoHint.textContent = g.tempo.min + "–" + g.tempo.max + " typical";
    if (!els.bars.value) {
      const mx = state.limits ? state.limits.bars[1] : 999;
      els.bars.value = els.barsRange.value = Math.min(g.defaults.bars, mx);
    }
    validateTempo();
  }

  function validateTempo() {
    const g = currentGenre(); const t = Number(els.tempo.value);
    els.tempo.classList.toggle("invalid", !(t >= 20 && t <= 300));
    els.tempoHint.style.color = g && (t < g.tempo.min || t > g.tempo.max) ? "var(--warn)" : "";
  }

  els.genre.addEventListener("change", applyGenreDefaults);
  els.tempoRange.addEventListener("input", () => { els.tempo.value = els.tempoRange.value; validateTempo(); });
  els.tempo.addEventListener("input", () => { els.tempoRange.value = els.tempo.value; validateTempo(); });
  els.barsRange.addEventListener("input", () => { els.bars.value = els.barsRange.value; });
  els.bars.addEventListener("input", () => { els.barsRange.value = els.bars.value; });
  $("randSeed").addEventListener("click", () => { els.seed.value = Math.floor(Math.random() * 1000000); });

  async function loadGenres() {
    setStatus("Loading genres…");
    try {
      const data = await api("/api/genres");
      Object.assign(state, { genres: data.genres, modes: data.modes, keys: data.keys, limits: data.limits,
        presets: data.presets || [], examples: data.examples || [], modeLabels: data.mode_labels || {} });
      applyLimits();
      renderPresets(els.presets, state.presets, false);
      els.genre.replaceChildren(...data.genres.map((g) => { const o = option(g.name, g.label || g.display); o.title = g.display; return o; }));
      els.genre.value = data.genres.some((g) => g.name === "house") ? "house" : data.genres[0].name;
      applyGenreDefaults();
      setStatus(data.genres.length + " genres loaded");
      try { const cfg = await api("/api/config"); state.limits = cfg.limits; applyLimits(); updateUsage(cfg.usage); } catch (e) { /* optional */ }
      restoreFromHash();
    } catch (e) {
      setStatus("Couldn't load the music styles. " + friendlyMessage(e), "err");
    }
  }

  // ---- prompt + presets: each preset sends its prompt text plus its id; the server applies the preset's settings
  function renderPresets(box, list, generateNow) {
    box.replaceChildren(...list.map((p) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "pchip" + (state.preset === p.id ? " active" : ""); b.dataset.preset = p.id;
      b.textContent = p.emoji + " " + p.label;
      const st = p.settings || {};
      b.title = '"' + p.prompt + '" → ' + [st.genre, st.mode, st.tempo && st.tempo + " BPM", st.seconds && "~" + st.seconds + " s"].filter(Boolean).join(", ");
      b.addEventListener("click", () => {
        state.preset = p.id; els.prompt.value = p.prompt; els.prompt.classList.remove("invalid");
        document.querySelectorAll(".pchip").forEach((c) => c.classList.toggle("active", c.dataset.preset === p.id));
        els.form.requestSubmit(els.promptBtn);
      });
      return b;
    }));
  }
  els.prompt.addEventListener("input", () => {
    const p = state.presets.find((x) => x.id === state.preset);
    if (p && els.prompt.value.trim() !== p.prompt) { state.preset = null; document.querySelectorAll(".pchip.active").forEach((c) => c.classList.remove("active")); }
  });
  els.prompt.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); els.form.requestSubmit(els.promptBtn); }
  });

  function renderInterp(song) {
    const box = $("interp"), i = song && song.interpreted;
    if (!i) { box.classList.add("hidden"); return; }
    const src = i.source === "preset" ? "Preset “" + i.preset + "”" : i.source === "prompt" ? "Your description “" + i.prompt + "”" : "Your manual settings";
    let h = '<div class="line">' + esc(i.line) + "</div>" +
      '<div class="sub">' + esc(src) + (i.title ? " · title: <b>" + esc(i.title) + "</b>" : "") +
      (i.recognised ? " · picked " + esc(i.genre_label) + " from " + esc(i.recognised.how) : "") +
      (i.explanation ? " · " + esc(i.explanation) : "") + "</div>" +
      '<div class="sub">' + esc(i.genre_label) + ": " + esc(i.genre_plain) + "</div>";
    if (i.fallback) h += '<div class="fb">⚠ ' + esc(i.fallback_message) + '</div><div class="presets" id="fbPresets"></div>';
    box.innerHTML = h;
    box.classList.toggle("fallback", !!i.fallback);
    box.classList.remove("hidden");
    if (i.fallback) renderPresets($("fbPresets"), state.presets.filter((p) => (i.suggested_presets || []).includes(p.id)), true);
  }

  els.form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const usePrompt = !ev.submitter || ev.submitter === els.promptBtn;
    const seed = els.seed.value === "" ? null : Number(els.seed.value);
    const body = usePrompt ? { prompt: els.prompt.value.trim(), preset: state.preset, seed } : {
      genre: els.genre.value, key: els.key.value || null, mode: els.mode.value || null,
      tempo: els.tempo.value === "" ? null : Number(els.tempo.value),
      bars: els.bars.value === "" ? null : Number(els.bars.value),
      seed,
    };
    if (usePrompt && !body.prompt && !body.preset) {
      els.prompt.classList.add("invalid");
      setStatus(FRIENDLY.empty_prompt(), "warn"); els.prompt.focus(); return;
    }
    els.genBtn.disabled = true; els.genBtn.classList.add("busy");
    els.promptBtn.disabled = true; els.promptBtn.classList.add("busy");
    [els.tempo, els.bars, els.seed, els.prompt].forEach((e) => e.classList.remove("invalid"));
    setStatus(usePrompt ? "Making music…" : "Generating…");
    try {
      const song = await api("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      player.stop();
      state.takes = {}; state.cmp.on = false; showCompare(false);
      acceptTake(song);
      loadSong(song);
      const clampNote = (song.notes || []).find((n) => /limit/.test(n));
      const fb = song.interpreted && song.interpreted.fallback;
      setStatus("Generated in " + song.elapsed_ms + " ms" + (fb ? " · fell back to generic pop (see below)" : clampNote ? " · " + clampNote : ""), clampNote || fb ? "warn" : "ok");
    } catch (e) {
      showError(e);
      if (e.field && els[e.field]) els[e.field].classList.add("invalid");
    } finally {
      els.genBtn.disabled = false; els.genBtn.classList.remove("busy");
      els.promptBtn.disabled = false; els.promptBtn.classList.remove("busy");
    }
  });

  // ---------------------------------------------------------------- song view
  function trackColor(t, i, tracks) { tracks = tracks || state.song.tracks; return ROLE_COLOR[t.role] && !tracks.slice(0, i).some((o) => o.role === t.role) ? ROLE_COLOR[t.role] : COLORS[i % COLORS.length]; }

  function loadSong(song, keepMix) {
    const sameShape = keepMix && state.song && state.song.tracks.length === song.tracks.length;
    state.song = song;
    if (!sameShape) { state.muted.clear(); state.soloed.clear(); }
    state.locks = new Set(song.locked || []);
    song.tracks.forEach((t, i) => { t.color = trackColor(t, i); t.notes.sort((a, b) => a[0] - b[0]); });
    // info line
    const info = $("songInfo");
    const ts = song.time_signature.join("/");
    renderInterp(song);
    const origin = song.kind === "regenerate" && song.parent_label
      ? '<span class="pill take">' + esc(song.label) + " ← " + esc(song.parent_label) + ": ⟳ " + esc((song.regenerated_names || []).join(", ")) + "</span>"
      : (song.label ? '<span class="pill take">' + esc(song.label) + "</span>" : "");
    info.innerHTML = origin + "<b>" + esc(song.title || song.genre) + "</b> &nbsp; " +
      ["" + song.key + " " + MODE_PLAIN(song.mode), song.tempo + " BPM", ts, song.bars + " bars", "seed " + song.seed]
        .map((x) => '<span class="pill">' + esc(x) + "</span>").join("") +
      " &nbsp;form: " + esc(song.sections.map((s) => s.name).join(" → ")) +
      (song.notes || []).filter((n) => !n.startsWith("random seed") && !n.startsWith("regenerated")).map((n) => '<span class="note">⚠ ' + esc(n) + "</span>").join("");
    els.seed.placeholder = "random (last: " + song.seed + ")";
    // download
    const dl = $("dlBtn");
    dl.href = song.download_url; dl.setAttribute("download", song.filename);
    dl.textContent = "⬇ Download" + (song.label ? " " + song.label : "") + " .mid";
    dl.title = song.filename;
    dl.classList.remove("disabled"); dl.removeAttribute("aria-disabled");
    $("playBtn").disabled = false; $("stopBtn").disabled = false;
    renderCritique(song.critique);
    $("empty").classList.add("hidden"); $("roll").classList.remove("hidden");
    const wrap = $("rollWrap"), keepScroll = keepMix ? wrap.scrollLeft : 0;
    fitZoom();
    buildRoll();
    wrap.scrollLeft = keepScroll;
    updateClock(player.position());
    renderTakes();
    updateHash();
  }

  function renderCritique(c) {
    $("critique").classList.remove("hidden");
    const s = $("score");
    s.textContent = c.score + "/100";
    s.className = "score" + (c.score < 70 ? " low" : c.score < 90 ? " mid" : "");
    const ul = $("findings"); ul.replaceChildren();
    if (!c.findings.length) { const li = document.createElement("li"); li.textContent = "No issues found."; ul.appendChild(li); }
    c.findings.forEach((f) => {
      const li = document.createElement("li"); li.className = f.severity;
      const sev = document.createElement("span"); sev.className = "sev"; sev.textContent = f.severity;
      const loc = [f.track, f.section, f.bars ? "bars " + f.bars.join(",") : ""].filter(Boolean).join(" · ");
      li.append(sev, document.createTextNode(f.check + ": " + f.message + (loc ? " (" + loc + ")" : "")));
      if (f.suggestion) { const sg = document.createElement("span"); sg.className = "sug"; sg.textContent = "→ " + f.suggestion; li.appendChild(sg); }
      ul.appendChild(li);
    });
  }

  const LABEL_W = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--label-w")) || 168;
  const MAX_CANVAS = 30000;

  function fitPpb() {
    const wrap = $("rollWrap");
    const avail = Math.max(200, wrap.clientWidth - LABEL_W() - 16);
    return clamp(avail / state.song.total_beats, 4, 200);
  }
  function fitZoom() { applyZoom(Number($("zoom").value)); }
  function applyZoom(z) {
    if (!state.song) return;
    const lo = fitPpb(), hi = Math.max(lo, Math.min(160, MAX_CANVAS / state.song.total_beats));
    state.ppb = lo * Math.pow(hi / lo, z);
  }

  let laneEls = [];
  let playheadEl = null;

  function buildRoll() {
    const song = state.song, roll = $("roll");
    roll.replaceChildren();
    laneEls = [];
    const width = Math.ceil(song.total_beats * state.ppb);
    // ruler
    const ruler = document.createElement("div"); ruler.className = "lane ruler";
    const rl = document.createElement("div"); rl.className = "lane-label"; rl.textContent = "energy · bars · chords";
    const rc = document.createElement("canvas");
    ruler.append(rl, rc); roll.appendChild(ruler);
    drawRuler(rc, width);
    rc.addEventListener("click", seekFromEvent);
    roll.appendChild(buildSectionStrip(width));
    // tracks
    song.tracks.forEach((t, i) => {
      const lane = document.createElement("div"); lane.className = "lane";
      const label = document.createElement("div"); label.className = "lane-label";
      const nm = document.createElement("div"); nm.className = "tname";
      const sw = document.createElement("span"); sw.className = "swatch"; sw.style.background = t.color;
      nm.append(sw, document.createTextNode(t.name)); nm.title = t.name;
      const meta = document.createElement("div"); meta.className = "tmeta";
      meta.textContent = t.role + " · " + (t.is_drums ? "GM drums ch10" : programName(t.program) + " · ch" + (t.channel + 1)) + " · " + t.notes.length + " notes";
      meta.title = meta.textContent;
      const btns = document.createElement("div"); btns.className = "lane-btns";
      const mute = document.createElement("button"); mute.className = "btn mini"; mute.textContent = "M"; mute.title = "Mute";
      const solo = document.createElement("button"); solo.className = "btn mini solo"; solo.textContent = "S"; solo.title = "Solo";
      mute.addEventListener("click", () => { toggle(state.muted, i); refreshMutes(); });
      solo.addEventListener("click", () => { toggle(state.soloed, i); refreshMutes(); });
      btns.append(mute, solo);
      label.append(nm, meta, btns);
      const cv = document.createElement("canvas");
      cv.addEventListener("click", seekFromEvent);
      lane.append(label, cv); roll.appendChild(lane);
      laneEls.push({ lane, mute, solo });
      drawTrack(cv, t, width);
    });
    playheadEl = document.createElement("div");
    Object.assign(playheadEl.style, { position: "absolute", top: "0", bottom: "0", width: "2px", left: LABEL_W() + "px",
      background: "#fff", boxShadow: "0 0 8px #fff8", zIndex: 1, pointerEvents: "none" });
    roll.appendChild(playheadEl);
    refreshMutes();
    setPlayhead(player.position());
  }

  function toggle(set, i) { if (set.has(i)) set.delete(i); else set.add(i); }
  function isAudible(i) { return !state.muted.has(i) && (state.soloed.size === 0 || state.soloed.has(i)); }
  function refreshMutes() {
    laneEls.forEach((l, i) => {
      l.mute.classList.toggle("on", state.muted.has(i));
      l.solo.classList.toggle("on", state.soloed.has(i));
      l.lane.classList.toggle("muted", !isAudible(i));
    });
    player.updateMutes();
  }

  function setupCanvas(cv, w, h) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2, MAX_CANVAS / w);
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    cv.style.width = w + "px"; cv.style.height = h + "px";
    const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  function drawGrid(ctx, w, h) {
    const song = state.song, ppb = state.ppb, bb = song.bar_beats;
    ctx.fillStyle = "#11161d"; ctx.fillRect(0, 0, w, h);
    song.sections.forEach((s, i) => {  // alternating section shading, locked tint, fresh-regen tint
      if (i % 2) { ctx.fillStyle = "#ffffff05"; ctx.fillRect(s.start * ppb, 0, (s.end - s.start) * ppb, h); }
      if (state.locks.has(i)) { ctx.fillStyle = "#38bdf812"; ctx.fillRect(s.start * ppb, 0, (s.end - s.start) * ppb, h); }
      else if ((song.regenerated || []).includes(i)) { ctx.fillStyle = "#fbbf240c"; ctx.fillRect(s.start * ppb, 0, (s.end - s.start) * ppb, h); }
    });
    const beatLines = ppb >= 10;
    for (let b = 0; b <= song.total_beats + 1e-6; b += 1) {
      const isBar = Math.abs(b / bb - Math.round(b / bb)) < 1e-6;
      if (!isBar && !beatLines) continue;
      ctx.fillStyle = isBar ? "#2a313c" : "#1b2129";
      ctx.fillRect(Math.round(b * ppb), 0, 1, h);
    }
    song.sections.forEach((s) => { ctx.fillStyle = "#4b5563"; ctx.fillRect(Math.round(s.start * ppb), 0, 1, h); });
  }

  function drawRuler(cv, w) {
    const h = 40, ctx = setupCanvas(cv, w, h), song = state.song, ppb = state.ppb, bb = song.bar_beats;
    ctx.fillStyle = "#141920"; ctx.fillRect(0, 0, w, h);
    ctx.font = "10.5px system-ui, sans-serif"; ctx.textBaseline = "middle";
    // sections band
    song.sections.forEach((s, i) => {
      ctx.fillStyle = i % 2 ? "#1f2733" : "#1a212b";
      ctx.fillRect(s.start * ppb, 0, (s.end - s.start) * ppb, 14);
      ctx.fillStyle = "#6b7a99"; if ((s.end - s.start) * ppb > 70) ctx.fillText("energy " + Math.round(s.energy * 100) + "%", s.start * ppb + 4, 7);
    });
    // bar numbers
    const step = Math.max(1, Math.ceil(28 / (bb * ppb)));
    for (let bar = 0; bar * bb <= song.total_beats; bar++) {
      const x = Math.round(bar * bb * ppb);
      ctx.fillStyle = "#2f3744"; ctx.fillRect(x, 14, 1, 26);
      if (bar % step === 0 && bar * bb < song.total_beats) { ctx.fillStyle = "#8b949e"; ctx.fillText(String(bar + 1), x + 3, 21); }
    }
    // chords
    ctx.fillStyle = "#6ee7b7";
    song.chords.forEach((c) => {
      const x = c.start * ppb, cw = (c.end - c.start) * ppb;
      if (cw > 18) { ctx.save(); ctx.beginPath(); ctx.rect(x, 28, cw - 2, 12); ctx.clip(); ctx.fillText(c.symbol, x + 3, 34); ctx.restore(); }
    });
  }

  function drawTrack(cv, t, w) {
    const ppb = state.ppb;
    if (t.is_drums) {
      const pitches = [...new Set(t.notes.map((n) => n[2]))].sort((a, b) => a - b);
      const laneH = 13, h = Math.max(48, pitches.length * laneH + 6);
      const ctx = setupCanvas(cv, w, h);
      drawGrid(ctx, w, h);
      const row = {}; pitches.forEach((p, i) => { row[p] = h - 3 - (i + 1) * laneH; });
      pitches.forEach((p, i) => { if (i % 2) { ctx.fillStyle = "#ffffff06"; ctx.fillRect(0, row[p], w, laneH); } });
      t.notes.forEach(([s, d, p, v]) => {
        const x = s * ppb, y = row[p] + 2, size = laneH - 4;
        ctx.globalAlpha = 0.35 + 0.65 * (v / 127);
        ctx.fillStyle = t.color;
        const ww = Math.max(2, Math.min(size, d * ppb));
        ctx.fillRect(x, y, ww, size);
      });
      ctx.globalAlpha = 1;
      // lane names drawn at a few positions so they stay visible while scrolling
      ctx.font = "9.5px system-ui, sans-serif"; ctx.textBaseline = "middle";
      for (let x0 = 4; x0 < w; x0 += 1600) {  // repeat lane names so they stay findable when zoomed in
        pitches.forEach((p) => {
          const label = GM_DRUMS[p] || ("Drum " + p);
          ctx.fillStyle = "#0e1116cc"; ctx.fillRect(x0 - 2, row[p] + 1, ctx.measureText(label).width + 6, laneH - 2);
          ctx.fillStyle = "#cbd5e1"; ctx.fillText(label, x0 + 1, row[p] + laneH / 2);
        });
      }
      return;
    }
    let lo = 127, hi = 0;
    t.notes.forEach((n) => { lo = Math.min(lo, n[2]); hi = Math.max(hi, n[2]); });
    if (lo > hi) { lo = 60; hi = 72; }
    lo -= 2; hi += 2;
    const span = hi - lo + 1;
    const rowH = clamp(150 / span, 2.5, 7), h = Math.round(Math.max(64, span * rowH));
    const ctx = setupCanvas(cv, w, h);
    drawGrid(ctx, w, h);
    const yOf = (p) => h - (p - lo + 1) * (h / span);
    // black-key shading + C markers
    for (let p = lo; p <= hi; p++) {
      if ([1, 3, 6, 8, 10].includes(p % 12)) { ctx.fillStyle = "#00000022"; ctx.fillRect(0, yOf(p), w, h / span); }
    }
    ctx.font = "9px system-ui, sans-serif"; ctx.textBaseline = "middle";
    const rh = h / span;
    t.notes.forEach(([s, d, p, v]) => {
      const x = s * ppb, y = yOf(p), ww = Math.max(1.5, d * ppb - 1);
      ctx.globalAlpha = 0.45 + 0.55 * (v / 127);
      ctx.fillStyle = t.color;
      ctx.fillRect(x, y + 0.5, ww, Math.max(1.5, rh - 1));
    });
    ctx.globalAlpha = 1;
    for (let p = lo; p <= hi; p++) {
      if (p % 12 === 0) { ctx.fillStyle = "#64748b"; ctx.fillText(noteName(p), 3, yOf(p) + rh / 2); }
    }
  }

  function seekFromEvent(ev) {
    if (!state.song) return;
    const rect = ev.currentTarget.getBoundingClientRect();
    const beat = clamp((ev.clientX - rect.left) / state.ppb, 0, state.song.total_beats);
    // snap to 16th
    player.seek(Math.round(beat * 4) / 4);
  }

  function setPlayhead(beat) {
    if (!playheadEl || !state.song) return;
    const x = LABEL_W() + beat * state.ppb;
    playheadEl.style.transform = "translateX(" + (x - LABEL_W()) + "px)";
    updateClock(beat);
  }

  function fmtTime(sec) { sec = Math.max(0, sec); const m = Math.floor(sec / 60), s = Math.floor(sec % 60); return m + ":" + String(s).padStart(2, "0"); }
  function updateClock(beat) {
    const song = state.song; if (!song) return;
    const bb = song.bar_beats, bar = Math.floor(beat / bb + 1e-9), b = Math.floor(beat - bar * bb + 1e-9);
    $("pos").textContent = (bar + 1) + "." + (b + 1);
    $("clock").textContent = fmtTime(beat * 60 / song.tempo) + " / " + fmtTime(song.total_beats * 60 / song.tempo);
  }

  function followPlayhead(beat) {
    const wrap = $("rollWrap"), x = beat * state.ppb, view = wrap.clientWidth - LABEL_W();
    if (x < wrap.scrollLeft || x > wrap.scrollLeft + view - 40) wrap.scrollLeft = Math.max(0, x - view * 0.15);
  }

  // ---------------------------------------------------------------- audio engine
  const player = (() => {
    let ac = null, master = null, comp = null, noiseBuf = null;
    let trackBus = [];          // per track GainNode
    let playing = false, startTime = 0, startBeat = 0, pausedBeat = 0;
    let cursor = [];            // next note index per track
    let scheduledTo = 0;        // beat up to which notes are scheduled
    let timer = null, raf = null;
    const voices = new Set();   // {nodes:[sources], end}
    const LOOKAHEAD = 0.15, TICK = 25;

    function ensure() {
      if (ac) return;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) throw new Error("Web Audio API not supported in this browser");
      ac = new AC();
      comp = ac.createDynamicsCompressor();
      comp.threshold.value = -14; comp.knee.value = 12; comp.ratio.value = 4; comp.attack.value = 0.005; comp.release.value = 0.15;
      master = ac.createGain(); master.gain.value = Number($("master").value);
      master.connect(comp); comp.connect(ac.destination);
      noiseBuf = ac.createBuffer(1, ac.sampleRate * 2, ac.sampleRate);
      const d = noiseBuf.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    }

    function buildBuses() {
      trackBus.forEach((b) => { try { b.out.disconnect(); } catch (e) { /* ignore */ } });
      trackBus = state.song.tracks.map((t, i) => {
        const g = ac.createGain();
        const level = (t.volume / 127) * ({ drums: 0.9, bass: 0.8, chords: 0.45, pad: 0.35, arp: 0.4, lead: 0.55 }[t.role] || 0.5);
        g.gain.value = isAudible(i) ? level : 0;
        let out = g;
        if (ac.createStereoPanner) {
          const pn = ac.createStereoPanner(); pn.pan.value = clamp((t.pan - 64) / 64, -1, 1);
          g.connect(pn); out = pn;
        }
        out.connect(master);
        return { in: g, out, level };
      });
    }

    function updateMutes() {
      if (!ac || !trackBus.length) return;
      const now = ac.currentTime;
      trackBus.forEach((b, i) => { b.in.gain.setTargetAtTime(isAudible(i) ? b.level : 0, now, 0.015); });
    }

    const mtof = (p) => 440 * Math.pow(2, (p - 69) / 12);
    function track(nodes, end) { const rec = { nodes, end }; voices.add(rec); nodes[0].onended = () => voices.delete(rec); }

    function envGain(t0, peak, a, dcy, sus, rel, offT) {
      const g = ac.createGain(), p = g.gain, relT = Math.max(t0 + a, offT);
      p.setValueAtTime(0.0001, t0);
      p.linearRampToValueAtTime(peak, t0 + a);
      p.setTargetAtTime(Math.max(0.0001, peak * sus), t0 + a, Math.max(0.005, dcy / 3));
      p.setTargetAtTime(0.0001, relT, Math.max(0.005, rel / 4));
      return g;
    }

    // timbre preset by role / GM program family
    function preset(t) {
      const fam = Math.floor(t.program / 8);
      if (t.role === "bass" || fam === 4) return { waves: ["sawtooth", "sine"], det: [0, 0], oct: [0, 0], gains: [0.5, 0.7], cutoff: 900, fenv: 2.5, a: 0.005, d: 0.25, s: 0.6, r: 0.08 };
      if (t.role === "pad" || fam === 11 || fam === 6 || fam === 5) return { waves: ["sawtooth", "sawtooth"], det: [-7, 7], oct: [0, 0], gains: [0.35, 0.35], cutoff: 1800, fenv: 0.3, a: 0.35, d: 0.8, s: 0.8, r: 0.6 };
      if (fam === 1 || fam === 14) return { waves: ["sine", "sine"], det: [0, 0], oct: [0, 2], gains: [0.6, 0.15], cutoff: 8000, fenv: 0, a: 0.002, d: 0.6, s: 0.0, r: 0.4, pluck: true };
      if (fam === 0) return { waves: ["triangle", "sine"], det: [0, 3], oct: [0, 1], gains: [0.55, 0.2], cutoff: 4000, fenv: 0.8, a: 0.003, d: 0.9, s: 0.15, r: 0.25, pluck: true };
      if (fam === 2) return { waves: ["square", "sine"], det: [0, 0], oct: [0, 1], gains: [0.2, 0.35], cutoff: 3000, fenv: 0, a: 0.01, d: 0.1, s: 0.9, r: 0.08 };
      if (fam === 3) return { waves: ["sawtooth", "triangle"], det: [0, 5], oct: [0, 0], gains: [0.3, 0.3], cutoff: 2600, fenv: 1.5, a: 0.003, d: 0.4, s: 0.2, r: 0.15, pluck: true };
      if (fam === 7) return { waves: ["sawtooth", "sawtooth"], det: [-4, 4], oct: [0, 0], gains: [0.3, 0.3], cutoff: 1400, fenv: 1.6, a: 0.03, d: 0.3, s: 0.7, r: 0.12 };
      if (fam === 8 || fam === 9) return { waves: ["square", "sine"], det: [0, 0], oct: [0, 0], gains: [0.15, 0.5], cutoff: 2400, fenv: 0.5, a: 0.03, d: 0.2, s: 0.8, r: 0.1 };
      if (t.role === "arp") return { waves: ["square", "triangle"], det: [0, 0], oct: [0, 0], gains: [0.25, 0.3], cutoff: 3000, fenv: 1.2, a: 0.002, d: 0.18, s: 0.25, r: 0.1, pluck: true };
      if (t.role === "lead" || fam === 10) return { waves: ["sawtooth", "square"], det: [-5, 5], oct: [0, 0], gains: [0.28, 0.18], cutoff: 3200, fenv: 0.8, a: 0.01, d: 0.2, s: 0.7, r: 0.12 };
      return { waves: ["triangle", "sine"], det: [0, 0], oct: [0, 1], gains: [0.5, 0.2], cutoff: 3000, fenv: 0.5, a: 0.005, d: 0.4, s: 0.4, r: 0.2 };
    }

    function playTone(t, bus, when, dur, pitch, vel) {
      const pr = t._preset || (t._preset = preset(t));
      const amp = Math.pow(vel / 127, 1.4);
      const off = when + Math.max(0.03, dur);
      const end = off + pr.r * 1.5 + 0.05;
      const filt = ac.createBiquadFilter(); filt.type = "lowpass"; filt.Q.value = 0.8;
      const fc = Math.min(16000, pr.cutoff * (0.6 + 0.6 * vel / 127));
      filt.frequency.setValueAtTime(fc * (1 + pr.fenv), when);
      filt.frequency.setTargetAtTime(fc, when, 0.06 + pr.d * 0.3);
      const g = envGain(when, amp, pr.a, pr.d, pr.s, pr.r, off);
      filt.connect(g); g.connect(bus.in);
      const srcs = pr.waves.map((w, k) => {
        const o = ac.createOscillator(); o.type = w;
        o.frequency.value = mtof(pitch + 12 * pr.oct[k]); o.detune.value = pr.det[k];
        const og = ac.createGain(); og.gain.value = pr.gains[k];
        o.connect(og); og.connect(filt);
        o.start(when); o.stop(end);
        return o;
      });
      track(srcs, end);
    }

    function noise(when, dur) {
      const s = ac.createBufferSource(); s.buffer = noiseBuf;
      s.start(when, Math.random() * 1.0); s.stop(when + dur + 0.02);
      return s;
    }

    function playDrum(bus, when, pitch, vel) {
      const amp = Math.pow(vel / 127, 1.3);
      const out = ac.createGain(); out.gain.value = 1; out.connect(bus.in);
      const srcs = [];
      const decayGain = (peak, dec, t0 = when) => {
        const g = ac.createGain();
        g.gain.setValueAtTime(peak, t0);
        g.gain.exponentialRampToValueAtTime(0.0008, t0 + dec);
        g.connect(out); return g;
      };
      const flt = (type, f, q) => { const b = ac.createBiquadFilter(); b.type = type; b.frequency.value = f; b.Q.value = q || 0.7; return b; };
      const chain = (src, ...nodes) => { let n = src; nodes.forEach((x) => { n.connect(x); n = x; }); srcs.push(src); };
      let len = 0.2;
      if (pitch === 35 || pitch === 36) {  // kick
        len = 0.5;
        const o = ac.createOscillator(); o.type = "sine";
        o.frequency.setValueAtTime(160, when); o.frequency.exponentialRampToValueAtTime(42, when + 0.12);
        o.start(when); o.stop(when + len);
        chain(o, decayGain(amp * 1.1, 0.45));
        const c = noise(when, 0.02); chain(c, flt("highpass", 3000), decayGain(amp * 0.25, 0.015));
      } else if (pitch === 38 || pitch === 40) { // snare
        len = 0.25;
        const n = noise(when, len); chain(n, flt("highpass", 1200), decayGain(amp * 0.7, 0.2));
        const o = ac.createOscillator(); o.type = "triangle";
        o.frequency.setValueAtTime(220, when); o.frequency.exponentialRampToValueAtTime(160, when + 0.08);
        o.start(when); o.stop(when + 0.15); chain(o, decayGain(amp * 0.5, 0.12));
      } else if (pitch === 39) { // clap: 3 quick bursts + tail
        len = 0.3;
        [0, 0.011, 0.023].forEach((dt) => { const n = noise(when + dt, 0.03); chain(n, flt("bandpass", 1500, 1.2), decayGain(amp * 0.7, 0.025, when + dt)); });
        const n = noise(when + 0.03, 0.25); chain(n, flt("bandpass", 1300, 0.9), decayGain(amp * 0.5, 0.2, when + 0.03));
      } else if (pitch === 37) { // rim
        len = 0.08;
        const o = ac.createOscillator(); o.type = "square"; o.frequency.value = 820; o.start(when); o.stop(when + len);
        chain(o, flt("bandpass", 1700, 3), decayGain(amp * 0.5, 0.05));
      } else if (pitch === 42 || pitch === 44) { // closed / pedal hat
        len = 0.08;
        const n = noise(when, len); chain(n, flt("highpass", 7500), decayGain(amp * 0.35, pitch === 44 ? 0.04 : 0.06));
      } else if (pitch === 46) { // open hat
        len = 0.4;
        const n = noise(when, len); chain(n, flt("highpass", 7000), decayGain(amp * 0.3, 0.35));
      } else if (pitch === 51 || pitch === 53 || pitch === 59) { // ride
        len = 0.8;
        const n = noise(when, len); chain(n, flt("highpass", 5500), decayGain(amp * 0.16, 0.7));
        [3150, 4700].forEach((f) => { const o = ac.createOscillator(); o.type = "square"; o.frequency.value = f; o.start(when); o.stop(when + len); chain(o, flt("highpass", 3000), decayGain(amp * 0.03, 0.6)); });
      } else if (pitch === 49 || pitch === 57 || pitch === 52 || pitch === 55) { // crash
        len = 1.6;
        const n = noise(when, len); chain(n, flt("highpass", 4000), decayGain(amp * 0.35, 1.5));
      } else if ([41, 43, 45, 47, 48, 50].includes(pitch)) { // toms
        len = 0.45;
        const f = { 41: 90, 43: 105, 45: 125, 47: 145, 48: 170, 50: 200 }[pitch];
        const o = ac.createOscillator(); o.type = "sine";
        o.frequency.setValueAtTime(f * 1.6, when); o.frequency.exponentialRampToValueAtTime(f, when + 0.08);
        o.start(when); o.stop(when + len); chain(o, decayGain(amp * 0.8, 0.4));
      } else if (pitch === 70 || pitch === 69 || pitch === 54) { // shaker / cabasa / tambourine
        len = pitch === 54 ? 0.15 : 0.07;
        const n = noise(when, len); chain(n, flt(pitch === 54 ? "highpass" : "bandpass", pitch === 54 ? 8000 : 6000, 1), decayGain(amp * 0.28, len));
      } else if (pitch === 56) { // cowbell
        len = 0.3;
        [540, 800].forEach((f) => { const o = ac.createOscillator(); o.type = "square"; o.frequency.value = f; o.start(when); o.stop(when + len); chain(o, flt("bandpass", 900, 2), decayGain(amp * 0.2, 0.25)); });
      } else { // anything else: short click-noise
        len = 0.1;
        const n = noise(when, len); chain(n, flt("bandpass", 2500, 1), decayGain(amp * 0.3, 0.08));
      }
      track(srcs, when + len);
    }

    function spb() { return 60 / state.song.tempo; }
    function beatAt(time) { return startBeat + (time - startTime) / spb(); }
    function timeOf(beat) { return startTime + (beat - startBeat) * spb(); }

    function resetCursors(beat) {
      cursor = state.song.tracks.map((t) => {
        let lo = 0, hi = t.notes.length;
        while (lo < hi) { const m = (lo + hi) >> 1; if (t.notes[m][0] < beat - 1e-6) lo = m + 1; else hi = m; }
        return lo;
      });
      scheduledTo = beat;
    }

    function schedule() {
      const song = state.song;
      const horizonBeat = Math.min(song.total_beats, beatAt(ac.currentTime + LOOKAHEAD));
      song.tracks.forEach((t, i) => {
        const notes = t.notes, bus = trackBus[i];
        while (cursor[i] < notes.length && notes[cursor[i]][0] < horizonBeat) {
          const [s, d, p, v] = notes[cursor[i]++];
          if (s < scheduledTo - 1e-6) continue;
          const when = Math.max(ac.currentTime, timeOf(s));
          if (t.is_drums || t.channel === 9) playDrum(bus, when, p, v); else playTone(t, bus, when, d * spb(), p, v);
        }
      });
      scheduledTo = horizonBeat;
      if (horizonBeat >= song.total_beats - 1e-6 && beatAt(ac.currentTime) >= song.total_beats) {
        if ($("loop").checked) { startAt(0); } else { stop(); }
      }
    }

    function frame() {
      if (!playing) return;
      const b = clamp(beatAt(ac.currentTime), 0, state.song.total_beats);
      setPlayhead(b); followPlayhead(b);
      raf = requestAnimationFrame(frame);
    }

    function startAt(beat) {
      silence();
      startBeat = beat; startTime = ac.currentTime + 0.06;
      resetCursors(beat);
    }

    function play() {
      if (!state.song) return;
      ensure();
      if (ac.state === "suspended") ac.resume();
      buildBuses();
      if (pausedBeat >= state.song.total_beats - 1e-6) pausedBeat = 0;
      playing = true;
      startAt(pausedBeat);
      clearInterval(timer); timer = setInterval(schedule, TICK); schedule();
      cancelAnimationFrame(raf); raf = requestAnimationFrame(frame);
      $("playBtn").textContent = "❚❚";
    }

    function silence() {
      if (!ac) return;
      const now = ac.currentTime;
      voices.forEach((v) => v.nodes.forEach((n) => { try { n.stop(now + 0.01); } catch (e) { /* already stopped */ } }));
      voices.clear();
      // quick fade on buses to avoid clicks from envelopes in flight
      trackBus.forEach((b, i) => { b.in.gain.cancelScheduledValues(now); b.in.gain.setValueAtTime(isAudible(i) ? b.level : 0, now); });
    }

    function pause() {
      if (!playing) return;
      pausedBeat = clamp(beatAt(ac.currentTime), 0, state.song.total_beats);
      playing = false; clearInterval(timer); cancelAnimationFrame(raf); silence();
      $("playBtn").textContent = "▶";
      setPlayhead(pausedBeat);
    }

    function stop() {
      playing = false; clearInterval(timer); cancelAnimationFrame(raf); silence();
      pausedBeat = 0; $("playBtn").textContent = "▶";
      setPlayhead(0); const w = $("rollWrap"); if (w) w.scrollLeft = 0;
    }

    function seek(beat) {
      pausedBeat = beat;
      if (playing) { startAt(beat); } else { setPlayhead(beat); }
    }

    // swap the song under a running transport (A/B compare, take switching): keep the playhead
    function reload(beat) {
      if (!playing) { pausedBeat = clamp(beat, 0, state.song.total_beats); setPlayhead(pausedBeat); return; }
      silence(); buildBuses(); startAt(clamp(beat, 0, state.song.total_beats));
    }

    return {
      play, pause, stop, seek, updateMutes, reload,
      toggle() { if (playing) pause(); else play(); },
      position() { return playing ? Math.max(0, beatAt(ac.currentTime)) : pausedBeat; },
      setMaster(v) { if (master) master.gain.setTargetAtTime(v, ac.currentTime, 0.02); },
      get playing() { return playing; },
    };
  })();

  // ---------------------------------------------------------------- limits, usage, errors
  function applyLimits() {
    const L = state.limits; if (!L) return;
    const [lo, hi] = L.bars;
    [els.bars, els.barsRange].forEach((e) => { e.min = lo; e.max = hi; });
    $("barsHint").textContent = lo + "–" + hi + " bars";
    if (Number(els.bars.value) > hi) els.bars.value = els.barsRange.value = hi;
  }

  function fmtWait(s) { s = Math.max(1, Math.round(s || 0)); return s >= 90 ? Math.ceil(s / 60) + " min" : s + " s"; }

  function updateUsage(u) {
    if (u) state.usage = u;
    const el = $("usage"), parts = [];
    const sess = state.session;
    if (sess) parts.push("Take " + sess.take_count + " of " + sess.max_takes);
    u = state.usage;
    if (u && u.generations_limit) {
      parts.push(u.generations_used + "/" + u.generations_limit + " generations this hour" +
        (u.generations_left === 0 ? " · more in " + fmtWait(u.resets_in_s) : ""));
    }
    el.textContent = parts.join(" · ");
    el.classList.toggle("warn", !!(u && u.generations_limit && u.generations_left !== null && u.generations_left <= 3));
    $("takeCount").textContent = sess ? "Take " + sess.take_count + " of " + sess.max_takes : "";
    const full = sess && sess.take_count >= sess.max_takes;
    const out = u && u.generations_limit && u.generations_left === 0;
    [$("newTakeBtn"), $("regenBtn")].forEach((b) => {
      b.classList.toggle("limited", !!(full || out));
      b.title = full ? "This song has " + sess.max_takes + " takes, the maximum. Delete one to make room."
        : out ? "Hourly generation limit reached; try again in " + fmtWait(u.resets_in_s) + "."
        : b.dataset.title || b.title;
    });
  }

  const FRIENDLY = {
    rate_limit: (e) => "You've used all " + ((state.usage && state.usage.generations_limit) || "") + " generations for this hour. Try again in " + fmtWait(e.info && e.info.retry_after_s) + ".",
    take_limit: (e) => e.message,
    session_limit: () => "The server is busy (too many songs open right now). Please try again in a few minutes.",
    session_not_found: () => "This song has expired on the server. Generate a new one.",
    take_not_found: () => "That take no longer exists. Generate a new one.",
    empty_prompt: () => "Describe the music you want (for example “" + (state.examples[0] || "chill coffee shop music") + "”), or click a preset.",
    not_understood: (e) => "Sorry, I didn't understand that. Try something like " + ((e.info && e.info.examples) || state.examples).slice(0, 3).map((x) => "“" + x + "”").join(", ") + ", or click a preset.",
    prompt_too_long: () => "That description is too long. Please keep it under 300 characters.",
    unknown_preset: () => "That preset isn't available any more. Reload the page and pick another.",
    unknown_genre: () => "That genre isn't available. Pick one from the list.",
    internal: () => "Something went wrong while making the music. Please try again, or pick a preset.",
    network: (e) => e.message,
  };
  // never show raw JSON, tracebacks or Python-ish messages to the user
  function friendlyMessage(e) {
    const f = e.code && FRIENDLY[e.code];
    if (f) return f(e);
    const m = String(e.message || "");
    if (e.status === 422) return "The generator couldn't make that combination. Try different settings, or use a preset.";
    if (e.status >= 500 || /Traceback|Error:|[{}\[\]]/.test(m)) return "Something went wrong on the server. Please try again, or pick a preset.";
    return m.charAt(0).toUpperCase() + m.slice(1);
  }
  function showError(e) {
    setStatus(friendlyMessage(e), e.status === 429 || e.status === 409 ? "warn" : "err");
    if (e.field && els[e.field]) els[e.field].classList.add("invalid");
    if (e.code === "rate_limit" && state.usage) { state.usage.generations_left = 0; state.usage.resets_in_s = (e.info && e.info.retry_after_s) || state.usage.resets_in_s; updateUsage(); }
    if (e.code === "session_not_found") { state.session = null; renderTakes(); }
  }

  // ---------------------------------------------------------------- takes
  function acceptTake(t) {
    state.takes[t.id] = t;
    if (t.session) state.session = t.session;
    updateUsage(t.usage);
  }

  function metaOf(id) { return state.session && state.session.takes.find((m) => m.id === id); }

  function renderTakes() {
    const bar = $("takesBar"), tabs = $("takeTabs"), sess = state.session;
    bar.classList.toggle("hidden", !sess || !state.song);
    if (!sess || !state.song) return;
    tabs.replaceChildren();
    sess.takes.forEach((m) => {
      const tab = document.createElement("div");
      tab.className = "tab" + (m.id === state.song.id ? " active" : "") +
        (state.cmp.on && (m.id === state.cmp.a || m.id === state.cmp.b) ? " in-cmp" : "");
      tab.setAttribute("role", "tab"); tab.setAttribute("aria-selected", m.id === state.song.id ? "true" : "false");
      tab.dataset.take = m.id;
      const b = document.createElement("button"); b.className = "tab-main";
      b.title = m.label + " · score " + m.score + (m.parent_label ? " · from " + m.parent_label + " (⟳ " + m.regenerated_names.join(", ") + ")" : " · seed " + m.seed);
      const nm = document.createElement("span"); nm.className = "tab-name"; nm.textContent = m.label;
      const sc = document.createElement("span"); sc.className = "tab-score" + (m.score < 70 ? " low" : m.score < 90 ? " mid" : ""); sc.textContent = m.score;
      b.append(nm, sc);
      if (m.parent_label) { const pr = document.createElement("span"); pr.className = "tab-parent"; pr.textContent = "↳" + m.parent_label.replace(/^Take /, ""); b.appendChild(pr); }
      b.addEventListener("click", () => showTake(m.id));
      tab.appendChild(b);
      if (sess.takes.length > 1) {
        const x = document.createElement("button"); x.className = "tab-x"; x.textContent = "×";
        x.title = "Delete " + m.label; x.setAttribute("aria-label", "Delete " + m.label);
        x.addEventListener("click", (ev) => { ev.stopPropagation(); deleteTake(m.id); });
        tab.appendChild(x);
      }
      tabs.appendChild(tab);
    });
    updateUsage();
    const unlocked = state.song.sections.length - state.locks.size;
    $("regenBtn").disabled = unlocked === 0;
    $("regenBtn").textContent = state.locks.size ? "⟳ Regenerate " + unlocked + " unlocked" : "⟳ Regenerate unlocked";
    $("regenBtn").title = unlocked === 0 ? "Every section is locked. Unlock one to regenerate it." : $("regenBtn").dataset.title;
    if (state.cmp.on) fillCompareSelects();
  }

  async function fetchTake(id) {
    if (state.takes[id]) return state.takes[id];
    const t = await api("/api/sessions/" + state.session.id + "/takes/" + id);
    state.takes[id] = t; return t;
  }

  // Put a take on screen. Keeps mute/solo, scroll and (if running) the transport position.
  async function showTake(id, opts) {
    opts = opts || {};
    try {
      const t = await fetchTake(id);
      const pos = player.position();
      loadSong(t, true);
      player.reload(pos);
      if (state.cmp.on && !opts.fromAB) { state.cmp.a = id; if (state.cmp.b === id) state.cmp.b = pickB(id); refreshCompare(); }
      else if (state.cmp.on) markAB();
    } catch (e) { showError(e); }
  }

  async function busy(btn, label, fn) {
    if (state.busy) return;
    state.busy = true; btn.classList.add("busy"); setStatus(label);
    document.body.classList.add("working");
    try { await fn(); } catch (e) { showError(e); } finally {
      state.busy = false; btn.classList.remove("busy"); document.body.classList.remove("working");
    }
  }

  // Client-side pre-check so a full song gets the friendly message without a doomed request
  // (the server still enforces the limit authoritatively).
  function takesFull() {
    const s = state.session;
    if (!s || s.take_count < s.max_takes) return false;
    showError({ code: "take_limit", status: 409, message: "This song already has " + s.max_takes + " takes (the maximum). Delete a take you don't need to make room." });
    return true;
  }

  function newTake() {
    if (!state.session || takesFull()) return;
    return busy($("newTakeBtn"), "Generating a new take…", async () => {
      const t = await api("/api/sessions/" + state.session.id + "/takes", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      acceptTake(t);
      await showTake(t.id);
      setStatus(t.label + ": new seed " + t.seed + " · score " + t.score + " · " + t.elapsed_ms + " ms", "ok");
    });
  }

  function regenerate(sections, btn) {
    if (!state.song || takesFull()) return;
    const parent = state.song;
    const body = { locked: [...state.locks].sort((a, b) => a - b) };
    if (sections) body.sections = sections;
    const what = sections ? sections.map((i) => parent.sections[i].name).join(", ") : "unlocked sections";
    return busy(btn || $("regenBtn"), "Regenerating " + what + "…", async () => {
      const t = await api("/api/sessions/" + state.session.id + "/takes/" + parent.id + "/regenerate",
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      // the parent remembers its locks server-side too
      if (state.takes[parent.id]) state.takes[parent.id].locked = body.locked;
      acceptTake(t);
      await showTake(t.id);
      const kept = t.locked.length ? " · kept " + t.locked.map((i) => t.sections[i].name).join(", ") + " exactly" : "";
      setStatus(t.label + " (from " + t.parent_label + "): ⟳ " + t.regenerated_names.join(", ") + kept + " · score " + t.score + " · " + t.elapsed_ms + " ms", "ok");
    });
  }

  let lockSync = null;
  function toggleLock(i) {
    if (state.locks.has(i)) state.locks.delete(i); else state.locks.add(i);
    const song = state.song, locked = [...state.locks].sort((a, b) => a - b);
    song.locked = locked;
    const m = metaOf(song.id); if (m) m.locked = locked;
    const sl = $("rollWrap").scrollLeft; buildRoll(); $("rollWrap").scrollLeft = sl; renderTakes();
    clearTimeout(lockSync);
    lockSync = setTimeout(() => {
      api("/api/sessions/" + state.session.id + "/takes/" + song.id + "/locks",
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ locked }) }).catch(() => { /* re-sent with regenerate anyway */ });
    }, 250);
  }

  async function deleteTake(id) {
    const m = metaOf(id); if (!m) return;
    if (!window.confirm("Delete " + m.label + "? Its .mid download goes away too.")) return;
    try {
      const sess = await api("/api/sessions/" + state.session.id + "/takes/" + id, { method: "DELETE" });
      delete state.takes[id];
      state.session = sess;
      Object.values(state.takes).forEach((t) => { t.session = sess; const mm = sess.takes.find((x) => x.id === t.id); if (mm) Object.assign(t, { parent_label: mm.parent_label }); });
      if (state.cmp.on) { if (state.cmp.a === id) state.cmp.a = null; if (state.cmp.b === id) state.cmp.b = null; }
      if (state.song.id === id) await showTake(sess.takes[sess.takes.length - 1].id);
      else renderTakes();
      if (state.cmp.on) { if (sess.takes.length < 2) showCompare(false); else refreshCompare(); }
      setStatus("Deleted " + m.label + " · " + sess.take_count + " of " + sess.max_takes + " takes used", "ok");
    } catch (e) { showError(e); }
  }

  // section strip: name, lock toggle, per-section regenerate
  function buildSectionStrip(width) {
    const song = state.song;
    const lane = document.createElement("div"); lane.className = "lane secstrip";
    const lbl = document.createElement("div"); lbl.className = "lane-label";
    lbl.innerHTML = '<div class="tname">Sections</div><div class="tmeta">🔒 keep exactly · ⟳ re-roll one</div>';
    const strip = document.createElement("div"); strip.className = "chips"; strip.style.width = width + "px";
    song.sections.forEach((s, i) => {
      const locked = state.locks.has(i), fresh = (song.regenerated || []).includes(i);
      const c = document.createElement("div");
      c.className = "chip" + (locked ? " locked" : "") + (fresh && !locked ? " fresh" : "");
      c.dataset.section = i;
      c.style.left = (s.start * state.ppb) + "px"; c.style.width = Math.max(10, (s.end - s.start) * state.ppb - 2) + "px";
      const nm = document.createElement("span"); nm.className = "chip-name";
      nm.textContent = s.name; nm.title = s.name + " · bars " + (s.start / song.bar_beats + 1) + "–" + (s.end / song.bar_beats) + " · energy " + Math.round(s.energy * 100) + "%" + (fresh ? " · regenerated in this take" : "");
      const lk = document.createElement("button"); lk.className = "chip-btn lock"; lk.textContent = locked ? "🔒" : "🔓";
      lk.setAttribute("aria-pressed", locked ? "true" : "false");
      lk.title = locked ? "Locked: notes stay exactly the same when regenerating. Click to unlock." : "Lock " + s.name + " (keep its notes exactly)";
      lk.setAttribute("aria-label", (locked ? "Unlock " : "Lock ") + s.name);
      lk.addEventListener("click", () => toggleLock(i));
      const rg = document.createElement("button"); rg.className = "chip-btn regen"; rg.textContent = "⟳";
      rg.disabled = locked;
      rg.title = locked ? "Unlock to regenerate" : "Regenerate only " + s.name + " (new take)";
      rg.setAttribute("aria-label", "Regenerate " + s.name);
      rg.addEventListener("click", () => regenerate([i], rg));
      c.append(lk, nm, rg);
      strip.appendChild(c);
    });
    lane.append(lbl, strip);
    return lane;
  }

  // ---------------------------------------------------------------- session in the URL (#s=…&t=…)
  function updateHash() {
    if (!state.session || !state.song) return;
    const h = "#s=" + state.session.id + "&t=" + state.song.id;
    if (location.hash !== h) history.replaceState(null, "", h);
  }
  async function restoreFromHash() {
    const m = /s=([0-9a-f]{24})(?:&t=([0-9a-f]{16}))?/.exec(location.hash);
    if (!m) return;
    try {
      const sess = await api("/api/sessions/" + m[1]);
      state.session = sess;
      const id = m[2] && sess.takes.some((t) => t.id === m[2]) ? m[2] : sess.takes[sess.takes.length - 1].id;
      const t = await fetchTake(id);
      loadSong(t);
      setStatus("Restored " + t.label + " of your last song", "ok");
    } catch (e) {
      history.replaceState(null, "", location.pathname);
      if (e.code === "session_not_found") setStatus("Your previous song has expired. Generate a new one.");
    }
  }

  // ---------------------------------------------------------------- compare
  function pickB(a) {
    const takes = state.session.takes, ma = takes.find((t) => t.id === a);
    if (ma && ma.parent_id && takes.some((t) => t.id === ma.parent_id)) return ma.parent_id;
    const others = takes.filter((t) => t.id !== a);
    return others.length ? others[others.length - 1].id : null;
  }

  function showCompare(on) {
    state.cmp.on = on;
    $("comparePanel").classList.toggle("hidden", !on);
    $("compareBtn").setAttribute("aria-pressed", on ? "true" : "false");
    $("compareBtn").classList.toggle("on", on);
    if (on) {
      if (!state.session || state.session.takes.length < 2) { setStatus("Make another take first (＋ New take or ⟳ Regenerate) to compare.", "warn"); showCompare(false); return; }
      state.cmp.a = state.song.id; state.cmp.b = pickB(state.song.id);
      refreshCompare();
    } else { state.cmp.data = null; }
    if (state.song) renderTakes();
  }

  function fillCompareSelects() {
    const takes = state.session.takes;
    [["cmpA", state.cmp.a], ["cmpB", state.cmp.b]].forEach(([id, val]) => {
      const sel = $(id); sel.replaceChildren(...takes.map((t) => option(t.id, t.label + " · " + t.score)));
      if (val) sel.value = val;
    });
  }

  async function refreshCompare() {
    const c = state.cmp; if (!c.on) return;
    const ids = state.session.takes.map((t) => t.id);
    if (!ids.includes(c.a)) c.a = state.song.id;
    if (!c.b || !ids.includes(c.b) || c.b === c.a) c.b = pickB(c.a);
    fillCompareSelects();
    try {
      const [ta, tb, d] = await Promise.all([fetchTake(c.a), fetchTake(c.b),
        api("/api/sessions/" + state.session.id + "/compare?a=" + c.a + "&b=" + c.b)]);
      c.data = d;
      const sd = d.score.diff, sign = sd > 0 ? "+" : "";
      const sc = $("cmpScore");
      sc.innerHTML = "";
      const mk = (cls, txt) => { const e = document.createElement("span"); e.className = cls; e.textContent = txt; return e; };
      sc.append(mk("", "Critic " + d.score.a + " → " + d.score.b + " "), mk("diff " + (sd > 0 ? "up" : sd < 0 ? "down" : "same"), "(" + sign + sd + ")"));
      const fx = [];
      if (d.findings_only_a.length) fx.push(d.findings_only_a.length + " issue" + (d.findings_only_a.length > 1 ? "s" : "") + " fixed in B");
      if (d.findings_only_b.length) fx.push(d.findings_only_b.length + " new in B");
      if (fx.length) sc.append(mk("fx", " · " + fx.join(" · ")));
      sc.title = [...d.findings_only_a.map((f) => "only A: " + f.check + ": " + f.message), ...d.findings_only_b.map((f) => "only B: " + f.check + ": " + f.message)].join("\n");
      drawMini($("cmpCanvasA"), ta, d.sections);
      drawMini($("cmpCanvasB"), tb, d.sections);
      const box = $("cmpSections"); box.replaceChildren();
      if (d.sections) {
        d.sections.forEach((s) => {
          const e = document.createElement("span");
          e.className = "cmp-sec " + (s.identical ? "same" : "diff");
          e.textContent = (s.identical ? "＝ " : "≠ ") + s.name;
          e.title = s.identical ? "Identical notes in A and B" : "Changed tracks: " + s.changed_tracks.join(", ");
          box.appendChild(e);
        });
      } else {
        const e = document.createElement("span"); e.className = "cmp-sec diff"; e.textContent = "Different arrangement (form differs), so there is no per-section diff"; box.appendChild(e);
      }
      markAB();
      renderTakesQuiet();
    } catch (e) { showError(e); }
  }
  function renderTakesQuiet() { document.querySelectorAll("#takeTabs .tab").forEach((t) => t.classList.toggle("in-cmp", t.dataset.take === state.cmp.a || t.dataset.take === state.cmp.b)); }

  function markAB() {
    const cur = state.song && state.song.id;
    $("abA").classList.toggle("on", cur === state.cmp.a);
    $("abB").classList.toggle("on", cur === state.cmp.b);
    $("abA").setAttribute("aria-pressed", cur === state.cmp.a ? "true" : "false");
    $("abB").setAttribute("aria-pressed", cur === state.cmp.b ? "true" : "false");
    document.querySelectorAll(".cmp-row").forEach((r, i) => r.classList.toggle("on", (i === 0 ? state.cmp.a : state.cmp.b) === cur));
  }

  function drawMini(cv, t, secDiff) {
    const w = Math.max(200, cv.parentElement.clientWidth - 34), h = 64;
    const ctx = setupCanvas(cv, w, h), ppb = w / t.total_beats;
    ctx.fillStyle = "#11161d"; ctx.fillRect(0, 0, w, h);
    t.sections.forEach((s, i) => {
      const x = s.start * ppb, sw = (s.end - s.start) * ppb;
      if (secDiff && secDiff[i] && !secDiff[i].identical) { ctx.fillStyle = "#fbbf2418"; ctx.fillRect(x, 0, sw, h); ctx.fillStyle = "#fbbf24"; ctx.fillRect(x, 0, sw, 2); }
      if ((t.locked || []).includes(i)) { ctx.fillStyle = "#38bdf8"; ctx.fillRect(x, h - 2, sw, 2); }
      ctx.fillStyle = "#4b5563"; ctx.fillRect(Math.round(x), 0, 1, h);
      ctx.fillStyle = "#94a3b8"; ctx.font = "9.5px system-ui, sans-serif"; ctx.textBaseline = "top";
      if (sw > 30) ctx.fillText(s.name, x + 3, 3);
    });
    let lo = 127, hi = 0;
    t.tracks.forEach((tr) => { if (!tr.is_drums) tr.notes.forEach((n) => { lo = Math.min(lo, n[2]); hi = Math.max(hi, n[2]); }); });
    if (lo > hi) { lo = 36; hi = 84; }
    const top = 14, span = hi - lo + 1, rh = (h - top - 12) / span;
    t.tracks.forEach((tr, ti) => {
      const color = tr.color || trackColor(tr, ti, t.tracks);
      ctx.fillStyle = color; ctx.globalAlpha = 0.75;
      tr.notes.forEach(([s, d, p]) => {
        if (tr.is_drums) { ctx.fillRect(s * ppb, h - 10, Math.max(1, Math.min(3, d * ppb)), 6); return; }
        ctx.fillRect(s * ppb, top + (hi - p) * rh, Math.max(1, d * ppb - 0.5), Math.max(1, rh));
      });
    });
    ctx.globalAlpha = 1;
    cv.onclick = (ev) => { const r = cv.getBoundingClientRect(); player.seek(Math.round(clamp((ev.clientX - r.left) / ppb, 0, t.total_beats) * 4) / 4); };
  }

  function hear(which) {
    const id = which === "a" ? state.cmp.a : state.cmp.b;
    if (!id || (state.song && state.song.id === id)) return;
    showTake(id, { fromAB: true });
  }

  ["newTakeBtn", "regenBtn"].forEach((id) => { $(id).dataset.title = $(id).title; });
  $("newTakeBtn").addEventListener("click", newTake);
  $("regenBtn").addEventListener("click", () => regenerate(null));
  $("compareBtn").addEventListener("click", () => showCompare(!state.cmp.on));
  $("cmpClose").addEventListener("click", () => showCompare(false));
  $("cmpA").addEventListener("change", (e) => { state.cmp.a = e.target.value; if (state.cmp.b === state.cmp.a) state.cmp.b = pickB(state.cmp.a); refreshCompare(); hear("a"); });
  $("cmpB").addEventListener("change", (e) => { state.cmp.b = e.target.value; if (state.cmp.b === state.cmp.a) state.cmp.a = pickB(state.cmp.b); refreshCompare(); });
  $("abA").addEventListener("click", () => hear("a"));
  $("abB").addEventListener("click", () => hear("b"));

  // ---------------------------------------------------------------- transport wiring
  $("playBtn").addEventListener("click", () => { try { player.toggle(); } catch (e) { setStatus(e.message, "err"); } });
  $("stopBtn").addEventListener("click", () => player.stop());
  $("master").addEventListener("input", (e) => player.setMaster(Number(e.target.value)));
  $("zoom").addEventListener("input", (e) => {
    if (!state.song) return;
    const wrap = $("rollWrap"), centerBeat = (wrap.scrollLeft + (wrap.clientWidth - LABEL_W()) / 2) / state.ppb;
    applyZoom(Number(e.target.value)); buildRoll();
    wrap.scrollLeft = Math.max(0, centerBeat * state.ppb - (wrap.clientWidth - LABEL_W()) / 2);
  });
  let resizeT = null;
  window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (state.song) { fitZoom(); buildRoll(); if (state.cmp.on) refreshCompare(); } }, 150); });
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (e.code === "Space" && tag !== "input" && tag !== "select" && tag !== "textarea" && tag !== "button") {
      if (state.song) { e.preventDefault(); try { player.toggle(); } catch (err) { setStatus(err.message, "err"); } }
    }
    if ((e.key === "x" || e.key === "X") && state.cmp.on && tag !== "input" && tag !== "select" && tag !== "textarea") {
      hear(state.song && state.song.id === state.cmp.a ? "b" : "a");
    }
  });

  loadGenres();
})();
