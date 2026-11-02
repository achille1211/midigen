"""Portable delivery packs built from a saved song, with no hosted service required."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from .arranger import render
from .critic import critique
from .midi_io import export_midi
from .preview import piano_roll
from .spec import check, save, quarter_bpm


PRODUCTS = {
    "creator": "Creator Cue Pack: full cue and 15/30/60-second cutdowns",
    "producer": "Producer MIDI Kit: full arrangement and isolated instrument MIDI",
    "rehearsal": "Rehearsal Pack: full arrangement, isolated parts and an 80% tempo practice take",
}


def deliver(spec, output, *, product="producer", audio=False, backend="auto"):
    """Write a new directory and ZIP; refuse to overwrite any existing delivery.

    MIDI is always included. Optional WAV rendering uses the existing audio backend.
    Cutdowns truncate at the requested time; they do not compose a new ending.
    """
    if product not in PRODUCTS:
        raise ValueError(f"unknown product {product!r}; choose {', '.join(PRODUCTS)}")
    spec = check(spec)
    output = Path(output).resolve()
    archive = output.with_name(output.name + ".zip")
    if output.exists() or archive.exists():
        raise ValueError("Delivery folder or ZIP already exists; choose a new output name")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".midigen-delivery-", dir=output.parent) as tmp:
        root = Path(tmp) / "pack"
        root.mkdir()
        perf = render(spec)
        save(spec, root / "song.json")
        piano_roll(perf, root / "preview.png")
        report = critique(spec)
        (root / "quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        versions = [("full", perf)]
        if product == "creator":
            seconds_per_beat = 60.0 / quarter_bpm(spec)
            for seconds in (15, 30, 60):
                end = seconds / seconds_per_beat
                if end >= perf.total_beats:
                    continue
                cut = copy.deepcopy(perf)
                cut.total_beats = end
                cut.sections = [s for s in cut.sections if s.start < end]
                for section in cut.sections:
                    section.end = min(section.end, end)
                cut.harmony = [h for h in cut.harmony if h.start < end]
                for chord in cut.harmony:
                    chord.end = min(chord.end, end)
                for track in cut.tracks:
                    track.notes = [n for n in track.notes if n.start < end]
                    for n in track.notes:
                        n.dur = min(n.dur, end - n.start)
                versions.append((f"cut-{seconds}s", cut))
        else:
            for index, track in enumerate(perf.tracks):
                if track.notes:
                    part = copy.deepcopy(perf)
                    part.tracks = [part.tracks[index]]
                    part.spec["tracks"] = [part.spec["tracks"][index]]
                    versions.append((f"part-{index + 1:02d}", part))
            if product == "rehearsal":
                practice = copy.deepcopy(perf)
                practice.spec["tempo"] = max(20, round(spec["tempo"] * .8, 2))
                versions.append(("practice", practice))
        assets = []
        for name, version in versions:
            export_midi(version, root / f"{name}.mid")
            if audio:
                from .audio import render_audio
                # Pass the exact exported MIDI so cutdowns and practice tempo survive rendering.
                render_audio(root / f"{name}.mid", root / f"{name}.wav", backend=backend)
            assets.append({"name": name, "tracks": [t.name for t in version.tracks],
                           "tempo": version.spec["tempo"],
                           "seconds": round(version.total_beats * 60 / quarter_bpm(version.spec), 3)})
        manifest = {"format_version": 1, "product": product, "description": PRODUCTS[product],
                    "seed": spec.get("seed"), "genre": spec.get("genre"), "assets": assets,
                    "limitations": ["Cutdowns truncate the cue and may need a composed ending.",
                                    "Rehearsal parts need player review; no notation is included.",
                                    "No ownership or licensing guarantee is supplied by this pack."],
                    "files": [{"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                              for p in sorted(root.iterdir()) if p.is_file()]}
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        zipped = Path(tmp) / "delivery.zip"
        with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(root.iterdir()):
                bundle.write(path, path.name)
        # All expensive work succeeds before either final artifact is published.
        root.rename(output)
        # The completed folder remains usable if the ZIP cannot be written.
        with archive.open("xb") as dest, zipped.open("rb") as source:
            shutil.copyfileobj(source, dest)
    return {"directory": str(output), "zip": str(archive), "product": product, "assets": len(assets)}


def add_parser(sub):
    parser = sub.add_parser("deliver", help="export a creator, producer or rehearsal product pack and ZIP")
    parser.add_argument("spec", help="saved song JSON")
    parser.add_argument("--product", choices=PRODUCTS, default="producer")
    parser.add_argument("--out", required=True, help="new output folder (must not exist)")
    parser.add_argument("--audio", action="store_true", help="include WAVs; requires audio extras")
    parser.add_argument("--backend", choices=("auto", "numpy", "fluidsynth"), default="auto")
    def run(args):
        song = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        print(json.dumps(deliver(song, args.out, product=args.product, audio=args.audio, backend=args.backend), indent=2))
    parser.set_defaults(fn=run)
