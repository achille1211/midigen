"""Melody engine: motifs, motif development and phrase structure.

A motif is a rhythm plus a contour in *scale steps*. It is developed by repetition, sequence
(transposition by scale steps), inversion and retrograde, and laid out in call/response phrases.
Realisation snaps strong-beat notes to chord tones, resolves leaps by step in the opposite
direction, keeps the line in range and lands phrase ends on cadence-appropriate tones."""
from __future__ import annotations

import math

from dataclasses import dataclass

from .core import Ctx, Note
from .generators import _range
from .theory import MODES, note_to_pc

# one-beat rhythm cells in 16th steps: list of (onset_step, length_steps)
CELLS = {
    "rest": [],
    "q": [(0, 4)],
    "h": [(0, 8)],          # spans two beats
    "8_8": [(0, 2), (2, 2)],
    "8r_8": [(2, 2)],
    "dot8_16": [(0, 3), (3, 1)],
    "16x4": [(0, 1), (1, 1), (2, 1), (3, 1)],
    "8_16_16": [(0, 2), (2, 1), (3, 1)],
    "16_16_8": [(0, 1), (1, 1), (2, 2)],
    "syn": [(0, 1), (1, 2), (3, 1)],
    "off16": [(1, 2), (3, 1)],
}
STYLE_WEIGHTS = {
    "sparse": {"rest": 3, "q": 3, "h": 3, "8_8": 1, "8r_8": 1},
    "melodic": {"rest": 1, "q": 3, "h": 1.5, "8_8": 3, "dot8_16": 1.5, "8r_8": 1, "8_16_16": 0.7},
    "riff": {"rest": 0.7, "8_8": 3, "8r_8": 2, "syn": 2, "off16": 1.5, "16_16_8": 1, "q": 1},
    "busy": {"8_8": 3, "16x4": 2, "8_16_16": 2, "16_16_8": 2, "q": 0.5, "dot8_16": 1},
}


@dataclass
class MotifNote:
    step: int      # 16th offset from motif start
    length: int    # 16ths
    interval: int  # scale steps from previous note (first note: offset from anchor)


def _weighted(rng, weights: dict):
    keys = list(weights)
    return rng.choices(keys, [weights[k] for k in keys])[0]


def make_motif(ctx: Ctx, bars: int, style: str) -> list[MotifNote]:
    rng = ctx.rng
    spb = ctx.steps_per_bar
    weights = dict(STYLE_WEIGHTS.get(style, STYLE_WEIGHTS["melodic"]))
    d = ctx.density
    weights["rest"] = weights.get("rest", 0.5) * (1.6 - d)
    for k in ("16x4", "8_16_16", "16_16_8", "syn"):
        if k in weights:
            weights[k] *= 0.5 + d
    beats = int(bars * spb / 4)
    rhythm: list[tuple[int, int]] = []
    b = 0
    cells = ctx.params.get("cells")
    if cells:
        rhythm = _rhythm_from_cells(rng, cells, bars * spb, d)
        beats = 0  # skip the beat-cell builder
    while b < beats:
        cell = _weighted(rng, weights)
        if b == 0 and cell in ("rest", "8r_8", "off16") and style != "riff":
            cell = "q"
        if cell == "h" and b + 1 >= beats:
            cell = "q"
        for on, ln in CELLS[cell]:
            rhythm.append((b * 4 + on, ln))
        b += 2 if cell == "h" else 1
    if not rhythm:
        rhythm = [(0, 4)]
    # contour: mostly steps, some thirds, occasional leap (resolved later)
    notes = []
    direction = rng.choice([-1, 1])
    for i, (st, ln) in enumerate(rhythm):
        if i == 0:
            iv = 0
        else:
            r = rng.random()
            step_p = float(ctx.params.get("stepwise", 0.55))
            third_p = step_p + (1 - step_p) * 0.55
            leap_p = third_p + (1 - third_p) * 0.55
            max_leap = int(ctx.params.get("max_leap", 12))
            if r < step_p:
                iv = direction
            elif r < third_p:
                iv = 2 * direction
            elif r < leap_p:
                iv = rng.choice([3, 4] if max_leap >= 7 else [2, 3]) * direction
            else:
                iv = 0 if style == "riff" else -direction
            if rng.random() < 0.3:
                direction = -direction
        notes.append(MotifNote(st, ln, iv))
    # make sure a note sustains to fill a small gap before the motif repeats
    return notes


def _rhythm_from_cells(rng, cells, total_steps: int, density: float) -> list[tuple[int, int]]:
    """Chain genre-pack rhythm cells (signed 16th lengths; negative = rest) to fill the motif."""
    out, t = [], 0
    usable = [c for c in cells if c and any(x > 0 for x in c)] or [[4]]
    first = True
    while t < total_steps:
        cell = rng.choice(usable)
        for x in cell:
            ln = int(abs(x)) or 1
            if t >= total_steps:
                break
            if x > 0 or (first and not out):
                out.append((t, min(ln, total_steps - t)))
            t += ln
        first = False
    return out or [(0, 4)]


def sequence(m: list[MotifNote], steps: int) -> list[MotifNote]:
    """The same rhythm and contour transposed by `steps` scale steps (a melodic sequence)."""
    if not m:
        return m
    return [MotifNote(m[0].step, m[0].length, m[0].interval + steps)] + [MotifNote(n.step, n.length, n.interval) for n in m[1:]]


def vary_tail(ctx: Ctx, m: list[MotifNote], motif_bars: int, style: str) -> list[MotifNote]:
    """Keep the first half of the motif and write a new second half: the 'varied ending' of a 4-bar phrase."""
    spb = ctx.steps_per_bar
    half = motif_bars * spb // 2 if motif_bars > 1 else spb // 2
    head = [MotifNote(n.step, n.length, n.interval) for n in m if n.step < half]
    fresh = make_motif(ctx, motif_bars, style)
    tail = [MotifNote(n.step, n.length, n.interval) for n in fresh if n.step >= half]
    if head and tail:
        tail[0].interval = ctx.rng.choice([-1, 1, 2, -2])
    for a, b in zip(head, head[1:] + tail[:1]):
        a.length = min(a.length, b.step - a.step)
    return head + tail if head + tail else m


def invert(m: list[MotifNote]) -> list[MotifNote]:
    return [MotifNote(n.step, n.length, -n.interval if i else 0) for i, n in enumerate(m)]


def retrograde(m: list[MotifNote]) -> list[MotifNote]:
    ivs = [n.interval for n in m][1:][::-1]
    return [MotifNote(n.step, n.length, 0 if i == 0 else -ivs[i - 1]) for i, n in enumerate(m)]


def plan_phrases(ctx: Ctx, motif_bars: int) -> list[dict]:
    """Lay out motif instances across the section as call/response phrases."""
    bars = ctx.bars
    name = ctx.section["name"].lower()
    plan = []
    phrase_bars = 4 if bars >= 4 else bars
    if "chorus" in name or "hook" in name or "drop" in name:
        devs = ["repeat", "repeat", "sequence_up", "repeat"]      # catchy: lots of literal repetition
    elif "bridge" in name or "b" == name or "break" in name:
        devs = ["invert", "sequence_down", "invert", "retrograde"]
    else:
        devs = ["repeat", "sequence_up", "repeat", "sequence_down"]
    i = 0
    for bar in range(0, bars, motif_bars):
        in_phrase = bar % phrase_bars
        phrase_idx = bar // phrase_bars
        last_in_phrase = in_phrase + motif_bars >= phrase_bars or bar + motif_bars >= bars
        n_phrases = max(1, (bars + phrase_bars - 1) // phrase_bars)
        is_last_phrase = phrase_idx == n_phrases - 1
        dev = "original" if bar == 0 else devs[i % len(devs)]
        role = None
        if last_in_phrase:
            # call (open, unstable) then response (closed). The section's final phrase follows its cadence.
            if is_last_phrase:
                role = "open" if ctx.section.get("cadence") == "half" else "close"
            else:
                role = "open" if phrase_idx % 2 == 0 else "close"
        plan.append({"bar": bar, "dev": dev, "end": role,
                     "tail": last_in_phrase and bar > 0 and dev in ("repeat", "sequence_up", "sequence_down")})
        i += 1
    return plan


def _deg_to_pitch(idx: int, tonic: int, scale: list[int], base_octave: int = 5) -> int:
    o, d = divmod(idx, 7)
    return tonic + 12 * (base_octave + o) + scale[d]


def _pitch_to_deg(p: int, tonic: int, scale: list[int], base_octave: int = 5) -> int:
    rel = p - tonic - 12 * base_octave
    o, r = divmod(rel, 12)
    d = min(range(7), key=lambda k: abs(scale[k] - r))
    return o * 7 + d


def gen_lead(ctx: Ctx) -> list[Note]:
    style = ctx.params.get("style", "melodic")
    lo, hi = _range(ctx, "lead")
    tonic = note_to_pc(ctx.key)
    scale = MODES[ctx.mode]
    spb = ctx.steps_per_bar
    sb = ctx.bar_beats / spb
    motif_bars = 1 if ctx.bars < 2 else 2
    motif = make_motif(ctx, motif_bars, style)
    plan = plan_phrases(ctx, motif_bars)
    centre = (lo + hi) // 2 + int(round((ctx.energy - 0.6) * 8))  # choruses sit higher than verses
    centre = max(lo + 3, min(hi - 3, centre))
    p_ct = float(ctx.params.get("chord_tone_strong", 0.9))
    tails = {}
    idx = _pitch_to_deg(centre, tonic, scale)
    notes: list[Note] = []
    prev_pitch = None
    prev_leap = 0
    shift = 0
    for unit in plan:
        m = motif
        if unit["dev"] == "invert":
            m = invert(motif)
        elif unit["dev"] == "retrograde":
            m = retrograde(motif)
        elif unit["dev"] == "sequence_up":
            shift += 1
            m = sequence(motif, 0)
        elif unit["dev"] == "sequence_down":
            shift -= 1
            m = sequence(motif, 0)
        elif unit["dev"] in ("repeat", "original"):
            shift = 0
        if unit.get("tail"):
            # the same varied ending each time this phrase position recurs (keeps choruses catchy)
            key = (unit["dev"], unit["bar"] % 8)
            if key not in tails:
                tails[key] = vary_tail(ctx, m, motif_bars, style)
            m = tails[key]
        start_idx = _pitch_to_deg(centre, tonic, scale) + shift + (ctx.rng.choice([0, 2]) if unit["dev"] == "original" else 0)
        idx = start_idx
        unit_notes = []
        for k, mn in enumerate(m):
            if unit["bar"] * spb + mn.step >= ctx.bars * spb:
                break
            if k == 0:
                idx += mn.interval
            if k > 0:
                iv = mn.interval
                if prev_leap and abs(prev_leap) > 4:
                    iv = -1 if prev_leap > 0 else 1   # resolve a leap by step the other way
                idx += iv
            p = _deg_to_pitch(idx, tonic, scale)
            if p > hi or p < lo:
                idx -= 2 * (mn.interval or 1) * (1 if p > hi else -1) if k else 0
                p = _deg_to_pitch(idx, tonic, scale)
                while p > hi:
                    p -= 12; idx -= 7
                while p < lo:
                    p += 12; idx += 7
            t_step = unit["bar"] * spb + mn.step
            t = t_step * sb
            span = ctx.chord_at(t)
            pcs = span.chord.pcs
            strong = t_step % (spb // 2 if spb >= 8 else spb) == 0 or abs(t - span.start) < 1e-6
            if strong and p % 12 not in pcs and ctx.rng.random() < p_ct:
                p = _nearest_chord_tone(p, pcs, lo, hi)
            elif p % 12 not in pcs:
                p = _fix_chromatic_clash(p, pcs, ctx.scale)
            idx = _pitch_to_deg(p, tonic, scale)
            if prev_pitch is not None:
                prev_leap = p - prev_pitch
            prev_pitch = p
            unit_notes.append(Note(t, mn.length * sb * 0.92, p, ctx.velocity(90, 8 if strong else 0)))
        if unit["end"] and unit_notes:
            last = unit_notes[-1]
            unit_end = min(ctx.length, (unit["bar"] + motif_bars) * ctx.bar_beats)
            span = ctx.chord_at(last.start)
            if unit["end"] == "close":
                targets = [tonic] if tonic in span.chord.pcs else [span.chord.root, span.chord.pcs[1]]
            else:
                targets = [pc for pc in (span.chord.pcs[1:3] + [(tonic + scale[1]) % 12, (tonic + scale[4]) % 12])
                           if pc in span.chord.pcs or pc in ctx.scale]
            ref = unit_notes[-2].pitch if len(unit_notes) > 1 else last.pitch
            cands = [p for pc in targets for p in range(lo, hi + 1) if p % 12 == pc]
            if cands:
                last.pitch = min(cands, key=lambda p: abs(p - ref))
            last.dur = max(last.dur, min(unit_end - last.start - 0.1, 2.0))
            prev_pitch = last.pitch
        notes += unit_notes
    notes.sort(key=lambda n: n.start)
    prev = None
    for n in notes:
        if prev is not None and abs(n.pitch - prev) > 12:
            q = n.pitch + (-12 if n.pitch > prev else 12)
            if lo <= q <= hi:
                n.pitch = q
        prev = n.pitch
    for n in notes:  # a held note must not become a dissonance when the harmony changes under it
        for span in ctx.chords:
            if n.start < span.start < n.start + n.dur - 0.05 and n.pitch % 12 not in span.chord.pcs:
                n.dur = span.start - n.start - 0.02
                break
    # a note held across a strong beat sounds on that beat too: keep it consonant there
    sb = ctx.bar_beats / 2 if float(ctx.bar_beats).is_integer() and int(ctx.bar_beats) % 2 == 0 else ctx.bar_beats
    for n in notes:
        k = math.floor(n.start / sb + 1e-6) + 1
        beat = k * sb
        if n.start + 1e-6 < beat < n.start + n.dur - 0.02:
            pcs = ctx.chord_at(beat).chord.pcs
            if n.pitch % 12 not in pcs:
                own = ctx.chord_at(n.start).chord.pcs
                both = [q for q in range(max(lo, n.pitch - 5), min(hi, n.pitch + 5) + 1) if q % 12 in pcs and q % 12 in own]
                if both:
                    n.pitch = min(both, key=lambda q: (abs(q - n.pitch), q))
                else:
                    n.dur = max(0.05, beat - n.start - 0.02)
    # remove accidental overlaps
    for a, b in zip(notes, notes[1:]):
        if a.start + a.dur > b.start:
            a.dur = max(0.05, b.start - a.start - 0.02)
    # a note followed by a rest is heard as a phrase ending: land it on a chord tone
    for i, n in enumerate(notes):
        nxt = notes[i + 1].start if i + 1 < len(notes) else None
        if nxt is None or nxt - (n.start + n.dur) >= 0.45:
            pcs = ctx.chord_at(n.start).chord.pcs
            if n.pitch % 12 not in pcs:
                n.pitch = _nearest_chord_tone(n.pitch, pcs, lo, hi)
    for a, b in zip(notes, notes[1:]):  # the fixes above may open a leap wider than an octave
        if abs(b.pitch - a.pitch) > 12:
            q = b.pitch + (-12 if b.pitch > a.pitch else 12)
            if lo <= q <= hi:
                b.pitch = q
    return notes


def _nearest_chord_tone(p: int, pcs: list[int], lo: int, hi: int) -> int:
    cands = [q for q in range(max(lo, p - 6), min(hi, p + 6) + 1) if q % 12 in pcs]
    return min(cands, key=lambda q: (abs(q - p), q)) if cands else p


def _fix_chromatic_clash(p: int, pcs: list[int], scale: list[int]) -> int:
    """If a scale tone sits a semitone from a chromatic chord tone (e.g. G over E7 in C), use the chord tone."""
    for d in (-1, 1):
        q = (p + d) % 12
        if q in pcs and q not in scale:
            return p + d
    return p
