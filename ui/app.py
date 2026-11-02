#!/usr/bin/env python3
"""midigen-ui: a tiny local web front end for the midigen multitrack MIDI generator.

Stdlib only (http.server) plus midigen's own deps (mido, jsonschema).
Run:  python3 app.py [--host 127.0.0.1] [--port 8766] [--config config.json] [--midigen-src /workspace/midigen/src]

Features: generate -> preview -> play -> download; song sessions with multiple takes; per-section lock and
"regenerate section" (only unlocked sections change, locked ones keep their exact notes); free-tier-style limits
from config.json. No login: a song session is identified by a random id.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import traceback
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import presets  # noqa: E402  (UI-layer presets and plain-language labels)
STATIC = HERE / "static"
DEFAULT_SRC = os.environ.get("MIDIGEN_SRC", str((HERE.parent / "src").resolve()) if (HERE.parent / "src" / "midigen").is_dir()
                             else str((HERE.parent / "midigen" / "src").resolve()))
DEFAULT_CONFIG_PATH = os.environ.get("MIDIGEN_UI_CONFIG", str(HERE / "config.json"))

SEED_MAX = 2**31 - 1
MAX_BODY = 64 * 1024
KEY_RE = re.compile(r"^[A-G][#b]?$")
KEYS = ["C", "C#", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]  # UI list; any A-G[#|b] is accepted
EPS = 1e-6

# ----------------------------------------------------------------- config
# name: (default, type, min, max, description). Values outside [min, max] are rejected (default used, warning printed).
CONFIG_SPEC = {
    "min_bars": (2, int, 1, 64, "smallest song length a request can ask for"),
    "max_bars": (32, int, 2, 128, "longest song (bars) a request can ask for; longer requests are clamped"),
    "default_bars": (16, int, 1, 128, "length used when a request gives none"),
    "max_takes_per_song": (8, int, 1, 100, "takes kept per song session; deleting a take frees a slot"),
    "max_active_sessions": (50, int, 1, 10000, "song sessions held in memory at once; idle ones expire first"),
    "session_ttl_minutes": (120, int, 1, 10080, "a session (and its .mid files) is dropped after this long idle"),
    "generations_per_hour_per_ip": (30, int, 0, 100000, "generate / new take / regenerate calls per client IP per rolling hour (0 = unlimited)"),
    "regen_candidates": (3, int, 1, 10, "candidate seeds tried per section regeneration; the best critic score wins"),
    "trust_proxy_headers": (False, bool, None, None, "use X-Forwarded-For for the rate-limit IP (only behind a trusted proxy)"),
}
DEFAULTS = {k: v[0] for k, v in CONFIG_SPEC.items()}
CONFIG: dict = dict(DEFAULTS)
CONFIG_INFO = {"path": None, "loaded": False, "warnings": []}


def load_config(path: str | Path | None) -> tuple[dict, list[str]]:
    """Read config.json over the defaults. Missing file -> defaults; bad values -> default + warning."""
    cfg, warnings = dict(DEFAULTS), []
    if not path or not Path(path).is_file():
        return cfg, warnings
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        return cfg, [f"cannot read config {path}: {e}; using defaults"]
    if not isinstance(raw, dict):
        return cfg, [f"config {path} must be a JSON object; using defaults"]
    for k, v in raw.items():
        if k.startswith("_") or k.startswith("$"):
            continue  # comments
        if k not in CONFIG_SPEC:
            warnings.append(f"config: unknown option {k!r} ignored")
            continue
        _, typ, lo, hi, _ = CONFIG_SPEC[k]
        if typ is bool:
            if not isinstance(v, bool):
                warnings.append(f"config: {k} must be true/false; using {DEFAULTS[k]}")
                continue
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
                warnings.append(f"config: {k} must be an integer; using {DEFAULTS[k]}")
                continue
            v = int(v)
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                warnings.append(f"config: {k}={v} outside {lo}-{hi}; using {DEFAULTS[k]}")
                continue
        cfg[k] = v
    if cfg["min_bars"] > cfg["max_bars"]:
        warnings.append("config: min_bars > max_bars; using defaults for both")
        cfg["min_bars"], cfg["max_bars"] = DEFAULTS["min_bars"], DEFAULTS["max_bars"]
    cfg["default_bars"] = min(cfg["max_bars"], max(cfg["min_bars"], cfg["default_bars"]))
    return cfg, warnings


def set_config(path: str | Path | None = None, **overrides) -> dict:
    cfg, warnings = load_config(path)
    cfg.update(overrides)
    CONFIG.clear()
    CONFIG.update(cfg)
    CONFIG_INFO.update(path=str(path) if path else None, loaded=bool(path and Path(path).is_file()), warnings=warnings)
    for w in warnings:
        print("warning:", w, file=sys.stderr)
    return CONFIG


def public_limits() -> dict:
    c = CONFIG
    return {"bars": [c["min_bars"], c["max_bars"]], "default_bars": c["default_bars"], "tempo": [20, 300],
            "seed": [0, SEED_MAX], "max_takes_per_song": c["max_takes_per_song"],
            "max_active_sessions": c["max_active_sessions"], "session_ttl_minutes": c["session_ttl_minutes"],
            "generations_per_hour": c["generations_per_hour_per_ip"], "regen_candidates": c["regen_candidates"],
            "downloads": ["mid"]}


mg = None  # midigen module, imported in load_midigen()
MODES: list[str] = []


def load_midigen(src: str | None):
    """Import midigen: prefer an installed package (pip install -e), else add src to sys.path."""
    global mg, MODES
    if src and Path(src).is_dir() and src not in sys.path:
        sys.path.insert(0, src)
    try:
        import midigen  # noqa: F401
    except ModuleNotFoundError as e:
        if e.name in ("mido", "jsonschema"):
            sys.exit(f"error: missing dependency {e.name!r}. Create the venv: python3 -m venv .venv && "
                     f".venv/bin/pip install -r requirements.txt, then run .venv/bin/python app.py")
        sys.exit(f"error: cannot import midigen ({e}). Pass --midigen-src /path/to/midigen/src")
    import midigen as _mg
    mg = _mg
    # modes come from the spec schema enum (same list theory.MODES supports)
    MODES = list(mg.SPEC_SCHEMA["properties"]["mode"]["enum"])
    return mg


# ----------------------------------------------------------------- generation

class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, field: str | None = None, code: str | None = None,
                 headers: dict | None = None, extra: dict | None = None):
        super().__init__(message)
        self.status, self.field, self.code, self.headers, self.extra = status, field, code, headers or {}, extra or {}


def all_genres() -> list[str]:
    """Every genre the library can build, including genre packs (what `midigen genres` lists).

    mg.list_genres() defaults to include_packs=False (built-ins only), which is why the UI used to show fewer
    genres than the CLI; available_genres() == list_genres(include_packs=True)."""
    if hasattr(mg, "available_genres"):
        return list(mg.available_genres())
    return list(mg.list_genres(include_packs=True))


def genres_payload() -> list[dict]:
    out = []
    for name in all_genres():
        g = mg.get_genre(name)
        t = g["tempo"]
        label, plain = presets.plain_genre(name, g.get("display", name), g.get("description", ""))
        out.append({
            "name": name, "display": g.get("display", name), "description": g.get("description", ""),
            "label": label, "plain": plain,
            "tempo": {"min": t["min"], "max": t["max"], "default": t["default"]},
            "modes": g.get("modes", []), "keys": g.get("keys", []),
            "time_signature": g.get("time_signature", [4, 4]),
            "defaults": {"key": g["keys"][0] if g.get("keys") else "C", "mode": (g.get("modes") or ["major"])[0],
                         "tempo": t["default"], "bars": CONFIG["default_bars"]},
            "roles": [r for r in ("drums", "bass", "chords", "pad", "arp", "lead") if r in g.get("instruments", {})],
        })
    return out


def _num(v, name, kind=float):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ApiError(f"{name} must be a number", field=name)
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ApiError(f"{name} must be a number", field=name)
    if f != f or f in (float("inf"), float("-inf")):
        raise ApiError(f"{name} must be finite", field=name)
    return int(round(f)) if kind is int else f


def validate_request(body: dict) -> tuple[dict, list[str]]:
    """Return normalised params and a list of notes about clamping/defaults."""
    if not isinstance(body, dict):
        raise ApiError("request body must be a JSON object")
    notes = []
    genre = body.get("genre")
    if not isinstance(genre, str) or not genre.strip():
        raise ApiError("genre is required", field="genre")
    try:
        g = mg.get_genre(genre)
    except KeyError:
        raise ApiError(f"unknown genre {genre!r}; choose one of {all_genres()}", field="genre", code="unknown_genre")
    tmin, tmax = g["tempo"]["min"], g["tempo"]["max"]

    key = body.get("key") or None
    if key is not None:
        if not isinstance(key, str):
            raise ApiError("key must be a string like 'C', 'F#' or 'Bb'", field="key")
        key = key.strip()
        key = key[:1].upper() + key[1:]
        if not KEY_RE.match(key):
            raise ApiError(f"invalid key {key!r}; use a note name like C, F#, Bb", field="key")

    mode = body.get("mode") or None
    if mode is not None:
        if not isinstance(mode, str) or mode.strip().lower().replace(" ", "_") not in MODES:
            raise ApiError(f"invalid mode {mode!r}; choose one of {MODES}", field="mode")
        mode = mode.strip().lower().replace(" ", "_")

    tempo = _num(body.get("tempo"), "tempo")
    if tempo is None:
        tempo = float(g["tempo"]["default"])
    # hard clamp to the schema's 20..300; genre range is advisory (noted, not forced)
    if tempo < 20 or tempo > 300:
        notes.append(f"tempo {tempo:g} clamped to 20-300 BPM")
        tempo = min(300.0, max(20.0, tempo))
    if not (tmin <= tempo <= tmax):
        notes.append(f"tempo {tempo:g} is outside {g['display']}'s typical {tmin}-{tmax} BPM")
    tempo = round(tempo, 2)

    lo, hi = CONFIG["min_bars"], CONFIG["max_bars"]
    bars = _num(body.get("bars", body.get("length")), "bars", int)
    if bars is None:
        bars = CONFIG["default_bars"]
    if bars < lo or bars > hi:
        notes.append(f"bars {bars} clamped to {lo}-{hi}" + (" (free-tier limit)" if bars > hi else ""))
        bars = min(hi, max(lo, bars))

    seed = _num(body.get("seed"), "seed", int)
    if seed is None:
        seed = random.randint(1, 999999)
        notes.append(f"random seed {seed}")
    if seed < 0 or seed > SEED_MAX:
        raise ApiError(f"seed must be between 0 and {SEED_MAX}", field="seed")

    return {"genre": g["name"], "key": key, "mode": mode, "tempo": tempo, "bars": bars, "seed": seed}, notes


def generate(params: dict):
    """Mirror `midigen new --genre ...`: build_spec + llm.refine (offline critic loop), then render."""
    spec = mg.build_spec(params["genre"], key=params["key"], mode=params["mode"], tempo=params["tempo"],
                         bars=params["bars"], seed=params["seed"])
    spec, report = mg.refine(spec)
    perf = mg.render(spec)
    return spec, perf, report


def generate_any(params: dict):
    """Dispatch on request kind. Returns (spec, perf, report, info) where info describes the interpretation."""
    if params.get("kind") == "prompt":
        return generate_prompt(params)
    spec, perf, report = generate(params)
    return spec, perf, report, {"source": "manual"}


# ---- prompt / preset mode ----------------------------------------------------------------------------------
#
# The prompt goes through the library's own entry point, midigen.llm.Composer(use_env=False).generate(prompt,
# seed) -- what `midigen new "<prompt>"` runs offline -- so parser improvements in the library flow through.
# record=False keeps the web app from writing the library's learning database (the music is identical; that
# flag only controls recording). On top of the parsed spec the UI layer applies, in order: preset settings
# (genre/mode/tempo/roles/instruments/length), a spoken duration ("15 seconds", "2 minutes") converted to bars
# from the tempo, and this server's bar limits. If anything changed, the spec is rebuilt with build_spec (keeping
# the prompt's title and "just X"/"no X" instrument choices) and refined with the same offline critic loop.

MAX_PROMPT = 300
_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15,
              "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90}
DURATION_RE = re.compile(r"\b(\d+(?:\.\d+)?|half an?|half|" + "|".join(sorted(map(re.escape, _NUM_WORDS), key=len, reverse=True))
                         + r")[\s-]*(seconds?|secs?|s|minutes?|mins?|m)\b(?!\s*(?:bpm))", re.I)
CLOCK_RE = re.compile(r"\b(\d{1,2}):([0-5]\d)\b")


def parse_duration(text: str) -> float | None:
    """'15 second intro' -> 15.0, '2 minutes' -> 120.0, 'half a minute' -> 30.0, '1:30' -> 90.0, else None."""
    t = text.lower()
    m = CLOCK_RE.search(t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"\bhalf an? (minute|min)\b", t)
    if m:
        return 30.0
    for m in DURATION_RE.finditer(t):
        n, unit = m.group(1), m.group(2).lower()
        glued = m.end(1) == m.start(2)
        if unit in ("s", "m") and glued and re.fullmatch(r"[1-9]0", n):
            continue                     # "80s" / "90s" is a decade, not a duration
        if n.startswith("half"):
            val = 0.5
        else:
            val = float(n) if n[0].isdigit() else float(_NUM_WORDS[n])
        secs = val * 60 if unit.startswith("m") else val
        if secs > 0:
            return secs
    return None


def recognised_genre(text: str) -> tuple[str | None, str | None]:
    """What (if anything) in the prompt picked the genre: ('synthwave', 'genre word') / ('lofi_hiphop', 'mood')."""
    t = text.lower()
    g = mg.detect_genre(t)
    if g:
        return g, "genre word"
    try:
        from midigen import nl
        for pat, gen in getattr(nl, "MOOD_GENRE", []):
            if re.search(pat, t):
                return gen, "mood/use-case words"
    except Exception:  # noqa: BLE001 - parser internals are optional
        pass
    return None, None


def presets_payload() -> list[dict]:
    out = []
    for p in presets.PRESETS:
        st = p["settings"]
        out.append({"id": p["id"], "label": p["label"], "emoji": p["emoji"], "prompt": p["prompt"],
                    "settings": {k: st[k] for k in ("genre", "mode", "tempo", "seconds") if k in st}})
    return out


def validate_prompt_request(body: dict) -> tuple[dict, list[str]]:
    if not isinstance(body, dict):
        raise ApiError("request body must be a JSON object")
    preset = body.get("preset") or None
    if preset is not None and (not isinstance(preset, str) or preset not in presets.PRESET_BY_ID):
        raise ApiError(f"unknown preset {preset!r}", field="preset", code="unknown_preset")
    prompt = body.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ApiError("prompt must be text", field="prompt")
    prompt = " ".join((prompt or "").split())
    if len(prompt) > MAX_PROMPT:
        raise ApiError(f"Please keep the description under {MAX_PROMPT} characters.", field="prompt", code="prompt_too_long")
    if not prompt and not preset:
        raise ApiError("Describe the music you want, or pick one of the presets.", field="prompt", code="empty_prompt",
                       extra={"examples": presets.EXAMPLE_PROMPTS})
    if not prompt:
        prompt = presets.PRESET_BY_ID[preset]["prompt"]
    seed = _num(body.get("seed"), "seed", int)
    if seed is None:
        seed = random.randint(1, 999999)
    if seed < 0 or seed > SEED_MAX:
        raise ApiError(f"seed must be between 0 and {SEED_MAX}", field="seed")
    return {"kind": "prompt", "prompt": prompt, "preset": preset, "seed": seed, "key": None, "mode": None}, []


def _roles(spec: dict) -> list[str]:
    return sorted({t.get("role") for t in spec.get("tracks", []) if t.get("role")})


def generate_prompt(params: dict):
    from midigen.llm import Composer
    prompt, seed = params["prompt"], params["seed"]
    preset = presets.PRESET_BY_ID.get(params.get("preset") or "")
    st = preset["settings"] if preset else {}
    try:
        r = Composer(use_env=False, record=False).generate(prompt, seed=seed)
    except getattr(mg, "NotUnderstood", ()) as e:
        raise ApiError(f"Sorry, I couldn't understand that ({e}). Try describing a mood, use or style, or pick a preset.",
                       status=422, field="prompt", code="not_understood", extra={"examples": presets.EXAMPLE_PROMPTS})
    parsed = r.spec
    rec, how = recognised_genre(prompt)
    notes = []
    genre = st.get("genre", parsed["genre"])
    key = params.get("key") or st.get("key") or parsed["key"]
    mode = params.get("mode") or st.get("mode") or parsed["mode"]
    tempo = float(st.get("tempo", parsed["tempo"]))
    num = (parsed.get("time_signature") or mg.get_genre(genre).get("time_signature") or [4, 4])[0]
    lo, hi = CONFIG["min_bars"], CONFIG["max_bars"]
    said = parse_duration(prompt)
    want_secs = said or st.get("seconds")
    cur_bars = mg.total_bars(parsed) if hasattr(mg, "total_bars") else sum(s["bars"] for s in parsed["sections"])
    if want_secs:
        bars = max(1, round(want_secs * tempo / (60.0 * num)))
        if bars < lo or bars > hi:
            clamped = min(hi, max(lo, bars))
            notes.append(f"{want_secs:g} s at {tempo:g} BPM would be {bars} bars; this server allows {lo}-{hi}, "
                         f"so it is {clamped} bars")
            bars = clamped
    elif lo <= cur_bars <= hi:
        bars = cur_bars
    else:
        bars = CONFIG["default_bars"] if cur_bars > hi else lo
        notes.append(f"the full-length song would be {cur_bars} bars; this server makes sketches of up to {hi} bars, "
                     f"so it is {bars} bars (say e.g. \"30 seconds\" to choose a length)")
    roles = st.get("roles")
    exclude = st.get("exclude_roles")
    if roles is None and genre == parsed["genre"]:
        full = _roles(mg.build_spec(genre, key=key, mode=mode, tempo=tempo, bars=lo, seed=seed))
        if _roles(parsed) != full:
            roles = _roles(parsed)      # keep the prompt's "just piano" / "no drums" choices
    changed = (genre != parsed["genre"] or key != parsed["key"] or mode != parsed["mode"]
               or abs(tempo - float(parsed["tempo"])) > 1e-6 or bars != cur_bars or roles is not None
               or exclude or st.get("programs"))
    if changed:
        kw = {"roles": roles} if roles else {}
        if exclude:
            kw["exclude_roles"] = exclude
        spec = mg.build_spec(genre, key=key, mode=mode, tempo=tempo, bars=bars, seed=seed,
                             title=parsed.get("title"), **kw)
        progs = st.get("programs") or {}
        ops = [{"op": "set_track", "track": t["name"], "program": progs[t.get("role")]}
               for t in spec["tracks"] if t.get("role") in progs]
        if ops:
            spec = mg.apply_ops(spec, ops)
        spec, report = mg.refine(spec)
    else:
        spec, report = parsed, r.report
    perf = mg.render(spec)
    if rec is None and not preset:
        fallback = ("I didn't recognise a musical style or mood in that description, so the style fell back to generic "
                    "pop (key, tempo, length and words like \"just piano\" or \"no drums\" were still applied). "
                    "Try a preset, or name a style or mood (e.g. \"chill coffee shop\", \"epic trailer\", \"80s retro\").")
    else:
        fallback = None
    info = {"source": "preset" if preset else "prompt", "prompt": prompt,
            "preset": preset["label"] if preset else None,
            "recognised": {"genre": rec, "how": how} if rec else None,
            "fallback": bool(fallback), "fallback_message": fallback,
            "suggested_presets": [p["id"] for p in presets.PRESETS[:4]] if fallback else [],
            "explanation": r.explanation, "requested_seconds": want_secs,
            "duration_from": "prompt" if said else ("preset" if want_secs else None),
            "rebuilt": bool(changed), "notes_extra": notes}
    return spec, perf, report, info


def _fmt_secs(x: float) -> str:
    x = int(round(x))
    return f"{x} s" if x < 60 else f"{x // 60}:{x % 60:02d}"


def interpret(t: Take) -> dict:
    """The 'Interpreted as' block shown after every generation (prompt, preset or manual)."""
    spec = t.spec
    g = mg.get_genre(spec["genre"])
    label, plain = presets.plain_genre(spec["genre"], g.get("display", spec["genre"]), g.get("description", ""))
    bars = sum(s["bars"] for s in spec["sections"])
    num = (spec.get("time_signature") or [4, 4])[0]
    secs = bars * num * 60.0 / float(spec["tempo"])
    out = {"genre": spec["genre"], "genre_label": label, "genre_plain": plain, "key": spec["key"],
           "mode": spec["mode"], "mode_plain": presets.plain_mode(spec["mode"]), "tempo": spec["tempo"],
           "bars": bars, "seconds": round(secs, 1), "title": spec.get("title")}
    out.update({k: v for k, v in t.info.items() if k != "notes_extra"})
    out.setdefault("source", "manual")
    out["line"] = (f"Interpreted as: {label} / {spec['key']} {spec['mode'].replace('_', ' ')} "
                   f"({out['mode_plain']}) / {spec['tempo']:g} BPM / {bars} bars (~{_fmt_secs(secs)})")
    return out


# ---- section regeneration (lock / regenerate) --------------------------------------------------------------
#
# midigen seeds every (track, section) part independently: arranger.render uses
#   stable_seed(spec.seed, params.seed, track name, section name)
# where params = track.generator updated with track.overrides[section name]. So writing a new `seed` into
# overrides[<section>] for every track re-rolls that section only (natively, through the spec). Two things the
# spec alone cannot express are handled by splicing at the Performance level:
#   * the same section name can appear several times in the arrangement (overrides are per name, not per
#     occurrence), so locking one occurrence and regenerating another needs a merge;
#   * whole-song post-passes (chord anticipation, duplicate-note merge) could in principle touch notes next to a
#     boundary.
# The merge keeps every note whose onset lies in a non-target section from the OLD performance (copied verbatim)
# and takes notes with onsets in target sections from the NEW render, so locked sections are byte-identical by
# construction. The generator never lets a note ring past its section end; new notes are still truncated at the end
# of their run of target sections, and a new note that duplicates a kept note (same pitch, onset < 30 ms apart
# at a boundary) is dropped.

def section_index_of(sections, beat: float) -> int:
    for i, s in enumerate(sections):
        if s.start <= beat + EPS < s.end:
            return i
    return len(sections) - 1


def splice(old, new, new_spec: dict, targets: set[int]):
    """Merged Performance: notes in `targets` sections from `new`, everything else from `old` (verbatim)."""
    from midigen.arranger import Performance, RenderedTrack
    from midigen.core import Note
    secs = old.sections
    if [(s.name, s.start, s.end) for s in secs] != [(s.name, s.start, s.end) for s in new.sections]:
        raise ApiError("regeneration changed the song form; cannot splice", status=500)
    # end of the contiguous run of target sections each target belongs to
    run_end = {}
    for i in sorted(targets, reverse=True):
        run_end[i] = run_end[i + 1] if (i + 1) in targets and (i + 1) in run_end else secs[i].end
    tracks = []
    for ot, nt in zip(old.tracks, new.tracks):
        if ot.name != nt.name:
            raise ApiError("regeneration changed the track list; cannot splice", status=500)
        kept = [Note(n.start, n.dur, n.pitch, n.vel) for n in ot.notes if section_index_of(secs, n.start) not in targets]
        kept_on = {}
        for n in kept:
            kept_on.setdefault(n.pitch, []).append(n.start)
        added = []
        for n in nt.notes:
            i = section_index_of(secs, n.start)
            if i not in targets:
                continue
            if any(abs(n.start - s) < 0.03 for s in kept_on.get(n.pitch, ())):
                continue
            dur = min(n.dur, run_end[i] - n.start)
            added.append(Note(n.start, max(0.03, dur), n.pitch, n.vel))
        rt = RenderedTrack(nt.name, nt.role, nt.channel, nt.program, nt.volume, nt.pan, is_drums=nt.is_drums)
        rt.notes = sorted(kept + added, key=lambda n: (n.start, n.pitch))
        tracks.append(rt)
    return Performance(new_spec, tracks, list(old.sections), list(old.harmony), old.total_beats, old.bar_beats)


def section_notes(perf, i: int) -> list[list]:
    """[[track index, start, dur, pitch, vel], ...] for notes with onsets in section i (exact floats)."""
    out = []
    for k, t in enumerate(perf.tracks):
        out += [[k, n.start, n.dur, n.pitch, n.vel] for n in t.notes if section_index_of(perf.sections, n.start) == i]
    return out


def section_diff(a, b) -> list[dict]:
    """Per section: which tracks differ between two performances of the same form."""
    out = []
    for i, s in enumerate(a.sections):
        changed = []
        for ta, tb in zip(a.tracks, b.tracks):
            na = [(n.start, n.dur, n.pitch, n.vel) for n in ta.notes if section_index_of(a.sections, n.start) == i]
            nb = [(n.start, n.dur, n.pitch, n.vel) for n in tb.notes if section_index_of(b.sections, n.start) == i]
            if na != nb:
                changed.append(ta.name)
        out.append({"index": i, "name": s.name, "identical": not changed, "changed_tracks": changed})
    return out


def regenerate_sections(spec: dict, perf, targets: set[int], rng: random.Random, candidates: int):
    """Re-roll the target sections natively (per-section seed overrides) and splice into perf.

    Tries `candidates` seeds and keeps the merged result with the best critic score (one that actually changed
    something). Returns (new_spec, merged_perf, report, section_seeds, spec_exact)."""
    names = sorted({perf.sections[i].name for i in targets})
    # spec reproduces the merged perf exactly only if every occurrence of each re-rolled name was a target
    spec_exact_here = all(i in targets for i, s in enumerate(perf.sections) if s.name in names)
    best = None
    for _ in range(max(1, candidates)):
        seeds = {nm: rng.randint(1, SEED_MAX) for nm in names}
        cand_spec = copy.deepcopy(spec)
        for tr in cand_spec["tracks"]:
            ov = tr.setdefault("overrides", {})
            for nm in names:
                ov.setdefault(nm, {})["seed"] = seeds[nm]
        new = mg.render(cand_spec)
        merged = splice(perf, new, cand_spec, targets)
        changed = any(not d["identical"] for d in section_diff(perf, merged))
        report = mg.critique(merged)
        key = (changed, report["score"])
        if best is None or key > best[0]:
            best = (key, cand_spec, merged, report, seeds)
    _, new_spec, merged, report, seeds = best
    return new_spec, merged, report, seeds, spec_exact_here


def perf_payload(spec: dict, perf, report: dict) -> dict:
    tracks = []
    for t in perf.tracks:
        notes = [[round(n.start, 4), round(n.dur, 4), int(n.pitch), int(n.vel)] for n in t.notes]
        pitches = [n[2] for n in notes]
        tracks.append({
            "name": t.name, "role": t.role, "channel": t.channel, "program": t.program,
            "volume": t.volume, "pan": t.pan, "is_drums": bool(t.is_drums),
            "pitch_range": [min(pitches), max(pitches)] if pitches else None,
            "notes": notes,
        })
    return {
        "title": spec.get("title"), "genre": spec.get("genre"), "key": spec["key"], "mode": spec["mode"],
        "tempo": spec["tempo"], "time_signature": spec["time_signature"], "seed": spec.get("seed"),
        "bars": int(round(perf.total_beats / perf.bar_beats)), "bar_beats": perf.bar_beats, "total_beats": perf.total_beats,
        "note_format": ["start_beat", "duration_beats", "pitch", "velocity"],
        "sections": [{"index": i, "name": s.name, "start": s.start, "end": s.end, "energy": s.energy,
                      "bars": int(round((s.end - s.start) / perf.bar_beats))} for i, s in enumerate(perf.sections)],
        "chords": [{"start": h.start, "end": h.end, "symbol": h.symbol, "section": h.section} for h in perf.harmony],
        "tracks": tracks,
        "critique": critique_payload(report),
    }


def critique_payload(report: dict) -> dict:
    sev_count = {}
    for f in report["findings"]:
        sev_count[f["severity"]] = sev_count.get(f["severity"], 0) + 1
    return {"score": report["score"], "counts": sev_count,
            "findings": [{k: f.get(k) for k in ("check", "severity", "message", "suggestion", "track", "section", "bars")
                          if f.get(k) is not None} for f in report["findings"]],
            "summary": mg.format_report(report)}


# ----------------------------------------------------------------- sessions & takes

def _new_id(nbytes: int) -> str:
    return secrets.token_hex(nbytes)


@dataclass
class Take:
    id: str
    number: int
    kind: str                      # "generate" | "new_take" | "regenerate"
    parent_id: str | None
    spec: dict
    perf: object
    report: dict
    path: Path
    filename: str
    locked: set = field(default_factory=set)
    regenerated: list = field(default_factory=list)   # section indices re-rolled to make this take
    section_seeds: dict = field(default_factory=dict)
    spec_exact: bool = True        # does render(spec) reproduce these notes exactly?
    notes: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    elapsed_ms: float = 0.0
    info: dict = field(default_factory=dict)     # how the request was interpreted (prompt/preset/manual)

    @property
    def label(self) -> str:
        return f"Take {self.number}"


@dataclass
class Session:
    id: str
    params: dict
    dir: Path
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    takes: "OrderedDict[str, Take]" = field(default_factory=OrderedDict)
    next_number: int = 1
    lock: threading.RLock = field(default_factory=threading.RLock)


class SessionStore:
    """In-memory song sessions; each take's .mid lives in <root>/<session id>/<take id>.mid."""

    def __init__(self, root: Path):
        self.root = root
        self.sessions: "OrderedDict[str, Session]" = OrderedDict()
        self.take_index: dict[str, str] = {}
        self.lock = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)

    def _drop(self, sid: str):
        s = self.sessions.pop(sid, None)
        if s:
            for tid in s.takes:
                self.take_index.pop(tid, None)
            shutil.rmtree(s.dir, ignore_errors=True)

    def purge(self, now: float | None = None):
        now = now or time.time()
        ttl = CONFIG["session_ttl_minutes"] * 60
        with self.lock:
            for sid in [sid for sid, s in self.sessions.items() if now - s.last_used > ttl]:
                self._drop(sid)

    def create(self, params: dict) -> Session:
        self.purge()
        with self.lock:
            if len(self.sessions) >= CONFIG["max_active_sessions"]:
                raise ApiError(f"The server is holding the maximum of {CONFIG['max_active_sessions']} songs right now. "
                               "Please try again in a little while.", status=503, code="session_limit",
                               headers={"Retry-After": "300"})
            sid = _new_id(12)
            s = Session(sid, dict(params), self.root / sid)
            s.dir.mkdir(parents=True, exist_ok=True)
            self.sessions[sid] = s
            return s

    def get(self, sid: str) -> Session:
        self.purge()
        with self.lock:
            s = self.sessions.get(sid)
            if not s:
                raise ApiError("This song session has expired or does not exist. Generate a new song.", status=404,
                               code="session_not_found")
            s.last_used = time.time()
            self.sessions.move_to_end(sid)
            return s

    def remove(self, sid: str):
        with self.lock:
            self._drop(sid)

    def index_take(self, tid: str, sid: str):
        with self.lock:
            self.take_index[tid] = sid

    def unindex_take(self, tid: str):
        with self.lock:
            self.take_index.pop(tid, None)

    def find_take(self, tid: str) -> tuple[Session, Take] | None:
        with self.lock:
            sid = self.take_index.get(tid)
            s = self.sessions.get(sid) if sid else None
            t = s.takes.get(tid) if s else None
        return (s, t) if t else None


STORE: SessionStore | None = None



class RateLimiter:
    """Rolling one-hour window of generation timestamps per client IP (in memory)."""
    WINDOW = 3600.0

    def __init__(self):
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def _trim(self, ip, now):
        q = self.hits.get(ip)
        if q is None:
            return deque()
        while q and now - q[0] >= self.WINDOW:
            q.popleft()
        if not q:
            self.hits.pop(ip, None)
        return q

    def usage(self, ip: str) -> dict:
        now = time.time()
        limit = CONFIG["generations_per_hour_per_ip"]
        with self.lock:
            q = self._trim(ip, now)
            used = len(q)
            reset = int(self.WINDOW - (now - q[0])) + 1 if q else 0
        return {"generations_used": used, "generations_limit": limit,
                "generations_left": None if not limit else max(0, limit - used), "resets_in_s": reset}

    def hit(self, ip: str):
        """Count one generation, or raise 429 if the client is over its hourly budget."""
        limit = CONFIG["generations_per_hour_per_ip"]
        now = time.time()
        with self.lock:
            q = self._trim(ip, now)
            if limit and len(q) >= limit:
                wait = int(self.WINDOW - (now - q[0])) + 1
                mins = max(1, (wait + 59) // 60)
                raise ApiError(f"You've used all {limit} generations for this hour. More become available in about "
                               f"{mins} minute{'s' if mins != 1 else ''}.", status=429, code="rate_limit",
                               headers={"Retry-After": str(wait)}, extra={"retry_after_s": wait})
            q.append(now)
            self.hits[ip] = q

    def reset(self):
        with self.lock:
            self.hits.clear()


RATE = RateLimiter()


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_") or "song"


def _check_take_room(sess: Session):
    n, lim = len(sess.takes), CONFIG["max_takes_per_song"]
    if n >= lim:
        raise ApiError(f"This song already has {lim} takes (the maximum). Delete a take you don't need to make room.",
                       status=409, code="take_limit", extra={"takes": n, "max_takes": lim})


def _add_take(sess: Session, kind: str, parent: Take | None, spec, perf, report, *, locked=(), regenerated=(),
              section_seeds=None, spec_exact=True, notes=(), elapsed_ms=0.0, info=None) -> Take:
    tid = _new_id(8)
    path = sess.dir / f"{tid}.mid"
    mg.export_midi(perf, path)    # export_midi accepts a Performance: the file is built from the (merged) notes
    num = sess.next_number
    sess.next_number += 1
    fname = _safe_name(f"{spec.get('genre')}_{spec['key']}_{spec['mode']}_{int(round(spec['tempo']))}bpm_"
                       f"s{spec.get('seed')}_take{num}") + ".mid"
    t = Take(tid, num, kind, parent.id if parent else None, spec, perf, report, path, fname, set(locked),
             sorted(regenerated), dict(section_seeds or {}), spec_exact, list(notes), elapsed_ms=elapsed_ms,
             info=dict(info if info is not None else (parent.info if parent else {})))
    sess.takes[tid] = t
    STORE.index_take(tid, sess.id)
    return t


def take_url(sess: Session, t: Take) -> str:
    return f"/api/sessions/{sess.id}/takes/{t.id}.mid"


def take_meta(sess: Session, t: Take) -> dict:
    parent = sess.takes.get(t.parent_id) if t.parent_id else None
    return {"id": t.id, "number": t.number, "label": t.label, "kind": t.kind, "parent_id": t.parent_id,
            "parent_label": parent.label if parent else (f"deleted take" if t.parent_id else None),
            "seed": t.spec.get("seed"), "score": t.report["score"], "locked": sorted(t.locked),
            "regenerated": t.regenerated, "regenerated_names": [t.perf.sections[i].name for i in t.regenerated],
            "section_seeds": t.section_seeds, "spec_exact": t.spec_exact, "created": round(t.created, 3),
            "download_url": take_url(sess, t), "filename": t.filename}


def session_payload(sess: Session) -> dict:
    return {"id": sess.id, "created": round(sess.created, 3), "params": sess.params,
            "takes": [take_meta(sess, t) for t in sess.takes.values()],
            "take_count": len(sess.takes), "max_takes": CONFIG["max_takes_per_song"],
            "expires_in_s": int(CONFIG["session_ttl_minutes"] * 60 - (time.time() - sess.last_used))}


def take_payload(sess: Session, t: Take, ip: str | None = None) -> dict:
    out = perf_payload(t.spec, t.perf, t.report)
    out.update(take_meta(sess, t))
    out.update({"take_id": t.id, "session_id": sess.id, "session": session_payload(sess),
                "request": sess.params, "notes": t.notes, "elapsed_ms": t.elapsed_ms,
                "interpreted": interpret(t)})
    if ip is not None:
        out["usage"] = RATE.usage(ip)
    return out


def _parse_indices(v, n: int, name: str) -> set[int]:
    if v is None:
        return set()
    if not isinstance(v, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in v):
        raise ApiError(f"{name} must be a list of section indices", field=name)
    bad = [x for x in v if x < 0 or x >= n]
    if bad:
        raise ApiError(f"{name}: no section with index {bad[0]} (song has {n} sections, 0-{n - 1})", field=name)
    return set(v)


def do_generate(body, ip) -> dict:
    if isinstance(body, dict) and ("prompt" in body or "preset" in body) and not body.get("genre"):
        params, notes = validate_prompt_request(body)
    else:
        params, notes = validate_request(body)
    RATE.hit(ip)
    t0 = time.perf_counter()
    spec, perf, report, info = generate_any(params)
    notes = list(notes) + info.pop("notes_extra", [])
    sess = STORE.create(params)
    try:
        with sess.lock:
            t = _add_take(sess, "generate", None, spec, perf, report, notes=notes,
                          elapsed_ms=round((time.perf_counter() - t0) * 1000, 1), info=info)
    except Exception:
        STORE.remove(sess.id)
        raise
    return take_payload(sess, t, ip)


def do_new_take(sess: Session, body, ip) -> dict:
    if not isinstance(body, dict):
        raise ApiError("request body must be a JSON object")
    with sess.lock:
        _check_take_room(sess)
        seed = _num(body.get("seed"), "seed", int)
        notes = []
        if seed is None:
            used = {t.spec.get("seed") for t in sess.takes.values()}
            seed = random.randint(1, 999999)
            while seed in used:
                seed = random.randint(1, 999999)
        if seed < 0 or seed > SEED_MAX:
            raise ApiError(f"seed must be between 0 and {SEED_MAX}", field="seed")
        first = next(iter(sess.takes.values()), None)
        # same settings as the song; key/mode pinned to what the first take resolved so takes stay comparable
        params = dict(sess.params, seed=seed)
        if first:
            params.update(key=first.spec["key"], mode=first.spec["mode"])
        RATE.hit(ip)
        t0 = time.perf_counter()
        spec, perf, report, info = generate_any(params)
        notes += info.pop("notes_extra", [])
        t = _add_take(sess, "new_take", None, spec, perf, report, notes=notes,
                      elapsed_ms=round((time.perf_counter() - t0) * 1000, 1), info=info)
        return take_payload(sess, t, ip)


def do_regenerate(sess: Session, parent: Take, body, ip) -> dict:
    if not isinstance(body, dict):
        raise ApiError("request body must be a JSON object")
    n = len(parent.perf.sections)
    with sess.lock:
        locked = _parse_indices(body["locked"], n, "locked") if "locked" in body else set(parent.locked)
        parent.locked = set(locked)          # remember the parent's locks
        if body.get("sections") is None:
            targets = set(range(n)) - locked
        else:
            req = _parse_indices(body["sections"], n, "sections")
            if req and req <= locked:
                names = ", ".join(parent.perf.sections[i].name for i in sorted(req))
                raise ApiError(f"{names} is locked. Unlock it first to regenerate it.", status=409, code="locked")
            targets = req - locked
        if not targets:
            raise ApiError("All sections are locked, so there is nothing to regenerate. Unlock a section first.",
                           status=409, code="all_locked")
        _check_take_room(sess)
        RATE.hit(ip)
        t0 = time.perf_counter()
        rng = random.Random(secrets.randbits(64))
        spec, perf, report, seeds, exact_here = regenerate_sections(parent.spec, parent.perf, targets, rng,
                                                                    CONFIG["regen_candidates"])
        seeds_all = dict(parent.section_seeds, **seeds)
        t = _add_take(sess, "regenerate", parent, spec, perf, report, locked=locked, regenerated=targets,
                      section_seeds=seeds_all, spec_exact=parent.spec_exact and exact_here,
                      elapsed_ms=round((time.perf_counter() - t0) * 1000, 1))
        return take_payload(sess, t, ip)


def do_compare(sess: Session, a: Take, b: Take) -> dict:
    same_form = [(s.name, s.start, s.end) for s in a.perf.sections] == [(s.name, s.start, s.end) for s in b.perf.sections]
    fa = {(f["check"], f.get("track"), f.get("section"), f["message"]) for f in a.report["findings"]}
    fb = {(f["check"], f.get("track"), f.get("section"), f["message"]) for f in b.report["findings"]}
    fmt = lambda s: [{"check": c, "track": t, "section": sec, "message": m} for c, t, sec, m in sorted(s, key=str)]
    return {"a": take_meta(sess, a), "b": take_meta(sess, b),
            "score": {"a": a.report["score"], "b": b.report["score"], "diff": b.report["score"] - a.report["score"]},
            "same_form": same_form,
            "sections": section_diff(a.perf, b.perf) if same_form else None,
            "findings_only_a": fmt(fa - fb), "findings_only_b": fmt(fb - fa),
            "note_counts": {"a": sum(len(t.notes) for t in a.perf.tracks), "b": sum(len(t.notes) for t in b.perf.tracks)}}


# ----------------------------------------------------------------- HTTP

SID = r"[0-9a-f]{24}"
TID = r"[0-9a-f]{16}"


class Handler(BaseHTTPRequestHandler):
    server_version = "midigen-ui/0.2"
    quiet = False

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stderr.write("%s - %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def client_ip(self) -> str:
        if CONFIG.get("trust_proxy_headers"):
            xff = self.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[0].strip()
        return self.client_address[0]

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj, headers: dict | None = None):
        self._send(status, json.dumps(obj, separators=(",", ":")).encode(), "application/json; charset=utf-8", headers)

    def _error(self, status: int, message: str, field: str | None = None, code: str | None = None,
               headers: dict | None = None, extra: dict | None = None):
        err = {"status": status, "message": message}
        if field:
            err["field"] = field
        if code:
            err["code"] = code
        err.update(extra or {})
        self._json(status, {"error": err}, headers)

    def _api_error(self, e: ApiError):
        return self._error(e.status, str(e), e.field, e.code, e.headers, e.extra)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError("bad Content-Length")
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ApiError("request body too large", status=413)
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ApiError(f"invalid JSON: {e}")

    def _take(self, sid, tid) -> tuple[Session, Take]:
        sess = STORE.get(sid)
        t = sess.takes.get(tid)
        if not t:
            raise ApiError("That take does not exist (it may have been deleted).", status=404, code="take_not_found")
        return sess, t

    def _mid(self, t: Take):
        if not t.path.exists():
            return self._error(404, "download not found or expired; generate again")
        return self._send(200, t.path.read_bytes(), "audio/midi",
                          {"Content-Disposition": f'attachment; filename="{t.filename}"'})

    def _run(self, fn):
        try:
            return fn()
        except ApiError as e:
            return self._api_error(e)
        except Exception as e:  # noqa: BLE001
            if mg is not None and isinstance(e, mg.SpecError):
                return self._error(422, f"generator rejected the spec: {e}")
            traceback.print_exc()
            return self._error(500, "Something went wrong while making the music. Please try again, or pick a preset.",
                               code="internal", extra={"detail": f"{type(e).__name__}: {e}"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        query = dict(p.split("=", 1) for p in self.path.split("?", 1)[1].split("&") if "=" in p) if "?" in self.path else {}

        def route():
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/genres":
                return self._json(200, {"genres": genres_payload(), "modes": MODES, "keys": KEYS, "limits": public_limits(),
                                        "mode_labels": {m: presets.plain_mode(m) for m in MODES},
                                        "presets": presets_payload(), "examples": presets.EXAMPLE_PROMPTS})
            if path == "/api/config":
                return self._json(200, {"limits": public_limits(), "usage": RATE.usage(self.client_ip()),
                                        "config_file": CONFIG_INFO["path"] if CONFIG_INFO["loaded"] else None,
                                        "active_sessions": len(STORE.sessions)})
            if path == "/api/health":
                return self._json(200, {"ok": True})
            m = re.fullmatch(rf"/api/download/({TID})(?:\.mid)?", path)   # legacy alias
            if m:
                found = STORE.find_take(m.group(1))
                if not found:
                    return self._error(404, "download not found or expired; generate again")
                return self._mid(found[1])
            m = re.fullmatch(rf"/api/sessions/({SID})", path)
            if m:
                sess = STORE.get(m.group(1))
                return self._json(200, dict(session_payload(sess), usage=RATE.usage(self.client_ip())))
            m = re.fullmatch(rf"/api/sessions/({SID})/takes/({TID})\.mid", path)
            if m:
                return self._mid(self._take(*m.groups())[1])
            m = re.fullmatch(rf"/api/sessions/({SID})/takes/({TID})", path)
            if m:
                sess, t = self._take(*m.groups())
                return self._json(200, take_payload(sess, t, self.client_ip()))
            m = re.fullmatch(rf"/api/sessions/({SID})/compare", path)
            if m:
                sess = STORE.get(m.group(1))
                if not query.get("a") or not query.get("b"):
                    raise ApiError("compare needs ?a=<take id>&b=<take id>", field="a" if not query.get("a") else "b")
                a, b = sess.takes.get(query["a"]), sess.takes.get(query["b"])
                if not a or not b:
                    raise ApiError("no such take in this session (it may have been deleted)", status=404,
                                   code="take_not_found")
                return self._json(200, do_compare(sess, a, b))
            if path.startswith("/api/"):
                return self._error(404, f"no such endpoint: {path}")
            return self._error(404, "not found")
        return self._run(route)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        ip = self.client_ip()

        def route():
            if path == "/api/generate":
                body = self._body()
                if body == {} and not int(self.headers.get("Content-Length") or 0):
                    raise ApiError("empty request body; send JSON")
                return self._json(200, do_generate(body, ip))
            m = re.fullmatch(rf"/api/sessions/({SID})/takes", path)
            if m:
                return self._json(200, do_new_take(STORE.get(m.group(1)), self._body(), ip))
            m = re.fullmatch(rf"/api/sessions/({SID})/takes/({TID})/regenerate", path)
            if m:
                sess, t = self._take(*m.groups())
                return self._json(200, do_regenerate(sess, t, self._body(), ip))
            m = re.fullmatch(rf"/api/sessions/({SID})/takes/({TID})/locks", path)
            if m:
                sess, t = self._take(*m.groups())
                body = self._body()
                if not isinstance(body, dict):
                    raise ApiError("request body must be a JSON object")
                with sess.lock:
                    t.locked = _parse_indices(body.get("locked", []), len(t.perf.sections), "locked")
                return self._json(200, take_meta(sess, t))
            m = re.fullmatch(rf"/api/sessions/({SID})/takes/({TID})/delete", path)
            if m:
                return self._delete_take(*m.groups())
            return self._error(404 if path.startswith("/api/") else 405, f"no POST endpoint: {path}")
        return self._run(route)

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]

        def route():
            m = re.fullmatch(rf"/api/sessions/({SID})/takes/({TID})", path)
            if m:
                return self._delete_take(*m.groups())
            m = re.fullmatch(rf"/api/sessions/({SID})", path)
            if m:
                STORE.get(m.group(1))
                STORE.remove(m.group(1))
                return self._json(200, {"ok": True})
            return self._error(404, f"no DELETE endpoint: {path}")
        return self._run(route)

    def _delete_take(self, sid, tid):
        sess, t = self._take(sid, tid)
        with sess.lock:
            if len(sess.takes) <= 1:
                raise ApiError("This is the song's only take, so it can't be deleted. Generate a new song instead.",
                               status=409, code="last_take")
            sess.takes.pop(tid, None)
            STORE.unindex_take(tid)
            t.path.unlink(missing_ok=True)
        return self._json(200, session_payload(sess))

    def _static(self, rel: str):
        p = (STATIC / rel).resolve()
        if STATIC.resolve() not in p.parents or not p.is_file():
            return self._error(404, "not found")
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(p.suffix, "application/octet-stream")
        self._send(200, p.read_bytes(), ctype)


def make_server(host: str, port: int, quiet: bool = False) -> ThreadingHTTPServer:
    global STORE
    if STORE is None:
        STORE = SessionStore(Path(tempfile.mkdtemp(prefix="midigen-ui-")))
    Handler.quiet = quiet
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local web UI for midigen")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8766)), help="port (default 8766; the REST service uses 8765)")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH, help=f"limits file (default {DEFAULT_CONFIG_PATH}; missing = defaults)")
    ap.add_argument("--midigen-src", default=DEFAULT_SRC, help=f"path to midigen/src (default {DEFAULT_SRC})")
    ap.add_argument("--quiet", action="store_true", help="no request logging")
    args = ap.parse_args(argv)
    set_config(args.config)
    load_midigen(args.midigen_src)
    srv = make_server(args.host, args.port, args.quiet)
    host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0", "") else args.host
    src = f"config {args.config}" if CONFIG_INFO["loaded"] else "built-in default limits (no config file)"
    print(f"midigen-ui serving on http://{host}:{srv.server_address[1]}/  (midigen from {Path(mg.__file__).parent}; {src})",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()
        if STORE:
            shutil.rmtree(STORE.root, ignore_errors=True)


if __name__ == "__main__":
    main()
