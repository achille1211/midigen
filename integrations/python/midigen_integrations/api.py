"""Stable, typed Python API for embedding midigen in other applications.

This module is the integration contract: the REST service, the streaming tool and third-party apps should call these
functions rather than midigen's internals. Inputs and outputs are plain JSON-serialisable values (``dict``/``list``/
``str``/numbers) plus raw MIDI ``bytes``, so the same shapes travel over HTTP unchanged.

Stability promise (API_VERSION "1"): function names, parameter names and the documented keys of returned dicts stay
compatible; new optional parameters and new keys may be added. The song ``spec`` itself is midigen's spec format
(``midigen.SPEC_SCHEMA``) and is versioned by midigen (``spec["version"]``).

All failures raise :class:`MidigenError` with a machine-readable ``kind`` and a list of human-readable ``errors``.
"""
from __future__ import annotations

import copy
import io
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

import midigen as mg
from midigen import spec as specmod
from midigen.genres import MAX_BARS, UnknownGenre
from midigen.midi_io import performance_to_midi
from midigen.theory import MODES

API_VERSION = "1"
KEYS = ["C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B"]
TEMPO_RANGE = (20.0, 300.0)

Spec = dict  # a midigen song spec (see midigen.SPEC_SCHEMA)


class MidigenError(ValueError):
    """Any failure in this API.

    Attributes:
        kind: one of ``"invalid_params"``, ``"unknown_genre"``, ``"invalid_spec"``, ``"invalid_ops"``,
            ``"not_understood"``.
        errors: human-readable error messages (at least one).
    """

    def __init__(self, kind: str, errors: Iterable[str]):
        self.kind = kind
        self.errors = [str(e) for e in errors] or [kind]
        super().__init__("; ".join(self.errors))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "errors": self.errors}


def _wrap(fn, *a, **kw):
    """Call into midigen and translate its exceptions to MidigenError."""
    try:
        return fn(*a, **kw)
    except MidigenError:
        raise
    except UnknownGenre as e:
        raise MidigenError("unknown_genre", [e.args[0] if e.args else str(e)]) from e
    except mg.SpecError as e:
        raise MidigenError("invalid_spec", getattr(e, "errors", None) or [str(e)]) from e
    except mg.OpError as e:
        raise MidigenError("invalid_ops", getattr(e, "errors", None) or [str(e)]) from e
    except mg.NotUnderstood as e:
        raise MidigenError("not_understood", [str(e)]) from e
    except ValueError as e:
        raise MidigenError("invalid_params", [str(e)]) from e


# --------------------------------------------------------------------------- parameters / results

@dataclass
class GenerateParams:
    """Parameters for :func:`generate`. Give ``genre`` and/or a free-text ``prompt``.

    Attributes:
        prompt: free text such as ``"moody lofi in D minor, 16 bars"`` (used when ``genre`` is not given; parsed
            offline unless ``use_llm`` is true and an LLM provider is configured via MIDIGEN_* env vars).
        genre: genre id, display name or alias (see :func:`list_genres`).
        key: note name ``A``-``G`` with optional ``#``/``b``. Default: the genre's choice.
        mode: one of ``list_options()["modes"]``. Default: the genre's first mode.
        tempo: BPM 20-300. Default: the genre default.
        bars: total bars 1-256 (exact). Default: the genre's form.
        seed: random seed; the same params and seed always give the same song.
        refine: run midigen's deterministic critic loop (as the CLI does for ``--genre``).
        title: optional song title.
        use_llm: allow an LLM provider from env vars (prompt path only). Default False (fully offline, deterministic).
    """

    prompt: Optional[str] = None
    genre: Optional[str] = None
    key: Optional[str] = None
    mode: Optional[str] = None
    tempo: Optional[float] = None
    bars: Optional[int] = None
    seed: int = 1
    refine: bool = True
    title: Optional[str] = None
    use_llm: bool = False

    def validate(self) -> None:
        """Raise MidigenError("invalid_params") if any value is out of range (0 is a value, not 'unset')."""
        errs = []
        if not (self.prompt and self.prompt.strip()) and not self.genre:
            errs.append("give a prompt or a genre")
        if self.tempo is not None and not TEMPO_RANGE[0] <= float(self.tempo) <= TEMPO_RANGE[1]:
            errs.append(f"tempo must be between 20 and 300 BPM (got {self.tempo:g})")
        if self.bars is not None and not 1 <= int(self.bars) <= MAX_BARS:
            errs.append(f"bars must be between 1 and {MAX_BARS} (got {self.bars})")
        if self.mode is not None and self.mode not in MODES:
            errs.append(f"mode {self.mode!r} is not a mode; choose from: {', '.join(sorted(MODES))}")
        if self.key is not None and not re.fullmatch(r"[A-Ga-g][#b]?", str(self.key).strip()):
            errs.append(f"key {self.key!r} is not a key; use a note name: {' '.join(KEYS)}")
        if errs:
            raise MidigenError("invalid_params", errs)


@dataclass
class Song:
    """Result of :func:`generate` / :func:`edit`.

    Attributes:
        spec: the full editable song spec (JSON-serialisable; feed it back to :func:`edit`, :func:`to_midi_bytes`).
        summary: compact metadata, see :func:`summarize`.
        critic: the critic report ``{"score": 0-100, "findings": [...], "stats": {...}}``.
        source: ``"offline"``, ``"llm"`` or ``"offline-fallback"``.
        ops: edit ops that were applied (for :func:`edit`; empty for generate).
    """

    spec: Spec
    summary: dict
    critic: dict
    source: str = "offline"
    ops: list = field(default_factory=list)

    @property
    def midi(self) -> bytes:
        """Standard MIDI file (type 1, one track per instrument, drums on channel 10) as bytes."""
        return to_midi_bytes(self.spec)

    def to_dict(self) -> dict:
        return asdict(self)


EditResult = Song  # edit() returns the same shape; alias kept for readability in type hints


# --------------------------------------------------------------------------- core functions

def summarize(spec: Spec, critic: Optional[dict] = None) -> dict:
    """Compact metadata for a spec.

    Keys: ``title, genre, key, mode, tempo, time_signature, bars, total_beats, duration_seconds, seed, sections
    [{name, bars, energy, chords}], arrangement, tracks [{name, role, channel (0-based), program, notes}], critic_score``.
    """
    spec = validate_spec(spec)
    perf = _wrap(mg.render, spec)
    critic = critic if critic is not None else _wrap(mg.critique, perf)
    qbpm = specmod.quarter_bpm(spec)
    return {
        "title": spec.get("title"), "genre": spec.get("genre"), "key": spec["key"], "mode": spec["mode"],
        "tempo": spec["tempo"], "time_signature": list(spec["time_signature"]), "bars": specmod.total_bars(spec),
        "total_beats": perf.total_beats, "duration_seconds": round(perf.total_beats * 60.0 / qbpm, 3),
        "seed": spec.get("seed"),
        "sections": [{"name": s["name"], "bars": s["bars"], "energy": s.get("energy"), "chords": s["chords"]}
                     for s in spec["sections"]],
        "arrangement": list(spec["arrangement"]),
        "tracks": [{"name": t.name, "role": t.role, "channel": t.channel, "program": t.program, "notes": len(t.notes)}
                   for t in perf.tracks],
        "critic_score": critic.get("score"),
    }


def validate_spec(spec: Spec) -> Spec:
    """Validate and normalise a spec (returns a normalised copy). Raises MidigenError("invalid_spec")."""
    if not isinstance(spec, dict):
        raise MidigenError("invalid_spec", ["spec must be a JSON object"])
    return _wrap(specmod.check, copy.deepcopy(spec))


def generate(params: Optional[GenerateParams] = None, **kwargs: Any) -> Song:
    """Generate a new song.

    Call as ``generate(GenerateParams(genre="house", bars=16))`` or ``generate(genre="house", bars=16)``.
    Deterministic for a given set of params when ``use_llm`` is false.

    Returns:
        :class:`Song` with ``spec``, ``summary``, ``critic`` and ``.midi`` bytes.
    Raises:
        MidigenError: ``invalid_params``, ``unknown_genre``.
    """
    p = params if params is not None else GenerateParams(**kwargs)
    if params is not None and kwargs:
        raise TypeError("pass either a GenerateParams or keyword arguments, not both")
    p.validate()
    source = "offline"
    if p.genre:
        spec = _wrap(mg.build_spec, p.genre, key=p.key, mode=p.mode, tempo=p.tempo, bars=p.bars, seed=p.seed,
                     title=p.title)
        if p.refine:
            spec, _ = _wrap(mg.refine, spec)
    else:
        comp = mg.Composer(use_env=p.use_llm, auto_refine=p.refine)
        r = _wrap(comp.generate, p.prompt, seed=p.seed)
        spec, source = r.spec, r.source
        spec = _overrides(spec, p)
    return _song(spec, source=source)


def _overrides(spec: Spec, p: GenerateParams) -> Spec:
    """Explicit key/mode/tempo/bars/title beat whatever the prompt said (same rules as the CLI)."""
    ops: list[dict] = []
    if p.tempo is not None:
        ops.append({"op": "set_tempo", "bpm": p.tempo})
    if p.key is not None or p.mode is not None:
        ops.append({"op": "set_key", "key": p.key or spec["key"], **({"mode": p.mode} if p.mode else {})})
    if ops:
        spec = _wrap(mg.apply_ops, spec, ops)
    if p.bars is not None and p.bars != specmod.total_bars(spec):
        spec = _wrap(mg.build_spec, spec.get("genre") or "pop", key=spec["key"], mode=spec["mode"],
                     tempo=spec["tempo"], bars=p.bars, seed=p.seed)
    if p.title:
        spec = _wrap(mg.apply_ops, spec, [{"op": "set_title", "title": p.title}])
    return spec


def edit(spec: Spec, ops: Optional[list] = None, instruction: Optional[str] = None, use_llm: bool = False) -> Song:
    """Edit a song with JSON edit ops (see ``list_options()["ops"]``) or a plain-English instruction.

    The input spec is not modified. Exactly one of ``ops`` / ``instruction`` must be given.

    Raises:
        MidigenError: ``invalid_spec``, ``invalid_ops``, ``not_understood``, ``invalid_params``.
    """
    if (ops is None) == (not instruction):
        raise MidigenError("invalid_params", ["give exactly one of ops or instruction"])
    spec = validate_spec(spec)
    if ops is not None:
        if isinstance(ops, dict):
            ops = [ops]
        new = _wrap(mg.apply_ops, spec, ops)
        return _song(new, source="offline", ops=list(ops))
    r = _wrap(mg.Composer(use_env=use_llm).edit, spec, instruction)
    return _song(r.spec, source=r.source, ops=list(r.ops))


def critique(spec: Spec) -> dict:
    """Run the music-theory critic: ``{"score": 0-100, "findings": [{check, severity, message, ...}], "stats": {...}}``."""
    return _wrap(mg.critique, validate_spec(spec))


def to_midi_bytes(spec: Spec) -> bytes:
    """Render a spec to a Standard MIDI File (type 1, 480 PPQ, conductor track + one track per instrument)."""
    perf = _wrap(mg.render, validate_spec(spec))
    buf = io.BytesIO()
    performance_to_midi(perf).save(file=buf)
    return buf.getvalue()


def render_notes(spec: Spec) -> dict:
    """Render to plain note data for hosts that build their own MIDI (DAWs, synths, games).

    Returns ``{"tempo_qpm", "time_signature", "total_beats", "sections": [{name, start, end, energy}],
    "tracks": [{name, role, channel, program, volume, pan, is_drums, notes: [[start, dur, pitch, velocity], ...]}]}``.
    Times are in quarter-note beats from the song start; ``tempo_qpm`` is quarter notes per minute.
    """
    spec = validate_spec(spec)
    perf = _wrap(mg.render, spec)
    return {
        "tempo_qpm": specmod.quarter_bpm(spec), "time_signature": list(spec["time_signature"]),
        "total_beats": perf.total_beats,
        "sections": [{"name": s.name, "start": s.start, "end": s.end, "energy": s.energy} for s in perf.sections],
        "tracks": [{"name": t.name, "role": t.role, "channel": t.channel, "program": t.program, "volume": t.volume,
                    "pan": t.pan, "is_drums": t.is_drums,
                    "notes": [[round(n.start, 6), round(n.dur, 6), int(n.pitch), int(n.vel)]
                              for n in sorted(t.notes, key=lambda n: (n.start, n.pitch))]}
                   for t in perf.tracks],
    }


def list_genres() -> list[dict]:
    """All available genres (builtin and packs): ``[{id, display, description, tempo: {min,max,default}, modes}]``."""
    out = []
    for gid in mg.available_genres():
        g = mg.get_genre(gid)
        out.append({"id": gid, "display": g.get("display", gid), "description": g.get("description", ""),
                    "tempo": dict(g.get("tempo", {})), "modes": list(g.get("modes", []))})
    return out


def genre_profile(name: str) -> dict:
    """Summary profile of one genre (form, harmony, instruments, ...). Raises MidigenError("unknown_genre")."""
    import json
    prof = _wrap(mg.profile_summary, _wrap(mg.resolve_genre, name))
    return json.loads(json.dumps(prof, default=list))  # tuples -> lists, JSON-safe


def list_options() -> dict:
    """Everything a UI needs to build a generate/edit form.

    Keys: ``api_version, genres (ids), keys, modes, limits {tempo:[min,max], bars:[min,max]}, instruments, styles
    (per role), drum_patterns, ops (op name -> JSON schema), spec_schema``.
    """
    return {
        "api_version": API_VERSION, "midigen_version": mg.__version__, "genres": mg.available_genres(),
        "keys": KEYS, "modes": list(MODES), "limits": {"tempo": list(TEMPO_RANGE), "bars": [1, MAX_BARS]},
        "instruments": sorted(mg.INSTRUMENTS), "styles": {k: list(v) for k, v in mg.STYLES.items()},
        "drum_patterns": list(mg.DRUM_PATTERNS), "ops": mg.OP_SCHEMAS, "spec_schema": mg.SPEC_SCHEMA,
    }


def _song(spec: Spec, source: str = "offline", ops: Optional[list] = None) -> Song:
    spec = validate_spec(spec)
    rep = _wrap(mg.critique, spec)
    return Song(spec=spec, summary=summarize(spec, rep), critic=rep, source=source, ops=ops or [])
