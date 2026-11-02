import copy

import midigen as mg
from midigen.core import Note
from midigen.arranger import RenderedTrack
from midigen.playability import constrain, RANGES
from midigen.theory import parse_chord


def test_sadder_changes_explicit_major_harmony_without_mutating_source():
    spec = mg.build_spec("pop", key="C", mode="major", bars=8, seed=4)
    for section in spec["sections"]:
        section["chords"] = ["C", "F", "G", "Am"]
    original = copy.deepcopy(spec)
    sad = mg.apply_ops(spec, mg.edit_to_ops(spec, "make it sadder"))
    assert spec == original
    assert sad["mode"] == "minor"
    for section in sad["sections"]:
        assert 3 in parse_chord(section["chords"][0], "C", "minor").intervals
        assert section["chords"] != ["C", "F", "G", "Am"]


def test_sadder_minor_does_not_borrow_bright_major_chords():
    spec = mg.build_spec("pop", mode="minor", bars=8, seed=4)
    ops = mg.edit_to_ops(spec, "make it sadder")
    assert not any(o.get("technique") == "borrowed" for o in ops)
    assert mg.apply_ops(spec, ops)["tempo"] < spec["tempo"]


def test_busier_pack_bass_and_chords_increase_rendered_notes():
    spec = mg.build_spec("pop", bars=8, seed=4)
    for role in ("bass", "chords"):
        track = next(t for t in spec["tracks"] if t["role"] == role)
        track["generator"]["rhythm"] = [100, 0, 0, 0] * 4
        if role == "bass":
            track["generator"]["style"] = "template"
            track["generator"]["tokens"] = ["root", None, None, None] * 4
        edited = mg.apply_ops(spec, mg.edit_to_ops(spec, f"busier {role}"))
        before = mg.render(spec).track(track["name"])
        after = mg.render(edited).track(track["name"])
        assert len(after.notes) > len(before.notes)


def test_solo_parts_have_range_single_voice_and_breaths():
    for program, (lo, hi) in RANGES.items():
        track = RenderedTrack("Solo", "lead", 0, program, 100, 64,
                              [Note(i * .25, 3, p, 90) for i in range(160) for p in (20, 110)])
        constrain([track], 120)
        assert all(lo <= n.pitch <= hi for n in track.notes)
        assert all(a.start + a.dur <= b.start + 1e-6 for a, b in zip(track.notes, track.notes[1:]))
        assert any(b.start - (a.start + a.dur) >= .6 - 1e-6 for a, b in zip(track.notes, track.notes[1:]))


def test_busier_fixed_style_parts_and_section_overrides():
    spec = mg.build_spec("pop", bars=8, seed=4)
    for role in ("bass", "chords"):
        track = next(t for t in spec["tracks"] if t["role"] == role)
        track["generator"].pop("rhythm", None)
        track["generator"]["density"] = .5
        section = spec["sections"][0]["name"]
        track.setdefault("overrides", {})[section] = {"density": .4}
        edited = mg.apply_ops(spec, mg.edit_to_ops(spec, f"busier {role}"))
        assert edited["tracks"][spec["tracks"].index(track)]["overrides"][section]["density"] == .6
        assert len(mg.render(edited).track(track["name"]).notes) > len(mg.render(spec).track(track["name"]).notes)
