"""Performance expression for MIDI export: sustain pedal (CC64), 808/bass glide and lead scoops (pitch bend).

Everything here is derived from the rendered notes plus a few generator params, so the note list itself
(and every note-level test, import round trip and analysis) is unchanged; the extra channel messages are only
added to the exported .mid.

Generator params (all optional; the defaults below apply when absent):
  sustain: bool              CC64 legato pedaling, re-pedalled just after each chord change. Default on for
                             chords/pad tracks on a piano or electric-piano program (GM 0-5) whose style is
                             not a short-note style (stabs, offbeat, pulse, power) and whose median note is
                             at least 3/4 beat (house stabs, reggae skank and funk clav stay dry).
  glide: {probability, time_ms}
                             bass portamento: a pitch-bend ramp from the held note into the next legato note
                             (|interval| <= 12, gap <= 1/8 beat), reset on the new note. Genre packs fill this
                             from bass.glide (trap 0.35 / 60 ms, neo-soul 0.25 / 90 ms, ...).
  bend: float 0-1            share of long lead notes (>= 3/4 beat) that scoop up into pitch from a semitone
                             below. Genre packs set 0.2 when melody.ornaments include slide/scoop/bend.
"""
from __future__ import annotations

import random

from .core import stable_seed

PEDAL_PROGRAMS = range(0, 6)          # acoustic and electric pianos (not harpsichord/clavinet: no damper pedal)
PEDAL_MIN_MEDIAN_DUR = 0.75           # beats: shorter comping (stabs, skanks, clav) is played dry
NO_PEDAL_STYLES = {"stabs", "offbeat", "pulse", "power"}
GLIDE_RANGE = 12                      # semitones of pitch-bend range set via RPN 0 on glide tracks
BEND_RANGE = 2


def _bend_value(semis: float, rng: int) -> int:
    return max(-8192, min(8191, int(round(semis / rng * 8192))))


def _monophonic(notes) -> bool:
    ns = sorted(notes, key=lambda n: n.start)
    return all(b.start >= a.start + a.dur - 0.05 for a, b in zip(ns, ns[1:]))


def sustain_on(tr_spec: dict, notes=()) -> bool:
    g = tr_spec.get("generator", {})
    if "sustain" in g:
        return bool(g["sustain"])
    if not (g.get("type") in ("chords", "pad") and int(tr_spec.get("program", 0)) in PEDAL_PROGRAMS
            and g.get("style", "block") not in NO_PEDAL_STYLES and tr_spec.get("channel") != 9):
        return False
    durs = sorted(n.dur for n in notes)
    return bool(durs) and durs[len(durs) // 2] >= PEDAL_MIN_MEDIAN_DUR


def pedal_events(notes, harmony) -> list[tuple[float, int]]:
    """(beat, value) CC64 events: legato pedaling. The pedal goes down just after the first attack of each
    chord span (an anticipation up to half a beat early counts), comes up just before the next chord's first
    attack, and stays up through spans where the track is silent."""
    out: list[tuple[float, int]] = []
    starts = sorted(n.start for n in notes)
    if not starts:
        return out
    down = False
    for h in harmony:
        first = next((s for s in starts if h.start - 0.5 <= s < h.end - 0.5), None)
        if first is None:
            if down:
                out.append((h.start - 0.03, 0))
                down = False
            continue
        if out and first - 0.03 <= out[-1][0]:
            continue  # same attack already pedalled (anticipation into a short span)
        if down:
            out.append((first - 0.03, 0))
        out.append((first + 0.06, 127))
        down = True
    if down:
        out.append((max(n.start + n.dur for n in notes), 0))
    return out


def glide_events(notes, params: dict, bpm: float, rng: random.Random) -> list[tuple[float, int]]:
    """(beat, bend) pitch-bend events ramping each gliding note into the next one."""
    gl = params.get("glide") or {}
    prob = float(gl.get("probability", 0) or 0)
    if prob <= 0 or not notes or not _monophonic(notes):
        return []
    glide_beats = max(0.03, float(gl.get("time_ms", 60) or 60) / 60000.0 * bpm)
    ns = sorted(notes, key=lambda n: n.start)
    out: list[tuple[float, int]] = []
    for a, b in zip(ns, ns[1:]):
        iv = b.pitch - a.pitch
        if iv == 0 or abs(iv) > GLIDE_RANGE or b.start - (a.start + a.dur) > 0.125:
            continue
        if rng.random() >= prob:
            continue
        g = min(glide_beats, a.dur * 0.5)
        t1 = min(b.start, a.start + a.dur) - 0.01   # finish the ramp while the first note still sounds
        steps = 6
        for k in range(1, steps + 1):
            out.append((t1 - g + g * k / steps, _bend_value(iv * k / steps, GLIDE_RANGE)))
        out.append((b.start, 0))
    return out


def scoop_events(notes, params: dict, rng: random.Random) -> list[tuple[float, int]]:
    prob = float(params.get("bend", 0) or 0)
    if prob <= 0 or not notes:
        return []
    ns = sorted(notes, key=lambda n: n.start)
    out: list[tuple[float, int]] = []
    for i, n in enumerate(ns):
        if n.dur < 0.75 or rng.random() >= prob:
            continue
        if any(m is not n and m.start < n.start + n.dur and m.start + m.dur > n.start - 0.02 for m in ns[max(0, i - 3):i + 4]):
            continue  # another note sounds: the bend would detune it
        g = min(0.25, n.dur * 0.3)
        out.append((n.start, _bend_value(-1, BEND_RANGE)))
        for k in range(1, 5):
            out.append((n.start + g * k / 4, _bend_value(-1 + k / 4, BEND_RANGE)))
    return out


def track_expression(perf, index: int, bpm: float) -> dict:
    """{'cc': [(beat, control, value)], 'bend': [(beat, value)], 'bend_range': int|None} for one track."""
    tr = perf.tracks[index]
    tr_spec = perf.spec["tracks"][index]
    g = tr_spec.get("generator", {})
    out = {"cc": [], "bend": [], "bend_range": None}
    if tr.is_drums or not tr.notes:
        return out
    rng = random.Random(stable_seed(perf.spec.get("seed", 1), tr.name, "expression"))
    if sustain_on(tr_spec, tr.notes):
        out["cc"] = [(b, 64, v) for b, v in pedal_events(tr.notes, perf.harmony)]
    if g.get("type") == "bass" and g.get("glide"):
        out["bend"] = glide_events(tr.notes, g, bpm, rng)
        if out["bend"]:
            out["bend_range"] = GLIDE_RANGE
    elif g.get("type") == "lead" and g.get("bend"):
        out["bend"] = scoop_events(tr.notes, g, rng)
        if out["bend"]:
            out["bend_range"] = BEND_RANGE
    return out
