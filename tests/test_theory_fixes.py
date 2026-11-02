"""Music-theory fixes: harmonic colour and rhythm, bass inversions, voice-leading carry, melodic contour,
per-seed progression variety, humanize vs chord changes, pedal on 2-beat chords."""
import copy
import random

import midigen as mg
from midigen.core import Note
from midigen.genres import SEED_VARIETY, colour_harmony, get_genre
from midigen.melody import MotifNote, arc_offset, vary_rhythm
from midigen.theory import parse_chord


def _lead_notes(spec, perf):
    return [n for t, tr in zip(spec["tracks"], perf.tracks) if t["generator"]["type"] == "lead" for n in tr.notes]


def test_colour_harmony_adds_secondary_dominants_and_borrowed_chords_but_keeps_the_ending():
    g = get_genre("pop")
    base, beats = ["I", "V", "vi", "IV"], [4.0, 4.0, 4.0, 4.0]
    seen_sec = seen_bor = False
    for s in range(40):
        ch, cb = colour_harmony(g, "major", list(base), list(beats), random.Random(s))
        assert ch[-1] == base[-1] and sum(cb) == sum(beats) and len(ch) == len(cb)
        seen_sec |= any(c.startswith("V7/") for c in ch)
        seen_bor |= any(c in ("iv", "iv7", "bVI", "iiø7") for c in ch)
        assert ch == colour_harmony(g, "major", list(base), list(beats), random.Random(s))[0]   # deterministic
    assert seen_sec and seen_bor


def test_colour_harmony_is_off_for_techno_secondary_dominants():
    g = get_genre("techno")
    for s in range(30):
        ch, _ = colour_harmony(g, "minor", ["i", "bVI", "bVII", "i"], [4.0] * 4, random.Random(s))
        assert not any("/" in c for c in ch)


def test_pack_songs_get_varied_harmonic_rhythm():
    spec = mg.build_spec("pop", seed=1)
    assert any(len(set(s.get("chord_beats") or [])) > 1 for s in spec["sections"])


def test_house_and_funk_seeds_do_not_share_one_progression_set():
    assert {"house", "funk"} <= SEED_VARIETY
    for g in ("house", "funk"):
        sets = [tuple(tuple(s["chords"]) for s in mg.build_spec(g, seed=s)["sections"]) for s in (1, 2, 3)]
        assert len(set(sets)) == 3, g


def test_pop_bass_uses_first_inversions_and_club_bass_does_not():
    def inversions(genre):
        spec = mg.build_spec(genre, seed=1)
        perf = mg.render(spec)
        bi = [i for i, t in enumerate(spec["tracks"]) if t["generator"]["type"] == "bass"][0]
        cnt = 0
        for h in perf.harmony:
            ns = [n for n in perf.tracks[bi].notes if abs(n.start - h.start) < 0.1]
            third = [(h.chord.root + i) % 12 for i in h.chord.intervals if i in (3, 4)]
            cnt += bool(ns and third and ns[0].pitch % 12 == third[0] and h.chord.bass == h.chord.root)
        return cnt
    assert inversions("pop") >= 2
    assert inversions("house") == 0


def test_inversions_param_zero_turns_them_off():
    spec = mg.build_spec("pop", seed=1)
    off = copy.deepcopy(spec)
    for t in off["tracks"]:
        if t["generator"]["type"] == "bass":
            t["generator"]["inversions"] = 0
    a, b = mg.render(spec), mg.render(off)
    bi = [i for i, t in enumerate(spec["tracks"]) if t["generator"]["type"] == "bass"][0]
    assert [n.pitch for n in a.tracks[bi].notes] != [n.pitch for n in b.tracks[bi].notes]


def test_chord_voicing_carries_across_section_boundaries():
    spec = mg.build_spec("neo_soul", seed=1)
    perf = mg.render(spec)
    ci = [i for i, t in enumerate(spec["tracks"]) if t["generator"]["type"] == "chords"][0]
    notes = perf.tracks[ci].notes
    leaps = []
    for s in perf.sections[1:]:
        before = [n.pitch for n in notes if s.start - 4 <= n.start < s.start - 0.3]
        after = [n.pitch for n in notes if s.start - 0.3 <= n.start < s.start + 0.5]
        if before and after:
            leaps.append(abs(sum(after) / len(after) - sum(before) / len(before)))
    assert leaps and max(leaps) <= 7


def test_humanize_never_pulls_a_note_ahead_of_a_mid_bar_chord_change():
    spec = mg.build_spec("pop", seed=1)
    perf = mg.render(spec)
    changes = [h.start for h in perf.harmony if h.start % 4]
    assert changes
    for t, tr in zip(spec["tracks"], perf.tracks):
        if t["generator"]["type"] not in ("chords", "pad"):
            continue
        for c in changes:
            assert not any(c - 0.05 < n.start < c - 1e-6 for n in tr.notes), (t["name"], c)


def test_arc_offset_rises_to_a_peak_and_falls():
    xs = [arc_offset(p / 20, 3.0) for p in range(21)]
    k = xs.index(max(xs))
    assert 0.55 <= k / 20 <= 0.75
    assert all(a <= b for a, b in zip(xs[:k], xs[1:k + 1])) and all(a >= b for a, b in zip(xs[k:], xs[k + 1:]))


def test_vary_rhythm_changes_rhythm_but_stays_inside_the_motif():
    m = [MotifNote(0, 4, 0), MotifNote(4, 2, 1), MotifNote(6, 2, 1), MotifNote(8, 8, -2)]
    changed = 0
    for s in range(20):
        v = vary_rhythm(random.Random(s), m, 16)
        assert all(0 <= n.step and n.step + n.length <= 16 and n.length > 0 for n in v)
        assert abs(len(v) - len(m)) <= 1
        changed += [(n.step, n.length) for n in v] != [(n.step, n.length) for n in m]
    assert changed >= 15


def test_most_lead_phrases_have_a_single_climax():
    ok = tot = 0
    for g in ("pop", "rock", "neo_soul"):
        spec = mg.build_spec(g, seed=1)
        perf = mg.render(spec)
        for t, tr in zip(spec["tracks"], perf.tracks):
            if t["generator"]["type"] != "lead":
                continue
            for s in perf.sections:
                ns = [n for n in tr.notes if s.start <= n.start < s.start + 16]
                if len(ns) >= 8:
                    top = max(n.pitch for n in ns)
                    tot += 1
                    ok += sum(n.pitch == top for n in ns) == 1
    # restated / hook-quoted sections reuse earlier material, so a few phrases can repeat their peak
    assert tot and ok / tot >= 0.75, (ok, tot)


def test_render_stays_deterministic():
    for g in ("pop", "jazz", "house"):
        a = mg.render(mg.build_spec(g, seed=2))
        b = mg.render(mg.build_spec(g, seed=2))
        assert [[n.as_list() for n in t.notes] for t in a.tracks] == [[n.as_list() for n in t.notes] for t in b.tracks]


def test_lead_range_and_leaps_stay_sane():
    for g in ("pop", "trap", "jazz", "synthwave"):
        spec = mg.build_spec(g, seed=1)
        ns = sorted(_lead_notes(spec, mg.render(spec)), key=lambda n: n.start)
        assert ns
        assert max(n.pitch for n in ns) - min(n.pitch for n in ns) <= 30


def test_pedal_lifts_at_two_beat_chord_changes():
    from midigen.expression import pedal_events
    harm = [type("H", (), {"start": s, "end": s + d, "chord": parse_chord(c, "C", "major")})()
            for s, d, c in ((0, 2, "IV"), (2, 2, "iv"), (4, 4, "I"))]
    notes = [Note(0, 1, 60, 80), Note(1.5, 0.4, 60, 80), Note(2, 1, 60, 80), Note(4, 2, 60, 80)]
    ev = pedal_events(notes, harm)
    downs = [b for b, v in ev if v == 127]
    assert any(1.9 < b < 2.2 for b in downs)    # re-pedalled on the iv, not held from the "and" of 2
