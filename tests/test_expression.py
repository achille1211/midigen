"""Step 2 of the roadmap: expressive MIDI export (CC64 sustain, 808/bass glide and lead scoops via pitch bend)."""
import copy

import midigen as mg
from midigen.midi_io import performance_to_midi


def _track_idx(spec, pred):
    return next(i for i, t in enumerate(spec["tracks"]) if pred(t))


def _abs(track):
    now, out = 0, []
    for m in track:
        now += m.time
        out.append((now, m))
    return out


def _notes(track):
    return [(m.type, m.note, m.velocity) for m in track if m.type in ("note_on", "note_off")]


def test_pack_glide_reaches_the_bass_generator():
    spec = mg.build_spec("trap", seed=1)
    bass = spec["tracks"][_track_idx(spec, lambda t: t["generator"]["type"] == "bass")]
    assert bass["generator"]["glide"] == {"probability": 0.35, "time_ms": 60.0}
    mg.check(spec)  # the new params validate against the schema


def test_808_glide_ramps_to_the_next_note_while_the_first_still_sounds():
    spec = mg.build_spec("trap", seed=1)
    perf = mg.render(spec)
    mid = performance_to_midi(perf)
    bi = _track_idx(spec, lambda t: t["generator"]["type"] == "bass")
    ev = _abs(mid.tracks[bi + 1])
    ccs = [(m.control, m.value) for _, m in ev if m.type == "control_change"]
    assert (6, 12) in ccs  # RPN 0 pitch-bend range = 12 semitones
    glides, held, cur, last_note = 0, set(), 0, None
    for i, (t, m) in enumerate(ev):
        if m.type == "pitchwheel":
            if m.pitch != 0:
                assert held, "bend ramp must happen while the gliding note sounds"
            cur = m.pitch
        elif m.type == "note_on" and m.velocity:
            if last_note is not None and prev_bend:
                iv = m.note - last_note
                assert abs(prev_bend - max(-8192, min(8191, round(iv / 12 * 8192)))) <= 1
                glides += 1
            assert cur == 0, "every note starts at its own pitch"
            held.add(m.note)
            last_note = m.note
        elif m.type in ("note_off", "note_on"):
            held.discard(m.note)
        if m.type == "pitchwheel" and m.pitch != 0:
            prev_bend = m.pitch
        elif m.type == "note_on" and m.velocity:
            prev_bend = 0
    assert glides >= 10


def test_expression_never_changes_the_notes():
    for g in ("trap", "pop", "neo_soul", "jazz"):
        perf = mg.render(mg.build_spec(g, seed=2))
        a, b = performance_to_midi(perf), performance_to_midi(perf, expression=False)
        assert [_notes(t) for t in a.tracks] == [_notes(t) for t in b.tracks]
        assert not any(m.type == "pitchwheel" or getattr(m, "control", None) == 64 for t in b.tracks for m in t)


def test_sustained_piano_is_pedalled_per_chord_but_stabs_and_skanks_stay_dry():
    spec = mg.build_spec("pop", seed=1)
    perf = mg.render(spec)
    mid = performance_to_midi(perf)
    pi = _track_idx(spec, lambda t: t["program"] == 0 and t["generator"]["type"] in ("chords", "pad"))
    ped = [(t, m.value) for t, m in _abs(mid.tracks[pi + 1]) if m.type == "control_change" and m.control == 64]
    vals = [v for _, v in ped]
    assert len(vals) >= 20 and vals[0] == 127 and vals[-1] == 0
    assert all(a != b for a, b in zip(vals, vals[1:]))           # strictly down/up/down/up
    assert all(t1 > t0 for (t0, _), (t1, _) in zip(ped, ped[1:]))
    # the pedal is lifted at (just before) chord changes: never held across more than one change
    changes = [round(h.start * 480) for h in perf.harmony]
    for (t0, v0), (t1, _) in zip(ped, ped[1:]):
        if v0 == 127:
            assert sum(1 for c in changes if t0 < c - 30 and c + 30 < t1) == 0
    for g in ("house", "reggae", "funk"):
        s = mg.build_spec(g, seed=1)
        m = performance_to_midi(mg.render(s))
        assert not any(getattr(x, "control", None) == 64 for t in m.tracks for x in t), g


def test_params_switch_expression_off():
    spec = mg.build_spec("trap", seed=1)
    s2 = copy.deepcopy(spec)
    for t in s2["tracks"]:
        g = t["generator"]
        if g["type"] == "bass":
            g["glide"]["probability"] = 0
        if g["type"] in ("chords", "pad"):
            g["sustain"] = False
        g.pop("bend", None)
    mid = performance_to_midi(mg.render(s2))
    assert not any(m.type == "pitchwheel" and m.pitch != 0 for t in mid.tracks for m in t)
    assert not any(getattr(m, "control", None) == 64 for t in mid.tracks for m in t)


def test_lead_scoop_from_pack_ornaments():
    spec = mg.build_spec("rock", seed=1)
    li = _track_idx(spec, lambda t: t["generator"]["type"] == "lead")
    assert spec["tracks"][li]["generator"]["bend"] == 0.2
    ev = _abs(performance_to_midi(mg.render(spec)).tracks[li + 1])
    bends = [m.pitch for _, m in ev if m.type == "pitchwheel"]
    assert -4096 in bends and max(bends) <= 0     # scoops come from a semitone below (range 2)
