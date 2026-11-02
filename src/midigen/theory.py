"""Music theory helpers: notes, scales, chord symbols and roman numerals."""
from __future__ import annotations

import re
from dataclasses import dataclass

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

MODES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "ionian": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
}

QUALITIES = {
    "": [0, 4, 7],
    "maj": [0, 4, 7],
    "m": [0, 3, 7],
    "min": [0, 3, 7],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "+": [0, 4, 8],
    "5": [0, 7],
    "sus2": [0, 2, 7],
    "sus4": [0, 5, 7],
    "6": [0, 4, 7, 9],
    "m6": [0, 3, 7, 9],
    "7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11],
    "m7": [0, 3, 7, 10],
    "mmaj7": [0, 3, 7, 11],
    "m7b5": [0, 3, 6, 10],
    "dim7": [0, 3, 6, 9],
    "7sus4": [0, 5, 7, 10],
    "add9": [0, 4, 7, 14],
    "madd9": [0, 3, 7, 14],
    "9": [0, 4, 7, 10, 14],
    "maj9": [0, 4, 7, 11, 14],
    "m9": [0, 3, 7, 10, 14],
}

_CHORD_RE = re.compile(r"^([A-Ga-g])([#b]?)([A-Za-z0-9+#]*)(?:/([A-Ga-g])([#b]?))?$")
_ROMAN_RE = re.compile(
    r"^([b#]?)(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)(°|o|ø|dim|\+|aug)?(maj7|m7b5|maj9|add9|sus2|sus4|7|9|6)?$"
)
_ROMAN_DEG = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}


def note_to_pc(name: str) -> int:
    name = name.strip()
    if not name or name[0].upper() not in _PC:
        raise ValueError(f"bad note name: {name!r}")
    pc = _PC[name[0].upper()]
    for ch in name[1:]:
        if ch == "#":
            pc += 1
        elif ch == "b":
            pc -= 1
        else:
            raise ValueError(f"bad note name: {name!r}")
    return pc % 12


def pc_to_name(pc: int, flats: bool = False) -> str:
    return (FLAT_NAMES if flats else NOTE_NAMES)[pc % 12]


def scale_pcs(key: str, mode: str) -> list[int]:
    root = note_to_pc(key)
    return [(root + i) % 12 for i in MODES[mode]]


@dataclass(frozen=True)
class Chord:
    root: int            # pitch class
    intervals: tuple     # semitones above root
    bass: int            # pitch class of bass note
    symbol: str

    @property
    def pcs(self) -> list[int]:
        return [(self.root + i) % 12 for i in self.intervals]


_ROMAN_ANY = re.compile(r"^([b#]?)(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)(.*)$")
_SUFFIX_TOKENS = ["maj13", "maj11", "maj9", "maj7", "M13", "M11", "M9", "M7", "6/9", "69", "add13", "add11", "add9",
                  "add4", "add2", "sus2", "sus4", "sus", "13", "11", "9", "7", "6", "5", "b5", "#5", "b9", "#9",
                  "#11", "b13", "alt", "(", ")", ",", " "]


def suffix_intervals(suffix: str, minor: bool = False) -> list[int] | None:
    """General chord-suffix parser: quality, extensions, add/sus and alterations.

    Handles e.g. "m7b5", "7b9", "9sus4", "7sus4", "add9", "5", "69", "13", "maj7#11", "7alt", "dim7", "ø7".
    Returns semitone intervals above the root, or None if the suffix is not understood."""
    s = suffix.strip()
    third, fifth, seventh = (3 if minor else 4), 7, None
    dim = False
    adds: set[int] = set()
    if s.startswith("ø"):
        third, fifth, seventh, s = 3, 6, 10, s[1:]
        s = s[1:] if s.startswith("7") else s
    elif s.startswith(("°", "o")) and not s.startswith("om"):
        third, fifth, dim, s = 3, 6, True, s[1:]
    elif s.startswith("dim"):
        third, fifth, dim, s = 3, 6, True, s[3:]
    elif s.startswith("+") or s.startswith("aug"):
        fifth, s = 8, s[1:] if s.startswith("+") else s[3:]
    elif s.startswith(("min", "mi")) and not s.startswith("mix"):
        third, s = 3, s[3:] if s.startswith("min") else s[2:]
    elif s.startswith("m") and not s.startswith("maj"):
        third, s = 3, s[1:]
    elif s.startswith("-"):
        third, s = 3, s[1:]
    no_third = False
    while s:
        tok = next((t for t in _SUFFIX_TOKENS if s.startswith(t)), None)
        if tok is None:
            return None
        s = s[len(tok):]
        if tok in "(), ":
            continue
        if tok.startswith(("maj", "M")):
            seventh = 11
            n = tok.lstrip("majM")
            if n in ("9", "11", "13"):
                adds.add(14)
            if n == "11":
                adds.add(17)
            if n == "13":
                adds.add(21)
        elif tok in ("6/9", "69"):
            adds |= {9, 14}
        elif tok.startswith("add"):
            adds.add({"2": 2, "4": 5, "9": 14, "11": 17, "13": 21}[tok[3:]])
        elif tok == "sus2":
            third = 2
        elif tok in ("sus4", "sus"):
            third = 5
        elif tok == "7":
            seventh = 9 if dim and seventh is None else (seventh or 10)
        elif tok in ("9", "11", "13"):
            seventh = seventh or (9 if dim else 10)
            adds.add(14)
            if tok == "11":
                adds.add(17)
            if tok == "13":
                adds.add(21)
        elif tok == "6":
            adds.add(9)
        elif tok == "5" and not adds and seventh is None:
            no_third = True
        elif tok == "b5":
            fifth = 6
        elif tok == "#5":
            fifth = 8
        elif tok == "b9":
            adds.discard(14); adds.add(13)
        elif tok == "#9":
            adds.discard(14); adds.add(15)
        elif tok == "#11":
            adds.discard(17); adds.add(18)
        elif tok == "b13":
            adds.discard(21); adds.add(20)
        elif tok == "alt":
            seventh = seventh or 10
            adds.discard(14); adds |= {13, 15}
            fifth = 8
        else:
            return None
    out = [0] + ([] if no_third else [third]) + [fifth]
    if seventh is not None:
        out.append(seventh)
    out += sorted(adds)
    return sorted(set(out))


def parse_chord(symbol: str, key: str = "C", mode: str = "major") -> Chord:
    """Parse a chord symbol ("Am7", "F/C", "G7b9") or roman numeral ("vi", "bVII", "V7", "V7/vi", "I/V").

    Roman slash chords: when the left numeral is a dominant-function chord (V... or vii...) the slash means a
    secondary function ("V7/vi" = V7 of vi); otherwise the right side is the bass degree ("I/V", "I7/III",
    "IV/V"), matching the midigen-genres numeral grammar."""
    s = symbol.strip().replace("6/9", "69")
    if "/" in s:
        left, right = s.split("/", 1)
        if _ROMAN_ANY.match(right) and _ROMAN_ANY.match(left):
            lm = _ROMAN_ANY.match(left)
            if lm.group(2).lower() in ("v", "vii") and not lm.group(1):
                # secondary function, e.g. V7/vi: left is read in the major key of the target chord
                target = parse_chord(right, key, mode)
                c = parse_chord(left, pc_to_name(target.root), "major")
                return Chord(c.root, c.intervals, c.bass, s)
            c = parse_chord(left, key, mode)
            b = parse_chord(right, key, mode)
            return Chord(c.root, c.intervals, b.root, s)
        if _ROMAN_ANY.match(left) and re.match(r"^[A-Ga-g][#b]?$", right):
            c = parse_chord(left, key, mode)
            return Chord(c.root, c.intervals, note_to_pc(right[0].upper() + right[1:]), s)
    m = _ROMAN_RE.match(s)
    gm = None if m else _ROMAN_ANY.match(s)
    if m or gm:
        acc, num = (m or gm).group(1), (m or gm).group(2)
        deg = _ROMAN_DEG[num.lower()]
        # Convention: plain numerals use the degrees of the current mode ("VI" in A minor = F);
        # numerals with an accidental are relative to the parallel major ("bVI" = lowered 6th).
        if acc:
            root = (note_to_pc(key) + MODES["major"][deg] + (1 if acc == "#" else -1)) % 12
        else:
            root = (note_to_pc(key) + MODES[mode][deg]) % 12
        minor = num.islower()
        if gm:
            ints = suffix_intervals(gm.group(3), minor)
            if ints is None:
                raise ValueError(f"unrecognised chord: {symbol!r}")
            return Chord(root, tuple(ints), root, s)
        _, _, qual, ext = m.groups()
        if qual == "ø":
            base, ext = "m7b5", None
        elif qual in ("°", "o", "dim"):
            base = "dim7" if ext == "7" else "dim"
            ext = None if ext == "7" else ext
        elif qual in ("+", "aug"):
            base = "aug"
        else:
            base = "m" if minor else ""
        if ext:
            if ext == "7":
                base = {"m": "m7", "": "7", "dim": "m7b5"}.get(base, base)
            elif ext == "9":
                base = "m9" if base == "m" else "9"
            elif ext == "maj7":
                base = "mmaj7" if base == "m" else "maj7"
            elif ext == "add9":
                base = "madd9" if base == "m" else "add9"
            else:
                base = ext if base in ("", "m") and ext in QUALITIES else base + ext
        ints = QUALITIES.get(base, QUALITIES["m" if minor else ""])
        return Chord(root, tuple(ints), root, s)
    m = _CHORD_RE.match(s) or re.match(r"^([A-Ga-g])([#b]?)([^/]*)(?:/([A-Ga-g])([#b]?))?$", s)
    if not m:
        raise ValueError(f"unrecognised chord: {symbol!r}")
    letter, acc, qual, bl, ba = m.groups()
    root = note_to_pc(letter.upper() + acc)
    q = qual.lower() if qual not in ("M7", "M9") else ("maj7" if qual == "M7" else "maj9")
    q = {"min": "m", "minor": "m", "major": "", "maj": "", "-": "m", "min7": "m7", "-7": "m7",
         "ø": "m7b5", "o": "dim", "°": "dim"}.get(q, q)
    bass = note_to_pc(bl.upper() + ba) if bl else root
    if q in QUALITIES:
        return Chord(root, tuple(QUALITIES[q]), bass, s)
    ints = suffix_intervals(qual)
    if ints is None:
        raise ValueError(f"unrecognised chord quality {qual!r} in {symbol!r}")
    return Chord(root, tuple(ints), bass, s)


def is_valid_chord(symbol: str, key: str = "C", mode: str = "major") -> bool:
    try:
        parse_chord(symbol, key, mode)
        return True
    except (ValueError, KeyError):
        return False


def is_roman(symbol: str) -> bool:
    s = symbol.strip()
    return bool(_ROMAN_ANY.match(s)) or ("/" in s and bool(_ROMAN_ANY.match(s.split("/", 1)[0])))


def transpose_symbol(symbol: str, semitones: int) -> str:
    """Transpose an absolute chord symbol; roman numerals are returned unchanged."""
    if is_roman(symbol):
        return symbol
    m = _CHORD_RE.match(symbol.strip())
    if not m:
        return symbol
    letter, acc, qual, bl, ba = m.groups()
    flats = acc == "b"
    root = pc_to_name(note_to_pc(letter.upper() + acc) + semitones, flats)
    out = root + qual
    if bl:
        out += "/" + pc_to_name(note_to_pc(bl.upper() + ba) + semitones, ba == "b")
    return out


def nearest_pitch(pc: int, target: int, lo: int = 0, hi: int = 127) -> int:
    """Pitch with pitch-class pc closest to target, clamped into [lo, hi]."""
    base = target - ((target - pc) % 12)
    cands = [base + k for k in (-12, 0, 12, 24)]
    cands = [c for c in cands if lo <= c <= hi] or [max(lo, min(hi, base))]
    return min(cands, key=lambda c: abs(c - target))


def midi_note_name(p: int) -> str:
    return f"{NOTE_NAMES[p % 12]}{p // 12 - 1}"
