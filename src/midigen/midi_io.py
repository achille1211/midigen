"""MIDI export (type-1, one track per instrument, drums on channel 10) and import/inspect."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import mido

from . import spec as specmod
from .arranger import Performance, render
from .expression import track_expression
from .spec import INSTRUMENTS, check, make_track
from .theory import MODES, NOTE_NAMES, midi_note_name, note_to_pc

TPB = 480
_MAJOR_FOR_MODE = {"major": 0, "ionian": 0, "lydian": 7, "mixolydian": 5, "dorian": 10, "phrygian": 8,
                   "locrian": 1, "minor": 3, "aeolian": 3, "harmonic_minor": 3}
_SHARP_KEYS = {"C": "C", "C#": "Db", "D": "D", "D#": "Eb", "E": "E", "F": "F", "F#": "F#", "G": "G", "G#": "Ab",
               "A": "A", "A#": "Bb", "B": "B"}


def _key_sig(key: str, mode: str) -> str | None:
    pc = note_to_pc(key)
    if mode in ("minor", "aeolian", "harmonic_minor"):
        name = {"C#": "C#", "D#": "Eb", "G#": "G#", "A#": "Bb"}.get(NOTE_NAMES[pc], NOTE_NAMES[pc])
        return name + "m"
    rel = (pc + _MAJOR_FOR_MODE.get(mode, 0)) % 12
    return _SHARP_KEYS[NOTE_NAMES[rel]]


def performance_to_midi(perf: Performance, expression: bool = True, mix: bool = True) -> mido.MidiFile:
    """Type-1 file. With ``expression`` (default) keyboard parts get CC64 sustain pedaling and bass/lead parts
    pitch-bend glides/scoops from their generator params (see expression.py); the notes are identical either way.
    With ``mix`` (default) each track gets the per-genre mixing defaults from ``midigen/data/mix_defaults.json``
    (CC7 volume, CC10 pan, CC91 reverb, CC93 chorus, all clamped to 0-127); ``mix=False`` writes only the spec's
    raw volume/pan as CC7/CC10 (the pre-mix behaviour)."""
    spec = perf.spec
    bpm = specmod.quarter_bpm(spec)
    mixes = None
    if mix:
        from .audio.mix import resolve_track_mix
        mixes = resolve_track_mix(spec.get("genre"), [{"role": t.role, "volume": t.volume, "pan": t.pan}
                                                      for t in perf.tracks])
    mid = mido.MidiFile(type=1, ticks_per_beat=TPB)
    beat_ticks = TPB  # spec/performance beats are quarter notes in every meter (bar = num*4/den quarters)
    to_t = lambda b: int(round(b * beat_ticks))
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name=spec.get("title", "midigen"), time=0))
    conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(specmod.quarter_bpm(spec)), time=0))  # quarter-note BPM
    num, den = spec["time_signature"]
    conductor.append(mido.MetaMessage("time_signature", numerator=num, denominator=den, time=0))
    try:
        ks = _key_sig(spec["key"], spec["mode"])
        if ks:
            conductor.append(mido.MetaMessage("key_signature", key=ks, time=0))
    except Exception:
        pass
    last = 0
    for s in perf.sections:
        t = to_t(s.start)
        conductor.append(mido.MetaMessage("marker", text=s.name, time=t - last))
        last = t
    conductor.append(mido.MetaMessage("end_of_track", time=max(0, to_t(perf.total_beats) - last)))
    mid.tracks.append(conductor)
    for ti, tr in enumerate(perf.tracks):
        mt = mido.MidiTrack()
        ch = tr.channel
        mt.append(mido.MetaMessage("track_name", name=tr.name, time=0))
        if not tr.is_drums:
            mt.append(mido.Message("program_change", channel=ch, program=tr.program, time=0))
        if mixes:
            m = mixes[ti]
            for cc, key in ((7, "volume"), (10, "pan"), (91, "reverb"), (93, "chorus")):
                mt.append(mido.Message("control_change", channel=ch, control=cc, value=m[key], time=0))
        else:
            mt.append(mido.Message("control_change", channel=ch, control=7, value=max(0, min(127, int(tr.volume))), time=0))
            mt.append(mido.Message("control_change", channel=ch, control=10, value=max(0, min(127, int(tr.pan))), time=0))
        events = []
        # truncate same-pitch overlaps so note-offs pair correctly
        by_pitch = defaultdict(list)
        for n in tr.notes:
            by_pitch[n.pitch].append(n)
        for p, ns in by_pitch.items():
            ns.sort(key=lambda n: n.start)
            for i, n in enumerate(ns):
                on, off = to_t(n.start), to_t(n.start + n.dur)
                if i + 1 < len(ns):
                    off = min(off, to_t(ns[i + 1].start))
                if off <= on:
                    off = on + 1
                    if i + 1 < len(ns) and off > to_t(ns[i + 1].start):
                        continue
                events.append((on, 2, p, ("note_on", {"note": p, "velocity": n.vel})))
                events.append((off, 0, p, ("note_off", {"note": p, "velocity": 0})))
        if expression:
            ex = track_expression(perf, ti, bpm)
            if ex["bend_range"]:
                # RPN 0 (pitch-bend sensitivity) so the bend values mean the same on every GM synth
                for c, v in ((101, 0), (100, 0), (6, ex["bend_range"]), (38, 0), (101, 127), (100, 127)):
                    mt.append(mido.Message("control_change", channel=ch, control=c, value=v, time=0))
                mt.append(mido.Message("pitchwheel", channel=ch, pitch=0, time=0))
            for i, (b, c, v) in enumerate(ex["cc"]):
                events.append((max(0, to_t(b)), 1, i, ("control_change", {"control": c, "value": v})))
            for i, (b, v) in enumerate(ex["bend"]):
                events.append((max(0, to_t(b)), 1, 1000 + i, ("pitchwheel", {"pitch": v})))
        events.sort(key=lambda e: (e[0], e[1], e[2]))
        now = 0
        for t, _prio, _k, (msg, kw) in events:
            mt.append(mido.Message(msg, channel=ch, time=t - now, **kw))
            now = t
        mt.append(mido.MetaMessage("end_of_track", time=max(0, to_t(perf.total_beats) - now)))
        mid.tracks.append(mt)
    return mid


def export_midi(spec_or_perf, path: str | Path, expression: bool = True, mix: bool = True) -> Performance:
    perf = spec_or_perf if isinstance(spec_or_perf, Performance) else render(spec_or_perf)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    performance_to_midi(perf, expression=expression, mix=mix).save(str(path))
    return perf


# ------------------------------------------------------------------ import

def read_midi(path: str | Path) -> dict:
    """Parse a MIDI file into a neutral structure: tempo, time signature, tracks with notes in beats."""
    mid = mido.MidiFile(str(path))
    tpb = mid.ticks_per_beat
    tempo, ts, key_sig, markers = 500000, (4, 4), None, []
    tracks = []
    for i, tr in enumerate(mid.tracks):
        now = 0
        name, program, channels = None, None, Counter()
        open_notes, notes = {}, []
        for msg in tr:
            now += msg.time
            if msg.is_meta:
                if msg.type == "set_tempo" and now == 0 or (msg.type == "set_tempo" and tempo == 500000):
                    tempo = msg.tempo
                elif msg.type == "time_signature":
                    ts = (msg.numerator, msg.denominator)
                elif msg.type == "key_signature":
                    key_sig = msg.key
                elif msg.type == "track_name":
                    name = msg.name
                elif msg.type == "marker":
                    markers.append((now / tpb, msg.text))
                continue
            if msg.type == "program_change" and program is None:
                program = msg.program
            if msg.type == "note_on" and msg.velocity > 0:
                channels[msg.channel] += 1
                open_notes.setdefault((msg.channel, msg.note), []).append((now, msg.velocity))
            elif msg.type in ("note_off", "note_on"):
                lst = open_notes.get((msg.channel, msg.note))
                if lst:
                    st, v = lst.pop(0)
                    notes.append([st / tpb, max(1, now - st) / tpb, msg.note, v])
        if notes:
            ch = channels.most_common(1)[0][0] if channels else 0
            notes.sort()
            tracks.append({"name": name or f"Track {i}", "channel": ch, "program": program or 0, "notes": notes})
    bpm = round(mido.tempo2bpm(tempo), 2)
    # positions stay in quarter-note beats; "tempo" here is the raw quarter-note BPM of the file
    for t in tracks:
        for n in t["notes"]:
            n[0], n[1] = round(n[0], 4), round(n[1], 4)
    return {"tempo": bpm, "time_signature": list(ts), "key_signature": key_sig,
            "markers": [(round(b, 4), m) for b, m in markers], "tracks": tracks}


def detect_key(tracks: list[dict]) -> tuple[str, str]:
    """Krumhansl-Schmuckler key finding over pitched tracks (duration-weighted)."""
    major = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
    minor = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
    hist = [0.0] * 12
    for t in tracks:
        if t["channel"] == 9:
            continue
        for st, d, p, v in t["notes"]:
            hist[p % 12] += d
    if not any(hist):
        return "C", "major"

    def corr(a, b):
        ma, mb = sum(a) / 12, sum(b) / 12
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
        return num / den if den else 0
    best = max(((corr([hist[(i + k) % 12] for i in range(12)], prof), k, m)
                for k in range(12) for prof, m in ((major, "major"), (minor, "minor"))))
    return NOTE_NAMES[best[1]], best[2]


def detect_chords(tracks: list[dict], total_beats: float, bar_beats: float, key: str, mode: str) -> list[str]:
    """One chord symbol per bar from the pitched notes sounding in it (template matching)."""
    from .theory import QUALITIES
    templates = {"": [0, 4, 7], "m": [0, 3, 7], "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10],
                 "dim": [0, 3, 6], "sus4": [0, 5, 7]}
    scale = {(note_to_pc(key) + i) % 12 for i in MODES[mode]}
    out = []
    nbars = max(1, int(round(total_beats / bar_beats)))
    for b in range(nbars):
        lo, hi = b * bar_beats, (b + 1) * bar_beats
        w = [0.0] * 12
        bass_w = [0.0] * 12
        for t in tracks:
            if t["channel"] == 9:
                continue
            for st, d, p, v in t["notes"]:
                ov = min(hi, st + d) - max(lo, st)
                if ov > 0:
                    w[p % 12] += ov
                    if p < 55:
                        bass_w[p % 12] += ov * 1.5
        if not any(w):
            out.append(out[-1] if out else key + ("m" if mode in ("minor", "aeolian") else ""))
            continue
        tot = [a + b2 for a, b2 in zip(w, bass_w)]
        best, best_s = None, -1e9
        for r in range(12):
            for q, iv in templates.items():
                pcs = [(r + i) % 12 for i in iv]
                s = sum(tot[p] for p in pcs) - 0.6 * sum(tot[p] for p in range(12) if p not in pcs) \
                    - 0.15 * (len(iv) - 3) * max(tot) + (0.1 * max(tot) if r in scale else 0) + bass_w[r] * 0.5
                if s > best_s:
                    best, best_s = NOTE_NAMES[r] + q, s
        out.append(best)
    return out


def import_midi(path: str | Path, title: str | None = None) -> dict:
    """Import any .mid file as an editable spec. Notes are kept verbatim as `clip` generators, split by section."""
    data = read_midi(path)
    num, den = data["time_signature"]
    bb = num * 4 / den
    total = max((n[0] + n[1] for t in data["tracks"] for n in t["notes"]), default=bb)
    nbars = max(1, int(-(-total // bb)))
    key, mode = detect_key(data["tracks"])
    if data["key_signature"]:
        ks = data["key_signature"]
        key, mode = (ks[:-1], "minor") if ks.endswith("m") else (ks, "major")
        key = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B"}.get(key, key)
    # sections from markers (if present), else 8-bar blocks
    bounds = [m for m in data["markers"] if m[0] < total]
    if not bounds or bounds[0][0] > 0:
        bounds = [(0.0, "a")] + bounds if bounds else [(float(b * bb), f"part{b // 8 + 1}") for b in range(0, nbars, 8)]
    sections, arrangement, used = [], [], Counter()
    for i, (st, name) in enumerate(bounds):
        en = bounds[i + 1][0] if i + 1 < len(bounds) else nbars * bb
        bars = max(1, int(round((en - st) / bb)))
        used[name] += 1
        uname = name if used[name] == 1 else f"{name}{used[name]}"
        sections.append({"name": uname, "bars": min(bars, 64), "start": st})
        arrangement.append(uname)
    for s in sections:
        seg = {"tracks": [{**t, "notes": [n for n in t["notes"] if s["start"] <= n[0] < s["start"] + s["bars"] * bb]}
                          for t in data["tracks"]]}
        chords = detect_chords([{**t, "notes": [[n[0] - s["start"]] + n[1:] for n in t["notes"]]} for t in seg["tracks"]],
                               s["bars"] * bb, bb, key, mode)
        s["chords"] = chords
    tracks = []
    names = Counter()
    for t in data["tracks"]:
        drums = t["channel"] == 9
        role = "drums" if drums else _guess_role(t)
        nm = t["name"]
        names[nm] += 1
        if names[nm] > 1:
            nm = f"{nm} {names[nm]}"
        tr = make_track(role, name=nm)
        tr["program"] = t["program"]
        clips = {}
        for s in sections:
            clip = [[round(n[0] - s["start"], 4), n[1], n[2], n[3]] for n in t["notes"]
                    if s["start"] <= n[0] < s["start"] + s["bars"] * bb]
            if clip:
                clips[s["name"]] = clip
        tr["generator"] = {"type": "clip", "clips": clips}
        tr["channel"] = 9 if drums else t["channel"]
        tracks.append(tr)
    for s in sections:
        s.pop("start")
    spec_tempo = max(20.0, min(300.0, round(data["tempo"] * den / 4, 2)))  # MIDI quarter BPM -> denominator-note BPM
    spec = {"version": 2, "title": title or Path(path).stem, "tempo": spec_tempo, "time_signature": [num, den],
            "key": key, "mode": mode, "seed": 1, "sections": sections, "arrangement": arrangement, "tracks": tracks[:16]}
    return check(spec)


def _guess_role(t: dict) -> str:
    ps = [n[2] for n in t["notes"]]
    med = sorted(ps)[len(ps) // 2]
    starts = Counter(round(n[0], 3) for n in t["notes"])
    poly = sum(1 for c in starts.values() if c > 1) / max(1, len(starts))
    if med < 50:
        return "bass"
    if poly > 0.4:
        return "pad" if sum(n[1] for n in t["notes"]) / len(ps) > 2 else "chords"
    return "lead"


def inspect_midi(path: str | Path) -> dict:
    data = read_midi(path)
    out = {"tempo": data["tempo"], "time_signature": data["time_signature"], "key_signature": data["key_signature"],
           "markers": [m for _, m in data["markers"]], "tracks": []}
    key, mode = detect_key(data["tracks"])
    out["detected_key"] = f"{key} {mode}"
    total = 0
    for t in data["tracks"]:
        ps = [n[2] for n in t["notes"]]
        end = max(n[0] + n[1] for n in t["notes"])
        total = max(total, end)
        out["tracks"].append({"name": t["name"], "channel": t["channel"] + 1, "program": t["program"],
                              "notes": len(ps), "range": f"{midi_note_name(min(ps))}-{midi_note_name(max(ps))}"})
    mf = mido.MidiFile(str(path))
    total = max(total, max(sum(m.time for m in tr) for tr in mf.tracks) / mf.ticks_per_beat)
    num, den = data["time_signature"]
    out["bars"] = round(total / (num * 4 / den), 2)
    out["note_count"] = sum(t["notes"] for t in out["tracks"])
    return out
