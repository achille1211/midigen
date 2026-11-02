"""Arranger: walks the arrangement, builds harmony timelines and renders every track."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from . import spec as specmod
from .core import ChordSpan, Ctx, Note, clamp, stable_seed
from .generators import DRUM_MAP, get_generator
from .melody import restate
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


NO_RESTATE = {"solo", "solos", "improv", "intro", "outro"}


DYNAMICS = 24   # velocity steps per 1.0 of section energy above/below the song's median (generator param "dynamics")
HOOK_FAMILIES = {"chorus", "hook", "drop", "peak", "climax", "bloom", "loop_b"}


def _quotes_hook(fam: str) -> bool:
    return fam.startswith(("post", "outro", "breakdown", "ending", "coda", "vamp_out", "tag"))


def section_family(name: str) -> str:
    """Sections that are instances of one another: chorus1/chorus2/final_chorus -> chorus, verse2 -> verse,
    head_a1 -> head_a, drop_2 -> drop. Pre-/post-choruses keep their own family."""
    base = re.sub(r"[\d_]+$", "", name.lower()) or name.lower()
    if base.startswith(("pre", "post")):
        return base
    for k in ("chorus", "hook", "drop"):
        if k in base:
            return k
    return base


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
    hook_memory: dict = {}   # track -> its first chorus/hook/drop lead, quoted in post-choruses and outros
    lead_memory: dict = {}   # (track, section family, style) -> the lead first played there, restated later
    carry: dict[int, list] = {}  # last chord voicing per track, so voice leading continues across sections
    # drums first so the bass can interlock with the kick
    track_order = sorted(range(len(spec["tracks"])), key=lambda i: 0 if spec["tracks"][i]["generator"]["type"] == "drums" else 1)
    es = sorted(secs[n].get("energy", 0.5) for n in order)
    e_ref = es[len(es) // 2] if es else 0.5
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
            if carry.get(ti):
                params["_prev_voicing"] = carry[ti]
            seed = stable_seed(spec.get("seed", 1), params.get("seed", 0), tr["name"], name)
            ctx = Ctx(spec=spec, track=tr, section=sec, params=params, bars=sec["bars"], bar_beats=bb,
                      chords=chords, key=spec["key"], mode=spec["mode"], scale=scale,
                      energy=sec.get("energy", 0.5), prev_energy=prev_e, next_energy=next_e, next_section=nxt,
                      occurrence=occ, rng=random.Random(seed), kick_onsets=list(kicks), genre=genre)
            notes = get_generator(params["type"])(ctx)
            if params["type"] == "lead" and params.get("restate", True):
                fam = section_family(name)
                key = (ti, fam, params.get("style"))
                if key in lead_memory and fam not in NO_RESTATE:
                    mem, mem_bars, mem_e = lead_memory[key]
                    notes = restate(ctx, mem, mem_bars, mem_e, notes)
                elif ti in hook_memory and fam not in HOOK_FAMILIES and _quotes_hook(fam) and sec["bars"] >= 4 \
                        and params.get("hook_quote", True):
                    # post-chorus, breakdown or outro: open with a 2-bar callback of the hook, then the fresh line
                    mem, mem_bars, mem_e = hook_memory[ti]
                    q = min(2, mem_bars) * bb
                    notes = restate(ctx, [n for n in mem if n.start < q], mem_bars, mem_e, notes, upto=q)
                if key not in lead_memory and notes and fam not in NO_RESTATE:
                    mem = []
                    for n in notes:
                        m = Note(n.start, n.dur, n.pitch, n.vel)
                        m.pcs = tuple(ctx.chord_at(n.start).chord.pcs)   # harmony it was written over
                        mem.append(m)
                    lead_memory[key] = (mem, sec["bars"], sec.get("energy", 0.5))
                    if fam in HOOK_FAMILIES and ti not in hook_memory:
                        hook_memory[ti] = lead_memory[key]
            dyn = params.get("dynamics", DYNAMICS) if params["type"] != "clip" else 0
            if dyn:
                # section dynamics: louder above the song's typical energy, softer below (contrast between
                # verse and chorus / breakdown and drop is heard, not only seen in the note count)
                dv = int(round((sec.get("energy", 0.5) - e_ref) * float(dyn)))
                for n in notes:
                    n.vel = max(1, min(127, n.vel + dv))
            if params.get("_last_voicing"):
                carry[ti] = params["_last_voicing"]
            if params["type"] == "drums" and not kicks:
                kicks = sorted({round(n.start, 4) for n in notes if n.pitch in (35, 36)})
            notes = _groove(notes, spec, params, random.Random(seed ^ 0x5EED), length, tr["channel"] == 9,
                            changes=[c.start for c in chords])
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
    _lift_peaks(tracks, spec, sections)
    _declash(tracks, spec, harmony)
    return Performance(spec, tracks, sections, harmony, t0, bb)


def _lift_peaks(tracks: list, spec: dict, sections: list) -> int:
    """A chorus / hook / drop melody that sits lower than the verses (the generator's range and a restated
    first chorus can leave it there) goes up an octave in every peak section, so the peak is heard as a lift.
    Applied uniformly, so restated choruses stay identical. Generator param "peak_lift": False opts out.
    Returns the number of sections lifted."""
    fam = {s.name: section_family(s.name) for s in sections}
    peaks = [s for s in sections if fam[s.name] in HOOK_FAMILIES]
    if not peaks:
        top = max((s.energy for s in sections), default=0)
        if top - min((s.energy for s in sections), default=0) < 0.04:
            return 0
        peaks = [s for s in sections if s.energy >= top - 1e-9]
    pk_e = min(s.energy for s in peaks)
    lifted = 0
    for t, ts in zip(tracks, spec["tracks"]):
        g = ts["generator"]
        if g["type"] != "lead" or not g.get("peak_lift", True):
            continue
        inside = lambda n, secs: any(s.start - 1e-6 <= n.start < s.end - 1e-6 for s in secs)
        pk = [n for n in t.notes if inside(n, peaks)]
        rest = [s for s in sections if s not in peaks and s.energy < pk_e and fam[s.name] not in ("intro", "outro")]
        ref = [n for n in t.notes if inside(n, rest)]
        if len(pk) < 4 or len(ref) < 4:
            continue
        mp = sum(n.pitch for n in pk) / len(pk)
        mr = sum(n.pitch for n in ref) / len(ref)
        hi = max(max(g.get("range", [0, 84])[1], max(n.pitch for n in ref)) + 3, 0)
        if mp < mr - 1.0 and max(n.pitch for n in pk) + 12 <= min(hi, 100):
            for n in pk:
                n.pitch += 12
            lifted += len({s.name for s in peaks})
    return lifted


CLASH_PRIORITY = {"lead": 3, "bass": 2}   # everything else (chords, pad, arp ...) is 1; clips are never touched


def _declash(tracks: list, spec: dict, harmony: list) -> int:
    """Strong-beat semitone / b9 rubs between parts. A note starting on a beat that sits a semitone (or b9)
    from a note another part is sounding is re-voiced, as an arranger would: a non-chord tone (passing tone
    landing on the beat) moves to the nearest chord tone; when both are chord tones (maj7 against the root, an
    extension against the melody) the lower-priority part (pad/comp below bass below lead) gives way and takes
    the nearest chord tone that sounds clean against everything around it. Returns the number of notes moved."""
    import bisect
    gtypes = [t["generator"]["type"] for t in spec["tracks"]]
    fixed = [g == "clip" or t["generator"].get("declash", True) is False for g, t in zip(gtypes, spec["tracks"])]
    hstarts = [h.start for h in harmony]

    def chord_at(t):
        i = bisect.bisect_right(hstarts, t + 1e-6) - 1
        return harmony[max(0, i)].chord if harmony else None

    pitched = [i for i, t in enumerate(tracks) if not t.is_drums and t.notes]
    allnotes = sorted(((n, i) for i in pitched for n in tracks[i].notes), key=lambda x: x[0].start)
    starts = [n.start for n, _ in allnotes]
    owner = {id(n): i for n, i in allnotes}

    def sounding(t0, t1, ti):
        """notes of other tracks overlapping [t0, t1)"""
        lo = bisect.bisect_left(starts, t0 - 16.0)
        hi = bisect.bisect_left(starts, t1)
        return [m for m, j in allnotes[lo:hi] if j != ti and m.vel > 0 and m.start < t1 and m.start + m.dur > t0]

    def pri(j):
        return CLASH_PRIORITY.get(gtypes[j], 1)

    def revoice(target, tj, ch, may_drop):
        """move target to the nearest chord tone that rubs with nothing around it (or drop a doubled voicing note)"""
        if fixed[tj] or (gtypes[tj] == "bass" and target.pitch % 12 == ch.bass):
            return False
        pcs = set(ch.pcs)
        around = sounding(target.start + 0.01, target.start + max(0.02, min(target.dur, 1.0)), tj)
        own = {m.pitch for m in tracks[tj].notes if m is not target and m.vel > 0 and abs(m.start - target.start) < 0.03}
        for d in sorted(range(-5, 6), key=lambda d: (abs(d), d > 0)):
            p = target.pitch + d
            if d and 0 <= p <= 127 and p not in own and p % 12 in pcs and not any(abs(p - m.pitch) % 12 == 1 for m in around):
                target.pitch = p
                return True
        if may_drop and gtypes[tj] not in MONOPHONIC and own:
            target.vel = 0
            return True
        return False

    moved = 0
    for n, ti in allnotes:
        if n.vel == 0 or abs(n.start - round(n.start)) > 0.03:
            continue
        foe = next((m for m in sounding(n.start + 0.01, n.start + 0.011, ti) if abs(n.pitch - m.pitch) % 12 == 1), None)
        ch = chord_at(n.start)
        if foe is None or ch is None:
            continue
        fj = owner[id(foe)]
        pcs = set(ch.pcs)
        same_onset = abs(foe.start - n.start) < 0.03          # a held note is only re-voiced if it starts here too
        cands = []
        # the melody (and its hook) is kept whenever the accompaniment can make room instead
        if gtypes[ti] == "lead" and pri(fj) == 1 and same_onset:
            cands = [(foe, fj), (n, ti)]
        elif gtypes[fj] == "lead" and pri(ti) == 1:
            cands = [(n, ti)]
        else:
            n_nct, f_nct = n.pitch % 12 not in pcs, foe.pitch % 12 not in pcs
            if same_onset and (f_nct and not n_nct or (n_nct == f_nct and pri(fj) < pri(ti))):
                cands = [(foe, fj), (n, ti)]
            else:
                cands = [(n, ti)] + ([(foe, fj)] if same_onset else [])
        if not same_onset and gtypes[ti] == "lead" and pri(fj) == 1 and not fixed[fj] and n.start - foe.start > 0.25:
            foe.dur = n.start - foe.start - 0.01        # the held pad/comp voice releases under the melody note
            moved += 1
            continue
        for k, (tg, tj) in enumerate(cands):
            if revoice(tg, tj, ch, k == len(cands) - 1):
                moved += 1
                break
    for i in pitched:
        tracks[i].notes = [n for n in tracks[i].notes if n.vel > 0]
    return moved


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


def _groove(notes: list[Note], spec: dict, params: dict, rng: random.Random, length: float, drums: bool,
            changes: list[float] | None = None) -> list[Note]:
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
            on_bar = abs(s / bb - round(s / bb)) < 1e-3 or any(abs(s - c) < 1e-3 for c in changes or ())
            # never pull a note ahead of a bar line or a mid-bar chord change (it would sound under the
            # previous chord)
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
