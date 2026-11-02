# Embedding midigen in other apps

midigen has four integration surfaces. Pick the one closest to your host:

| host | use |
|---|---|
| Python app, notebook, game tool, plugin with embedded Python | `midigen_integrations.api` (in-process, no server) |
| anything that speaks HTTP: web/Electron/Node apps, DAW scripts, other languages | the REST service (`midigen-service`) + `ts-client` or plain `curl` |
| LLM agents (Claude Desktop, Cursor, ...) | the existing MCP server `midigen-mcp` (see the main README) |
| live instruments, DAWs listening on a MIDI input | `midigen-stream` (python-rtmidi, prototype) |

The unit of exchange everywhere is the **spec**: a JSON object describing the song (sections, roman-numeral
chords, energy, tracks and their generators). The engine turns a spec into notes deterministically, so a spec plus
its `seed` *is* the song. Store specs, not MIDI, if you want to edit later.

## 1. Python facade (`midigen_integrations.api`)

```python
from midigen_integrations import api

song = api.generate(api.GenerateParams(genre="house", key="A", mode="minor", bars=16, seed=7))
song.spec        # editable JSON spec
song.summary     # title, genre, key, mode, tempo, time_signature, bars, total_beats, duration_seconds, seed,
                 # sections[{name,bars,energy,chords}], arrangement, tracks[{name,role,channel,program,notes}], critic_score
song.critic      # {"score": 0-100, "findings": [...], "stats": {...}}
song.midi        # bytes: Standard MIDI File, type 1, 480 PPQ, conductor + one track per instrument, drums on ch 10

song = api.edit(song.spec, ops=[{"op": "transpose", "semitones": 2}])   # JSON ops (list_options()["ops"])
song = api.edit(song.spec, instruction="faster, add strings")           # plain English (offline parser)
api.critique(spec); api.to_midi_bytes(spec); api.render_notes(spec)
api.list_genres(); api.genre_profile("Drum and Bass"); api.list_options()
```

| function | returns | raises `MidigenError.kind` |
|---|---|---|
| `generate(params \| **kw)` | `Song` | `invalid_params`, `unknown_genre` |
| `edit(spec, ops=None, instruction=None, use_llm=False)` | `Song` (with `.ops`) | `invalid_spec`, `invalid_ops`, `not_understood`, `invalid_params` |
| `critique(spec)` | critic report dict | `invalid_spec` |
| `to_midi_bytes(spec)` | `bytes` (.mid) | `invalid_spec` |
| `render_notes(spec)` | `{tempo_qpm, time_signature, total_beats, sections, tracks[{..., notes: [[start, dur, pitch, vel]]}]}` (times in quarter-note beats) | `invalid_spec` |
| `summarize(spec)` | summary dict | `invalid_spec` |
| `validate_spec(spec)` | normalised copy | `invalid_spec` |
| `list_genres()` / `genre_profile(name)` / `list_options()` | dicts | `unknown_genre` |

`GenerateParams`: `prompt, genre, key (A-G[#b]), mode, tempo (20-300), bars (1-256, exact), seed=1, refine=True, title,
use_llm=False`. Give `genre` or `prompt`. Explicit key/mode/tempo/bars override what a prompt says (same rule as the CLI).
With `use_llm=False` everything is offline and deterministic: same params, same bytes.

**Stability.** `API_VERSION = "1"`. Names, parameters and documented keys stay; new optional parameters/keys may be
added. The spec format is versioned by midigen itself (`spec["version"]`, schema in `list_options()["spec_schema"]`).
Everything the facade returns is JSON-serialisable (except `.midi` bytes), and the REST service returns the same shapes.

## 2. REST service

```bash
pip install -e "integrations/python[service]"
midigen-service --host 127.0.0.1 --port 8765     # interactive docs: /docs, schema: /openapi.json
```

| method & path | body | response |
|---|---|---|
| `GET /health` | – | `{status, api_version, midigen_version}` |
| `GET /options` | – | genres, keys, modes, limits, instruments, styles, drum patterns, op schemas, spec schema |
| `GET /genres`, `GET /genres/{name}` | – | genre list / one profile (404 if unknown) |
| `POST /generate?format=json\|midi\|lines&include_midi=bool` | `GenerateParams` as JSON | song JSON, `audio/midi`, or text |
| `POST /edit?format=json\|midi\|lines` | `{spec, ops?, instruction?}` | edited song |
| `POST /critique` | `{spec}` | critic report |
| `POST /export?format=midi\|notes\|lines` | `{spec}` | `.mid`, note JSON, or text |

Errors are always `{"error": kind, "errors": [messages]}` with status 422 (404 for `unknown_genre`); request
validation errors use the same shape with `error: "invalid_params"`. MIDI responses carry
`Content-Disposition: attachment; filename=...` and `X-Midigen-Summary: bars=..;tempo=..;key=..;mode=..;tracks=..`.

The service is **stateless**: send the spec back with each request. That keeps it horizontally scalable and lets the
client own undo history (keep a stack of specs). The `lines` format exists for hosts without a JSON parser (Lua):

```
H  tempo_qpm  num  den  total_beats
T  channel(0-15)  program  is_drums(0/1)  volume  pan  name      <- one per track, followed by its notes
N  start_beats  dur_beats  pitch  velocity
```

Security defaults: binds to 127.0.0.1; LLM use is refused unless `MIDIGEN_SERVICE_ALLOW_LLM=1`; CORS is off unless
`MIDIGEN_CORS_ORIGINS` lists origins. There is no auth: put it behind a reverse proxy before exposing it.

## 3. TypeScript / JavaScript (`integrations/ts-client`)

```ts
import { MidigenClient } from "@midigen/client";
const mg = new MidigenClient("http://127.0.0.1:8765");
const song = await mg.generate({ genre: "house", bars: 16, seed: 7 });
const edited = await mg.edit(song.spec, { instruction: "add strings" });
const bytes: Uint8Array = await mg.exportMidi(edited.spec);      // write to disk, or feed to WebMIDI / Tone.js
```

Zero dependencies (global `fetch`), typed responses, `MidigenError {status, kind, errors}`. `npm run build` emits
`dist/`; `node examples/generate.ts` runs the example directly on Node >= 22.18. For browsers, enable CORS on the
service (`MIDIGEN_CORS_ORIGINS=http://localhost:5173`).

## 4. DAWs

**REAPER** (`integrations/reaper/midigen_generate.lua`): an action that prompts for genre/key/mode/bars/seed/prompt,
calls `/generate?format=lines` with `curl`, and inserts one new track per instrument at the edit cursor with
`CreateNewMIDIItemInProj` + `MIDI_InsertNote` (program change, CC7 volume, CC10 pan, drums on channel 10), plus a
tempo/time-signature marker (`CONFIG.set_project_tempo`). One undo step. Notes are placed by quarter-note position, so
they land on the grid even if you change the tempo afterwards. It has been run in a Lua 5.4 runtime against a stub
`reaper` table and the live service (`python integrations/reaper/test_reascript.py`), **not inside REAPER**.

**Any other DAW**: drag the `.mid` from `/generate?format=midi` in; it is a type-1 file with named tracks, so DAWs
that split multitrack MIDI on import (Ableton, Logic, Bitwig, FL, Cubase) give one track per instrument.

## 5. Live MIDI streaming (`midigen-stream`, prototype)

```bash
pip install -e "integrations/python[stream]"      # python-rtmidi
midigen-stream --genre house --bars 8 --virtual midigen   # Linux (ALSA/JACK) and macOS: creates port "midigen"
midigen-stream --list; midigen-stream --genre jazz --port "loopMIDI Port 1"   # Windows: needs loopMIDI
midigen-stream --file song.mid --dry-run                  # no MIDI system needed
```

`stream.play(source, port, speed=1.0)` merges the tracks with mido (tempo-aware), sleeps until each event is due and
always sends All-Notes-Off/All-Sound-Off on all 16 channels at the end or on Ctrl-C. `port` is anything with
`send(mido.Message)`, and the clock/sleep are injectable (that is how it is tested).

Feasibility notes:
- Virtual ports work with python-rtmidi on Linux (ALSA sequencer, needs `/dev/snd/seq`, i.e. the `snd-seq` kernel
  module) and macOS (CoreMIDI). Windows WinMM has no virtual ports; use loopMIDI or teVirtualMIDI.
- python-rtmidi 1.5.8 publishes wheels up to CPython 3.12. On 3.13 pip builds from source, which needs a C++
  compiler and the ALSA headers (`libasound2-dev`).
- Timing uses `time.monotonic` + `sleep` in 50 ms slices, which is fine for previews. For tight sync, send MIDI
  clock (0xF8) or follow the DAW's clock instead; see next steps.

## 6. MCP

Unchanged: `midigen-mcp` keeps session state by `song_id`. The REST service and the MCP server call the same engine;
the facade's `generate` follows the CLI's rules (validation, prompt overrides) rather than the MCP tool's.

## Next steps

- **JUCE/VST3/AU plugin**: possible as a thin C++ plugin that calls the REST service and drops MIDI into the host,
  or embeds CPython (heavier). A host-agnostic first step is a "drag MIDI out" panel.
- **Ableton Live**: a Max for Live device calling the service through `node.script` (it can reuse `ts-client`), or a
  Remote Script (Python 3 inside Live) that imports `midigen_integrations.api` directly.
- **Bitwig / Logic / FL**: file import already works. Live insertion needs each host's API (Bitwig Java/JS
  extension, Logic scripting is limited, FL has Python MIDI scripting).
- **Streaming**: MIDI clock out/in, loop mode, section-aware live edits (re-render the spec and switch at the next bar).
- **Service**: auth token, request size and time limits, optional `/songs` session store mirroring the MCP server,
  WebSocket progress for LLM runs, Docker image.
- **TS client**: publish to npm, generate types from `/openapi.json` in CI so the client and service can't drift.
