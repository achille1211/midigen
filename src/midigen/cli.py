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
from . import learn


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
        from .nl import duration_seconds, fmt_duration
        print(f"wrote {js} and {mid}{' (+png)' if png else ''} — {specmod.total_bars(spec)} bars @ {spec['tempo']} BPM "
              f"(≈ {fmt_duration(duration_seconds(spec))}), "
              f"{spec['key']} {spec['mode']}, tracks [{counts}]")


def _summary(rep: dict, verbose: bool = False) -> str:
    """One plain line by default; problems (warn/error) listed below it; info notes only with -v."""
    if verbose:
        return format_report(rep)
    shown = [f for f in rep["findings"] if f["severity"] in ("warn", "warning", "error")]
    info = len(rep["findings"]) - len(shown)
    line = f"critic score: {rep['score']}/100" + (f" ({info} minor note(s); -v to show)" if info else "")
    return "\n".join([line] + [f"  [{f['severity']}] {f['check']}: {f['message']}"
                                + (f" -> {f['suggestion']}" if f.get("suggestion") else "") for f in shown])


def _auto_out(spec: dict) -> str:
    """song title -> slug, uniquified (-1, -2, ...) so a new song never silently replaces an old one."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", (spec.get("title") or spec.get("genre") or "song").lower()).strip("-")[:40] or "song"
    out, i = slug, 1
    while any(Path(out + ext).exists() for ext in (".json", ".mid")):
        out, i = f"{slug}-{i}", i + 1
    return out


def _composer(args) -> Composer:
    return Composer(use_env=not getattr(args, "offline", False))


MAX_BARS = 256


def _validate_new(args) -> None:
    """Explicit range checks (0 is a value, not 'unset')."""
    if args.tempo is not None and not 20 <= args.tempo <= 300:
        raise ValueError(f"--tempo must be between 20 and 300 BPM (got {args.tempo:g})")
    if getattr(args, "seconds", None) is not None:
        if not 2 <= args.seconds <= 1800:
            raise ValueError(f"--seconds must be between 2 and 1800 (got {args.seconds:g})")
        if args.bars is not None:
            raise ValueError("give --bars or --seconds, not both")
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
    if getattr(args, "seconds", None):
        from .nl import bars_for_seconds
        args.bars = bars_for_seconds(args.seconds, float(spec["tempo"]), spec.get("time_signature", [4, 4])[0])
    if args.bars is not None and args.bars != specmod.total_bars(spec):
        spec = build_spec(spec.get("genre") or "pop", key=spec["key"], mode=spec["mode"], tempo=spec["tempo"],
                          bars=args.bars, seed=args.seed)
    return spec


def _recorded(spec: dict, out: str, sid: str | None = None, **kw) -> None:
    """Record the written song in the learning store (or point the composer's record at the file)."""
    js = str(_paths(out)[0])
    if sid and learn.store().song(sid) and learn.store().song(sid)["fingerprint"] == learn.fingerprint(spec):
        learn.store().update_song(sid, path=str(Path(js).resolve()))
    else:
        sid = learn.record(spec, path=js, **kw)
    if sid:
        print(f"song id {sid} — rate it: midigen rate {sid} 1-5 [--note \"...\"]  (or: midigen rate {js} 4)")


def cmd_new(args):
    _validate_new(args)
    sid, kw = None, {}
    if args.genre:
        spec = build_spec(args.genre, key=args.key, mode=args.mode, tempo=args.tempo, bars=args.bars, seed=args.seed)
        kw["choices"] = learn.choices_for(spec)
        from .llm import refine
        spec, _ = refine(spec)
        src = "offline"
        if getattr(args, "seconds", None):
            spec = _overrides(spec, args)
    else:
        if not args.prompt:
            sys.exit("give a prompt or --genre")
        prompt = args.prompt
        if args.seconds:  # let the interpreter size it, so solo/instrument choices survive
            prompt = f"{prompt}, {args.seconds:g} seconds"
            args.seconds = None
        r = _composer(args).generate(prompt, seed=args.seed)
        spec, src, sid = _overrides(r.spec, args), r.source, r.song_id
        kw["prompt"] = args.prompt
        if r.explanation:
            print(f"[{src}] {r.explanation}")
    if not args.out:
        args.out = _auto_out(spec)
    elif any(p.exists() for p in _paths(args.out)):
        print(f"note: overwriting existing {_paths(args.out)[0]} / {_paths(args.out)[1]}")
    _write(spec, args.out, args.png)
    rep = critique(spec)
    print(_summary(rep, getattr(args, "verbose", False)))
    _recorded(spec, args.out, sid, source=src, report=rep, **kw)


def cmd_edit(args):
    spec = specmod.load(args.spec)
    parent = None
    if learn.recording_enabled():
        try:  # also notices hand edits made to the file since it was recorded
            parent = learn.resolve(args.spec)["id"]
        except (KeyError, ValueError, OSError):
            parent = None
    from . import linked
    if args.ops:
        ops = json.loads(Path(args.ops).read_text() if Path(args.ops).exists() else args.ops)
        new = apply_ops(spec, ops)
        if args.auto_follow:
            sugg = linked.suggest_followups(spec, ops, limit=2)
            ops = (ops if isinstance(ops, list) else [ops]) + [o for x in sugg for o in x["ops"]]
            new = apply_ops(spec, ops)
            if sugg:
                print("followed up:", "; ".join(x["reason"] for x in sugg))
        print(f"applied {len(ops) if isinstance(ops, list) else 1} op(s)")
    else:
        r = _composer(args).edit(spec, args.instruction, auto_follow=args.auto_follow)
        new, ops = r.spec, r.ops
        print(f"[{r.source}] ops: {json.dumps(r.ops)}")
        if r.followups:
            print("followed up:", "; ".join(x["reason"] for x in r.followups))
    if args.suggest:
        print("suggested follow-ups (linked tracks; apply with --ops or re-run with --auto-follow):")
        print(linked.format_suggestions(linked.suggest_followups(spec, ops)))
    out = args.out or args.spec
    if not args.out:  # editing in place: keep the previous version next to it
        import shutil
        js = _paths(out)[0]
        if js.exists():
            bak = js.with_suffix(".prev.json")
            shutil.copyfile(js, bak)
            print(f"saved previous version as {bak} — to undo, copy it back over {js.name}")
    _write(new, out, args.png)
    print(_summary(critique(new), getattr(args, "verbose", False)))
    if args.ops:
        sid = learn.record_edit(spec, new, ops=ops, source="ops", path=str(_paths(out)[0]), parent=parent)
    else:
        sid = r.song_id
        if sid:
            learn.store().update_song(sid, path=str(_paths(out)[0].resolve()))
    if sid:
        print(f"song id {sid} — rate it: midigen rate {sid} 1-5")


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
    last_edit = None
    print("midigen chat — describe a song, then edit it in plain words. Commands: /undo /redo /show /critic /suggest /follow N /save "
          "/rate N [note] /help /quit")
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
        elif line == "/suggest" or line.startswith("/follow"):
            from . import linked
            if not last_edit:
                print("make an edit first; /suggest lists follow-ups for the other tracks")
                continue
            sugg = linked.suggest_followups(*last_edit)
            if line == "/suggest":
                print(linked.format_suggestions(sugg) + ("\n  apply with /follow N (or /follow all)" if sugg else ""))
                continue
            arg = line[len("/follow"):].strip() or "1"
            pick = sugg if arg == "all" else [s for s in sugg if str(s["rank"]) in arg.replace(",", " ").split()]
            if not pick:
                print("nothing to apply; /suggest lists the options")
                continue
            ed.replace(apply_ops(ed.spec, [o for s in pick for o in s["ops"]]), f"follow-up {arg}")
            last_edit = None
            print("applied:", "; ".join(s["reason"] for s in pick), f" critic {critique(ed.spec)['score']}/100")
        elif line == "/critic":
            print(format_report(critique(ed.spec)))
            continue
        elif line.startswith("/rate"):
            parts = line.split(maxsplit=2)
            try:
                _print_fb(learn.rate(ed.spec, float(parts[1]), note=parts[2] if len(parts) > 2 else None))
            except (IndexError, ValueError, KeyError) as e:
                print("usage: /rate 1-5 [note]", f"({e})" if not isinstance(e, IndexError) else "")
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
            last_edit = (ed.spec, r.ops) if r.ops else None
            ed.replace(r.spec, line)
            print(f"[{r.source}] {json.dumps(r.ops)}  critic {r.report['score']}/100  (/suggest for linked-track follow-ups)")
        _write(ed.spec, str(path), args.png, quiet=True)
        row = learn.store().find(ed.spec) if learn.recording_enabled() else None
        if row:
            learn.store().update_song(row["id"], path=str(path.with_suffix(".json").resolve()))
        print(f"saved {path.with_suffix('.json')} / {path.with_suffix('.mid')}")


def _target(args) -> str | None:
    if getattr(args, "track", None):
        return f"track:{args.track}"
    if getattr(args, "section", None):
        return f"section:{args.section}"
    return None


def _print_fb(out: dict) -> None:
    u = out.get("user_reward")
    bits = [f"song {out.get('song_id')} ({out.get('genre') or 'no genre'})"]
    if u is not None:
        bits.append(f"user score {u:.2f}")
    for sg in out.get("signals", []):
        bits.append(f"learned: {sg['phrase']}" if "phrase" in sg else f"credited {', '.join(sg['dims'])} ({sg['reward']:.2f})")
    print("; ".join(bits))


def cmd_rate(args):
    _print_fb(learn.rate(args.song, args.rating, note=args.note, target=_target(args)))


def cmd_feedback(args):
    _print_fb(learn.feedback(args.song, args.note, target=_target(args)))


def cmd_keep(args):
    _print_fb(learn.keep(args.song, note=args.note))


def cmd_reject(args):
    _print_fb(learn.reject(args.song, note=args.note))


def cmd_learn(args):
    what = args.what
    if what == "status":
        st = learn.status(args.genre and resolve_genre(args.genre))
        print(json.dumps(st, indent=1) if args.json else learn.format_status(st))
    elif what == "reset":
        removed = learn.reset(args.genre and resolve_genre(args.genre), store_too=args.all)
        print(f"removed {len(removed)} file(s)" + ("".join(f"\n  {r}" for r in removed)))
    elif what == "export":
        print("wrote", learn.export(args.path or "midigen-learned.json", args.genre and resolve_genre(args.genre)))
    elif what == "import":
        if not args.path:
            raise ValueError("learn import needs a path")
        print("imported weights for:", ", ".join(learn.import_weights(args.path)) or "nothing")
    else:
        p = Path(what)
        if p.suffix.lower() not in (".mid", ".midi") or not p.exists():
            raise ValueError(f"{what!r}: give a .mid file to learn from, or status | reset | export | import")
        if not args.genre:
            raise ValueError("learning from a MIDI file needs --genre")
        out = learn.learn_midi(p, resolve_genre(args.genre))
        a, ad = out["analysis"], out["added"]
        print(f"learned from {p.name} into [{out['genre']}]: {a['key']} {a['mode']} {a['tempo']} BPM, {a['bars']} bars")
        for x in ad["progressions"]:
            print(f"  progression {x['arm']}: {' '.join(x['chords'])}")
        if ad["bass"]:
            print(f"  bass pattern {ad['bass']}: {a['bass']['rhythm']}")
        if ad["cells"]:
            print(f"  lead rhythm cells {ad['cells']}: {a['lead']['cells']} (stepwise {a['lead']['stepwise']:.0%})")
        if ad["drums"]:
            print(f"  nearest drum pattern {ad['drums']['nearest']} (similarity {ad['drums']['similarity']})")
        if ad["energy"]:
            print(f"  energy curve {ad['energy']}")
        print("new songs in this genre now sample these as options; rate them to keep the ones you like")


def cmd_train(args):
    genres = [resolve_genre(g) for g in args.genres.split(",")] if args.genres else None
    res = learn.train(genres, rounds=args.rounds, candidates=args.candidates, bars=args.bars,
                      chart=args.chart, seed=args.seed)
    print(res["ascii_chart"])
    first, last = res["curve"][0]["eval_critic"], res["final_mean"]
    print(f"baseline {res['baseline_mean']:.2f} -> round 0 {first:.2f} -> final {last:.2f} "
          f"(critic score on fixed eval seeds; the critic is a proxy — your ratings are the ground truth)")
    if res.get("chart"):
        print("chart:", res["chart"])
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1))
        print("wrote", args.json)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="midigen", description="Theory-driven multitrack MIDI generator/arranger")
    ap.add_argument("--genre-path", action="append", default=[], metavar="DIR_OR_FILE",
                    help="extra genre-pack folder or .json file (repeatable; later wins over earlier, over "
                         "MIDIGEN_GENRE_PATH, the default packs and the builtin genres)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", aliases=["generate"], help="create a song from a prompt or a genre")
    p.add_argument("prompt", nargs="?")
    p.add_argument("-o", "--out", help="output name (default: from the song title, never overwriting)")
    p.add_argument("--seconds", type=float, help="target length in seconds (converted to bars at the song's tempo)")
    p.add_argument("-v", "--verbose", action="store_true", help="show every critic note")
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
    p.add_argument("--no-learn", action="store_true", help="ignore learned preferences (plain genre defaults)")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("edit", help="edit a spec with an instruction or JSON ops")
    p.add_argument("spec")
    p.add_argument("instruction", nargs="?", default="")
    p.add_argument("--ops", help="JSON ops (string or file) instead of natural language")
    p.add_argument("-o", "--out")
    p.add_argument("--png", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true", help="show every critic note")
    p.add_argument("--suggest", action="store_true", help="also list ranked follow-up edits for the other tracks")
    p.add_argument("--auto-follow", action="store_true", help="apply the top validated linked-track follow-ups too")
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
    p.add_argument("--no-learn", action="store_true", help="ignore learned preferences")
    p.set_defaults(fn=cmd_chat)

    ref_help = "song id, 'last', or the song's .json/.mid path"
    p = sub.add_parser("rate", help="rate a song 1-5 (teaches the per-genre policy)")
    p.add_argument("song", help=ref_help)
    p.add_argument("rating", type=float)
    p.add_argument("--note", help="free text, e.g. 'love the groove, chorus should be bigger'")
    p.add_argument("--track", help="rate one track (name or role) instead of the song")
    p.add_argument("--section", help="rate one section (e.g. chorus) instead of the song")
    p.set_defaults(fn=cmd_rate)

    p = sub.add_parser("feedback", help="free-text feedback on a song, a track or a section")
    p.add_argument("song", help=ref_help)
    p.add_argument("note")
    p.add_argument("--track")
    p.add_argument("--section")
    p.set_defaults(fn=cmd_feedback)

    for name, fn, hp in (("keep", cmd_keep, "mark a song as a keeper (positive signal)"),
                         ("reject", cmd_reject, "mark a song as rejected (negative signal)")):
        p = sub.add_parser(name, help=hp)
        p.add_argument("song", help=ref_help)
        p.add_argument("--note")
        p.set_defaults(fn=fn)

    p = sub.add_parser("learn", help="learn from a reference MIDI (learn FILE.mid --genre X), or status | reset | "
                                     "export [PATH] | import PATH")
    p.add_argument("what", help="a .mid file, or status / reset / export / import")
    p.add_argument("path", nargs="?")
    p.add_argument("--genre")
    p.add_argument("--all", action="store_true", help="(reset) also delete the experience store")
    p.add_argument("--json", action="store_true", help="(status) machine-readable output")
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("train", help="offline self-training against the critic (+ your learned ratings)")
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--candidates", type=int, default=8, help="candidates per genre per round")
    p.add_argument("--genres", help="comma-separated (default: all)")
    p.add_argument("--bars", type=int, help="bars per candidate (default: the genre form; 16 is faster)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--chart", help="write the improvement curve as a PNG")
    p.add_argument("--json", help="write the full result as JSON")
    p.set_defaults(fn=cmd_train)

    from . import doctor as _doctor, ui as _ui  # setup helpers: `midigen doctor`, `midigen ui`
    _doctor.add_parser(sub)
    _ui.add_parser(sub)

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
    if getattr(args, "no_learn", False):
        import os
        os.environ["MIDIGEN_LEARN"] = "0"
    if getattr(args, "spec", None) and args.cmd == "chat":
        args.spec = str(Path(args.spec).with_suffix(".json"))
    try:
        args.fn(args)
    except FileNotFoundError as e:
        sys.exit(f"error: file not found: {e.filename or e}")
    except IsADirectoryError as e:
        sys.exit(f"error: {e.filename or e} is a folder, not a song file")
    except UnicodeDecodeError:
        sys.exit("error: that file is not a text song spec (.json). For a .mid file use: midigen inspect FILE.mid --to-spec song.json")
    except json.JSONDecodeError as e:
        sys.exit(f"error: the song file is not valid JSON (line {e.lineno}, column {e.colno}: {e.msg}). "
                 "If you edited it by hand, check for a missing comma or quote; a .prev.json backup may exist next to it.")
    except UnknownGenre as e:
        sys.exit(f"error: {e}")
    except (specmod.SpecError, OpError, NotUnderstood, ValueError) as e:
        sys.exit(f"error: {e}")
    except KeyError as e:
        sys.exit(f"error: {e.args[0] if e.args else e}")


if __name__ == "__main__":
    main()
