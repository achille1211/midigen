"""Harmony engine: diatonic chords, functional harmony, progressions, cadences,
secondary dominants, borrowed chords, extensions and voice leading."""
from __future__ import annotations

import random
from itertools import combinations

from .theory import MODES, Chord, is_roman, nearest_pitch, note_to_pc, parse_chord

NUMERALS = ["i", "ii", "iii", "iv", "v", "vi", "vii"]

# Functional role of each scale degree (0-based). T = tonic, S = predominant, D = dominant.
FUNCTIONS = {
    "major": {0: "T", 1: "S", 2: "T", 3: "S", 4: "D", 5: "T", 6: "D"},
    "minor": {0: "T", 1: "S", 2: "T", 3: "S", 4: "D", 5: "S", 6: "D"},
}
# Functional grammar: probability of the next function given the current one.
TRANSITIONS = {
    "T": {"T": 0.2, "S": 0.55, "D": 0.25},
    "S": {"S": 0.2, "D": 0.6, "T": 0.2},
    "D": {"T": 0.8, "S": 0.1, "D": 0.1},
}
CADENCES = {
    "authentic": ["V", "I"],      # D -> T, strongest close
    "plagal": ["IV", "I"],        # S -> T, "amen"
    "half": ["?", "V"],           # ends on D, open/question
    "deceptive": ["V", "vi"],     # D -> vi, surprise
    "none": [],
}
# Borrowed from the parallel minor, used in major keys (modal mixture).
BORROWED_MAJOR = {"IV": "iv", "vi": "bVI", "V": "bVII", "ii": "iiø7", "I": "i"}
BORROWED_MINOR = {"v": "V", "iv": "IV"}  # Picardy/dorian colour, raised leading tone


def _family(mode: str) -> str:
    return "major" if MODES[mode][2] == 4 else "minor"


def diatonic_chord(degree: int, mode: str, seventh: bool = False, ninth: bool = False) -> str:
    """Roman numeral for the triad/7th/9th built on a scale degree of the mode."""
    sc = MODES[mode]
    def iv(a, b):
        return (sc[(degree + b) % 7] - sc[(degree + a) % 7]) % 12
    third, fifth, sev = iv(0, 2), iv(0, 4), iv(0, 6)
    num = NUMERALS[degree]
    if third == 4:
        num = num.upper()
    acc = ""
    major = MODES["major"]
    if sc[degree] - major[degree] == -1:
        acc = "b"
    elif sc[degree] - major[degree] == 1:
        acc = "#"
    if fifth == 6:
        return acc + num + ("ø7" if (seventh or ninth) else "°")
    if fifth == 8:
        return acc + num + "+"
    if ninth:
        ext = "maj9" if sev == 11 and third == 4 else "9"
        return acc + num + ext
    if seventh:
        ext = "maj7" if sev == 11 and third == 4 else "7"
        return acc + num + ext
    return acc + num


def diatonic_chords(mode: str, sevenths: bool = False) -> list[str]:
    return [diatonic_chord(d, mode, sevenths) for d in range(7)]


def degree_of(symbol: str, key: str, mode: str) -> int | None:
    """Scale degree (0-6) of a chord's root, or None if chromatic."""
    c = parse_chord(symbol, key, mode)
    rel = (c.root - note_to_pc(key)) % 12
    sc = MODES[mode]
    return sc.index(rel) if rel in sc else None


def function_of(symbol: str, key: str, mode: str) -> str:
    """T / S / D / 'secondary' / 'borrowed' / 'chromatic'."""
    if "/" in symbol and is_roman(symbol):
        return "secondary"
    c = parse_chord(symbol, key, mode)
    scale = set((note_to_pc(key) + i) % 12 for i in MODES[mode])
    d = degree_of(symbol, key, mode)
    if d is None or not set(c.pcs[:3]) <= scale:
        # dominant chords on degree 5 in minor are still dominant (harmonic minor)
        if d == 4:
            return "D"
        return "borrowed" if d is not None or _is_mixture(c, key, mode) else "chromatic"
    return FUNCTIONS[_family(mode)][d]


def _is_mixture(c: Chord, key: str, mode: str) -> bool:
    other = "minor" if _family(mode) == "major" else "major"
    sc = set((note_to_pc(key) + i) % 12 for i in MODES[other])
    return set(c.pcs[:3]) <= sc


def analyze(chords: list[str], key: str, mode: str) -> list[dict]:
    out = []
    for i, sym in enumerate(chords):
        c = parse_chord(sym, key, mode)
        out.append({"chord": sym, "function": function_of(sym, key, mode),
                    "root_pc": c.root, "pcs": c.pcs})
    if len(chords) >= 2:
        out[-1]["cadence"] = detect_cadence(chords, key, mode)
    return out


def detect_cadence(chords: list[str], key: str, mode: str) -> str:
    """Classify the final two chords by root motion relative to the tonic."""
    if len(chords) < 2:
        return "none"
    tonic = note_to_pc(key)
    a = (parse_chord(chords[-2], key, mode).root - tonic) % 12
    b = (parse_chord(chords[-1], key, mode).root - tonic) % 12
    if b == 7:
        return "half"
    if b == 0 and a in (7, 11, 10):
        return "authentic"
    if b == 0 and a == 5:
        return "plagal"
    if a == 7 and b in (8, 9):
        return "deceptive"
    return "none"


def _pick(rng: random.Random, weights: dict):
    r, acc = rng.random() * sum(weights.values()), 0.0
    for k, w in weights.items():
        acc += w
        if r <= acc:
            return k
    return k


def _degree_symbol(degree: int, mode: str) -> str:
    sym = diatonic_chord(degree, mode)
    # in minor keys use a major V for dominant pull unless modal flavour wanted
    return sym


def generate_progression(mode: str, length: int, rng: random.Random, cadence: str = "authentic",
                         start_tonic: bool = True, minor_dominant: str = "V") -> list[str]:
    """Generate a functional progression of `length` chords ending on a cadence."""
    fam = _family(mode)
    by_func: dict[str, list[int]] = {"T": [], "S": [], "D": []}
    for d, f in FUNCTIONS[fam].items():
        if diatonic_chord(d, mode).endswith("°"):
            continue  # diminished triads are rarely used as block chords in popular styles
        by_func[f].append(d)
    weights = {0: 3.0, 5: 2.0, 3: 2.5, 1: 1.5, 4: 2.5, 2: 0.6, 6: 1.2}
    cad = CADENCES.get(cadence, [])
    body_len = max(0, length - len(cad))
    prog: list[str] = []
    func = "T"
    for i in range(body_len):
        if i == 0 and start_tonic:
            prog.append(_degree_symbol(0, mode))
            continue
        func = _pick(rng, TRANSITIONS[func])
        cands = {d: weights[d] for d in by_func[func]}
        if prog:
            last = degree_of_symbol(prog[-1])
            cands.pop(last, None)
            if func == "D":
                cands.pop(4 if last == 4 else -1, None)
        d = _pick(rng, cands or {0: 1.0})
        prog.append(_degree_symbol(d, mode))
        func = FUNCTIONS[fam][d]
    cad_syms = []
    for sym in cad:
        if sym == "?":
            sym = _degree_symbol(rng.choice([3, 1]), mode)  # predominant before the half cadence
        elif fam == "minor":
            sym = {"I": "i", "IV": diatonic_chord(3, mode), "vi": "bVI", "V": minor_dominant}.get(sym, sym)
        cad_syms.append(sym)
    if prog and cad_syms and degree_of_symbol(prog[-1]) == degree_of_symbol(cad_syms[0]) and len(prog) > 1:
        bad = {degree_of_symbol(cad_syms[0]), degree_of_symbol(prog[-2])}
        alt = [d for d in by_func["T"] + by_func["S"] if d not in bad]
        if alt:
            prog[-1] = _degree_symbol(rng.choice(alt), mode)
    prog += cad_syms
    return prog[:length] if length else prog


def degree_of_symbol(sym: str) -> int | None:
    s = sym.lstrip("b#").lower()
    for n in sorted(NUMERALS, key=len, reverse=True):
        if s.startswith(n):
            return NUMERALS.index(n)
    return None


def add_extensions(chords: list[str], mode: str, level: str = "7ths") -> list[str]:
    """Turn diatonic triads into 7th or 9th chords (jazz, neo-soul, lo-fi colour)."""
    if level in ("triads", None, "none"):
        return list(chords)
    out = []
    for sym in chords:
        d = degree_of_symbol(sym)
        plain = d is not None and sym == diatonic_chord(d, mode)
        if plain:
            out.append(diatonic_chord(d, mode, seventh=True, ninth=(level == "9ths" and not diatonic_chord(d, mode).endswith("°"))))
        elif sym in ("V", "bVII", "IV", "bVI") and level in ("7ths", "9ths"):
            out.append(sym + ("7" if sym == "V" else "maj7" if sym in ("bVI",) else ""))
        else:
            out.append(sym)
    return out


def add_secondary_dominants(chords: list[str], mode: str, rng: random.Random, prob: float = 0.3) -> list[str]:
    """Replace the chord before a diatonic target with V7/target (tonicisation)."""
    out = list(chords)
    for i in range(1, len(out)):
        tgt = out[i]
        d = degree_of_symbol(tgt)
        if d in (None, 0) or "/" in tgt or tgt.endswith("°") or tgt.endswith("ø7"):
            continue
        if degree_of_symbol(out[i - 1]) == 0 and i - 1 == 0:
            continue  # keep the opening tonic
        if rng.random() < prob:
            base = tgt.rstrip("0123456789").replace("maj", "").replace("ø", "")
            out[i - 1] = "V7/" + base
    return out


def borrow_chords(chords: list[str], mode: str, rng: random.Random, prob: float = 0.25) -> list[str]:
    """Modal mixture: swap some chords for their parallel-mode versions."""
    table = BORROWED_MAJOR if _family(mode) == "major" else BORROWED_MINOR
    out = list(chords)
    for i, sym in enumerate(out):
        if i == 0:
            continue
        core = sym.rstrip("0123456789").replace("maj", "")
        if core in table and rng.random() < prob:
            out[i] = table[core]
    return out


def reharmonize(chords: list[str], key: str, mode: str, rng: random.Random, technique: str) -> list[str]:
    if not all(is_roman(c) for c in chords):
        chords = to_roman(chords, key, mode)
    if technique == "secondary_dominants":
        return add_secondary_dominants(chords, mode, rng, 0.6)
    if technique == "borrowed":
        return borrow_chords(chords, mode, rng, 0.6)
    if technique in ("7ths", "9ths", "extensions"):
        return add_extensions(chords, mode, "9ths" if technique == "9ths" else "7ths")
    if technique == "triads":
        import re
        out = []
        for c in chords:
            head, _, tail = c.partition("/")
            head = re.sub(r"(maj9|maj7|add9|m7b5|sus2|sus4|13|11|9|7|6)+$", "", head).replace("ø", "°")
            out.append(head + ("/" + tail if tail else ""))
        return out
    if technique == "functional":
        return generate_progression(mode, len(chords), rng, "authentic")
    raise ValueError(f"unknown technique {technique!r}")


def to_roman(chords: list[str], key: str, mode: str) -> list[str]:
    """Best-effort conversion of absolute chord symbols to roman numerals."""
    out = []
    tonic = note_to_pc(key)
    for sym in chords:
        if is_roman(sym):
            out.append(sym)
            continue
        c = parse_chord(sym, key, mode)
        rel = (c.root - tonic) % 12
        sc = MODES[mode]
        if rel in sc:
            d, acc = sc.index(rel), ""
        elif (rel + 1) % 12 in MODES["major"]:
            d, acc = MODES["major"].index((rel + 1) % 12), "b"
        else:
            d, acc = MODES["major"].index((rel - 1) % 12), "#"
        minor3 = 3 in c.intervals and 4 not in c.intervals
        num = NUMERALS[d] if minor3 else NUMERALS[d].upper()
        ext = ""
        ints = set(c.intervals)
        if {3, 6, 10} <= ints:
            num, ext = NUMERALS[d], "ø7"
        elif {3, 6} <= ints:
            num, ext = NUMERALS[d], "°"
        elif 11 in ints:
            ext = "maj7"
        elif 10 in ints:
            ext = "9" if 14 in ints else "7"
        out.append(acc + num + ext)
    return out


# ---------------------------------------------------------------- voice leading

def _candidate_voicings(chord: Chord, lo: int, hi: int, voices: int) -> list[list[int]]:
    pcs = chord.pcs
    if len(pcs) > voices:
        # drop the fifth first (least defining), then the root (bass covers it)
        keep = [pcs[0], pcs[1]] + pcs[3:]
        if len(keep) < voices:
            keep.append(pcs[2])
        pcs = keep[:voices] if len(keep) >= voices else pcs[:voices]
    while len(pcs) < voices:
        pcs = pcs + [pcs[0]]
    out = []
    center = (lo + hi) // 2
    for base in range(lo, hi - 11):
        if base % 12 not in pcs:
            continue
        # stack the remaining pitch classes upward from base (closed / open positions)
        rest = list(pcs)
        rest.remove(base % 12)
        v = [base]
        for pc in sorted(rest, key=lambda p: (p - base) % 12):
            x = base + ((pc - base) % 12 or 12)
            while x in v:
                x += 12
            v.append(x)
        v = sorted(v)
        if v[-1] <= hi:
            out.append(v)
            if len(v) >= 4:  # drop-2 voicing (jazz)
                d2 = sorted(v[:-2] + [v[-2] - 12] + v[-1:])
                if d2[0] >= lo:
                    out.append(d2)
    return out or [sorted(nearest_pitch(pc, center, lo, hi) for pc in pcs)]


def parallel_perfects(a: list[int], b: list[int]) -> int:
    """Count voice pairs moving in parallel perfect fifths or octaves from voicing a to b."""
    if len(a) != len(b):
        return 0
    n = 0
    for i, j in combinations(range(len(a)), 2):
        ia, ib = (a[j] - a[i]) % 12, (b[j] - b[i]) % 12
        moved = a[i] != b[i] and a[j] != b[j]
        same_dir = (b[i] - a[i]) * (b[j] - a[j]) > 0
        if moved and same_dir and ia == ib and ia in (0, 7):
            n += 1
    return n


def voice_lead(chord: Chord, prev: list[int] | None, lo: int = 52, hi: int = 76, voices: int = 4) -> list[int]:
    """Choose the voicing closest to the previous one, avoiding parallel 5ths/8ves."""
    cands = _candidate_voicings(chord, lo, hi, voices)
    center = (lo + hi) / 2
    def cost(v):
        c = abs(sum(v) / len(v) - center) * 0.5
        if prev and len(prev) == len(v):
            c += sum(abs(x - y) for x, y in zip(v, prev))
            c += 6 * parallel_perfects(prev, v)
        if v[1] - v[0] <= 2 and v[0] < 55:
            c += 4  # muddy close interval low down
        c += 3 * sum(1 for x, y in zip(v, v[1:]) if y - x == 1)  # semitone clusters
        return c
    return min(cands, key=cost)
