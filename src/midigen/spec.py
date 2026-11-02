"""Song spec: a JSON-serialisable description of a song. Notes are derived from it."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema

from .schema import SPEC_SCHEMA, STYLES
from .theory import is_valid_chord


class SpecError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


# General MIDI presets per role: (program, default generator)
INSTRUMENTS = {
    "piano": 0, "electric_piano": 4, "organ": 16, "guitar": 25, "clean_guitar": 27, "distorted_guitar": 30,
    "acoustic_bass": 32, "finger_bass": 33, "pick_bass": 34, "synth_bass": 38, "strings": 48,
    "string_ensemble": 48, "choir": 52, "brass": 61, "sax": 65, "flute": 73, "square_lead": 80,
    "saw_lead": 81, "synth": 81, "lead": 81, "pad": 89, "warm_pad": 89, "polysynth": 90, "space_pad": 91,
    "french_horn": 60, "overdriven_guitar": 29, "bell": 14, "vibraphone": 11, "marimba": 12, "pluck": 45, "harp": 46,
}

ROLE_PRESETS = {
    "drums": {"program": 0, "generator": {"type": "drums", "pattern": "pop", "density": 0.5, "fill": True}},
    "bass": {"program": 38, "generator": {"type": "bass", "style": "root_fifth", "density": 0.5, "octave": 2}},
    "chords": {"program": 4, "generator": {"type": "chords", "style": "stabs", "density": 0.5, "octave": 4}},
    "pad": {"program": 89, "generator": {"type": "pad", "style": "pad", "density": 0.3, "octave": 4}},
    "arp": {"program": 81, "generator": {"type": "arp", "style": "up", "rate": "1/16", "octave": 4, "gate": 0.6}},
    "lead": {"program": 81, "generator": {"type": "lead", "style": "melodic", "density": 0.5, "range": [64, 86]}},
    "other": {"program": 0, "generator": {"type": "chords", "style": "block", "density": 0.4}},
}
DEFAULT_VOLUME = {"drums": 105, "bass": 100, "chords": 82, "pad": 70, "arp": 72, "lead": 96, "other": 80}


def make_track(role: str, name: str | None = None, instrument: str | None = None, **extra) -> dict:
    preset = copy.deepcopy(ROLE_PRESETS[role])
    track = {
        "name": name or role.capitalize(),
        "role": role,
        "program": preset["program"],
        "channel": 9 if role == "drums" else 0,
        "volume": DEFAULT_VOLUME[role],
        "pan": 64,
        "mute": False,
        "sections": None,
        "generator": preset["generator"],
        "overrides": {},
    }
    if instrument:
        key = instrument.lower().replace(" ", "_")
        if key not in INSTRUMENTS:
            raise SpecError([f"unknown instrument {instrument!r}; choose from {sorted(INSTRUMENTS)}"])
        track["program"] = INSTRUMENTS[key]
    track.update(extra)
    return track


def _is_drum(t: dict) -> bool:
    return t.get("role") == "drums" or t.get("generator", {}).get("type") == "drums"


def assign_channels(spec: dict) -> dict:
    """Give every non-drum track a unique channel (skipping 9); drums always on 9 (MIDI ch 10)."""
    used: set[int] = set()
    free = [c for c in range(16) if c != 9]
    for t in spec["tracks"]:
        if _is_drum(t):
            t["channel"] = 9
            continue
        ch = t.get("channel")
        if not isinstance(ch, int) or ch == 9 or ch in used or not 0 <= ch <= 15:
            avail = [c for c in free if c not in used]
            if not avail:
                raise SpecError(["tracks: too many melodic tracks (max 15 plus drums)"])
            ch = avail[0]
        t["channel"] = ch
        used.add(ch)
    return spec


SPEC_VERSION = 2


def _migrate_v1(s: dict) -> None:
    """Version 1 used swing 0-0.33 (offbeat at 0.5 + swing/2). Version 2 uses the midigen-genres scale 0-1
    (offbeat at 0.5 + swing/6), so the rendered timing is unchanged when swing is multiplied by 3."""
    def up(x):
        return round(min(1.0, float(x) * 3.0), 4) if isinstance(x, (int, float)) else x
    if "swing" in s:
        s["swing"] = up(s["swing"])
    for t in s.get("tracks", []) or []:
        if isinstance(t, dict):
            if "swing" in (t.get("generator") or {}):
                t["generator"]["swing"] = up(t["generator"]["swing"])
            for ov in (t.get("overrides") or {}).values():
                if isinstance(ov, dict) and "swing" in ov:
                    ov["swing"] = up(ov["swing"])
    s["version"] = SPEC_VERSION


def normalize(spec: dict) -> dict:
    """Fill defaults so downstream code can rely on fields existing. Returns a new dict."""
    s = copy.deepcopy(spec)
    s.setdefault("version", SPEC_VERSION)
    if s.get("version") == 1:
        _migrate_v1(s)
    s.setdefault("title", "Untitled")
    s.setdefault("seed", 1)
    s.setdefault("time_signature", [4, 4])
    s.setdefault("mode", "major")
    for sec in s.get("sections", []):
        sec.setdefault("energy", 0.5)
    for t in s.get("tracks", []):
        t.setdefault("program", ROLE_PRESETS.get(t.get("role", "other"), ROLE_PRESETS["other"])["program"])
        t.setdefault("channel", 9 if t.get("role") == "drums" else 0)
        t.setdefault("volume", DEFAULT_VOLUME.get(t.get("role", "other"), 90))
        t.setdefault("pan", 64)
        t.setdefault("mute", False)
        t.setdefault("sections", None)
        t.setdefault("overrides", {})
    if "tracks" in s:
        assign_channels(s)
    return s


def validate(spec: dict) -> list[str]:
    """Return a list of human/LLM-readable errors (empty = valid)."""
    errors = []
    v = jsonschema.Draft202012Validator(SPEC_SCHEMA)
    for e in sorted(v.iter_errors(spec), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in e.absolute_path) or "(root)"
        errors.append(f"{path}: {e.message}")
    schema_failed = bool(errors)
    try:
        errors += _semantic(spec)
    except Exception:  # structure too broken for semantic checks; schema errors explain it
        if not schema_failed:
            raise
    return errors


def _semantic(spec: dict) -> list[str]:
    errors = []
    names = [s["name"] for s in spec["sections"]]
    if len(set(names)) != len(names):
        errors.append("sections: section names must be unique")
    for i, name in enumerate(spec["arrangement"]):
        if name not in names:
            errors.append(f"arrangement/{i}: unknown section {name!r}; defined sections are {names}")
    for sec in spec["sections"]:
        for c in sec["chords"]:
            if not is_valid_chord(c, spec["key"], spec["mode"]):
                errors.append(f"sections/{sec['name']}/chords: unrecognised chord {c!r} "
                              "(use symbols like Am7, F, C/E, Bbmaj7 or roman numerals like vi, bVII, V7)")
    tnames = [t["name"] for t in spec["tracks"]]
    if len(set(tnames)) != len(tnames):
        errors.append("tracks: track names must be unique")
    for t in spec["tracks"]:
        for s in t.get("sections") or []:
            if s not in names:
                errors.append(f"tracks/{t['name']}/sections: unknown section {s!r}")
        for s in t.get("overrides", {}):
            if s not in names:
                errors.append(f"tracks/{t['name']}/overrides: unknown section {s!r}")
        gtype = t["generator"]["type"]
        styles = [t["generator"].get("style")] + [o.get("style") for o in t.get("overrides", {}).values()]
        for st in styles:
            if st and gtype in STYLES and st not in STYLES[gtype]:
                errors.append(f"tracks/{t['name']}/generator/style: {st!r} is not a {gtype} style; use one of {STYLES[gtype]}")
        if gtype == "drums":
            from .generators import drum_patterns
            known = drum_patterns()
            for src in [t["generator"]] + list(t.get("overrides", {}).values()):
                for key in ("pattern", "variation", "fill_pattern", "transition"):
                    if src.get(key) and src[key] not in known:
                        errors.append(f"tracks/{t['name']}/generator/{key}: unknown drum pattern {src[key]!r}; "
                                      f"builtin patterns: {sorted(k for k in known if '.' not in k)}")
        if t["generator"]["type"] == "drums" and t.get("channel", 9) != 9:
            errors.append(f"tracks/{t['name']}: drum tracks must use channel 9 (MIDI channel 10)")
    bars = sum(next(s["bars"] for s in spec["sections"] if s["name"] == n) for n in spec["arrangement"] if n in names)
    if bars > 512:
        errors.append(f"arrangement: song is {bars} bars; maximum is 512")
    return errors


def check(spec: dict) -> dict:
    """Normalise and validate; raise SpecError on problems."""
    s = normalize(spec)
    errs = validate(s)
    if errs:
        raise SpecError(errs)
    return s


def total_bars(spec: dict) -> int:
    bars = {s["name"]: s["bars"] for s in spec["sections"]}
    return sum(bars[n] for n in spec["arrangement"])


def quarter_bpm(spec: dict) -> float:
    """MIDI (quarter-note) tempo. `spec["tempo"]` counts the time signature's denominator note
    (6/8 at 120 = 120 eighths a minute = 60 quarter-note BPM; 2/2 at 100 = 200 quarter BPM).
    In x/4 meters the two are the same. Note positions/durations are always in quarter notes."""
    return spec["tempo"] * 4 / spec.get("time_signature", [4, 4])[1]


def bar_beats(spec: dict) -> float:
    num, den = spec["time_signature"]
    return num * 4.0 / den


def find_track(spec: dict, ref: str) -> dict:
    """Find a track by exact name, case-insensitive name, or role."""
    for t in spec["tracks"]:
        if t["name"] == ref:
            return t
    low = ref.lower().strip()
    for t in spec["tracks"]:
        if t["name"].lower() == low:
            return t
    matches = [t for t in spec["tracks"] if t["role"] == low or t["role"] == low.rstrip("s")]
    if len(matches) >= 1:
        # a role reference means the primary part: genre extras sit after the primary chords/bass/pad
        # but before the primary lead (which is always the last track built)
        return matches[-1] if matches[0]["role"] == "lead" else matches[0]
    raise SpecError([f"no track named {ref!r}; tracks are {[t['name'] for t in spec['tracks']]}"])


def find_section(spec: dict, ref: str) -> dict:
    for s in spec["sections"]:
        if s["name"] == ref or s["name"].lower() == ref.lower().strip():
            return s
    raise SpecError([f"no section named {ref!r}; sections are {[s['name'] for s in spec['sections']]}"])


def dumps(spec: dict) -> str:
    return json.dumps(spec, indent=2)


def save(spec: dict, path: str | Path) -> None:
    Path(path).write_text(dumps(spec) + "\n")


def load(path: str | Path) -> dict:
    return check(json.loads(Path(path).read_text()))
