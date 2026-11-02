"""Conservative MIDI constraints for solo brass, winds and vocal lines.

These bounds provide rehearsal sketches, not a substitute for a player's review.
GM ensemble patches are deliberately excluded from solo voice constraints.
"""
from .core import Note


RANGES = {
    52: (48, 79), 53: (48, 79), 54: (48, 79),  # choir lead / voice / synth voice
    56: (54, 82), 57: (40, 70), 58: (28, 58), 59: (54, 82), 60: (41, 77),
    64: (56, 84), 65: (49, 77), 66: (44, 72), 67: (36, 65),
    68: (58, 84), 69: (52, 79), 70: (34, 72), 71: (50, 84),
    72: (74, 96), 73: (60, 91), 74: (60, 84), 75: (60, 84),
    76: (60, 84), 77: (60, 84), 78: (60, 84), 79: (60, 84),
}


def constrain(tracks, tempo):
    """Fold to register, remove solo polyphony, and leave regular breathing space."""
    beat_seconds = 60.0 / tempo
    for track in tracks:
        if track.is_drums or track.program not in RANGES:
            continue
        if track.program == 52 and track.role != "lead":
            continue  # choir chords are an ensemble, not a solo singer
        lo, hi = RANGES[track.program]
        out = []
        phrase_start = None
        last_end = -1.0
        breath = 0.3 / beat_seconds
        phrase = 8.0 / beat_seconds
        for source in sorted(track.notes, key=lambda n: (n.start, -n.vel, -n.pitch)):
            if out and source.start - out[-1].start < 0.08 / beat_seconds:
                continue
            if phrase_start is None or source.start - last_end >= breath:
                phrase_start = source.start
            deadline = phrase_start + phrase
            if source.start >= deadline:
                if source.start < deadline + breath:
                    continue
                phrase_start = source.start
                deadline = phrase_start + phrase
            pitch = source.pitch
            while pitch < lo:
                pitch += 12
            while pitch > hi:
                pitch -= 12
            if out:
                pitch = min((p for p in range(lo, hi + 1) if p % 12 == pitch % 12),
                            key=lambda p: abs(p - out[-1].pitch))
            note = Note(source.start, min(source.dur, deadline - source.start), pitch, source.vel)
            if out:
                out[-1].dur = min(out[-1].dur, max(0.001, note.start - out[-1].start))
            if note.dur > 0.001:
                out.append(note)
                last_end = note.start + note.dur
        track.notes = out
