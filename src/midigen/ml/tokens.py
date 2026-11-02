"""Tokenizers.

1. REMI-like multitrack event tokens (``encode_score`` / ``decode_tokens``)::

       BOS TS_4/4 TEMPO_120 INST_0_drums_0_1 INST_1_bass_33_0 ...
       BAR POS_0 TRK_0 D_36 DUR_1 VEL_13 TRK_1 P_45 DUR_4 VEL_11 POS_4 ... BAR ... EOS

   Positions are 16th-note steps inside the bar (4 per quarter note), durations are in steps (1..64),
   velocities in 16 bins of 8. Encoding quantises to that grid, so ``decode(encode(x))`` equals the quantised score
   and ``encode(decode(tokens)) == tokens`` exactly.

2. Compact chord/section tokens (``chord_tokens``): one sequence per section, roman numerals relative to the key.

3. Drum-grid tokens (``drum_grid_tokens``): one token per 16th step, naming the drum voices hit on it.

A *score* is the neutral structure used everywhere here::

    {"tempo": quarter_bpm, "time_signature": [n, d], "total_beats": float,
     "tracks": [{"name", "role", "program", "is_drums", "notes": [[start_beats, dur_beats, pitch, vel], ...]}]}
"""
from __future__ import annotations

import re

STEPS_PER_BEAT = 4
MAX_DUR = 64
VEL_BIN = 8

ROLES = ("drums", "bass", "chords", "pad", "arp", "lead", "other")


# ------------------------------------------------------------------ scores

def score_from_performance(perf) -> dict:
    from ..spec import quarter_bpm
    return {"tempo": round(quarter_bpm(perf.spec)), "time_signature": list(perf.spec["time_signature"]),
            "total_beats": perf.total_beats,
            "tracks": [{"name": t.name, "role": "drums" if t.is_drums else (t.role if t.role in ROLES else "other"),
                        "program": int(t.program), "is_drums": bool(t.is_drums),
                        "notes": [[n.start, n.dur, n.pitch, n.vel] for n in t.notes]} for t in perf.tracks]}


def score_from_midi(path) -> dict:
    """Score from any .mid file (via midigen's reader); roles are guessed like ``import_midi`` does."""
    from ..midi_io import _guess_role, read_midi
    raw = read_midi(path)
    tracks = []
    for t in raw["tracks"]:
        drums = t["channel"] == 9
        role = "drums" if drums else _guess_role(t)
        tracks.append({"name": t["name"], "role": role if role in ROLES else "other", "program": int(t["program"]),
                       "is_drums": drums, "notes": [list(n) for n in t["notes"]]})
    end = max((n[0] + n[1] for t in tracks for n in t["notes"]), default=0.0)
    return {"tempo": round(raw["tempo"]), "time_signature": list(raw["time_signature"]), "total_beats": end,
            "tracks": tracks}


def bar_steps(ts) -> int:
    return max(1, int(round(ts[0] * 4 / ts[1] * STEPS_PER_BEAT)))


def quantize_score(score: dict) -> dict:
    """The score as the tokenizer sees it (grid-quantised, clipped durations/velocities, sorted)."""
    out = {k: v for k, v in score.items() if k != "tracks"}
    out["tracks"] = []
    for t in score["tracks"]:
        notes = set()
        for s, d, p, v in t["notes"]:
            st = int(round(s * STEPS_PER_BEAT))
            if st < 0:
                continue
            du = max(1, min(MAX_DUR, int(round(d * STEPS_PER_BEAT))))
            vb = max(0, min(127, int(v))) // VEL_BIN
            notes.add((st, du, int(p), vb))
        out["tracks"].append({**{k: v for k, v in t.items() if k != "notes"},
                              "notes": [[s / STEPS_PER_BEAT, d / STEPS_PER_BEAT, p, vb * VEL_BIN + VEL_BIN // 2]
                                        for s, d, p, vb in sorted(notes)]})
    return out


# ------------------------------------------------------------------ REMI encode / decode

def encode_score(score: dict) -> list[str]:
    ts = score.get("time_signature") or [4, 4]
    bs = bar_steps(ts)
    toks = ["BOS", f"TS_{ts[0]}/{ts[1]}", f"TEMPO_{int(round(score.get('tempo', 120)))}"]
    events = []
    for i, t in enumerate(score["tracks"]):
        role = "drums" if t.get("is_drums") else (t.get("role") if t.get("role") in ROLES else "other")
        toks.append(f"INST_{i}_{role}_{int(t.get('program', 0))}_{1 if t.get('is_drums') else 0}")
        for s, d, p, v in t["notes"]:
            st = int(round(s * STEPS_PER_BEAT))
            if st < 0:
                continue
            du = max(1, min(MAX_DUR, int(round(d * STEPS_PER_BEAT))))
            events.append((st, i, int(p), du, max(0, min(127, int(v))) // VEL_BIN, bool(t.get("is_drums"))))
    events = sorted(set(events))
    total = score.get("total_beats") or 0
    n_bars = max(int(-(-int(round(total * STEPS_PER_BEAT)) // bs)) if total else 0,
                 (events[-1][0] // bs + 1) if events else 0)
    ei = 0
    for bar in range(n_bars):
        toks.append("BAR")
        pos, trk = None, None
        while ei < len(events) and events[ei][0] < (bar + 1) * bs:
            st, i, p, du, vb, dr = events[ei]
            k = st - bar * bs
            if k != pos:
                toks.append(f"POS_{k}")
                pos, trk = k, None
            if i != trk:
                toks.append(f"TRK_{i}")
                trk = i
            toks += [f"{'D' if dr else 'P'}_{p}", f"DUR_{du}", f"VEL_{vb}"]
            ei += 1
    toks.append("EOS")
    return toks


class TokenError(ValueError):
    pass


def decode_tokens(toks: list[str]) -> dict:
    """Tokens -> quantised score. Raises TokenError on malformed sequences (used as a validity check)."""
    ts, tempo, tracks = [4, 4], 120, []
    bar, pos, trk, pending = -1, None, None, None
    it = iter(toks)
    for tok in it:
        if tok in ("BOS", "PAD"):
            continue
        if tok == "EOS":
            break
        head, _, rest = tok.partition("_")
        if head == "TS":
            n, d = rest.split("/")
            ts = [int(n), int(d)]
        elif head == "TEMPO":
            tempo = int(rest)
        elif head == "INST":
            i, role, prog, dr = rest.split("_")
            if int(i) != len(tracks):
                raise TokenError(f"track ids must be declared in order, got {tok}")
            tracks.append({"name": f"{role} {i}", "role": role, "program": int(prog), "is_drums": dr == "1", "notes": []})
        elif head == "BAR":
            bar, pos, trk = bar + 1, None, None
        elif head == "POS":
            if bar < 0:
                raise TokenError("POS before first BAR")
            pos, trk = int(rest), None
            if not 0 <= pos < bar_steps(ts):
                raise TokenError(f"position {pos} outside the bar")
        elif head == "TRK":
            trk = int(rest)
            if not 0 <= trk < len(tracks):
                raise TokenError(f"undeclared track {trk}")
        elif head in ("P", "D"):
            if pos is None or trk is None:
                raise TokenError(f"{tok} without POS/TRK")
            if (head == "D") != tracks[trk]["is_drums"]:
                raise TokenError(f"{tok} on wrong track type")
            pitch = int(rest)
            try:
                du = next(it)
                vb = next(it)
            except StopIteration:
                raise TokenError("truncated note") from None
            if not du.startswith("DUR_") or not vb.startswith("VEL_"):
                raise TokenError(f"note {tok} must be followed by DUR_ and VEL_, got {du} {vb}")
            d, v = int(du[4:]), int(vb[4:])
            if not (0 <= pitch <= 127 and 1 <= d <= MAX_DUR and 0 <= v <= 127 // VEL_BIN):
                raise TokenError(f"value out of range in {tok} {du} {vb}")
            st = bar * bar_steps(ts) + pos
            tracks[trk]["notes"].append([st / STEPS_PER_BEAT, d / STEPS_PER_BEAT, pitch, v * VEL_BIN + VEL_BIN // 2])
        else:
            raise TokenError(f"unknown token {tok}")
    for t in tracks:
        t["notes"].sort()
    return {"tempo": tempo, "time_signature": ts, "total_beats": (bar + 1) * bar_steps(ts) / STEPS_PER_BEAT,
            "tracks": tracks}


def score_to_midi(score: dict, path=None):
    """Write a score as a type-1 MIDI file (drums on channel 10). Returns the mido.MidiFile."""
    import mido
    tpb = 480
    mid = mido.MidiFile(type=1, ticks_per_beat=tpb)
    cond = mido.MidiTrack()
    cond.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(score.get("tempo", 120)), time=0))
    n, d = score.get("time_signature", [4, 4])
    cond.append(mido.MetaMessage("time_signature", numerator=n, denominator=d, time=0))
    mid.tracks.append(cond)
    ch_iter = iter([c for c in range(16) if c != 9])
    for t in score["tracks"]:
        ch = 9 if t.get("is_drums") else next(ch_iter, 15)
        mt = mido.MidiTrack()
        mt.append(mido.MetaMessage("track_name", name=t.get("name", "track"), time=0))
        if not t.get("is_drums"):
            mt.append(mido.Message("program_change", channel=ch, program=int(t.get("program", 0)) % 128, time=0))
        ev = []
        last_on = {}
        for s, du, p, v in sorted(t["notes"]):
            on, off = int(round(s * tpb)), int(round((s + du) * tpb))
            ev.append((on, 1, p, max(1, min(127, int(v)))))
            ev.append((max(off, on + 1), 0, p, 0))
            last_on[p] = on
        ev.sort(key=lambda e: (e[0], e[1], e[2]))
        now = 0
        for tt, kind, p, v in ev:
            mt.append(mido.Message("note_on" if kind else "note_off", channel=ch, note=p, velocity=v, time=tt - now))
            now = tt
        mid.tracks.append(mt)
    if path is not None:
        mid.save(str(path))
    return mid


# ------------------------------------------------------------------ chord / section tokens

def roman_chords(chords: list[str], key: str, mode: str) -> list[str]:
    from ..harmony import to_roman
    try:
        return to_roman(chords, key, mode)
    except Exception:
        return list(chords)


def section_base(name: str) -> str:
    """'chorus2' -> 'chorus', 'verse_b' -> 'verse'."""
    b = re.sub(r"[\d_\- ]+[a-z]?$", "", name.lower()) or name.lower()
    return re.sub(r"[^a-z]", "", b) or "section"


def chord_tokens(spec: dict) -> list[dict]:
    """One record per section: roman-numeral chord sequence plus its conditioning context."""
    out = []
    for s in spec["sections"]:
        out.append({"genre": spec.get("genre") or "unknown", "mode": spec["mode"], "section": section_base(s["name"]),
                    "energy": round(float(s.get("energy", 0.5)), 2), "cadence": s.get("cadence", "none"),
                    "chords": roman_chords(s["chords"], spec["key"], spec["mode"])})
    return out


def compact_song_tokens(spec: dict) -> str:
    """Single-line compact song token string: header + per-section chord tokens."""
    parts = [f"<G:{spec.get('genre')}>", f"<M:{spec['mode']}>", f"<T:{int(round(spec['tempo']))}>",
             f"<TS:{spec['time_signature'][0]}/{spec['time_signature'][1]}>"]
    for t in spec.get("tracks", []):
        parts.append(f"<TRK:{t['role']}:{t['program']}:{t['generator'].get('style') or t['generator'].get('pattern') or t['generator']['type']}>")
    secs = {s["name"]: s for s in spec["sections"]}
    for name in spec.get("arrangement") or list(secs):
        s = secs.get(name)
        if not s:
            continue
        parts.append(f"<S:{section_base(name)}:{s['bars']}:{round(float(s.get('energy', .5)), 1)}>")
        parts += roman_chords(s["chords"], spec["key"], spec["mode"])
    return " ".join(parts)


# ------------------------------------------------------------------ drum grids

DRUM_ABBR = {35: "K", 36: "K", 37: "R", 38: "S", 40: "S", 39: "C", 42: "H", 44: "H", 46: "O", 51: "Y", 53: "Y", 59: "Y",
             49: "X", 57: "X", 41: "T", 43: "T", 45: "T", 47: "T", 48: "T", 50: "T", 54: "M", 69: "M", 70: "M", 82: "M",
             56: "B"}
ABBR_PITCH = {"K": 36, "R": 37, "S": 38, "C": 39, "H": 42, "O": 46, "Y": 51, "X": 49, "T": 47, "M": 70, "B": 56, "Q": 75}


def drum_grid_tokens(score: dict) -> list[list[str]]:
    """Per bar, one token per 16th step naming the voices hit ('.' = rest, e.g. 'HK', 'S')."""
    drums = [t for t in score["tracks"] if t.get("is_drums")]
    if not drums:
        return []
    bs = bar_steps(score.get("time_signature") or [4, 4])
    hits: dict[int, set] = {}
    for t in drums:
        for s, _d, p, _v in t["notes"]:
            hits.setdefault(int(round(s * STEPS_PER_BEAT)), set()).add(DRUM_ABBR.get(int(p), "Q"))
    n_bars = int(-(-int(round((score.get("total_beats") or 0) * STEPS_PER_BEAT)) // bs)) or (max(hits) // bs + 1 if hits else 0)
    return [["".join(sorted(hits.get(b * bs + k, ()))) or "." for k in range(bs)] for b in range(n_bars)]


def grid_to_notes(bars: list[list[str]], vel: int = 96) -> list[list]:
    """Drum-grid tokens back to drum notes ([start_beats, dur, pitch, vel])."""
    out = []
    for b, steps in enumerate(bars):
        for k, tok in enumerate(steps):
            if tok == ".":
                continue
            for ch in tok:
                out.append([(b * len(steps) + k) / STEPS_PER_BEAT, 0.25, ABBR_PITCH.get(ch, 75), vel])
    return out
