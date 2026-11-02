"""Small, validated, reversible edit operations on a song spec, plus an undo/redo editor.

Every op is a JSON object {"op": name, ...}. `apply_ops` never mutates its input: it works on a copy,
applies all ops, validates the result and either returns the new spec or raises OpError with
LLM-readable messages (so a model can be asked to fix its own mistakes)."""
from __future__ import annotations

import copy
import random

import jsonschema

from . import harmony, spec as specmod
from .core import stable_seed
from .schema import OP_SCHEMAS
from .spec import INSTRUMENTS, SpecError, find_section, find_track, make_track
from .theory import note_to_pc, pc_to_name, transpose_symbol


class OpError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_op(op) -> list[str]:
    if not isinstance(op, dict) or "op" not in op:
        return [f"each op must be an object with an 'op' field; got {op!r}"]
    sch = OP_SCHEMAS.get(op["op"])
    if sch is None:
        return [f"unknown op {op['op']!r}; valid ops: {sorted(OP_SCHEMAS)}"]
    v = jsonschema.Draft202012Validator(sch)
    return [f"op {op['op']}: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in v.iter_errors(op)]


def validate_ops(ops) -> list[str]:
    if isinstance(ops, dict):
        ops = [ops]
    if not isinstance(ops, list):
        return ["ops must be a list of op objects"]
    errs = []
    for i, op in enumerate(ops):
        errs += [f"ops[{i}] {e}" for e in validate_op(op)]
    return errs


def _tracks(s, ref):
    return s["tracks"] if ref is None else [find_track(s, ref)]


def _sections(s, ref):
    return s["sections"] if ref is None else [find_section(s, ref)]


def _gen_params(t, section, params):
    if section:
        t.setdefault("overrides", {}).setdefault(section, {}).update(params)
    else:
        t["generator"].update(params)


def _apply(s: dict, op: dict) -> None:
    name = op["op"]
    if name == "set_tempo":
        s["tempo"] = op["bpm"]
    elif name == "change_tempo":
        s["tempo"] = max(20, min(300, s["tempo"] + op["delta"]))
    elif name == "set_key":
        old_key, old_mode = s["key"], s["mode"]
        new_mode = op.get("mode", old_mode)
        if new_mode != old_mode:
            # Changing the melody scale alone leaves explicit major chords sounding happy.
            # Revoice diatonic harmony in the destination mode; retain chromatic colours.
            from .theory import parse_chord, scale_pcs
            old_scale = set(scale_pcs(old_key, old_mode))
            for sec in s["sections"]:
                chords = []
                for symbol in sec["chords"]:
                    chord = parse_chord(symbol, old_key, old_mode)
                    degree = harmony.degree_of(symbol, old_key, old_mode)
                    if degree is not None and set(chord.pcs) <= old_scale:
                        symbol = harmony.diatonic_chord(degree, new_mode,
                                                       seventh=any(x in chord.intervals for x in (10, 11)),
                                                       ninth=14 in chord.intervals)
                    chords.append(symbol)
                sec["chords"] = chords
        s["key"] = op["key"][0].upper() + op["key"][1:]
        if "mode" in op:
            s["mode"] = op["mode"]
    elif name == "transpose":
        n = op["semitones"]
        s["key"] = pc_to_name((note_to_pc(s["key"]) + n) % 12, flats="b" in s["key"])
        for sec in s["sections"]:
            sec["chords"] = [transpose_symbol(c, n) for c in sec["chords"]]
        for t in s["tracks"]:
            if t["generator"]["type"] == "clip" and t["channel"] != 9:
                for clip in t["generator"].get("clips", {}).values():
                    for note in clip:
                        note[2] = max(0, min(127, note[2] + n))
    elif name == "set_time_signature":
        old_den = s.get("time_signature", [4, 4])[1]
        s["time_signature"] = [op["numerator"], op["denominator"]]
        if op["denominator"] != old_den and not op.get("keep_tempo"):
            # spec tempo counts the denominator note: rescale it so the quarter-note pulse (the song's
            # speed) is unchanged, e.g. 4/4 at 110 -> 6/8 at 220 (eighths) = 110 quarter BPM
            s["tempo"] = max(20, min(300, round(s["tempo"] * op["denominator"] / old_den, 2)))
    elif name == "set_chords":
        sec = find_section(s, op["section"])
        sec["chords"] = list(op["chords"])
        sec.pop("chord_beats", None)
        if "beats_per_chord" in op:
            sec["beats_per_chord"] = op["beats_per_chord"]
    elif name == "add_section":
        new = copy.deepcopy(op["section"])
        if any(x["name"] == new["name"] for x in s["sections"]):
            raise OpError([f"add_section: a section named {new['name']!r} already exists"])
        new.setdefault("energy", 0.5)
        s["sections"].append(new)
        if op.get("add_to_arrangement", True):
            pos = op.get("position", len(s["arrangement"]))
            s["arrangement"].insert(pos, new["name"])
    elif name == "remove_section":
        sec = find_section(s, op["section"])
        s["sections"].remove(sec)
        s["arrangement"] = [a for a in s["arrangement"] if a != sec["name"]]
        for t in s["tracks"]:
            if t.get("sections"):
                t["sections"] = [x for x in t["sections"] if x != sec["name"]] or None
            t.get("overrides", {}).pop(sec["name"], None)
            if t["generator"]["type"] == "clip":
                t["generator"].get("clips", {}).pop(sec["name"], None)
    elif name == "set_section":
        sec = find_section(s, op["section"])
        if "bars" in op:
            sec["bars"] = op["bars"]
        if "energy" in op:
            sec["energy"] = op["energy"]
        if op.get("new_name"):
            old, new = sec["name"], op["new_name"]
            sec["name"] = new
            s["arrangement"] = [new if a == old else a for a in s["arrangement"]]
            for t in s["tracks"]:
                if t.get("sections"):
                    t["sections"] = [new if x == old else x for x in t["sections"]]
                if old in t.get("overrides", {}):
                    t["overrides"][new] = t["overrides"].pop(old)
                if t["generator"]["type"] == "clip" and old in t["generator"].get("clips", {}):
                    t["generator"]["clips"][new] = t["generator"]["clips"].pop(old)
    elif name == "extend_section":
        sec = find_section(s, op["section"])
        sec["bars"] = sec["bars"] + op["bars"]
        if not 1 <= sec["bars"] <= 64:
            raise OpError([f"extend_section: {sec['name']} would have {sec['bars']} bars (allowed 1-64)"])
    elif name == "set_arrangement":
        s["arrangement"] = list(op["order"])
    elif name == "repeat_section":
        sec = find_section(s, op["section"])
        if "position" in op:
            s["arrangement"].insert(op["position"], sec["name"])
        else:
            last = max(i for i, a in enumerate(s["arrangement"] + [sec["name"]]) if a == sec["name"])
            s["arrangement"].insert(min(last + 1, len(s["arrangement"])), sec["name"])
    elif name == "add_track":
        t = copy.deepcopy(op["track"])
        if any(x["name"].lower() == t["name"].lower() for x in s["tracks"]):
            raise OpError([f"add_track: a track named {t['name']!r} already exists"])
        s["tracks"].append(t)
    elif name == "add_instrument":
        role = op["role"]
        g = None
        if s.get("genre"):
            from .genres import genre_track, get_genre
            try:
                g = get_genre(s["genre"])
            except KeyError:
                g = None
        if op.get("instrument") and op["instrument"].lower().replace(" ", "_") not in INSTRUMENTS:
            raise OpError([f"add_instrument: unknown instrument {op['instrument']!r}; known: {sorted(INSTRUMENTS)}"])
        inst = op.get("instrument", "").lower().replace(" ", "_") or None
        if g and role in g["instruments"]:
            t = genre_track(g, role, s["sections"], name=op.get("name"), instrument=inst)
        else:
            t = make_track(role, name=op.get("name"), instrument=inst)
            default = {"bass": [33, 52], "chords": [50, 70], "pad": [48, 70], "arp": [72, 91], "lead": [67, 86]}.get(role)
            if default and role in ("arp", "lead"):
                other = [x["generator"].get("range") for x in s["tracks"]
                         if x["role"] == ("lead" if role == "arp" else "arp") and x["generator"].get("range")]
                if other:
                    lo, hi = other[0]
                    default = [min(hi - 2, 84), min(hi - 2, 84) + 19] if role == "arp" else [max(lo - 19, 55), max(lo - 19, 55) + 17]
            if default and t["generator"]["type"] not in ("drums", "clip"):
                t["generator"]["range"] = default
                t["generator"].pop("octave", None)
        base, n = t["name"], 2
        while any(x["name"].lower() == t["name"].lower() for x in s["tracks"]):
            t["name"] = f"{base} {n}"
            n += 1
        if "sections" in op:
            t["sections"] = op["sections"]
        t["channel"] = None
        s["tracks"].append(t)
    elif name == "remove_track":
        s["tracks"].remove(find_track(s, op["track"]))
    elif name == "replace_track":
        old = find_track(s, op["track"])
        s["tracks"][s["tracks"].index(old)] = copy.deepcopy(op["with"])
    elif name == "set_track":
        t = find_track(s, op["track"])
        for k in ("name", "program", "volume", "pan", "mute", "sections"):
            if k in op:
                t[k] = op[k]
    elif name == "set_generator":
        t = find_track(s, op["track"])
        if op.get("section"):
            find_section(s, op["section"])
        _gen_params(t, op.get("section"), copy.deepcopy(op["params"]))
    elif name == "set_drum_pattern":
        ts = [find_track(s, op["track"])] if op.get("track") else [t for t in s["tracks"] if t["generator"]["type"] == "drums"]
        if not ts:
            raise OpError(["set_drum_pattern: the song has no drum track; add one with add_instrument role=drums"])
        for t in ts:
            _gen_params(t, op.get("section"), {"pattern": op["pattern"]})
    elif name == "set_density":
        if "density" not in op and "delta" not in op:
            raise OpError(["set_density: give density or delta"])
        for t in _tracks(s, op.get("track")):
            if t["generator"]["type"] == "clip":
                continue
            if not op.get("section"):
                for section, override in list(t.get("overrides", {}).items()):
                    if "density" in override or "rhythm" in override or "rhythm_scale" in override:
                        _apply(s, dict(op, track=t["name"], section=section))
            src = t.get("overrides", {}).get(op.get("section"), {}) if op.get("section") else {}
            cur = src.get("density", t["generator"].get("density", 0.5))
            val = op["density"] if "density" in op else cur + op["delta"]
            val = round(max(0.0, min(1.0, val)), 3)
            params = {"density": val}
            rhythm = src.get("rhythm", t["generator"].get("rhythm"))
            if rhythm and t["role"] in ("bass", "chords") and val != cur:
                rhythm = list(rhythm)
                candidates = [i for i, v in enumerate(rhythm) if (not v if val > cur else v and i % 16)]
                # Prefer eighth-note subdivisions; keep bar downbeats when thinning.
                candidates.sort(key=lambda i: (i % 2, i % 4, i))
                count = min(len(candidates), max(1, round(abs(val - cur) * len(rhythm))))
                for i in candidates[:count]:
                    rhythm[i] = 80 if val > cur else 0
                params["rhythm"] = rhythm
                tokens = src.get("tokens", t["generator"].get("tokens"))
                if val > cur and tokens and len(tokens) == len(rhythm):
                    tokens = list(tokens)
                    for i in candidates[:count]:
                        if tokens[i] is None or tokens[i] == "rest":
                            tokens[i] = "root"
                    params["tokens"] = tokens
            elif t["role"] in ("bass", "chords") and val != cur:
                scale = src.get("rhythm_scale", t["generator"].get("rhythm_scale", 1.0))
                params["rhythm_scale"] = round(max(.25, min(4, scale * (1 + (val - cur) * 2))), 3)
            _gen_params(t, op.get("section"), params)
    elif name == "humanize":
        for t in _tracks(s, op.get("track")):
            t["generator"]["humanize"] = {"timing": op.get("timing", 0.015), "velocity": op.get("velocity", 10)}
    elif name == "reseed":
        seed = op.get("seed")
        if op.get("track"):
            t = find_track(s, op["track"])
            t["generator"]["seed"] = seed if seed is not None else t["generator"].get("seed", 0) + 1
        else:
            s["seed"] = seed if seed is not None else s.get("seed", 1) + 1
    elif name == "reharmonize":
        for sec in _sections(s, op.get("section")):
            rng = random.Random(stable_seed("reharm", sec["name"], op.get("seed", s.get("seed", 1)), op["technique"]))
            new = harmony.reharmonize(sec["chords"], s["key"], s["mode"], rng, op["technique"])
            if len(new) != len(sec["chords"]):
                sec.pop("chord_beats", None)
            sec["chords"] = new
    elif name == "set_cadence":
        from .genres import _apply_cadence
        sec = find_section(s, op["section"])
        if not all(_is_roman(c) for c in sec["chords"]):
            sec["chords"] = harmony.to_roman(sec["chords"], s["key"], s["mode"])
        new = _apply_cadence(sec["chords"], s["mode"], op["cadence"])
        if len(new) != len(sec["chords"]):
            sec.pop("chord_beats", None)
        sec["chords"] = new
        sec["cadence"] = op["cadence"]
    elif name == "apply_genre":
        _apply_genre(s, op["genre"], op.get("keep_harmony", True))
    elif name == "set_swing":
        s["swing"] = op["amount"]
        if "unit" in op:
            s["swing_unit"] = op["unit"]
    elif name == "set_title":
        s["title"] = op["title"]
    else:  # pragma: no cover - guarded by validate_op
        raise OpError([f"unknown op {name!r}"])


def _is_roman(c):
    from .theory import is_roman
    return is_roman(c)


def _apply_genre(s: dict, genre: str, keep_harmony: bool) -> None:
    from .genres import genre_track, get_genre, section_harmony
    try:
        g = get_genre(genre)
    except KeyError as e:
        raise OpError([str(e.args[0])])
    s["genre"] = g["name"]
    s["tempo"] = g["tempo"]["default"] if not g["tempo"]["min"] <= s["tempo"] <= g["tempo"]["max"] else s["tempo"]
    s["swing"] = g.get("swing", 0.0)
    s["swing_unit"] = g.get("swing_unit", "1/16")
    if not keep_harmony:
        if s["mode"] not in g["modes"]:
            s["mode"] = g["modes"][0]
        rng = random.Random(stable_seed("genre", g["name"], s.get("seed", 1)))
        form = {f["name"].rstrip("0123456789"): f for f in g["form"]}
        for sec in s["sections"]:
            tmpl = dict(form.get(sec["name"].rstrip("0123456789"), {"progression": None}))
            tmpl.update({"name": sec["name"], "bars": sec["bars"]})
            sec["chords"], cb = section_harmony(g, s["mode"], tmpl, rng)
            if cb:
                sec["chord_beats"] = cb
            else:
                sec.pop("chord_beats", None)
    for sec in s["sections"]:
        sec["beats_per_chord"] = g["harmony"].get("beats_per_chord", sec.get("beats_per_chord", 4))
    roles = [t["role"] for t in s["tracks"] if t["generator"]["type"] != "clip"] or list(g["instruments"])
    keep = [t for t in s["tracks"] if t["generator"]["type"] == "clip"]
    new = []
    for role in dict.fromkeys(roles):
        if role in g["instruments"]:
            new.append(genre_track(g, role, s["sections"]))
        else:
            new.append(make_track(role))
    names = set()
    for t in keep + new:
        while t["name"] in names:
            t["name"] += " 2"
        names.add(t["name"])
        t["channel"] = None
    s["tracks"] = keep + new


def apply_ops(spec: dict, ops) -> dict:
    """Apply one or more ops to a copy of `spec`; returns the new validated spec or raises OpError."""
    if isinstance(ops, dict):
        ops = [ops]
    errs = validate_ops(ops)
    if errs:
        raise OpError(errs)
    s = specmod.normalize(spec)
    for i, op in enumerate(ops):
        try:
            _apply(s, op)
        except OpError as e:
            raise OpError([f"ops[{i}] {m}" for m in e.errors])
        except SpecError as e:
            raise OpError([f"ops[{i}] ({op['op']}): {m}" for m in e.errors])
        except (KeyError, ValueError) as e:
            raise OpError([f"ops[{i}] ({op['op']}): {e}"])
    try:
        return specmod.check(s)
    except SpecError as e:
        raise OpError([f"result invalid: {m}" for m in e.errors])


class Editor:
    """Holds a spec plus undo/redo history of snapshots and the ops/labels that produced them."""

    def __init__(self, spec: dict, max_history: int = 200):
        self.spec = specmod.check(spec)
        self._undo: list[tuple[dict, str]] = []
        self._redo: list[tuple[dict, str]] = []
        self.max_history = max_history
        self.log: list[str] = []

    def apply(self, ops, label: str | None = None) -> dict:
        new = apply_ops(self.spec, ops)
        self._push(new, label or ", ".join(o["op"] for o in ([ops] if isinstance(ops, dict) else ops)))
        return self.spec

    def replace(self, new_spec: dict, label: str = "replace spec") -> dict:
        self._push(specmod.check(new_spec), label)
        return self.spec

    def _push(self, new: dict, label: str) -> None:
        self._undo.append((self.spec, label))
        self._undo = self._undo[-self.max_history:]
        self._redo.clear()
        self.spec = new
        self.log.append(label)

    def undo(self) -> str | None:
        if not self._undo:
            return None
        prev, label = self._undo.pop()
        self._redo.append((self.spec, label))
        self.spec = prev
        return label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        nxt, label = self._redo.pop()
        self._undo.append((self.spec, label))
        self.spec = nxt
        return label

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
