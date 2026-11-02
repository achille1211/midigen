# midigen-ui

A small local web app for the [midigen](../midigen) multitrack MIDI generator. Pick a genre, key, mode, tempo,
length and seed, then generate. You get a per-track piano roll, playback in the browser and a `.mid` download.
After that you can **lock the sections you like and regenerate the rest**. Every try is kept as a **take** that you can
switch to, **A/B compare** or download.

There's no build step and no CDN. The backend is Python stdlib `http.server` and the frontend is one static
HTML/JS/CSS bundle, so everything works offline. midigen is used read-only from `../midigen/src` (nothing there is modified).

## Features

* **Describe it in words, or click a preset.** The top of the panel is a text box ("chill coffee shop music,
  30 seconds") and a row of mood/use-case presets. The genre/key/mode/tempo/length form is still there under
  **Advanced**. After every generation an **"Interpreted as: genre / key / mode / tempo / length"** line shows what was
  made: length in bars and seconds, plus the title and how the genre was chosen. If nothing in your words named a
  style or mood, it says plainly that it fell back to generic pop and offers presets. See
  [Prompt mode and presets](#prompt-mode-and-presets).
* **All library genres** (20+, packs included; the list follows the library), with plain-language names and descriptions ("Lo-fi hip hop:
  mellow, dusty beats for studying or relaxing"). The technical description is shown underneath in small print. Modes
  read as "major (bright / happy)" and "minor (darker / sadder)".
* **Friendly errors.** The page never shows raw JSON, tracebacks or Python error text. Every error code maps to a
  plain sentence, and unknown or 5xx errors get a generic "Something went wrong… try again, or pick a preset".
* **Generate → preview → play → download**, same as before. A first-time visitor sees only the form and an empty roll
  with three steps. The take and section tools appear after the first song.
* **Section lock + regenerate.** A *Sections* strip sits above the tracks and has one chip per arrangement section:
  * 🔒/🔓 locks a section. Locked sections are tinted blue and **their notes stay byte-identical** in every take you
    derive from this one.
  * ⟳ on a chip re-rolls just that section.
  * **⟳ Regenerate unlocked** (takes bar) re-rolls every unlocked section.
  * Regenerating never changes the take you started from. It creates a **derived take** that records its parent and
    keeps your locks. Freshly regenerated sections are tinted amber.
* **Takes.** Tabs show the take number, critic score and parent (`↳1`). There's also a **＋ New take** button
  (whole song, new seed, same genre/key/mode/tempo/bars), a × delete on each tab and a "Take 3 of 8" counter.
  Take numbers are never reused within a song: delete Take 4 and the next take is still Take 9, so a label always
  means the same music. The counter counts takes that still exist; at the limit the buttons dim and explain why.
  Each take has its own download URL and file name (`…_take3.mid`). Switching takes keeps mute/solo, scroll and the
  playhead, so playback carries on from the same spot.
* **Compare.** **⇄ Compare** opens a panel where you pick take A and take B:
  * stacked mini piano rolls, with sections that differ outlined in amber and locked sections underlined in blue
  * a per-section *＝ same / ≠ changed* row (the tooltip lists the changed tracks)
  * the critic score diff (`Critic 92 → 100 (+8)`) and which findings were fixed or introduced
  * **Hear A / Hear B** (or the <kbd>X</kbd> key) swaps the song under the running transport without losing the
    playhead, so you can A/B in place
* **Limits** come from `config.json`: max bars, takes per song, active sessions, generations per hour per IP and
  file retention. The UI shows usage ("Take 3 of 8 · 5/30 generations this hour"). At a limit you get a plain message
  such as *"You've used all 30 generations for this hour. Try again in 12 min."* or *"This song already has 8 takes…
  Delete a take you don't need to make room."*
* The session is kept in the URL hash (`#s=<session>&t=<take>`), so a reload brings back your takes until the
  session expires.

## Run

```bash
cd /workspace/midigen-ui
python3 -m venv .venv                       # once
.venv/bin/pip install -r requirements.txt   # once: mido + jsonschema (midigen's only runtime deps)
.venv/bin/python app.py                     # -> http://localhost:8766/
```

Options:

| flag | default | meaning |
|---|---|---|
| `--port N` | `8766` (or `$PORT`; the REST service uses 8765) | port to listen on |
| `--host H` | `127.0.0.1` (or `$HOST`) | bind address; use `0.0.0.0` to expose on the LAN |
| `--midigen-src PATH` | `../midigen/src` (or `$MIDIGEN_SRC`) | where the `midigen` package lives |
| `--config PATH` | `./config.json` | limits file (see below); a missing file means built-in defaults |
| `--quiet` | off | no request log |

If midigen is already pip-installed (`pip install -e /workspace/midigen`) in the interpreter you use, that install is
imported and `--midigen-src` is just a fallback. Plain `python3 app.py` works too if `mido` and `jsonschema` are
installed system-wide; otherwise the app exits with a message telling you to create the venv.

Tests: `.venv/bin/python test_app.py` (also runs under pytest). It starts the server on a free port and covers:
* every endpoint
* the downloaded `.mid` files, parsed with mido
* CLI parity (`midigen new --genre …` writes the same notes for the same parameters)
* lock semantics (locked sections identical on note data and in the MIDI; unlocked sections changed)
* native-vs-splice regeneration, unique take ids, per-take downloads and delete
* the take, rate and session limits, and config defaults and validation

The tests use built-in defaults, not your `config.json`.

## Config (`config.json`)

All keys are optional. A missing file, or a missing or invalid key, falls back to the default below (a warning is
printed at startup for bad values or unknown keys). Keys starting with `_` are comments.

| key | default | meaning |
|---|---|---|
| `min_bars` / `max_bars` | `2` / `32` | requested length is clamped to this range; a note tells the user when the max was applied |
| `default_bars` | `16` | used when a request has no `bars` |
| `max_takes_per_song` | `8` | takes per session (generate, new take and regenerate all add one). Deleting a take frees a slot. Over the limit → `409 take_limit` |
| `max_active_sessions` | `50` | songs held in memory at once (expired ones are purged first). Over the limit → `503 session_limit` |
| `session_ttl_minutes` | `120` | **file retention**: a session and its `.mid` files are deleted this long after last use. Everything is removed on shutdown |
| `generations_per_hour_per_ip` | `30` | rolling 1 h window per client IP, counting generate + new take + regenerate. Reads, downloads, locks and compare are free. Over the limit → `429 rate_limit` with `Retry-After`. `0` = unlimited |
| `regen_candidates` | `3` | candidates rendered per regenerate; the one with the best critic score is kept (1–8) |
| `trust_proxy_headers` | `false` | use `X-Forwarded-For` as the client IP (only behind a trusted reverse proxy) |

Downloads are `.mid` only (no audio rendering, no spec/JSON export). The effective limits are at `GET /api/config`.



## Prompt mode and presets

`POST /api/generate` with `{prompt, preset?, seed?}` (no `genre`) runs the library's own prompt entry point,
`midigen.llm.Composer(use_env=False, record=False).generate(prompt, seed)`. That is what `midigen new "<prompt>"`
runs offline, so improvements to the library's parser show up here without UI changes. `record=False` only stops
the web app from writing the library's learning database; the music is identical. On top of the parsed song the UI
layer (`app.py` → `generate_prompt`) applies these steps, in order:

1. **Preset settings.** Each preset (`presets.py`) carries explicit `genre`, `mode`, `tempo` and target `seconds`,
   and optionally `roles` / `exclude_roles` / GM `programs` (e.g. 8-bit uses square/saw leads, sad piano is
   piano-only, lullaby has no drums). It also carries its prompt text. So a preset gives the intended result even
   when the offline parser doesn't know its words ("horror" alone parses as pop).

   | preset | genre · mode · tempo · length |
   |---|---|
   | 🧘 Calm focus | ambient · major · 66 · 60 s |
   | ⚔️ Tense boss fight | cinematic · harmonic minor · 140 · 45 s |
   | ☕ Chill coffee shop | lo-fi hip hop · major · 82 · 60 s |
   | 🎬 Upbeat vlog | pop · major · 124 · 30 s |
   | 🎹 Sad piano | pop · minor · 72 · 45 s, piano only |
   | 🎥 Epic trailer | cinematic · minor · 96 · 45 s |
   | 🎙️ Podcast intro (15 s) | pop · major · 128 · 15 s |
   | 🌙 Lullaby | ambient · major · 58 · 45 s, no drums |
   | 👻 Horror | cinematic · harmonic minor · 62 · 45 s |
   | 👾 8-bit game | synthwave · major · 120 · 20 s, square/saw leads |
   | 🏆 Victory fanfare | cinematic · major · 110 · 10 s |

   Clicking a preset fills the box with its text and generates. Editing the text drops the preset, and the text is
   then used on its own.
2. **Spoken duration.** "15 second(s)", "30s", "2 minutes", "half a minute", "one minute", "1:30" become bars:
   `round(seconds × tempo / 60 / beats per bar)`. "80s" / "90s" are treated as decades, not durations. A duration
   typed in the box beats the preset's length. "N bars" is handled by the library parser itself.
3. **Server limits.** Bars are clamped to `min_bars`–`max_bars`, with a note. When no length was given and the
   library's full-length song (usually 44–144 bars) is over `max_bars`, the sketch uses `default_bars` and the note
   says how to ask for a length.

If any of these changed the parsed song, it is rebuilt with `build_spec`, keeping the prompt's title and its
"just piano" / "no drums" choices, and refined with the same offline critic loop. Otherwise the parsed spec is used
as is: the notes are identical to the library's (tested).

**Fallback honesty.** The library never rejects a prompt: unknown words give generic pop. The UI checks whether a
genre word (`detect_genre`) or a mood/use phrase (`nl.MOOD_GENRE`) matched. If neither did and no preset was used,
`interpreted.fallback` is true and the page says so and suggests presets. A `NotUnderstood` from the library maps to
code `not_understood` with example prompts. **＋ New take** on a prompt song re-runs the same prompt/preset with a new
seed (key/mode pinned to Take 1's). Regenerated sections work as before.

Every take JSON has an `interpreted` block: `{line, genre, genre_label, genre_plain, key, mode, mode_plain, tempo,
bars, seconds, title, source: prompt|preset|manual, prompt?, preset?, recognised?, fallback, fallback_message,
suggested_presets, explanation, requested_seconds, duration_from, rebuilt}`.

**Why the UI used to show 16 genres while the CLI showed 20:** `mg.list_genres()` defaults to `include_packs=False`
(built-in genres only), while `midigen genres` uses `mg.available_genres()` (= `list_genres(include_packs=True)`).
The UI now uses `available_genres()`, so it always matches the CLI, including any packs added later.

## Using it

* **Describe the music** in the box and press **Make music** (or <kbd>Enter</kbd>; <kbd>Shift</kbd>+<kbd>Enter</kbd>
  adds a new line), or click a preset. **Advanced** opens the manual form below; its button is "Generate with these settings".

* **Genre** sets the typical key list, mode list and tempo range (slider spans the genre range; the number box accepts 20–300).
* **Key** "Auto" lets midigen pick from the genre's keys (depends on the seed, like the CLI).
* **Length** is bars (2–32 by default, see `max_bars`). midigen fits its form to roughly that length; the actual bar count is shown above the roll.
* **Seed**: blank picks a random one (shown in the info bar so you can reproduce it); 🎲 fills a random seed in.
* Piano roll: one canvas lane per track, sections, bar numbers and chord symbols on the ruler, drums drawn as one lane per
  drum sound (GM names). Click anywhere on the roll to seek. Zoom with 🔍.
* Transport: ▶/❚❚ (or <kbd>Space</kbd>), ■ stop, Loop, master volume, per-track **M**ute / **S**olo (live while playing).
* **Download Take N .mid** gets the file exported for the take on screen (type-1, one track per instrument, drums on
  channel 10). For a regenerated take it is built from the merged notes, exactly what the roll shows and the player plays.
* **Sections strip**: 🔒 lock, ⟳ regenerate one section. **⟳ Regenerate unlocked**, **＋ New take** and **⇄ Compare** are in the
  takes bar under the transport.

## API

Errors are always `{"error": {"status", "message", "field"?, "code"?, …}}`. Codes: `rate_limit` (429, plus
`retry_after_s`), `take_limit` (409), `session_limit` (503), `session_not_found` / `take_not_found` (404),
`locked` / `all_locked` / `last_take` (409), `empty_prompt` / `prompt_too_long` / `unknown_preset` / `unknown_genre`
(400; `empty_prompt` adds `examples`), `not_understood` (422, plus `examples`), `internal` (500; plain message,
technical text only in `detail`, which the page doesn't show).

| method & path | description |
|---|---|
| `GET /` | the UI (`static/index.html`; assets under `/static/`) |
| `GET /api/genres` | `{genres:[{name, display, description, label, plain, tempo:{min,max,default}, modes, keys, time_signature, defaults, roles}], modes, mode_labels, keys, limits, presets:[{id, label, emoji, prompt, settings}], examples}`. All library genres, packs included |
| `GET /api/config` | `{limits:{bars:[min,max], default_bars, tempo, seed, max_takes_per_song, max_active_sessions, session_ttl_minutes, generations_per_hour, regen_candidates, downloads:["mid"]}, usage:{generations_used, generations_limit, generations_left, resets_in_s}, config_file, active_sessions}` |
| `POST /api/generate` | manual: body `{genre, key?, mode?, tempo?, bars? (alias length), seed?}`; prompt: `{prompt, preset?, seed?}` (see [Prompt mode](#prompt-mode-and-presets)) → **new session** with Take 1 (take JSON, below, including `interpreted`) |
| `GET /api/sessions/<sid>` | session summary: `{id, params, takes:[take meta], take_count, max_takes, expires_in_s}` |
| `DELETE /api/sessions/<sid>` | drop the session and its files |
| `POST /api/sessions/<sid>/takes` | **new take**: same settings, new seed (key/mode pinned to Take 1's) → take JSON |
| `GET /api/sessions/<sid>/takes/<tid>` | full take JSON (notes etc.) |
| `GET /api/sessions/<sid>/takes/<tid>.mid` | that take's MIDI file (`audio/midi`, attachment, `…_take<N>.mid`) |
| `POST /api/sessions/<sid>/takes/<tid>/regenerate` | body `{sections?: [idx…], locked?: [idx…]}` → **derived take**. Without `sections`, every unlocked section is regenerated. Without `locked`, the parent's saved locks are used. The given locks are saved on the parent |
| `POST /api/sessions/<sid>/takes/<tid>/locks` | body `{locked:[idx…]}`: save a take's locks (no generation) → take meta |
| `DELETE /api/sessions/<sid>/takes/<tid>` | delete a take and its file (alias `POST …/<tid>/delete`). The last take can't be deleted → 409 |
| `GET /api/sessions/<sid>/compare?a=<tid>&b=<tid>` | `{a, b, score:{a,b,diff}, same_form, sections:[{index,name,identical,changed_tracks}], findings_only_a, findings_only_b, note_counts}` |
| `GET /api/download/<tid>.mid` | legacy alias for a take's MIDI (kept for old links) |
| `GET /api/health` | `{ok:true}` |

Take JSON (abridged):

```jsonc
{
  "id": "9f2c…(16 hex)", "take_id": "…", "session_id": "…(24 hex)", "number": 2, "label": "Take 2",
  "kind": "generate | new_take | regenerate", "parent_id": "…|null", "parent_label": "Take 1|null",
  "locked": [0, 2], "regenerated": [1], "regenerated_names": ["groove"], "section_seeds": {"groove": 123456},
  "spec_exact": true, "score": 100,
  "download_url": "/api/sessions/<sid>/takes/<tid>.mid", "filename": "house_A_minor_124bpm_s7_take2.mid",
  "genre": "house", "key": "A", "mode": "minor", "tempo": 124, "time_signature": [4, 4], "seed": 7,
  "bars": 16, "bar_beats": 4, "total_beats": 64,
  "note_format": ["start_beat", "duration_beats", "pitch", "velocity"],
  "sections": [{"index": 0, "name": "intro", "start": 0, "end": 16, "bars": 4, "energy": 0.35}, …],
  "chords":   [{"start": 0, "end": 4, "symbol": "i9", "section": "intro"}, …],
  "tracks": [{"name": "Drums", "role": "drums", "channel": 9, "program": 0, "volume": 100, "pan": 64,
              "is_drums": true, "pitch_range": [36, 46], "notes": [[0.0, 0.225, 36, 112], …]}, …],
  "critique": {"score": 100, "counts": {…}, "findings": [...], "summary": "critic score: 100/100\n…"},
  "session": {…session summary…}, "usage": {…}, "request": {…normalised inputs…},
  "notes": ["tempo 999 clamped to 20-300 BPM", …], "elapsed_ms": 20.1
}
```

Beats are midigen "spec beats" (time-signature beats, which are quarter notes in 4/4). `tempo` is in those beats,
so playback uses `60/tempo` seconds per beat.

Validation: these return `400 {"error":{"status","message","field"}}`:
* unknown genre
* bad key (`^[A-G][#b]?$`, case-insensitive first letter)
* unknown mode
* non-numeric tempo, bars or seed, and negative seeds
* section indices out of range

Tempo is clamped to 20–300 (going outside the genre's typical range is allowed but noted). Bars are clamped to
`min_bars`–`max_bars`. Other errors: bad JSON → 400, oversized body → 413, unknown id → 404, generator spec errors →
422, anything else → 500 with a JSON message. Rejected requests don't count against the hourly limit.

## How lock / regenerate works (native vs splice)

midigen has no "regenerate one section" call, but its renderer seeds every part independently:
`arranger.render` seeds each (track, section) part with `stable_seed(spec.seed, params.seed, track, section name)`,
where `params` is the track's `generator` merged with `overrides[section name]`. So the app does a **hybrid**:

1. **Native variation.** For each target section it picks a fresh random seed and writes it to
   `track.overrides[<section name>].seed` for *every* track of a copy of the parent's spec. Then it calls
   `mg.render(spec)`. Only parts in that section get new seeds, so only they change. Locked sections render exactly
   as before. This is also how midigen's own `refine` re-rolls whole tracks.
2. **Splice for the guarantee.** Some whole-track post-passes (anticipation, duplicate-note merge) could in theory
   touch a note at a boundary, and an arrangement can repeat a section name (overrides are per *name*). So the
   result is spliced at the Performance level:
   * every note whose onset is in a locked or untouched section is copied verbatim from the parent
   * target-section notes come from the new render, cut at the end of the regenerated run
   * new notes that duplicate a kept note at a boundary are dropped

   Locked notes are therefore byte-identical by construction (tested on API note data and on the `.mid`).
3. **Best of N.** It renders `regen_candidates` (default 3) seeds and keeps the candidate where every target section
   actually changed and the critic score (`mg.critique` on the merged Performance) is highest.
4. **Merged result everywhere.** The piano roll, playback, critic score and `.mid`
   (`mg.export_midi(merged_performance)`) all use the same merged notes.
5. `spec_exact` on a take is true when re-rendering the take's own spec reproduces its notes (the usual case). It is
   false when a repeated section name forced the splice to keep one occurrence and re-roll another. Then the take's
   notes exist only as the merged Performance (which is what the `.mid` contains).

`mg.refine` is not used on regenerate because it reseeds whole tracks and would change locked sections. A new song
or new take still goes through `build_spec → refine → render`, exactly like `midigen new` (CLI parity is tested).

## Architecture

```
browser (static/index.html + app.js + style.css)
   │  /api/genres, /api/config, /api/generate, /api/sessions/<sid>/takes/…
   ▼
app.py  (ThreadingHTTPServer, stdlib only)
   ├─ validate_request()   clamp / default / reject inputs
   ├─ generate()           mg.build_spec(...) → mg.refine(spec)   ← same as `midigen new --genre` (cmd_new)
   │                       → mg.render(spec) → Performance (tracks, notes in beats, sections, harmony)
   ├─ regenerate_sections() per-section seed overrides → mg.render → splice(parent, new, targets) → best of N by critic
   ├─ SessionStore         random session ids → ordered takes (spec, merged Performance, report, locks, parent id);
   │                       in memory + one temp dir per session; TTL purge + session cap
   ├─ RateLimiter          rolling 1 h window per IP for generate / new take / regenerate
   ├─ mg.export_midi(perf) → <session dir>/<take id>.mid (built from the merged Performance)
   └─ perf_payload()       Performance + mg.critique report → JSON
```

* **Matching the CLI**: `cmd_new` runs `build_spec` then `llm.refine` (the deterministic, offline critic loop that reseeds
  tracks the critic flags) before writing. The API does the same, so the same inputs give note-for-note the same `.mid`
  as `midigen new --genre G --key K --mode M --tempo T --bars N --seed S` (checked in `test_app.py`). No LLM is called.
* **Modes** come from midigen's spec schema enum (`major, minor, dorian, phrygian, lydian, mixolydian, locrian,
  harmonic_minor, ionian, aeolian`).
* **Frontend** (`static/app.js`, plain ES2017, no libraries). Takes are cached client-side by id. Switching or A/B
  swaps `state.song` and restarts the scheduler at the current beat (`player.reload`).
  * piano roll: one `<canvas>` per track (HiDPI-aware, width = beats × zoom, capped at 30k px), redrawn only on
    generate/zoom/resize/lock toggles; the playhead is a separate absolutely-positioned line moved with `requestAnimationFrame`, and the view auto-scrolls to follow it.
  * synth: Web Audio with a look-ahead scheduler (25 ms tick, 150 ms horizon) using `AudioContext.currentTime` and the song tempo.
    Pitched tracks use 2-oscillator voices → low-pass filter (with a filter envelope) → ADSR gain, with the timbre picked by
    role and GM program family (bass, pad/strings, piano, bells, organ, guitar, brass, reeds, leads, arps). Drums
    (channel 10) are synthesized per GM pitch: sine-sweep kick, noise+tone snare, clap bursts, filtered-noise
    hats/ride/crash/shaker/tambourine, pitched toms, rim, cowbell. Each track has its own gain (mute/solo) and stereo pan,
    into a master gain and a compressor.

## Files

```
app.py            HTTP server + API, sessions/takes, lock/regenerate, limits (stdlib only)
presets.py        UI-layer presets, example prompts, plain-language genre/mode labels
config.json       limits (optional; defaults built in)
static/index.html UI markup
static/app.js     controls, piano roll, section strip, takes, compare, Web Audio player
static/style.css  dark theme
test_app.py       integration tests
requirements.txt  mido, jsonschema
```

## Limitations / caveats

* Prompt understanding is the library's offline keyword parser. Words it doesn't know fall back to generic pop (the
  page says so). Very short presets (10–20 s) can come out as an intro/outro-style form without the lead melody.
* Lock and regenerate work on whole arrangement sections. You can't lock individual tracks or bars, and all tracks of
  a section are re-rolled together. Regenerated sections keep their chords, length and energy; only the parts change.
* Regenerated takes skip midigen's `refine` loop (it would change locked parts), so their score can be lower than a
  fresh take's. Best-of-N picks the best candidate. Regenerate again, or try ＋ New take.
* A new take keeps Take 1's key and mode, even if they were "Auto", so takes stay comparable. To change key or mode,
  generate a new song.
* Compare's per-section diff needs both takes to have the same form. Takes from the same session always do, because
  bars and genre are fixed.
* Everything is in memory: restarting the server loses all sessions. Takes expire `session_ttl_minutes` after last use.
* Browser playback is a simple built-in synth for previewing, not a GM sound set. The `.mid` file sounds as good as
  the synth you open it in.
* Browsers only start audio after a user gesture, so the first ▶ press creates the AudioContext.
* Very long songs at high zoom are capped at ~30k px of canvas per lane.
* The rate limit is per IP. Behind a proxy, set `trust_proxy_headers` (otherwise every client shares the proxy's IP).
* The server is meant for local use (no auth). Keep the default `127.0.0.1` bind unless you trust the network.
