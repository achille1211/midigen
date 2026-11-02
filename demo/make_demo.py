"""Offline demo: builds songs with the theory engine (no LLM/API key), exports MIDI + piano rolls,
re-reads every .mid and checks track/note counts. Run: python demo/make_demo.py"""
import json
from pathlib import Path

import midigen as mg

OUT = Path(__file__).parent
results = {}


def make(name, spec, edits=()):
    ed = mg.Editor(spec)
    for instr in edits:
        ed.apply(mg.edit_to_ops(ed.spec, instr), label=instr)
    spec, rep = mg.refine(ed.spec)
    mid = OUT / f"{name}.mid"
    perf = mg.export_midi(spec, mid)
    mg.save(spec, OUT / f"{name}.json")
    mg.piano_roll(perf, OUT / f"{name}.png", f"{spec['title']} — {spec['genre']} {spec['key']} {spec['mode']} {spec['tempo']:g} BPM")
    (OUT / f"{name}.critic.txt").write_text(mg.format_report(rep) + "\n")
    info = mg.inspect_midi(mid)
    expected = {t.name: len(t.notes) for t in perf.tracks}
    got = {t["name"]: t["notes"] for t in info["tracks"]}
    assert got == expected, (name, got, expected)
    assert info["bars"] == mg.total_bars(spec), (info["bars"], mg.total_bars(spec))
    results[name] = {"genre": spec["genre"], "key": f"{spec['key']} {spec['mode']}", "tempo": spec["tempo"],
                     "bars": info["bars"], "tracks": got, "total_notes": info["note_count"],
                     "drum_channel": [t["channel"] for t in info["tracks"] if t["name"].lower().startswith("drum")],
                     "sections": [f"{s['name']}({s['bars']}): {' '.join(s['chords'])}" for s in spec["sections"]],
                     "critic_score": rep["score"], "edits": list(edits)}
    print(f"{name}: {info['bars']} bars, {len(got)} tracks, {info['note_count']} notes, critic {rep['score']}/100 -> verified")


# main demo: 16-bar pop song with drums, bass, chords, lead (+pad), one conversational-style edit
make("demo", mg.build_spec("pop", key="C", mode="major", tempo=110, bars=16, seed=7, title="Demo Song"),
     edits=["jazzier chords in the chorus"])
# contrasting genres
make("lofi_hiphop", mg.prompt_to_spec("a sad lofi hip hop beat in D minor at 82 bpm, 16 bars", seed=3))
make("techno", mg.build_spec("techno", key="A", mode="minor", bars=32, seed=11, title="Warehouse"),
     edits=["add an arp"])
make("jazz", mg.build_spec("jazz", key="Bb", mode="major", bars=16, seed=5, title="Blue Room"),
     edits=["more swing"])

(OUT / "verification.json").write_text(json.dumps(results, indent=2) + "\n")
print("wrote", OUT / "verification.json")
