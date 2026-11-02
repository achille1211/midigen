# midigen integrations

Everything that lets other apps use midigen. All of it is additive: nothing in `src/midigen` changes.

| path | what | status |
|---|---|---|
| `python/midigen_integrations/api.py` | stable typed Python facade (`generate`, `edit`, `critique`, `to_midi_bytes`, `render_notes`, `list_genres`, `genre_profile`, `list_options`) | tested |
| `python/midigen_integrations/service.py` | FastAPI REST service, OpenAPI at `/docs` and `/openapi.json` | tested (TestClient + live curl) |
| `python/midigen_integrations/stream.py` | real-time streaming to a MIDI port via python-rtmidi (prototype) | scheduler tested; real ports **not** tested here (no ALSA sequencer in the box) |
| `ts-client/` | zero-dependency TypeScript client + runnable example | typechecked, example run against the live service |
| `reaper/midigen_generate.lua` | REAPER ReaScript: generate and insert as multitrack MIDI | run in Lua 5.4 with a stubbed `reaper` API against the live service; **not run inside REAPER** |
| `examples/` | `python_embed.py`, `rest_curl.sh` | run |
| `docs/embedding.md` | how to embed midigen in other apps | – |

## Install

From the repo root, in the same venv as midigen (`pip install -e .` first if it isn't installed yet):

```bash
pip install -e integrations/python lupa                  # facade + streaming code, lupa for the ReaScript test
pip install -e "integrations/python[service,test]"       # REST service (fastapi, uvicorn) + test deps (httpx, pytest)
pip install -e "integrations/python[stream]"             # optional: python-rtmidi, for real MIDI ports
```

| extra | pulls in | needed for |
|---|---|---|
| *(none)* | `midigen` | `midigen_integrations.api`, `midigen-stream --dry-run` |
| `service` | `fastapi`, `uvicorn` | `midigen-service`, the service tests, the ReaScript test (it starts a service) |
| `test` | `pytest`, `httpx`, `fastapi`, `uvicorn`, `lupa` | running all integration tests |
| `stream` | `python-rtmidi` | `midigen-stream --virtual/--port` (wheels up to Python 3.12; 3.13 builds from source, needs a C++ compiler + ALSA headers on Linux) |

`lupa` is only used by `reaper/test_reascript.py` (it runs the Lua script in Lua 5.4 with a stubbed REAPER API);
REAPER itself doesn't need it. The ReaScript test also needs `curl` on PATH.

## Tests

```bash
python -m pytest -q                          # from the repo root: midigen + integrations tests
python -m pytest -q integrations             # just the integrations (facade, service, streaming, ReaScript)
python integrations/reaper/test_reascript.py [http://127.0.0.1:8765]   # as a script; starts its own service if no URL
```

Tests whose dependencies are missing are skipped, not errors: everything under `integrations/` if
`midigen_integrations` isn't installed, the service and ReaScript tests without fastapi/uvicorn, and the ReaScript
test without lupa or curl.

## Quick start

```bash
midigen-service --port 8765 &                # http://127.0.0.1:8765/docs
curl -X POST 'http://127.0.0.1:8765/generate?format=midi' -H 'Content-Type: application/json' \
     -d '{"genre":"house","bars":16,"seed":7}' -o house.mid
(cd integrations/ts-client && node examples/generate.ts)   # Node >= 22.18 runs .ts directly
```

See [docs/embedding.md](docs/embedding.md).
