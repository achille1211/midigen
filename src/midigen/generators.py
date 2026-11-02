"""Deterministic note generators. Each takes a Ctx and returns notes relative to the section start."""
from __future__ import annotations

import json
from importlib import resources

from .core import Ctx, Note, clamp
from .harmony import voice_lead
from .theory import nearest_pitch

DRUM_MAP = {"kick": 36, "snare": 38, "clap": 39, "rim": 37, "chh": 42, "phh": 44, "ohh": 46,
            "ride": 51, "crash": 49, "tom_hi": 50, "tom_mid": 47, "tom_lo": 41, "shaker": 70,
            "cowbell": 56, "tamb": 54}
HIT_VEL = {"x": 112, "o": 92, "g": 48, "r": 80}
KICKS, BACKBEAT, HATS, TOMS = {35, 36}, {37, 38, 39, 40}, {42, 44}, {41, 43, 45, 47, 48, 50}
TIMEKEEPERS = {42, 44, 46, 51, 53, 59, 54, 69, 70, 82}  # hats, rides, tambourine, shakers: metric accents apply

_PATTERNS = None
_EXTERNAL: dict = {}


def _grid_from_string(grid: str) -> tuple[list[int], list[int]]:
    vel, rolls = [], []
    for i, ch in enumerate(grid):
        vel.append(HIT_VEL.get(ch, 0))
        if ch == "r":
            rolls.append(i)
    return vel, rolls


def _legacy_pattern(name: str, raw: dict) -> dict:
    voices, ratchets = [], []
    for inst, grid in raw.items():
        if not isinstance(grid, str) or not grid or inst in ("comp", "keep_ghosts"):
            continue
        vel, rolls = _grid_from_string(grid)
        base = "crash" if inst == "crash4" else inst
        voices.append({"name": inst, "note": DRUM_MAP[base], "grid": vel, "every": 4 if inst == "crash4" else 1})
        ratchets += [{"note": DRUM_MAP[base], "step": st, "count": 3, "curve": "crescendo"} for st in rolls]
    steps = max([len(v["grid"]) for v in voices] or [16])
    return {"name": name, "voices": voices, "ratchets": ratchets, "steps": steps, "type": "main",
            "keep_ghosts": {DRUM_MAP[k] for k in raw.get("keep_ghosts", [])},
            "comp": DRUM_MAP.get(raw.get("comp")) if raw.get("comp") else None, "variants": {}}


def builtin_drum_patterns() -> dict:
    global _PATTERNS
    if _PATTERNS is None:
        raw = json.loads(resources.files("midigen").joinpath("data/drums.json").read_text())
        out = {}
        for k, v in raw.items():
            if k.startswith("_"):
                continue
            out[k] = _legacy_pattern(k, v)
            for sub in ("verse", "chorus"):
                if isinstance(v.get(sub), dict):
                    out[f"{k}.{sub}"] = _legacy_pattern(f"{k}.{sub}", v[sub])
                    out[k]["variants"][sub] = f"{k}.{sub}"
        _PATTERNS = out
    return _PATTERNS


def register_drum_patterns(patterns: dict) -> None:
    """Register step-grid patterns from genre packs (see packs.py). Names are namespaced, e.g. 'bossa_nova.main'."""
    _EXTERNAL.update(patterns)


def clear_external_patterns() -> None:
    _EXTERNAL.clear()


def drum_patterns() -> dict:
    from . import packs
    packs.ensure_loaded()
    return {**builtin_drum_patterns(), **_EXTERNAL}


def _pattern_bar(pat: dict, bar: int, spb: int):
    """(voice, velocities for this bar) for a pattern that may span several bars."""
    nbars = max(1, pat["steps"] // spb)
    for v in pat["voices"]:
        g = v["grid"]
        off = (bar % nbars) * spb
        yield v, [g[(off + i) % len(g)] for i in range(spb)]


DEFAULT_RANGES = {"bass": (28, 52), "chords": (52, 74), "pad": (48, 72), "arp": (60, 86), "lead": (64, 88)}


def _range(ctx: Ctx, kind: str) -> tuple[int, int]:
    if "range" in ctx.params:
        lo, hi = ctx.params["range"]
        return (min(lo, hi), max(lo, hi))
    lo, hi = DEFAULT_RANGES[kind]
    if "octave" in ctx.params:
        # shift the default window so its centre sits in the requested octave
        centre = 12 * (ctx.params["octave"] + 1) + (7 if kind != "bass" else 4)
        half = (hi - lo) // 2
        return (max(0, centre - half), min(127, centre + half))
    return (lo, hi)




# ------------------------------------------------------------------ drums

def gen_drums(ctx: Ctx) -> list[Note]:
    pats = drum_patterns()
    pat = pats.get(ctx.params.get("pattern", "pop"), pats["pop"])
    name = ctx.section["name"].lower()
    chorusy = any(w in name for w in ("chorus", "hook", "drop", "shout", "peak", "climax")) or ctx.energy >= 0.8
    if chorusy and "chorus" in pat.get("variants", {}):
        pat = pats[pat["variants"]["chorus"]]
    elif "verse" in name and "verse" in pat.get("variants", {}):
        pat = pats[pat["variants"]["verse"]]
    variation = pats.get(ctx.params.get("variation") or "")
    fill_pat = pats.get(ctx.params.get("fill_pattern") or "")
    transition = pats.get(ctx.params.get("transition") or "")
    fill_every = int(ctx.params.get("fill_every") or 0)
    spb = ctx.steps_per_bar
    step = ctx.bar_beats / spb
    d = ctx.density
    e = ctx.energy
    rng = ctx.rng
    notes: list[Note] = []
    bars = ctx.bars
    rising = ctx.next_energy is not None and ctx.next_energy - e >= 0.25
    fill = ctx.params.get("fill", True) and ctx.next_section is not None
    build = rising and ("build" in ctx.section["name"] or ctx.next_energy >= 0.85)
    entering = ctx.prev_energy is not None and e - ctx.prev_energy >= 0.2
    # seed-dependent variation: drop some optional (off-beat, medium) kick hits for this section
    drop_opt = {i for i in range(spb * 4) if rng.random() < 0.2}
    for bar in range(bars):
        last_bar = bar == bars - 1
        cur = pat
        if transition is not None and bar == 0 and entering:
            cur = transition
        elif variation is not None and (bar // 4) % 2 == 1:
            cur = variation
        phrase_fill = fill_every and (bar + 1) % fill_every == 0 and not last_bar and bars > fill_every
        if fill_pat is not None and ((fill and last_bar and not build) or phrase_fill):
            cur = fill_pat
        replaced_by_fill = cur is fill_pat and fill_pat is not None
        for voice, grid in _pattern_bar(cur, bar if cur is not fill_pat else bar - (bars - 1) + fill_pat["steps"] // spb - 1, spb):
            note = voice["note"]
            if voice.get("every", 1) > 1 and bar % voice["every"]:
                continue
            if e < 0.25 and note in BACKBEAT - {37} and any(v["note"] in KICKS for v in cur["voices"]):
                continue  # low energy: strip the backbeat
            uniform = len({v for v in grid if v}) <= 1
            for i in range(spb):
                v = grid[i]
                ghost = False
                if not v:
                    if note in HATS and d > 0.7 and i % 2 == 1 and rng.random() < (d - 0.6):
                        v, ghost = 48, True  # busier: 16th hats
                    elif note in (38, 40) and d > 0.65 and i % 4 in (1, 3) and rng.random() < (d - 0.6) * 0.5:
                        v, ghost = 44, True  # ghost notes
                    else:
                        continue
                if v < 60 and d < 0.35 and note not in cur.get("keep_ghosts", ()):
                    continue
                if note in TIMEKEEPERS and d < 0.3 and i % 4 != 0:
                    continue
                if note in KICKS and i % 4 and v < 100 and (bar * spb + i) % (spb * 4) in drop_opt and cur is pat:
                    continue
                t = bar * ctx.bar_beats + i * step
                if (fill and last_bar and not replaced_by_fill
                        and t >= (bars - 1) * ctx.bar_beats + ctx.bar_beats * (0.5 if not build else 0)):
                    if note in BACKBEAT | TOMS or (note in KICKS and build):
                        continue  # replaced by the procedural fill below
                # metric accents for timekeepers with flat velocities, plus seeded jitter on every hit
                if note in TIMEKEEPERS and uniform and not ghost:
                    v += {0: 8, 2: -6, 1: -12, 3: -14}[i % 4]
                v = int(max(1, min(127, v + rng.randint(-5, 5))))
                rat = next((r for r in cur["ratchets"] if r["note"] == note and r["step"] == (i + bar * spb) % cur["steps"]
                            or (r["note"] == note and r["step"] == i and cur["steps"] <= spb)), None)
                if rat:
                    n = rat["count"]
                    for k in range(n):
                        f = k / max(1, n - 1)
                        scale = {"crescendo": 0.6 + 0.4 * f, "decrescendo": 1.0 - 0.4 * f}.get(rat["curve"], 1.0)
                        notes.append(Note(t + k * step / n, step / n, note, int(max(1, min(127, v * scale)))))
                    continue
                notes.append(Note(t, step * 0.9, note, v))
            if cur.get("comp") == note and note in BACKBEAT and d >= 0.35:
                # jazz-style snare comping: a couple of random "drops" per bar at low velocity
                for i in rng.sample(range(spb), k=min(spb, 1 + int(d * 2))):
                    if i % 4 and not grid[i]:
                        notes.append(Note(bar * ctx.bar_beats + i * step, step * 0.9, note, 55 + rng.randint(-6, 8)))
    if (ctx.prev_energy is None or e >= 0.6 or (ctx.prev_energy is not None and e - ctx.prev_energy >= 0.2)) and e >= 0.4:
        notes.append(Note(0.0, 1.0, DRUM_MAP["crash"], 105))
    if fill and (fill_pat is None or build):
        notes += _fill(ctx, build)
    return notes


def _fill(ctx: Ctx, build: bool) -> list[Note]:
    bb = ctx.bar_beats
    end = ctx.length
    out = []
    if build:
        # snare roll over the last bar, 8ths then 16ths, crescendo; last 16th left empty for impact
        n = int(bb * 4)
        for i in range(n - 1):
            t = end - bb + i * 0.25
            if i < n // 2 and i % 2:
                continue
            out.append(Note(t, 0.2, DRUM_MAP["snare"], int(60 + 60 * i / n)))
        return out
    if ctx.next_energy is not None and ctx.next_energy < ctx.energy - 0.25:
        out.append(Note(end - 0.5, 0.5, DRUM_MAP["ohh"], 90))  # into a breakdown: open hat, no fill
        return out
    start = end - min(2.0, bb / 2)
    toms = [DRUM_MAP["snare"], DRUM_MAP["tom_hi"], DRUM_MAP["tom_mid"], DRUM_MAP["tom_lo"]]
    n = int((end - start) / 0.25)
    for i in range(n):
        if ctx.rng.random() < 0.25 and i not in (0, n - 1):
            continue
        out.append(Note(start + i * 0.25, 0.22, toms[min(3, i * 4 // n)], 80 + int(30 * i / n)))
    return out


# ------------------------------------------------------------------ bass

def gen_bass(ctx: Ctx) -> list[Note]:
    style = ctx.params.get("style", "root")
    lo, hi = _range(ctx, "bass")
    target = lo + 7
    d = ctx.density
    notes: list[Note] = []
    kicks = sorted(ctx.kick_onsets)
    prev_pitch = None
    planned = None  # a root already targeted by a chromatic approach note

    def bp(pc, near=None):
        return nearest_pitch(pc, near if near is not None else target, lo, hi)

    for ci, span in enumerate(ctx.chords):
        ch = span.chord
        root = bp(ch.bass, prev_pitch if prev_pitch is not None else target)
        if abs(root - target) > 7:
            root = bp(ch.bass)
        if planned is not None and planned % 12 == ch.bass and lo <= planned <= hi:
            root = planned
        planned = None
        fifth = bp((ch.root + 7) % 12, root + 7)
        octave = root + 12 if root + 12 <= hi else root
        nxt = ctx.chords[ci + 1].chord if ci + 1 < len(ctx.chords) else None
        s, L = span.start, span.dur
        vel = ctx.velocity(96)
        if style == "root":
            hits = [0.0] if d < 0.5 else [x * 1.0 for x in range(int(L))] if d > 0.75 else [x * 2.0 for x in range(int(L // 2) or 1)]
            for i, h in enumerate(hits):
                dur = (hits[i + 1] - h if i + 1 < len(hits) else L - h) * 0.9
                notes.append(Note(s + h, dur, root, vel))
        elif style == "root_fifth":
            for b in range(int(L)):
                if b % 2 == 0:
                    p = root if b % 4 == 0 else fifth
                    notes.append(Note(s + b, 1.4 if d < 0.6 else 0.9, p, vel if b % 4 == 0 else vel - 8))
                    if d >= 0.6 and b + 1.5 < L:
                        notes.append(Note(s + b + 1.5, 0.45, p, vel - 18))
        elif style in ("pulse", "octave"):
            step = 0.5 if d < 0.8 else 0.25
            n = int(L / step)
            for i in range(n):
                p = root if style == "pulse" or i % 2 == 0 else octave
                notes.append(Note(s + i * step, step * 0.8, p, vel - (0 if i % 2 == 0 else 12)))
        elif style == "offbeat":
            for b in range(int(L)):
                notes.append(Note(s + b + 0.5, 0.45, root, vel))
                if d > 0.75 and b % 2 == 1:
                    notes.append(Note(s + b + 0.75, 0.2, octave, vel - 20))
        elif style == "rolling":
            kick_set = {round(k, 2) for k in kicks}
            for i in range(int(L * 4)):
                t = s + i * 0.25
                if round(t, 2) in kick_set or (not kick_set and i % 4 == 0):
                    continue  # leave room for the kick (interlock by avoidance)
                if d < 0.5 and i % 2 == 0:
                    continue
                p = root if ctx.rng.random() > 0.2 else (octave if ctx.rng.random() > 0.5 else fifth)
                notes.append(Note(t, 0.2, p, vel - (0 if i % 4 == 2 else 15)))
        elif style == "walking":
            line = _walking_line(ctx, ch, nxt, root, prev_pitch, int(L), lo, hi, bp)
            if nxt is not None and int(L) >= 2:
                planned = bp(nxt.bass, line[-1])
                if abs(planned - line[-1]) != 1:
                    planned = line[-1] + 1 if (line[-1] + 1) % 12 == nxt.bass else line[-1] - 1 if (line[-1] - 1) % 12 == nxt.bass else planned
            for b, p in enumerate(line):
                notes.append(Note(s + b, 0.95, clamp(p, lo, hi), vel - (0 if b == 0 else 10)))
        elif style == "template":
            # genre-pack bass: a 16th-step rhythm (velocities) and per-step note tokens
            rhythm = ctx.params.get("rhythm") or [1, 0, 0, 0] * 4
            tokens = ctx.params.get("tokens") or ["root"]
            spb = ctx.steps_per_bar
            stp = ctx.bar_beats / spb
            hits = []
            for i in range(int(round(L / stp))):
                r = rhythm[i % len(rhythm)]
                if not r:
                    continue
                if d < 0.35 and i % spb and (i % 4):
                    continue
                hits.append((i, r))
            prev = root
            for hi_, (i, r) in enumerate(hits):
                tok = tokens[i % len(tokens)] if len(tokens) == len(rhythm) else tokens[hi_ % len(tokens)]
                t = i * stp
                nxt_t = hits[hi_ + 1][0] * stp if hi_ + 1 < len(hits) else L
                p = _bass_token(ctx, tok, ch, nxt, root, prev, t, L, lo, hi, bp)
                if p is None:
                    continue
                v = int(r) if isinstance(r, (int, float)) and r > 1 else vel
                gate = float(ctx.params.get("gate", 0.9))
                notes.append(Note(s + t, max(0.1, (nxt_t - t) * gate), p, int(clamp(ctx.velocity(v) if v != vel else v, 1, 127))))
                prev = p
        elif style in ("kick_lock", "sustained"):
            local = [k - s for k in kicks if s - 1e-6 <= k < s + L - 1e-6]
            if style == "sustained" and not local:
                local = [0.0]
            if not local or local[0] > 0.01:
                local = [0.0] + local
            if style == "sustained" and d < 0.4:
                local = [x for i, x in enumerate(local) if i == 0 or x >= L / 2][:2]
            for i, h in enumerate(local):
                nxt_t = local[i + 1] if i + 1 < len(local) else L
                dur = (nxt_t - h) * (0.95 if style == "sustained" else min(1.0, 0.85))
                if style == "kick_lock":
                    dur = min(dur, 1.5)
                p = root if (i == 0 or ctx.rng.random() > 0.25) else (octave if style == "sustained" else fifth)
                notes.append(Note(s + h, max(0.2, dur), p, vel))
        elif style == "syncopated":
            cell = ctx.params.get("_cell") or _bass_cell(ctx)
            ctx.params["_cell"] = cell
            kick_rel = {round(k - s, 2) for k in kicks if s <= k < s + L}
            for bar_off in range(0, int(L), int(ctx.bar_beats) or 4):
                for st, kind in cell:
                    t = bar_off + st
                    if t >= L:
                        continue
                    p = {"r": root, "o": octave, "f": fifth, "7": bp((ch.root + 10) % 12, root + 10) if 10 in ch.intervals else fifth}[kind]
                    dur = 0.75 if round(t, 2) not in kick_rel else 1.0  # clipped to the next onset later
                    notes.append(Note(s + t, dur, p, vel - (0 if st == 0 else 12)))
            if nxt is not None and d > 0.5:
                planned = bp(nxt.bass, root)
                notes.append(Note(s + L - 0.25, 0.22, planned - 1, vel - 20))
        elif style == "reggae":
            # leave beat 1 open; melodic phrase on chord tones
            seq = [(0.5, root), (1.0, root), (1.75, fifth), (2.5, octave if d > 0.5 else root), (3.0, fifth)]
            for bar_off in range(0, int(L), int(ctx.bar_beats) or 4):
                for t, p in seq:
                    if bar_off + t < L:
                        notes.append(Note(s + bar_off + t, 0.45, p, vel - 6))
        elif style == "arp":
            tones = [bp(pc, root + 6) for pc in ch.pcs]
            for i in range(int(L * 2)):
                notes.append(Note(s + i * 0.5, 0.45, tones[i % len(tones)], vel - 8))
        else:
            notes.append(Note(s, L * 0.95, root, vel))
        prev_pitch = root
    bb = ctx.bar_beats
    for n in notes:  # metric accents: the one and the beats push, offbeats relax
        pos = round(n.start % bb, 3)
        acc = 10 if pos == 0 else 5 if pos % 1 == 0 else -8
        n.vel = int(clamp(n.vel + acc + ctx.rng.randint(-4, 4), 1, 127))
    return [n for n in notes if lo - 1 <= n.pitch <= hi + 1]


def _scale_pitches(ctx: Ctx, ch, lo: int, hi: int) -> list[int]:
    pcs = set(ctx.scale) | set(ch.pcs)
    return [p for p in range(lo, hi + 1) if p % 12 in pcs]


def _walking_line(ctx: Ctx, ch, nxt, root, prev, beats, lo, hi, bp) -> list[int]:
    """Stepwise walking: beat 1 root, beat 3 a chord tone near the line, other beats scale steps,
    last beat a chromatic approach into the next root."""
    if beats <= 1:
        return [root]
    scale = _scale_pitches(ctx, ch, lo, hi)
    tones = [p for p in range(lo, hi + 1) if p % 12 in set(ch.pcs)]
    line = [root]
    target_next = bp(nxt.bass, root) if nxt is not None else root
    for b in range(1, beats):
        cur = line[-1]
        if b == beats - 1 and nxt is not None:
            line.append(target_next + (-1 if (ctx.rng.random() < 0.6 and target_next - 1 >= lo) or target_next + 1 > hi else 1))
            continue
        if b % 2 == 0:  # strong beat: chord tone near the current note, moving towards the next target
            cands = sorted((t for t in tones if t != cur and abs(t - cur) <= 5), key=lambda t: (abs(t - cur), ctx.rng.random()))
            line.append(cands[0] if cands else cur)
            continue
        # weak beat: scale step towards the next strong-beat goal
        goal = target_next if b + 1 >= beats - 1 else cur + ctx.rng.choice((-4, 4))
        direction = 1 if goal > cur else -1
        if cur + direction * 2 > hi or cur + direction * 2 < lo:
            direction = -direction
        steps = [p for p in scale if 0 < (p - cur) * direction <= 2]
        line.append(min(steps, key=lambda p: abs(p - cur)) if steps else cur + direction)
    return line


def _bass_token(ctx: Ctx, tok, ch, nxt, root, prev, t, L, lo, hi, bp):
    if tok is None or tok == "rest":
        return None
    tok = str(tok).lower()
    ivs = ch.intervals
    def iv(n, fallback=7):
        return bp((ch.root + n) % 12, root + n)
    if tok in ("root", "r", "1"):
        return root
    if tok in ("octave", "8", "oct"):
        return root + 12 if root + 12 <= hi else root
    if tok in ("fifth", "5"):
        return iv(next((x for x in ivs if x in (6, 7, 8)), 7))
    if tok in ("third", "3"):
        return iv(next((x for x in ivs if x in (3, 4)), 4))
    if tok in ("seventh", "7"):
        return iv(next((x for x in ivs if x in (9, 10, 11)), 10))
    if tok in ("ninth", "9"):
        return iv(2)
    if tok in ("chord_tone", "chordtone"):
        tones = [p for p in range(lo, hi + 1) if p % 12 in set(ch.pcs)]
        return min(tones, key=lambda p: (abs(p - prev), ctx.rng.random())) if tones else root
    if tok in ("scale_step", "step", "passing"):
        sc = [p for p in _scale_pitches(ctx, ch, lo, hi) if 0 < abs(p - prev) <= 2]
        return ctx.rng.choice(sc) if sc else prev
    if tok in ("approach", "chromatic_approach", "chromatic"):
        tgt = bp((nxt or ch).bass, prev)
        return clamp(tgt - 1 if tgt - 1 >= lo else tgt + 1, lo, hi)
    return root


def _bass_cell(ctx: Ctx) -> list[tuple[float, str]]:
    """A one-bar funk/neo-soul bass rhythm that always lands on the one and on most kicks."""
    bb = ctx.bar_beats
    kicks_in_bar = sorted({round(k % bb, 2) for k in ctx.kick_onsets if k < bb * 2})
    cand = [0.0, 0.75, 1.5, 2.0, 2.5, 2.75, 3.25, 3.5]
    cell = {0.0: "r"}
    for k in kicks_in_bar:
        cell[k] = "r" if k == 0 else ctx.rng.choice("rof")
    for c in cand:
        if c < bb and ctx.rng.random() < ctx.density * 0.6:
            cell.setdefault(c, ctx.rng.choice("roof7"))
    return sorted(cell.items())


# ------------------------------------------------------------------ chords / pads

def _n_voices(ctx: Ctx, chord) -> int:
    n = len(chord.intervals)
    return 3 if n <= 3 else 4


def gen_chords(ctx: Ctx) -> list[Note]:
    style = ctx.params.get("style", "pad" if ctx.params.get("type") == "pad" else "block")
    kind = "pad" if ctx.params.get("type") == "pad" else "chords"
    lo, hi = _range(ctx, kind)
    d = ctx.density
    gate = ctx.params.get("gate", 0.9)
    notes: list[Note] = []
    prev = None
    rhythm = ctx.params.get("rhythm")
    cell = _rhythm_cell(ctx, rhythm) if rhythm else _comp_cell(ctx, style)
    for span in ctx.chords:
        v = voice_lead(span.chord, prev, lo, hi, _n_voices(ctx, span.chord))
        prev = v
        s, L = span.start, span.dur
        base = ctx.velocity(80 if style != "pad" else 66) + ctx.rng.randint(-4, 4)
        if style == "power":
            root = nearest_pitch(span.chord.bass, lo + 5, lo, hi)
            fifth = root + (6 if 6 in span.chord.intervals and 7 not in span.chord.intervals else 7)
            stack = [root, fifth] + ([root + 12] if root + 12 <= hi + 5 else [])
            hits = list(_iter_cell(cell, L, ctx.bar_beats)) if rhythm else (
                [(i * 0.5, 6 if i % 2 == 0 else -6) for i in range(int(L * 2))] if d > 0.55 else
                [(i * 2.0, 6) for i in range(int(L // 2) or 1)])
            for i, (t, acc) in enumerate(hits):
                nt = hits[i + 1][0] if i + 1 < len(hits) else L
                for p in stack:
                    notes.append(Note(s + t, max(0.1, (nt - t) * (0.85 if d > 0.55 else 0.97)), p, int(clamp(base + acc, 1, 127))))
        elif rhythm and style not in ("pad", "arpeggio"):
            hits = list(_iter_cell(cell, L, ctx.bar_beats))
            for i, (t, acc) in enumerate(hits):
                nt = hits[i + 1][0] if i + 1 < len(hits) else L
                order = v if style != "strum" or int(t * 2) % 2 == 0 else list(reversed(v))
                for k, p in enumerate(order):
                    off = k * 0.015 if style == "strum" else 0.0
                    notes.append(Note(s + t + off, max(0.1, (nt - t) * gate - off), p, int(clamp(base + acc - (k * 2 if style == "strum" else 0), 1, 127))))
        elif style in ("block", "pad"):
            restrike = (style == "block" and d > 0.6) or (style == "pad" and d > 0.75)
            seg = ctx.bar_beats if restrike else L
            t = 0.0
            while t < L - 1e-6:
                dur = min(seg, L - t)
                for p in v:
                    notes.append(Note(s + t, dur * (gate if style == "block" else 0.99), p, base))
                t += seg
        elif style == "strum":
            for t, acc in _iter_cell(cell, L, ctx.bar_beats):
                down = int(t * 2) % 2 == 0
                order = v if down else list(reversed(v))
                for k, p in enumerate(order):
                    notes.append(Note(s + t + k * 0.015, 0.45, p, base + acc - k * 2))
        elif style == "arpeggio":
            n = int(L * 2)
            for i in range(n):
                notes.append(Note(s + i * 0.5, 0.5 * gate, v[i % len(v)], base - (0 if i % 4 == 0 else 10)))
        elif style == "offbeat":
            rate = ctx.params.get("rate", "1/8")
            hits = [b + 1.0 for b in range(0, int(L), 2)] if rate == "1/4" else [b + 0.5 for b in range(int(L))]
            for t in hits:
                if t < L:
                    for p in v:
                        notes.append(Note(s + t, 0.3, p, base))
        else:  # stabs, comp, bossa, pulse
            for t, acc in _iter_cell(cell, L, ctx.bar_beats):
                dur = 0.35 if style in ("stabs", "pulse") else 0.7 if style == "comp" else 0.45
                for p in v:
                    notes.append(Note(s + t, dur, p, base + acc))
    return notes


def _comp_cell(ctx: Ctx, style: str) -> list[tuple[float, int]]:
    """One-bar rhythm (beat offset, accent) for rhythmic chord styles, chosen by style + density."""
    d = ctx.density
    bb = ctx.bar_beats
    if style == "bossa":
        return [(0.0, 8), (0.75, 0), (1.5, 4), (2.5, 0), (3.0, 6)] if bb >= 4 else [(0.0, 8), (1.5, 0)]
    if style == "pulse":
        return [(i * 0.5, 6 if i % 2 == 0 else 0) for i in range(int(bb * 2))]
    if style == "strum":
        base = [(0.0, 10), (1.0, 0), (1.5, -4), (2.5, -4), (3.0, 4), (3.5, -6)]
        if d < 0.4:
            base = [(0.0, 10), (2.0, 2)]
        elif d > 0.75:
            base = [(i * 0.5, 8 if i % 2 == 0 else -4) for i in range(8)]
        return [b for b in base if b[0] < bb]
    if style == "comp":
        options = [[(0.0, 6), (1.5, 0)], [(0.5, 0), (2.0, 4)], [(0.0, 6), (2.5, 0), (3.5, -4)], [(1.5, 0), (3.0, 4)]]
        if d > 0.6:
            options += [[(0.0, 6), (1.5, 0), (2.5, -2), (3.5, -4)]]
        return [b for b in ctx.rng.choice(options) if b[0] < bb]
    # stabs
    hits = [(0.0, 8)]
    for t in [0.75, 1.5, 2.0, 2.5, 3.0, 3.5]:
        if t < bb and ctx.rng.random() < 0.2 + d * 0.6:
            hits.append((t, 0 if t % 1 else 4))
    return hits


def _rhythm_cell(ctx: Ctx, rhythm) -> list[tuple[float, int]]:
    """A pack rhythm (velocity per 16th step, possibly several bars) as (beat offset, accent) for one cycle."""
    spb = ctx.steps_per_bar
    stp = ctx.bar_beats / spb
    nb = max(1, len(rhythm) // spb)
    return [(i * stp, int((v - 100) / 3)) for i, v in enumerate(rhythm[:nb * spb]) if v] or [(0.0, 0)]


def _iter_cell(cell, L, bb):
    bar = 0.0
    span = bb * max(1, int(max(t for t, _ in cell) // bb) + 1) if cell else bb
    while bar < L - 1e-6:
        for t, acc in cell:
            if bar + t < L - 1e-6:
                yield bar + t, acc
        bar += span


# ------------------------------------------------------------------ arp

RATE_BEATS = {"1/4": 1.0, "1/8": 0.5, "1/16": 0.25, "1/8t": 1 / 3, "1/16t": 1 / 6}


def gen_arp(ctx: Ctx) -> list[Note]:
    style = ctx.params.get("style", "up")
    lo, hi = _range(ctx, "arp")
    step = RATE_BEATS.get(ctx.params.get("rate", "1/16"), 0.25)
    gate = ctx.params.get("gate", 0.6)
    d = ctx.density
    notes = []
    prev = None
    for span in ctx.chords:
        v = voice_lead(span.chord, prev, lo, min(hi, lo + 14), 3 if len(span.chord.intervals) <= 3 else 4)
        prev = v
        tones = v + [v[0] + 12] if v[0] + 12 <= hi else v
        if style == "down":
            seq = list(reversed(tones))
        elif style == "updown":
            seq = tones + list(reversed(tones[1:-1]))
        elif style == "pinned":
            seq = []
            for t in tones[1:]:
                seq += [tones[0], t]
        else:
            seq = tones
        n = int(round(span.dur / step))
        if style == "random" and "_arp_idx" not in ctx.params:
            # a seeded 2-bar figure that repeats, rather than fresh noise every step
            ctx.params["_arp_idx"] = [ctx.rng.randrange(8) for _ in range(int(round(2 * ctx.bar_beats / step)))]
        for i in range(n):
            if d < 0.35 and i % 2 == 1:
                continue
            if style == "random":
                idx = ctx.params["_arp_idx"]
                k = int(round((span.start + i * step) / step)) % len(idx)
                p = tones[idx[k] % len(tones)]
            else:
                p = seq[i % len(seq)]
            beat_pos = (i * step) % 1.0
            notes.append(Note(span.start + i * step, step * gate, p, ctx.velocity(78, 8 if beat_pos < 1e-6 else 0)))
    return notes


# ------------------------------------------------------------------ clip (imported material)

def gen_clip(ctx: Ctx) -> list[Note]:
    clip = ctx.params.get("clips", {}).get(ctx.section["name"], [])
    return [Note(float(s), float(du), int(p), int(v)) for s, du, p, v in clip if s < ctx.length]


def get_generator(kind: str):
    from .melody import gen_lead
    return {"drums": gen_drums, "bass": gen_bass, "chords": gen_chords, "pad": gen_chords,
            "arp": gen_arp, "lead": gen_lead, "clip": gen_clip}[kind]
