"""Playback-only humanization of a MIDI file (applied to the copy that gets rendered, never to the export).

Seeded and deterministic: the same (file, seed, amounts) always gives byte-identical output. Pure Python (mido)."""
from __future__ import annotations

import random
from collections import defaultdict

import mido

# GM drum notes: kick/snare/clap/rim are the time-keepers -> half the jitter and half the velocity spread;
# hats/shakers/rides get the full spread (that is where small variation is most natural).
_ANCHOR_DRUMS = {35, 36, 37, 38, 39, 40}
_CYMBALS = {42, 44, 46, 49, 51, 52, 53, 55, 57, 59, 69, 70, 54}


def _first_tempo(mid: mido.MidiFile) -> int:
    for tr in mid.tracks:
        for m in tr:
            if m.type == "set_tempo":
                return m.tempo
    return 500000


def humanize_midi(mid: mido.MidiFile, seed: int = 1, timing_ms: float = 4.0, velocity: int = 5,
                  drums_channel: int = 9) -> mido.MidiFile:
    """Return a NEW MidiFile with small random timing (+/- timing_ms) and velocity (+/- velocity) offsets.

    - Note-offs move with their note-on, so durations are preserved (then clipped so a same-pitch note never
      overlaps the next one).
    - Drum kick/snare/clap/rim get half the timing and velocity spread; notes are never moved before tick 0.
    - Non-note events (CCs, program changes, meta) keep their positions.
    The input file is not modified."""
    tpb = mid.ticks_per_beat
    ticks_per_ms = tpb / (_first_tempo(mid) / 1000.0)
    max_dt = float(timing_ms) * ticks_per_ms
    out = mido.MidiFile(type=mid.type, ticks_per_beat=tpb)
    for ti, tr in enumerate(mid.tracks):
        rng = random.Random(f"midigen-humanize:{seed}:{ti}")
        now = 0
        others, notes = [], []
        open_notes = defaultdict(list)
        end_tick = 0
        for m in tr:
            now += m.time
            if m.type == "end_of_track":
                end_tick = now
                continue
            if m.type == "note_on" and m.velocity > 0:
                open_notes[(m.channel, m.note)].append((now, m.velocity))
            elif m.type in ("note_off", "note_on"):
                lst = open_notes.get((m.channel, m.note))
                if lst:
                    on, vel = lst.pop(0)
                    notes.append([m.channel, m.note, on, now, vel])
            else:
                others.append((now, m))
        # (unterminated notes are dropped: midigen never writes them)
        moved = []
        for ch, note, on, off, vel in notes:
            drum = ch == drums_channel
            anchor = drum and note in _ANCHOR_DRUMS
            t_amt = max_dt * (0.5 if anchor else 1.0)
            v_amt = velocity * (0.5 if anchor else 1.0)
            dt = int(round(rng.uniform(-t_amt, t_amt))) if t_amt > 0 else 0
            dv = int(round(rng.uniform(-v_amt, v_amt))) if v_amt > 0 else 0
            n_on = max(0, on + dt)
            n_off = max(n_on + 1, off + (n_on - on))
            moved.append([ch, note, n_on, n_off, max(1, min(127, vel + dv))])
        # same-pitch notes must not overlap after jitter
        by_key = defaultdict(list)
        for n in moved:
            by_key[(n[0], n[1])].append(n)
        for ns in by_key.values():
            ns.sort(key=lambda n: n[2])
            for a, b in zip(ns, ns[1:]):
                if b[2] <= a[2]:
                    b[2] = a[2] + 1
                a[3] = min(a[3], b[2])
                if a[3] <= a[2]:
                    a[3] = a[2] + 1
                    b[2] = max(b[2], a[3])
                    b[3] = max(b[3], b[2] + 1)
        events = [(t, 0, i, m) for i, (t, m) in enumerate(others)]
        for k, (ch, note, on, off, vel) in enumerate(moved):
            events.append((off, 1, (note, ch, k), mido.Message("note_off", channel=ch, note=note, velocity=0)))
            events.append((on, 2, (note, ch, k), mido.Message("note_on", channel=ch, note=note, velocity=vel)))
        events.sort(key=lambda e: (e[0], e[1], e[2] if e[1] else (e[2],)))
        nt = mido.MidiTrack()
        cur = 0
        for t, _, _, m in events:
            nt.append(m.copy(time=t - cur))
            cur = t
        nt.append(mido.MetaMessage("end_of_track", time=max(0, end_tick - cur)))
        out.tracks.append(nt)
    return out
