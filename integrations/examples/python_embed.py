"""Runnable example: embed midigen in a Python app through the stable facade (no server needed).

    python integrations/examples/python_embed.py [out_dir]
"""
import json
import sys
from pathlib import Path

from midigen_integrations import api

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
out.mkdir(parents=True, exist_ok=True)

opts = api.list_options()
print(f"api v{opts['api_version']}: {len(opts['genres'])} genres, modes {', '.join(opts['modes'][:4])}...")

song = api.generate(api.GenerateParams(genre="lofi-hip-hop", key="D", mode="minor", bars=8, seed=4))
s = song.summary
print(f"{s['title']}: {s['bars']} bars @ {s['tempo']} BPM, {s['key']} {s['mode']}, {s['duration_seconds']}s, "
      f"critic {s['critic_score']}")

song = api.edit(song.spec, instruction="add strings and more swing")
print("ops applied:", json.dumps(song.ops))
song = api.edit(song.spec, ops=[{"op": "transpose", "semitones": -2}])

(out / "lofi.mid").write_bytes(song.midi)
(out / "lofi.json").write_text(json.dumps(song.spec, indent=2))
notes = api.render_notes(song.spec)
print(f"wrote {out/'lofi.mid'} ({len(song.midi)} bytes); tracks:",
      ", ".join(f"{t['name']}={len(t['notes'])}" for t in notes["tracks"]))

try:
    api.generate(genre="house", bars=999)
except api.MidigenError as e:
    print(f"expected error: {e.kind}: {e.errors[0]}")
