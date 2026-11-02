"""One-call listening check: render mix (+MP3) and stems, analyse, write JSON + text, attach to the critic report."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .render import render_audio, render_stems, to_midifile


def listening_check(source, out_dir, *, name: str = "song", backend: str = "auto", soundfont: str | None = None,
                    mix: bool = True, humanize=None, mp3: bool = True, stems: bool = True, keep_stems: bool = False,
                    normalize_lufs: float | None = None, genre: str | None = None, critic: bool = True) -> dict:
    """Render `source` (spec dict/.json/.mid) to out_dir/<name>.wav (+.mp3), analyse it and return the mix report.

    Writes <name>.wav, <name>.mp3, <name>.mid (the exact MIDI rendered), <name>.mix.json, <name>.mix.txt and, when the
    source is a spec and critic=True, <name>.critic.txt with the new "mix" section."""
    from .analysis import analyze_mix
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    mid, spec = to_midifile(source, mix=mix)
    genre = genre or (spec or {}).get("genre")
    res = render_audio(mid, out / f"{name}.wav", mp3=mp3, backend=backend, soundfont=soundfont, humanize=humanize,
                       genre=genre, save_midi=out / f"{name}.mid", normalize_lufs=normalize_lufs)
    stem_info = None
    if stems:
        rendered = mid if not humanize else __import__("mido").MidiFile(str(out / f"{name}.mid"))
        stem_dir = out / f"{name}_stems"
        stem_info = render_stems(rendered, stem_dir, backend=backend, soundfont=soundfont)
        if spec:  # restore spec roles (render_stems only sees the MIDI)
            roles = {t["name"]: t.get("role", "other") for t in spec["tracks"]}
            for s in stem_info:
                if s.get("role") != "kick":
                    s["role"] = roles.get(s.get("source_track", s["name"]), s["role"])
    rep = analyze_mix(res.wav, genre=genre, stems=stem_info)
    rep["render"] = {k: v for k, v in res.to_dict().items() if k != "stems"}
    rep["render"]["mix_defaults_applied"] = mix
    if stems and not keep_stems:
        shutil.rmtree(out / f"{name}_stems", ignore_errors=True)
        for s in rep["stems"]:
            s.pop("wav", None)
    (out / f"{name}.mix.json").write_text(json.dumps(rep, indent=1))
    (out / f"{name}.mix.txt").write_text(rep["summary"] + "\n")
    if spec is not None and critic:
        from ..critic import critique, format_report
        crep = critique(spec, mix=rep)
        (out / f"{name}.critic.txt").write_text(format_report(crep) + "\n")
        rep["critic_score"] = crep["score"]
    return rep
