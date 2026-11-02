"""midigen command line."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import spec as specmod
from .critic import critique, format_report
from . import packs
from .genres import UnknownGenre, available_genres, build_spec, get_genre, resolve_genre
from .theory import MODES
from .llm import Composer
from .midi_io import export_midi, import_midi, inspect_midi
from .nl import HELP, NotUnderstood
from .ops import Editor, OpError, apply_ops


def _paths(out: str) -> tuple[Path, Path]:
    p = Path(out)
    if p.suffix in (".json", ".mid"):
        p = p.with_suffix("")
    return p.with_suffix(".json"), p.with_suffix(".mid")


def _write(spec: dict, out: str, png: bool = False, quiet: bool = False) -> None:
    js, mid = _paths(out)
    js.parent.mkdir(parents=True, exist_ok=True)
    specmod.save(spec, js)
    perf = export_midi(spec, mid)
    if png:
        from .preview import piano_roll
        piano_roll(perf, js.with_suffix(".png"))
    if not quiet:
        counts = ", ".join(f"{t.name}:{len(t.notes)}" for t in perf.tracks)
        print(f"wrote {js} and {mid}{' (+png)' if png else ''} — {specmod.total_bars(spec)} bars @ {spec['tempo']} BPM, "
              f"{spec['key']} {spec['mode']}, tracks [{counts}]")


def _composer(args) -> Composer:
    return Composer(use_env=not getattr(args, "offline", False))


MAX_BARS = 256


def _validate_new(args) -> None:
    """Explicit range checks (0 is a value, not 'unset')."""
    if args.tempo is not None and not 20 <= args.tempo <= 300:
        raise ValueError(f"--tempo must be between 20 and 300 BPM (got {args.tempo:g})")
    if args.bars is not None and not 1 <= args.bars <= MAX_BARS:
        raise ValueError(f"--bars must be between 1 and {MAX_BARS} (got {args.bars})")
    if args.mode is not None and args.mode not in MODES:
        raise ValueError(f"--mode {args.mode!r} is not a mode; choose from: {', '.join(sorted(MODES))}")
    if args.key is not None:
        import re
        if not re.fullmatch(r"[A-Ga-g][#b]?", args.key.strip()):
            raise ValueError(f"--key {args.key!r} is not a key; use a note name: C C# Db D D# Eb E F F# Gb G G# Ab A A# Bb B")
    if args.genre is not None:
        args.genre = resolve_genre(args.genre)
    if args.random_seed:
        import random
        args.seed = random.SystemRandom().randrange(1, 2**31)
        print(f"seed {args.seed} (re-use with --seed {args.seed})")


def _overrides(spec: dict, args) -> dict:
    """Apply explicit --key/--mode/--tempo/--bars to a prompt-generated spec."""
    ops = []
    if args.tempo is not None:
        ops.append({"op": "set_tempo", "bpm": args.tempo})
    if args.key is not None or args.mode is not None:
        ops.append({"op": "set_key", "key": args.key or spec["key"], **({"mode": args.mode} if args.mode else {})})
    if ops:
        spec = apply_ops(spec, ops)
    if args.bars is not None and args.bars != specmod.total_bars(spec):
        spec = build_spec(spec.get("genre") or "pop", key=spec["key"], mode=spec["mode"], tempo=spec["tempo"],
                          bars=args.bars, seed=args.seed)
    return spec


def cmd_new(args):
    _validate_new(args)
    if args.genre:
        spec = build_spec(args.genre, key=args.key, mode=args.mode, tempo=args.tempo, bars=args.bars, seed=args.seed)
        from .llm import refine
        spec, _ = refine(spec)
        src = "offline"
    else:
        if not args.prompt:
            sys.exit("give a prompt or --genre")
        r = _composer(args).generate(args.prompt, seed=args.seed)
        spec, src = _overrides(r.spec, args), r.source
        if r.explanation:
            print(f"[{src}] {r.explanation}")
    _write(spec, args.out, args.png)
    print(format_report(critique(spec)))


def cmd_edit(args):
    spec = specmod.load(args.spec)
    if args.ops:
        ops = json.loads(Path(args.ops).read_text() if Path(args.ops).exists() else args.ops)
        new = apply_ops(spec, ops)
        print(f"applied {len(ops) if isinstance(ops, list) else 1} op(s)")
    else:
        r = _composer(args).edit(spec, args.instruction)
        new = r.spec
        print(f"[{r.source}] ops: {json.dumps(r.ops)}")
    _write(new, args.out or args.spec, args.png)
    print(format_report(critique(new)))


def cmd_render(args):
    spec = specmod.load(args.spec)
    _write(spec, args.out or args.spec, args.png)


def cmd_inspect(args):
    p = Path(args.file)
    if p.suffix.lower() in (".mid", ".midi"):
        info = inspect_midi(p)
        print(json.dumps(info, indent=2))
        if args.to_spec:
            spec = import_midi(p)
            specmod.save(spec, args.to_spec)
            print(f"imported as editable spec -> {args.to_spec}")
    else:
        spec = specmod.load(p)
        from .arranger import render
        perf = render(spec)
        print(f"{spec.get('title')} — {spec.get('genre', '-')} | {spec['key']} {spec['mode']} | {spec['tempo']} BPM | "
              f"{specmod.total_bars(spec)} bars")
        for s in spec["sections"]:
            print(f"  section {s['name']:<10} {s['bars']:>2} bars energy {s.get('energy', .5):.2f}  {' '.join(s['chords'])}")
        print("  arrangement:", " ".join(spec["arrangement"]))
        for t, rt in zip(spec["tracks"], perf.tracks):
            g = t["generator"]
            print(f"  track {t['name']:<14} {t['role']:<7} ch{t['channel'] + 1:<3} prog {t['program']:<3} "
                  f"{g['type']}/{g.get('style') or g.get('pattern') or '-'} notes {len(rt.notes)}"
                  + ("  (muted)" if t.get("mute") else "") + (f"  sections {t['sections']}" if t.get("sections") else ""))
        print(format_report(critique(perf)))


def cmd_critique(args):
    rep = critique(specmod.load(args.spec))
    print(json.dumps(rep, indent=2) if args.json else format_report(rep))


def cmd_genres(args):
    if args.name:
        print(json.dumps(get_genre(args.name), indent=2, default=str))
        return
    for g in available_genres():
        d = get_genre(g)
        src = "+".join(s if s == "builtin" else Path(s).parent.name for s in d.get("_sources", ["builtin"]))
        print(f"{g:<14} {d['tempo']['min']}-{d['tempo']['max']} BPM  [{src}]  {d.get('description', '')}")
    rep = packs.report()
    if rep.get("shadowed"):
        print("\nalso available as id@pack:", ", ".join(sorted(rep["shadowed"])))
    if rep.get("failed"):
        print("\npack files that failed to load:")
        for f, why in rep["failed"].items():
            print(f"  {f}: {why}")


def cmd_preview(args):
    from .preview import piano_roll
    out = args.out or str(Path(args.spec).with_suffix(".png"))
    piano_roll(specmod.load(args.spec), out)
    print(f"wrote {out}")


def cmd_chat(args):
    comp = _composer(args)
    path = Path(args.spec)
    if path.exists():
        ed = Editor(specmod.load(path))
    else:
        ed = None
    print("midigen chat — describe a song, then edit it in plain words. Commands: /undo /redo /show /critic /save /help /quit")
    print("LLM provider:", "configured" if comp.provider else "none (offline rules; set MIDIGEN_API_KEY/BASE_URL/MODEL)")
    while True:
        try:
            line = input("» ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit", "quit", "exit"):
            break
        if line == "/help":
            print(HELP)
            continue
        if ed is None and not line.startswith("/"):
            r = comp.generate(line, seed=args.seed)
            ed = Editor(r.spec)
            print(f"[{r.source}] new {r.spec.get('genre')} song: {r.spec['key']} {r.spec['mode']} {r.spec['tempo']} BPM, "
                  f"{specmod.total_bars(r.spec)} bars. critic {r.report['score']}/100")
        elif ed is None:
            print("describe a song first")
            continue
        elif line in ("/undo", "undo"):
            print("undid:", ed.undo() or "nothing to undo")
        elif line in ("/redo", "redo"):
            print("redid:", ed.redo() or "nothing to redo")
        elif line == "/show":
            ns = argparse.Namespace(file=str(path), to_spec=None)
            specmod.save(ed.spec, path)
            cmd_inspect(ns)
            continue
        elif line == "/critic":
            print(format_report(critique(ed.spec)))
            continue
        elif line.startswith("/new"):
            r = comp.generate(line[4:].strip() or "pop song", seed=args.seed)
            ed.replace(r.spec, "new song")
            print(f"[{r.source}] new song {r.spec.get('genre')} {r.spec['key']} {r.spec['mode']}")
        elif line != "/save":
            try:
                r = comp.edit(ed.spec, line)
            except NotUnderstood as e:
                print(f"didn't understand ({e}). Try /help, or configure an LLM provider.")
                continue
            except OpError as e:
                print("edit rejected:", e)
                continue
            ed.replace(r.spec, line)
            print(f"[{r.source}] {json.dumps(r.ops)}  critic {r.report['score']}/100")
        _write(ed.spec, str(path), args.png, quiet=True)
        print(f"saved {path.with_suffix('.json')} / {path.with_suffix('.mid')}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="midigen", description="Theory-driven multitrack MIDI generator/arranger")
    ap.add_argument("--genre-path", action="append", default=[], metavar="DIR_OR_FILE",
                    help="extra genre-pack folder or .json file (repeatable; later wins over earlier, over "
                         "MIDIGEN_GENRE_PATH, the default packs and the builtin genres)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", aliases=["generate"], help="create a song from a prompt or a genre")
    p.add_argument("prompt", nargs="?")
    p.add_argument("-o", "--out", default="song")
    p.add_argument("--genre")
    p.add_argument("--key")
    p.add_argument("--mode")
    p.add_argument("--tempo", type=float, help="BPM of the time signature's beat note, 20-300 (default: genre default)")
    p.add_argument("--bars", type=int, help=f"total bars, 1-{MAX_BARS} (default: the genre's form)")
    p.add_argument("--seed", type=int, default=1,
                   help="random seed (default 1, so the same command gives the same song); different seeds vary "
                        "the progression choice, motifs, fills and voicings")
    p.add_argument("--random-seed", action="store_true", help="pick a fresh random seed and print it")
    p.add_argument("--png", action="store_true", help="also write a piano-roll PNG")
    p.add_argument("--offline", action="store_true", help="never call the LLM")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("edit", help="edit a spec with an instruction or JSON ops")
    p.add_argument("spec")
    p.add_argument("instruction", nargs="?", default="")
    p.add_argument("--ops", help="JSON ops (string or file) instead of natural language")
    p.add_argument("-o", "--out")
    p.add_argument("--png", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(fn=cmd_edit)

    p = sub.add_parser("render", help="render spec.json to .mid")
    p.add_argument("spec")
    p.add_argument("-o", "--out")
    p.add_argument("--png", action="store_true")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("inspect", help="summarise a spec.json or a .mid file")
    p.add_argument("file")
    p.add_argument("--to-spec", help="(for .mid) import into an editable spec json")
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("critique", help="run the music-theory critic on a spec")
    p.add_argument("spec")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_critique)

    p = sub.add_parser("genres", help="list genres or show one profile")
    p.add_argument("name", nargs="?")
    p.set_defaults(fn=cmd_genres)

    p = sub.add_parser("preview", help="piano-roll PNG of a spec")
    p.add_argument("spec")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("chat", help="interactive REPL (LLM if configured, else offline rules)")
    p.add_argument("--spec", default="chat_song")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--png", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(fn=cmd_chat)

    # --genre-path is accepted before or after the subcommand
    argv = list(sys.argv[1:] if argv is None else argv)
    gp, rest, i = [], [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--genre-path" and i + 1 < len(argv):
            gp.append(argv[i + 1])
            i += 2
            continue
        if a.startswith("--genre-path="):
            gp.append(a.split("=", 1)[1])
        else:
            rest.append(a)
        i += 1
    args = ap.parse_args(rest)
    args.genre_path = gp
    if gp:
        missing = [x for x in gp if not Path(x).expanduser().exists()]
        if missing:
            sys.exit(f"error: --genre-path not found: {', '.join(missing)}")
        packs.set_paths(gp)
    if getattr(args, "cmd", None) == "edit" and not args.instruction and not args.ops:
        ap.error("edit needs an instruction or --ops")
    if getattr(args, "spec", None) and args.cmd == "chat":
        args.spec = str(Path(args.spec).with_suffix(".json"))
    try:
        args.fn(args)
    except UnknownGenre as e:
        sys.exit(f"error: {e}")
    except (specmod.SpecError, OpError, NotUnderstood, ValueError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
