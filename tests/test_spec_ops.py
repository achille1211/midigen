import json
import pytest
import midigen as mg


def test_spec_is_json_roundtrip(pop, tmp_path):
    p = tmp_path / "s.json"
    mg.save(pop, p)
    assert mg.load(p) == mg.check(pop)
    json.dumps(pop)


def test_validation_errors_are_specific(pop):
    bad = json.loads(json.dumps(pop))
    bad["tempo"] = 1000
    bad["sections"][0]["chords"] = ["Hmaj"]
    next(t for t in bad["tracks"] if t["role"] == "bass")["generator"]["style"] = "nonsense"
    errs = mg.validate(bad)
    text = " ".join(errs)
    assert "tempo" in text and "Hmaj" in text and "nonsense" in text


def test_drums_on_channel_10(pop):
    drums = [t for t in pop["tracks"] if t["role"] == "drums"]
    assert drums and all(t["channel"] == 9 for t in drums)
    assert all(t["channel"] != 9 for t in pop["tracks"] if t["role"] != "drums")


def test_ops_apply_and_undo_restore_exactly(pop):
    ed = mg.Editor(pop)
    orig = json.dumps(ed.spec, sort_keys=True)
    chain = [
        [{"op": "set_tempo", "bpm": 128}],
        [{"op": "transpose", "semitones": 3}],
        [{"op": "add_instrument", "role": "arp"}],
        [{"op": "reharmonize", "technique": "7ths"}],
        [{"op": "set_cadence", "section": ed.spec["sections"][-1]["name"], "cadence": "plagal"}],
        [{"op": "apply_genre", "genre": "house", "keep_harmony": True}],
        [{"op": "set_swing", "amount": 0.2, "unit": "1/16"}],
    ]
    for ops in chain:
        ed.apply(ops)
    assert ed.spec["tempo"] == 128 or ed.spec["genre"] == "house"
    while ed.can_undo:
        ed.undo()
    assert json.dumps(ed.spec, sort_keys=True) == orig
    ed.redo()
    assert ed.spec["tempo"] == 128


def test_invalid_ops_rejected_with_index(pop):
    with pytest.raises(mg.OpError) as e:
        mg.apply_ops(pop, [{"op": "set_tempo", "bpm": 120}, {"op": "set_tempo", "bpm": "fast"}])
    assert "op[1]" in str(e.value) or "1" in str(e.value)
    with pytest.raises(mg.OpError):
        mg.apply_ops(pop, [{"op": "no_such_op"}])
    with pytest.raises(mg.OpError):
        mg.apply_ops(pop, [{"op": "remove_track", "track": "Kazoo"}])


def test_apply_ops_does_not_mutate_input(pop):
    before = json.dumps(pop, sort_keys=True)
    mg.apply_ops(pop, [{"op": "transpose", "semitones": 2}])
    assert json.dumps(pop, sort_keys=True) == before


def test_op_schemas_cover_all_ops():
    assert len(mg.OP_SCHEMAS) >= 25
    for name, sch in mg.OP_SCHEMAS.items():
        assert sch["properties"]["op"]["const"] == name and sch["additionalProperties"] is False
