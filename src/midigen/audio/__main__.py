"""python -m midigen.audio {render,analyze,check,backends} ..."""
from __future__ import annotations

import argparse
import json
import sys


def _humanize_arg(v):
    if v is None:
        return None
    return int(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m midigen.audio",
                                 description="Render midigen songs to audio and run a measurement-based listening check.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--backend", default="auto", choices=["auto", "fluidsynth", "numpy"])
    common.add_argument("--soundfont", help="path to a GM .sf2/.sf3 (default: auto-discover, see AUDIO_NOTES.md)")
    common.add_argument("--no-mix", action="store_true", help="for spec input: skip the per-genre mix defaults (CC7/10/91/93)")
    common.add_argument("--humanize", nargs="?", const=1, default=None, metavar="SEED",
                        help="playback-only timing/velocity humanize (genre defaults), optional seed (default 1)")
    common.add_argument("--normalize", type=float, metavar="LUFS", help="MP3 only: normalise to LUFS (true peak <= -1 dBTP)")

    r = sub.add_parser("render", parents=[common], help="spec .json / .mid -> .wav (+ .mp3)")
    r.add_argument("input")
    r.add_argument("output", help="output .wav")
    r.add_argument("--mp3", nargs="?", const=True, default=False, help="also write MP3 (optional path)")
    r.add_argument("--stems", metavar="DIR", help="also render one WAV per track into DIR")

    a = sub.add_parser("analyze", help="measure a .wav (optionally with stems.json from --stems)")
    a.add_argument("wav")
    a.add_argument("--stems", metavar="STEMS_JSON")
    a.add_argument("--genre")
    a.add_argument("--json", action="store_true", help="print the full JSON report")

    c = sub.add_parser("check", parents=[common], help="render + stems + analysis + critic mix section")
    c.add_argument("input")
    c.add_argument("out_dir")
    c.add_argument("--name", default=None)
    c.add_argument("--no-mp3", action="store_true")
    c.add_argument("--no-stems", action="store_true")
    c.add_argument("--keep-stems", action="store_true")
    c.add_argument("--json", action="store_true")

    sub.add_parser("backends", help="show which backends are available")
    args = ap.parse_args(argv)

    from .render import AudioBackendError
    try:
        if args.cmd == "backends":
            from .render import available_backends
            print(json.dumps(available_backends(), indent=1))
            return 0
        if args.cmd == "render":
            from .render import render_audio, render_stems
            res = render_audio(args.input, args.output, mp3=args.mp3, backend=args.backend, soundfont=args.soundfont,
                               mix=not args.no_mix, humanize=_humanize_arg(args.humanize), normalize_lufs=args.normalize)
            if args.stems:
                res.stems = render_stems(args.input, args.stems, backend=args.backend, soundfont=args.soundfont,
                                         mix=not args.no_mix)
            print(json.dumps(res.to_dict(), indent=1))
            return 0
        if args.cmd == "analyze":
            from .analysis import analyze_mix
            stems = json.load(open(args.stems)) if args.stems else None
            rep = analyze_mix(args.wav, genre=args.genre, stems=stems)
            print(json.dumps(rep, indent=1) if args.json else rep["summary"])
            return 0
        if args.cmd == "check":
            from pathlib import Path
            from .check import listening_check
            rep = listening_check(args.input, args.out_dir, name=args.name or Path(args.input).stem,
                                  backend=args.backend, soundfont=args.soundfont, mix=not args.no_mix,
                                  humanize=_humanize_arg(args.humanize), mp3=not args.no_mp3, stems=not args.no_stems,
                                  keep_stems=args.keep_stems, normalize_lufs=args.normalize)
            print(json.dumps(rep, indent=1) if args.json else rep["summary"])
            return 0
    except (AudioBackendError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
