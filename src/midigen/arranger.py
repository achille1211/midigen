"""Arranger: walks the arrangement, builds harmony timelines and renders every track."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import spec as specmod
from .core import ChordSpan, Ctx, Note, clamp, stable_seed
from .generators import DRUM_MAP, get_generator
from .theory import Chord, parse_chord, scale_pcs


@dataclass
class RenderedTrack:
    name: str
    role: str
    channel: int
    program: int
    volume: int
    pan: int
    notes: list = field(default_factory=list)
    is_drums: bool = False


@dataclass
class SectionSpan:
    name: str
    start: float
    end: float
    energy: float
    index: int


@dataclass
class HarmonySpan:
    start: float
    end: float
    chord: Chord
    symbol: str
    section: str


@dataclass
class Performance:
    spec: dict
    tracks: list
    sections: list
    harmony: list
    total_beats: float
    bar_beats: float

    def track(self, name: str) -> RenderedTrack:
        return next(t for t in self.tracks if t.name == name)

    def chord_at(self, beat: float) -> HarmonySpan:
        for h in self.harmony:
            if h.start <= beat + 1e-6 < h.end:
                return h
        return self.harmony[-1]

    def section_at(self, beat: float) -> SectionSpan:
        for s in self.sections:
            if s.start <= beat + 1e-6 < s.end:
                return s
        return self.sections[-1]


def section_chords(spec: dict, section: dict, bb: float) -> list[ChordSpan]:
    per = float(section.get("beats_per_chord") or bb)
    durs = [float(x) for x in section.get("chord_beats") or []]
    length = section["bars"] * bb
    out, t, i = [], 0.0, 0
    syms = section["chords"]
    while t < length - 1e-6:
        sym = syms[i % len(syms)]
        dur = min(durs[i % len(durs)] if durs else per, length - t)
        out.append(ChordSpan(t, dur, parse_chord(sym, spec["key"], spec["mode"])))
        t += dur
        i += 1
    return out


def _plays(track: dict, section_name: str) -> bool:
    return not track.get("mute") and (track.get("sections") is None or section_name in track["sections"])


def _genre(spec: dict):
    if not spec.get("genre"):
        return None
    from .genres import get_genre
    try:
        return get_genre(spec["genre"])
    except KeyError:
        return None


def render(spec: dict) -> Performance:
    spec = specmod.check(spec)
    bb = specmod.bar_beats(spec)
    secs = {s["name"]: s for s in spec["sections"]}
    order = spec["arrangement"]
    genre = _genre(spec)
    scale = scale_pcs(spec["key"], spec["mode"])
    tracks = [RenderedTrack(t["name"], t["role"], t["channel"], t["program"], t.get("volume", 100), t.get("pan", 64),
                            is_drums=t["channel"] == 9) for t in spec["tracks"]]
    sections, harmony = [], []
    t0 = 0.0
    seen: dict[str, int] = {}
    # drums first so the bass can interlock with the kick
    track_order = sorted(range(len(spec["tracks"])), key=lambda i: 0 if spec["tracks"][i]["generator"]["type"] == "drums" else 1)
    for idx, name in enumerate(order):
        sec = secs[name]
        occ = seen.get(name, 0)
        seen[name] = occ + 1
        length = sec["bars"] * bb
        chords = section_chords(spec, sec, bb)
        sections.append(SectionSpan(name, t0, t0 + length, sec.get("energy", 0.5), idx))
        sym_cycle = sec["chords"]
        for i, c in enumerate(chords):
            harmony.append(HarmonySpan(t0 + c.start, t0 + c.end, c.chord, sym_cycle[i % len(sym_cycle)], name))
        prev_e = secs[order[idx - 1]].get("energy", 0.5) if idx > 0 else None
        nxt = order[idx + 1] if idx + 1 < len(order) else None
        next_e = secs[nxt].get("energy", 0.5) if nxt else None
        kicks: list[float] = []
        for ti in track_order:
            tr = spec["tracks"][ti]
            if not _plays(tr, name):
                continue
            params = dict(tr["generator"])
            params.update(tr.get("overrides", {}).get(name, {}))
            seed = stable_seed(spec.get("seed", 1), params.get("seed", 0), tr["name"], name)
            ctx = Ctx(spec=spec, track=tr, section=sec, params=params, bars=sec["bars"], bar_beats=bb,
                      chords=chords, key=spec["key"], mode=spec["mode"], scale=scale,
                      energy=sec.get("energy", 0.5), prev_energy=prev_e, next_energy=next_e, next_section=nxt,
                      occurrence=occ, rng=random.Random(seed), kick_onsets=list(kicks), genre=genre)
            notes = get_generator(params["type"])(ctx)
            if params["type"] == "drums" and not kicks:
                kicks = sorted({round(n.start, 4) for n in notes if n.pitch in (35, 36)})
            notes = _groove(notes, spec, params, random.Random(seed ^ 0x5EED), length, tr["channel"] == 9)
            for n in notes:
                n.start += t0
            tracks[ti].notes.extend(notes)
        t0 += length
    for t in tracks:
        # chord parts that push a chord change (start up to half a beat early and ring across it)
        # play the NEW chord, as an arranger writes anticipations
        if spec["tracks"][tracks.index(t)]["generator"]["type"] in ("chords", "pad"):
            _anticipate(t.notes, harmony)
        # near-identical (onset, pitch) pairs (e.g. a pattern crash plus a section crash, nudged apart by
        # humanize) would collapse into one MIDI note anyway: keep the loudest
        kept: list[Note] = []
        last: dict = {}
        for n in sorted(t.notes, key=lambda n: (n.start, -n.vel)):
            p = last.get(n.pitch)
            if p is not None and n.start - p.start < 0.03:
                if n.vel > p.vel:
                    p.vel = n.vel
                p.dur = max(p.dur, n.dur)
                continue
            last[n.pitch] = n
            kept.append(n)
        t.notes = sorted(kept, key=lambda n: (n.start, n.pitch))
    return Performance(spec, tracks, sections, harmony, t0, bb)


def _anticipate(notes: list[Note], harmony: list) -> None:
    import bisect
    starts = [h.start for h in harmony]
    for n in notes:
        i = bisect.bisect_right(starts, n.start + 1e-6)
        if i >= len(harmony):
            continue
        change = starts[i]
        over = n.start + n.dur - change
        if 0 < change - n.start <= 0.5 and 0 < over <= 0.1:
            n.dur = max(0.03, change - n.start - 0.01)  # a humanize nudge, not a tie: stop before the change
        elif 0 < change - n.start <= 0.5 and over > 0.1:
            pcs = set(harmony[i].chord.pcs)
            if n.pitch % 12 not in pcs:
                n.pitch = min((p for p in range(n.pitch - 6, n.pitch + 7) if p % 12 in pcs and 0 <= p <= 127),
                              key=lambda p: (abs(p - n.pitch), p > n.pitch), default=n.pitch)


def swing_time(t: float, swing: float, unit: float) -> float:
    """Swing time warp. Each pair of `unit`s keeps its length; its midpoint moves to 0.5 + swing/6 of the pair
    and grid points (multiples of unit/2) in between scale linearly. Off-grid times (triplets, humanized
    clips) are left alone."""
    half = unit / 2.0
    if not swing or abs(t / half - round(t / half)) > 1e-3:
        return t
    t = round(t / half) * half
    pair = 2.0 * unit
    k = int(t // pair)
    p = t - k * pair
    if p < 1e-9:
        return t
    d = clamp(swing, 0.0, 1.0) * unit / 3.0
    if p <= unit:
        return k * pair + p * (unit + d) / unit
    return k * pair + unit + d + (p - unit) * (unit - d) / unit


DEFAULT_HUMANIZE = {"timing": 0.006, "velocity": 4}  # used when a generated track sets no humanize
MONOPHONIC = ("bass", "lead")


def _groove(notes: list[Note], spec: dict, params: dict, rng: random.Random, length: float, drums: bool) -> list[Note]:
    """Swing, laid-back backbeat, humanize, then clip monophonic lines so no note rings into the next onset.

    Swing convention (spec version 2, identical to the midigen-genres files): `swing` runs 0-1 and puts the
    off-beat of each `swing_unit` pair at 0.5 + swing/6 of the pair, i.e. 0 = straight 50%, 0.25 ~ 54%,
    0.5 ~ 58% (MPC), 1 = full triplet 66.7%. Grid notes inside a pair are time-warped with it, so with
    1/8 swing the in-between 16ths move proportionally. Version-1 specs (old 0-0.33 scale) are migrated
    by spec.normalize."""
    swing = params.get("swing", spec.get("swing", 0.0)) or 0.0
    unit = 0.5 if spec.get("swing_unit", "1/16") == "1/8" else 0.25
    gtype = params.get("type")
    hum = params.get("humanize")
    if hum is None:
        hum = {} if gtype == "clip" else DEFAULT_HUMANIZE
    tj, vj = hum.get("timing", 0.0), hum.get("velocity", 0)
    lb = float(params.get("laid_back", 0.0) or 0.0) / 1000.0 * specmod.quarter_bpm(spec) / 60.0  # ms -> quarter beats
    bb = spec["time_signature"][0] * 4 / spec["time_signature"][1]
    out = []
    for n in notes:
        s = n.start
        d0 = n.dur
        if swing:
            s = swing_time(n.start, swing, unit)
            if not drums and s != n.start:
                end = n.start + n.dur
                # a grid-aligned end moves with the grid; otherwise keep the end so the short note doesn't ring on
                d0 = max(0.03, swing_time(end, swing, unit) - s)
        if lb and (not drums or n.pitch in (37, 38, 39, 40)):
            s += lb
        if tj:
            on_bar = abs(s / bb - round(s / bb)) < 1e-3
            # never pull a note ahead of a bar line (it would sound under the previous chord)
            s += rng.uniform(0, tj) if s <= 0.01 or (on_bar and not drums) else rng.uniform(-tj, tj)
        v = n.vel + (rng.randint(-vj, vj) if vj else 0)
        s = clamp(s, 0.0, max(0.0, length - 0.01))
        dur = max(0.03, min(d0, length - s))
        out.append(Note(s, dur, int(clamp(n.pitch, 0, 127)), int(clamp(v, 1, 127))))
    if gtype in MONOPHONIC:
        out.sort(key=lambda n: (n.start, -n.vel))
        onsets = sorted({round(n.start, 4) for n in out})
        import bisect
        for n in out:
            i = bisect.bisect_right(onsets, round(n.start, 4) + 1e-4)
            if i < len(onsets):
                n.dur = max(0.03, min(n.dur, onsets[i] - n.start - 0.01))
    return out
