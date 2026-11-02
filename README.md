# midigen

Theory-driven **multitrack MIDI generation and arrangement**, with a conversational layer that lets an LLM
(or plain English, offline) create and edit whole songs, packaged as a library you can embed in other apps.

**How it divides the work.** LLMs write unreliable note lists, so midigen never asks them to. The LLM (or the offline
parser) makes **spec-level** decisions: genre, form, sections, energy, roman-numeral harmony, cadences,
instrumentation and small edit ops. A **deterministic music-theory engine** writes every note, and a **rule-based critic**
checks the result and sends its findings back to the LLM.

```
prompt / instruction ──► LLM or offline parser ──► spec JSON or edit ops ──► validate ─┐ errors → retry
                                                                                       ▼
                     theory engine: harmony · melody · arranger · grooves ──► Performance ──► critic ─┐ findings → revise
                                                                                       ▼
                                                     .mid (one track per instrument, drums on ch 10) · .png piano roll
```

## Quick start (no setup needed)

### Delivery products

`midigen deliver` turns a saved song into a portable product folder and matching ZIP:

```sh
midigen new "calm piano for a podcast intro" --offline --seed 23 --bars 16 -o cue
midigen deliver cue.json --product creator --out creator-pack
midigen deliver cue.json --product producer --out producer-kit
midigen deliver cue.json --product rehearsal --out rehearsal-pack
```

- **Creator Cue Pack:** full MIDI cue plus 15/30/60-second cutdowns when the source is long enough.
- **Producer MIDI Kit:** full MIDI arrangement and isolated MIDI for every active instrument.
- **Rehearsal Pack:** full MIDI arrangement, isolated parts and a practice take at 80% tempo.

Every pack includes the editable song JSON, a piano-roll PNG, a critic report, and a manifest
with tempo, duration, track names and SHA-256 file checksums. Existing output folders and ZIPs
are protected from overwriting. Add `--audio --backend numpy` for WAV copies after installing
`pip install -e ".[audio]"`; FluidSynth is also supported through `--backend fluidsynth`.

Cutdowns truncate the cue; they do not compose an ending or guarantee seamless loops.
Rehearsal packs contain MIDI rather than notation and need player review. Solo brass, winds and
vocal lines now receive conservative range, monophony and breathing constraints. Ensemble patches
and idiomatic instrument technique still need human arrangement. See [PRODUCTS.md](PRODUCTS.md)
for the product scope and next development candidates.

The web UI now accepts plain-language edits such as “make it sadder” and “busier bass” in
**Edit current take**. Each successful edit creates a new take; the original remains available.
Unlock sections first, and choose a take with an exact song spec if section regeneration merged
different performances. PNG previews are included in the standard installation.

### Install and generate

1. Download midigen (on GitHub: green **Code** button, then **Download ZIP**) and unzip it.
2. You need Python 3.10 or newer. Check with `python3 --version` (Windows: `py --version`); if it's missing or older,
   get it from [python.org/downloads](https://www.python.org/downloads/) (Windows: tick "Add python.exe to PATH").
3. Run the installer from the unzipped folder:
   - **Windows:** double-click `install.bat`
   - **macOS / Linux:** open a terminal in the folder and run `./install.sh` (or `python3 install.py`)

   It creates a private `.venv` folder (nothing system-wide changes), installs midigen, writes a test song
   `demo.mid` and checks the setup. Add `--audio` for WAV rendering or `--stream` for live MIDI out.
4. Use it (the installer prints the exact paths):
   - `.venv/bin/midigen ui` (Windows: `.venv\Scripts\midigen ui`) opens the bundled web UI (`ui/`) at http://localhost:8766/
     (the REST service, if installed, uses 8765)
   - `.venv/bin/midigen new "chill lofi beat in D minor" -o mysong` writes `mysong.mid`, which any DAW opens
   - `.venv/bin/midigen doctor` explains, in plain English, anything that isn't working

The installer has been tested on Linux only so far; macOS and Windows should work but are untested.

## Install (developers)

```bash
cd midigen
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[preview,mcp,dev]"      # core needs only mido + jsonschema
pytest -q                                # 321 tests incl. integrations (audio tests need numpy, scipy and soundfile) (pack tests skip if the pack folders are absent)
python demo/make_demo.py                 # offline demo songs in demo/
```

## CLI

```bash
midigen new "a sad lofi hip hop beat in D minor at 82 bpm, 16 bars" -o song --png   # song.json + song.mid + song.png
midigen new --genre techno --key A --mode minor --bars 32 -o warehouse
midigen edit song.json "make it faster and add strings and jazzier chords in the chorus"
midigen edit song.json --ops '[{"op":"reharmonize","technique":"secondary_dominants","section":"b"}]'
midigen render song.json --png          # spec -> .mid (+ png)
midigen inspect song.json               # sections, chords, tracks, critic report
midigen inspect song.mid --to-spec imported.json   # read any .mid; import into an editable spec
midigen critique song.json [--json]
midigen genres [house]                  # list genres (with their pack source), shadowed id@pack versions, load failures
midigen --genre-path ~/my-packs new --genre mygenre -o x   # extra genre pack folder/file (repeatable, any position)
midigen new --genre rock --bars 2 --random-seed -o riff  # exact bar count; fresh seed printed so you can reproduce it
midigen chat --spec mysong              # REPL: describe a song, then edit it; /undo /redo /show /critic /help
```

**Arguments and defaults.** `new` checks its arguments and exits with `error: ...` (exit code 1, no traceback, no files written) for
tempo outside 20–300, bars outside 1–256, an unknown key (A–G with optional `#`/`b`), mode, or genre. Bars are exact: `--bars 2` gives
2 bars (the form is trimmed or stretched to fit). `--seed` defaults to **1**, so the same command always gives the same song;
`--random-seed` picks a fresh seed and prints it. Genre names accept ids, kebab ids, display names and aliases (`Big Band`,
`lofi-hip-hop`, `Drum and Bass`) or `id@pack` for a shadowed pack version (`jazz@midigen-knowledge`). An id or display name always
beats another genre's alias. Explicit `--tempo/--key/--mode/--bars` override whatever the prompt says.

Add `--offline` to skip the LLM even when one is configured. Without a provider, everything runs on the offline rules.

Offline phrases the parser understands include: *faster/slower, 120 bpm, in E minor, transpose up 2, add strings/an arp/a pad/a walking
bass, remove/mute/solo the X, louder/quieter, busier/sparser (in the chorus), more/less swing, humanize, jazzier/richer chords,
secondary dominants, borrowed chords, simpler chords, chords to Am F C G in the chorus, end the chorus with a plagal cadence,
extend the chorus by 4 bars, repeat the chorus, new melody, make it house*, and others (see `midigen chat` then `/help`).
**Plain-language extras.** Prompts print how they were read, for example
`Interpreted as: calm ambient · E major · 55 BPM · 34 bars ≈ 2:28 · solo piano, no drums`, along with any typo fixes ("chil → chill")
and words that weren't understood. Moods and use-cases (sad, tense, boss fight, meditation, 80s driving, jingle, study…) pick a genre,
mode and tempo. Solo instruments (piano, violin, cello, guitar, harp, flute…) give a stripped-back arrangement. "30 seconds" or "2 minutes" sets
the length (also `--seconds N`). With no length given, defaults longer than 5 minutes (slow ambient) are cut to about 2½ minutes.
Edits also accept *make it shorter/longer, make it 30 seconds, more energy at the end, loop it (drops the intro and outro), fade out
(lowers the last section's energy and density; this is not a volume fade), more intense, more/fewer drums, fewer instruments, add violin/cello,
and bare moods or genres (sad, chill, upbeat, jazz)*. A typo gets one retry ("fastr" works). If a phrase still isn't understood,
the error suggests close matches. "add drums" when the song already has drums explains what to try instead. New tracks are named
after their instrument.
`new` without `-o` names the files from the title and never overwrites (`-1`, `-2`…); with `-o` it says when it overwrites. Editing
in place keeps `<name>.prev.json`. Output is one summary line (critic minor notes behind `-v`). Missing files, non-JSON or binary
files and broken JSON give a one-line error instead of a traceback.

**Linked tracks.** An edit to one part can suggest matching changes to the parts that depend on it (drums → bass/comping,
tempo → lead density, chords → bass/pad, swing → everything that grooves). Suggestions are checked with the critic and only kept
if the score does not drop; each says which track leads, which follows, and the score before and after.
`midigen edit song.json "busier drums" --suggest` lists them without applying; `--auto-follow` applies the top two.
In `midigen chat`, `/suggest` lists suggestions for the last edit and `/follow N` (or `/follow all`) applies them.
From Python: `midigen.suggest_followups(spec, edit)` or `Composer.edit(spec, text, auto_follow=True)` (the result's `.followups`).
The MCP server has a `suggest_followups` tool. The REST service does not expose this yet.

Words like "chorus", "drop" and "verse" resolve to the matching section, or to the highest/lowest-energy section if the form uses other names.

## LLM provider (optional)

Any OpenAI-compatible `/chat/completions` endpoint works: OpenAI, OpenRouter, Groq, Together, or a local Ollama, LM Studio or vLLM server.

| env var | meaning |
|---|---|
| `MIDIGEN_API_KEY` | API key (may be omitted for local servers) |
| `MIDIGEN_BASE_URL` | e.g. `https://api.openai.com/v1`, `http://localhost:11434/v1` (default: OpenAI) |
| `MIDIGEN_MODEL` | model name (required; the provider is enabled only when this is set) |

No keys are stored in the code.

**Reliability loop** (`midigen.llm.Composer`):
1. The prompt contains the current spec, the genre's knowledge-base profile, the theory rules (roman-numeral conventions,
   functional harmony, cadences, layering, registers), the full vocabulary (styles, drum patterns, instruments) and the op catalogue.
2. The LLM answers `{"action":"ops","ops":[…]}` (preferred: a small edit) or `{"action":"spec","spec":{…}}`. For new songs it
   **edits a theory-built draft** rather than writing from scratch.
3. Each reply is parsed (code fences tolerated) and checked against the JSON schema, the semantics and chord parsing. If it fails,
   the exact errors go back to the LLM, up to `max_retries` (default 3).
4. The result is rendered and critiqued. Warnings go back to the LLM for fixing ops, and a revision is kept only if the critic score does not drop.
5. If the provider is unreachable or keeps failing, midigen falls back to the offline parser (`source="offline-fallback"`).

## Embedding the library

```python
import midigen as mg

spec = mg.build_spec("house", key="A", mode="minor", bars=32, seed=7)      # plain JSON-serialisable dict
spec = mg.apply_ops(spec, [                                               # validated, pure (input untouched)
    {"op": "reharmonize", "technique": "9ths"},
    {"op": "add_instrument", "role": "arp", "sections": ["drop"]},
    {"op": "set_cadence", "section": "drop", "cadence": "deceptive"},
])
report = mg.critique(spec)            # {"score": 0-100, "findings": [...], "stats": {...}}
perf = mg.render(spec)                # Performance: tracks -> Note(start, dur, pitch, vel) in beats
mg.export_midi(spec, "song.mid")
mg.piano_roll(spec, "song.png")       # needs matplotlib
info = mg.inspect_midi("any.mid"); spec2 = mg.import_midi("any.mid")

ed = mg.Editor(spec)                  # undo/redo history
ed.apply([{"op": "transpose", "semitones": -2}]); ed.undo(); ed.redo()

comp = mg.Composer()                  # LLM from env vars, or offline if none
r = comp.generate("dreamy synthwave, 16 bars"); r = comp.edit(r.spec, "darker, borrowed chords in the chorus")
r.spec, r.ops, r.report, r.source, r.log
# bring your own client: any object with .chat(messages) -> str
comp = mg.Composer(provider=MyClient())
```

The schemas `mg.SPEC_SCHEMA`, `mg.OP_SCHEMAS` and `mg.OPS_SCHEMA` are plain JSON Schema, so you can use them for function calling or structured outputs.

To use midigen from other apps (REST service, TypeScript client, REAPER script, live MIDI streaming), install the
add-on package with `pip install -e integrations/python lupa` (add `"integrations/python[service,test]"` for the
REST service and its tests, `[stream]` for python-rtmidi); see [integrations/README.md](integrations/README.md).
Its tests run with the main suite from the repo root and are skipped when it isn't installed.

## MCP server

`midigen-mcp` (or `python -m midigen.mcp_server`) runs over stdio. Its tools are `generate_song`, `edit_song` (natural language,
JSON ops, or `undo`/`redo`), `export_midi`, `inspect` (a song or any .mid), `load_song`, `list_ops`, `genre_profile`, and for the learning loop `rate_song`, `song_feedback`, `learn_status`, `learn_reference` and `train`. Files are written to
`MIDIGEN_WORKDIR` (default `./midigen_songs`).

```json
{
  "mcpServers": {
    "midigen": {
      "command": "/ABSOLUTE/PATH/midigen/.venv/bin/midigen-mcp",
      "env": { "MIDIGEN_WORKDIR": "/ABSOLUTE/PATH/songs" }
    }
  }
}
```

The MCP client's own model does the creative reasoning, and midigen validates its choices and renders them.

## Learning loop

midigen learns what *you* like, locally and on the CPU, with no API keys. Every generated or edited song is recorded.
Ratings, notes, keep/reject and edits then update a per-genre policy, which `build_spec` samples from on later runs.

```bash
midigen new --genre pop -o a          # prints "song id 3f2c… — rate it: midigen rate 3f2c… 1-5"
midigen rate a.json 4 --note "love the bass, busier drums please"
midigen rate last 2 --track Bass      # feedback on one track (or --section chorus)
midigen feedback last "too sparse in the verse" --section verse
midigen keep last  /  midigen reject last --note "boring"
midigen learn ref.mid --genre lofi_hiphop   # learn progressions, bass/lead rhythm cells, drum grids, tempo/swing from a MIDI
midigen learn status [--genre pop] [--json]
midigen learn export weights.json  /  midigen learn import weights.json  /  midigen learn reset --genre pop [--all]
midigen train --rounds 5 --candidates 8 --genres pop,funk --chart curve.png   # self-play against the critic
midigen new --genre pop --no-learn    # ignore what was learned (also: MIDIGEN_LEARN=0, build_spec(..., learn=False))
```

In chat, use `/rate N [note]`. In the library, use `mg.rate(ref, 4, note=…)`, `mg.feedback`, `mg.keep`, `mg.reject`, `mg.thumbs`,
`mg.learn_midi`, `mg.train` and `mg.learn_status`. `ref` can be a song id, `last`, a `.json` path or a spec dict.
For UIs, `midigen.learn.handle({"action": "rate", "song": id, "rating": 4})` is a JSON-in/JSON-out API. Its actions are
record, rate, thumbs, keep, reject, feedback, edit, learn_midi, status, preferences, few_shot, songs, reset, export and train.

**How it works**
- An *arm* is a discrete generator choice, such as bass style = octave, chorus progression, drum grid, lead rhythm cells,
  density, energy curve or tempo nudge.
- Each genre keeps Beta statistics per arm, with user and critic evidence kept separately.
  User evidence weighs 4× more and decays by 0.97 per update. Critic evidence decays by 0.995 and is capped.
- `build_spec` Thompson-samples arms only once a genre has learned data. The sampling is seeded, so the output is
  deterministic for a given weights state. With an empty store the output is unchanged, and recording alone never changes weights.
- Explicit arguments (tempo, roles) are never overridden.
- Notes are mapped to dimensions ("busier", "more swing", "darker", "boring bass"). Hand edits to a song's .json that are detected
  on the next `rate` count as preference signals. Routine `edit`/chat edits are logged but do **not** move the policy by default
  (a tester's "faster, faster" was drifting everyone's tempo); set `MIDIGEN_LEARN_EDITS=1` or pass `record_edit(..., learn_policy=True)`
  to let them. Under pytest (or `MIDIGEN_TEST_MODE=1`) without an explicit `MIDIGEN_DATA_DIR`, a throwaway temp store is used.
- The LLM Composer gets the user's top-rated songs and stated preferences as few-shot "user memory" in its prompt.

**The critic is a proxy; your ratings are the ground truth.** `train` only optimises the built-in critic's score, so it can
improve rule-following (voice leading, clashes, groove consistency) but not taste. Human ratings outweigh critic evidence.
`train` additionally blends in a user model's prediction once there are ratings.

**Measured** (`demo/learning/make_learning_demo.py`, reproducible):
- A simulated listener with a hidden taste (octave bass + busier parts, noisy 1–5 ratings) rated 40 pop songs
  (`tools/simulate_user.py`). The bandit's top arms became exactly the hidden ones. In the first 10 songs the hidden combo
  came up 0% of the time (mean rating 2.13). In the last 10 it came up 90% of the time (mean rating 4.41).
  This is asserted in `tests/test_learn.py`.
- `train --rounds 6` on pop/funk/lofi_hiphop/house, with 12 held-out eval seeds, raised the mean critic score from 95.7 to 99.7
  (`demo/learning/train_curve.png`/`.txt`). The critic is near its ceiling, so this mostly removes weak outliers.

**Limits.** The arms are a fixed menu, so the bandit picks among things the generator can already do and cannot invent new styles.
Preferences are per genre and per store, and don't transfer across genres. The simulated user is far cleaner than a real one.
Learning from MIDI uses heuristics: it guesses roles from program and register, and works best on clean multitrack files.

**Experience store schema** (for downstream readers such as a reward-model/dataset pipeline)

The root is `$MIDIGEN_DATA_DIR` (default `~/.midigen/experience`). Under pytest, or in a process started by a test, with no
`MIDIGEN_DATA_DIR` set, a temp store is used so automated runs never touch a real one. `MIDIGEN_NO_RECORD=1` disables recording.
Read it with `midigen.learn.store().songs(limit=…)` (dict rows with JSON columns decoded) or with any SQLite client.

`experience.sqlite`:

| table | columns |
|---|---|
| `songs` | `id` TEXT pk, `ts` REAL (unix), `genre`, `seed` INT, `source` (offline/llm/cli/mcp/manual/import/train/…), `parent` (song id), `fingerprint`, `path`, `prompt`, `spec` JSON, `params` JSON, `choices` JSON {dim: arm}, `critic_score` REAL 0–100, `critic_sub` JSON {check: score}, `user_reward` REAL 0..1 or NULL (unrated), `applied_user`, `applied_dims`, `status` (`kept`/`rejected`/NULL) |
| `feedback` | `id` INT pk, `song_id`, `ts`, `kind` (`rating`/`thumbs`/`keep`/`reject`/`note`), `value` REAL, `target` (`song`, `track:<name>`, `section:<name>`), `note` TEXT |
| `edits` | `id` INT pk, `song_id` (before), `new_song_id` (after), `ts`, `ops` JSON, `signals` JSON {dim: direction}, `source` |
| `llm_pairs` | `id` INT pk, `ts`, `genre`, `kind` (generate/edit), `prompt`, `response`, `song_id`, `score` |

`user_reward` combines all feedback on a song. The latest whole-song rating counts double, mapped (r−1)/4. Thumbs, keep (0.85),
reject (0) and note sentiment also count. Re-rating replaces the earlier rating. `weights/<genre>.json` holds
`{genre, version, updates, user_events, critic_events, arms: {dim: {arm: {ua, ub, ca, cb}}}, prefs, history}`.
`profiles/<genre>.json` holds what was learned from reference MIDI: `references, progressions, bass, cells, drum_grids, energy, tempo, swing, intervals`.

## Genre packs

midigen loads external genre files (schema **v1.0.0**, the format of `midigen-genres/`) and maps each one to a midigen profile.
Both pack folders ship in the zip next to `midigen/` and are loaded by default. midigen never writes to them.

**Precedence, lowest to highest (a later source wins an id):**
1. builtin profiles (`src/midigen/data/genres/*.json`, 16 genres)
2. `midigen-knowledge/`, then `midigen-genres/`, found next to the checkout, in the current directory or in `/workspace`.
   `midigen-genres` is the curated base library, so it wins jazz, pop and rock, which both folders define.
   Set `MIDIGEN_NO_DEFAULT_PACKS=1` to skip both folders.
3. `MIDIGEN_GENRE_PATH`: folders or `.json` files separated by `:` (`;` on Windows). Later entries win.
4. `--genre-path PATH` on the CLI (repeatable) or `mg.packs.set_paths([...])` in code. Later entries win.

**Duplicate-id policy.**
- Ids are normalised to lower case, with `-` and spaces turned into `_`. `lofi-hip-hop` is aliased to `lofi_hiphop`.
- Between packs, the higher-precedence file wins. The loser stays reachable as `id@pack` (e.g. `jazz@midigen-knowledge`), and its aliases are merged into the winner.
- Against a builtin, `MIDIGEN_GENRE_MERGE` decides:
  - `merge` (default): the pack's fields win. Aliases are unioned. Builtin-only harmony settings (extensions, secondary dominants, borrowed chords) and density targets are kept. So are builtin generator idioms such as walking bass and power chords.
  - `replace`: the pack profile replaces the builtin.
  - `builtin`: the builtin wins, and the pack is only reachable as `id@pack`.
- Every profile records `_sources`.

`mg.list_genres()` returns the 16 builtin ids. `mg.available_genres()` also includes pack-only genres: currently **20**, adding bigband, emo, metal and screamo.
`mg.packs.report()` returns `loaded`, `failed`, `skipped` (the `index.json` files), `shadowed` and per-genre `warnings`, and `midigen genres` prints the same information.
Files in sub-folders (e.g. `midigen-knowledge/arranging/`) and folders without genre files (e.g. `midigen-songwriting/`) are not loaded.

**Mapping (schema v1.0.0 → profile):**

| pack field | midigen |
|---|---|
| `id`, `name`, `aliases`, `description` | id, display name, aliases (for genre detection and name resolution) |
| `tempo.min/max/default`, `timeSignature` | tempo range and default; meter |
| `groove.swing`, `swingSubdivision`, `laidBackMs`, `timingMs` | swing (see the convention below), swing unit (`8th` → `1/8`, otherwise `1/16`), laid-back feel |
| `scales`, `harmony.progressions[].numerals/sections/weight`, `tonics` | modes (major, minor, dorian and so on) and weighted progressions per section, with numerals checked against the mode |
| `instrumentation[]` (`role`, `programs`, `register`, `octaveRange`, `channel`, `isDrum`) | tracks per role (drums, bass, chords, pad, arp, lead, plus extra chord, pad or backing parts), at most 10 tracks |
| `drums.patterns` (`voices`, `velocity`, `stepsPerBar`, `variations`, `ratchets`) | drum grids and fills, including the velocity curve |
| `bass` (`rhythm`, `walking`, `stepNotes`), `melody` (`rhythmCells`, `notesPerBarMin/Max`, `chordToneOnStrongBeat`, `density`) | bass template or walking style; lead rhythm cells, density and the probability of snapping strong beats to chord tones |
| `arrangement.sections` (`bars`, `energy`, `play`, `transition`) | form, energy curve, which instruments play in each section |

**Register slots.** Pack instruments usually share wide ranges, so roles are moved into fixed slots to avoid masking:
the bass centres at or below MIDI 44, chords around 61, pads around 58, arps at 61 or above, and the lead at 75 or above.
Tracks are ordered drums, bass, chords, pad, arp, extras, then the lead last.

**Swing convention.** One scale everywhere (spec `swing`, `set_swing`, generator `swing`, bundled and pack genres): 0–1 on the
`swing_unit` grid, with the off-beat of each pair at 0.5 + swing/6 of the pair. 0 is straight (50%), 0.25 ≈ 54% (house),
0.5 ≈ 58% (MPC/lo-fi), 0.7 ≈ 62% (jazz) and 1 is a full triplet (66.7%). A pack's `groove.swing` is used as is. Grid notes
between the beats are time-warped with the off-beat, so with 1/8 swing the in-between 16ths move too. Specs saved as
`"version": 1` used a 0–0.33 scale; `normalize` migrates them to version 2 (swing × 3), so they render the same.

**Not mapped:**
- drum kit programs;
- `fx`/`perc` instruments, such as the ambient texture and cinematic timpani (skipped, with a warning);
- instruments beyond 10 tracks (bigband drops tb1–3, btb, pno and gtr, with a warning);
- glide, noteChoices weights, ornaments, phrase lengths;
- free-text notes and rules.

Extra melody instruments (a second horn, a second sax) become section backing parts, and a warning lists each one.
No genre failed to load or map.

## The spec

```jsonc
{
  "title": "Demo", "genre": "pop", "tempo": 110, "time_signature": [4, 4], "key": "C", "mode": "major",
  "swing": 0.0, "swing_unit": "1/16", "seed": 7,
  "sections": [ {"name": "verse", "bars": 4, "chords": ["vi", "IV", "I", "V"], "energy": 0.5, "cadence": "half",
                 "beats_per_chord": 4} ],
  "arrangement": ["intro", "verse", "chorus", "verse", "chorus"],
  "tracks": [ {"name": "Bass", "role": "bass", "program": 33, "channel": 0,
               "generator": {"type": "bass", "style": "kick_lock", "density": 0.6, "range": [33, 52]},
               "sections": ["verse", "chorus"], "overrides": {"chorus": {"style": "octave"}}} ]
}
```

- **Tempo convention.** `tempo` counts the time signature's beat note: 6/8 at 180 means 180 eighth notes per minute, which is 90 quarter BPM.
  The MIDI `set_tempo` is always quarter BPM (`mg.spec.quarter_bpm(spec)` = tempo × 4 / denominator), and note positions are in quarter notes
  (one bar = numerator × 4 / denominator beats). `set_time_signature` rescales `tempo` when the denominator changes, so the song keeps its speed.
  For example, 4/4 at 110 becomes 6/8 at 220. Pass `"keep_tempo": true` to keep the number instead. `import_midi` converts back the same way.
- **Chords** can be roman numerals (`vi`, `bVII`, `V7/vi`, `iiø7`, `I/III`) or symbols (`Am7`, `C/E`). Plain numerals follow the
  mode, and `b`/`#` numerals are relative to the parallel major. Using numerals keeps the harmony intact through key changes.
- **Generator types:** drums (genre patterns from `data/drums.json`), bass, chords, pad, arp, lead, and clip (raw notes, used for imported MIDI).
- **Edit ops (27):**

  | Area | Ops |
  |---|---|
  | Tempo, key and meter | set_tempo, change_tempo, set_key, transpose, set_time_signature |
  | Harmony | set_chords, reharmonize, set_cadence |
  | Sections and form | add_section, remove_section, set_section, extend_section, repeat_section, set_arrangement |
  | Tracks | add_track, add_instrument, remove_track, replace_track, set_track |
  | Generators and feel | set_generator, set_drum_pattern, set_density, humanize, reseed, set_swing |
  | Song-wide | apply_genre, set_title |

## Theory engine

| module | what it does |
|---|---|
| `theory.py` | pitch classes, modes, chord and roman-numeral parsing (secondary dominants, borrowed, slash, extensions) |
| `harmony.py` | functional-harmony progression generator (T→PD→D), cadence application and detection, secondary dominants, modal mixture, extensions, reharmonisation, voice leading (minimal motion, no parallel 5ths/8ths) |
| `melody.py` | motifs (rhythm cells and contour), development (repeat, sequence, inversion, retrograde), call-and-response phrasing, chord-tone targeting on strong beats, leaps resolved by step, phrase endings on stable tones |
| `generators.py` | bass styles (kick-lock, walking with chromatic approach notes, syncopated, reggae…), comping, strums, pads, arps, genre drum grooves with fills and crashes |
| `arranger.py` | renders sections; energy curves drive density, layering thresholds, fills; drums first so the bass can lock to the kick; swing and humanise |
| `data/genres/*.json` | 16 genre profiles: tempo, modes, progressions by section, forms, instruments, registers, density targets, layering, swing, cadences |
| `critic.py` | out-of-key and clashing notes (passing tones excused), parallel 5ths/8ths, register masking, bass–kick interlock, melodic leaps, density against genre targets, flat energy, cadence mismatch; returns a 0–100 score. Parallel fifths are info-only for rock, metal, emo, screamo and punk, and skipped for power-chord tracks |

Genres: ambient, bossa_nova, cinematic, drum_and_bass, edm, funk, house, jazz, lofi_hiphop, neo_soul, pop, reggae, rock,
synthwave, techno, trap, plus bigband, emo, metal and screamo from the packs. Add a genre by dropping a schema-v1.0.0 file into a pack folder
(or `MIDIGEN_GENRE_PATH` / `--genre-path`), or a builtin-format JSON into `src/midigen/data/genres/`.

## Demo

`demo/make_demo.py` runs offline and writes `.mid`, `.json`, `.png` and `.critic.txt` for each song, then re-reads every .mid and checks track and note counts
(`demo/verification.json`):
- `demo.mid`: 16-bar pop song (drums, bass, chords, pad, lead) with the edit "jazzier chords in the chorus"
- `lofi_hiphop.mid`, `techno.mid` (32 bars, plus an arp) and `jazz.mid` (walking bass, swing): contrasting genres

## Reviewing output

`tools/analyze.py DIR` is the independent analyzer from the critic review (`midigen-critic/analyze.py`, with its paths made relative).
It measures every `.mid`/`.json` pair in DIR and writes `DIR/analysis.json`.
`review/after/` holds the 28 review songs regenerated with `midigen new --genre G --seed N --offline`.
`review/COMPARISON.md` compares them with the original review samples.

