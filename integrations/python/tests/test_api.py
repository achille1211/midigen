import io
import json

import mido
import pytest

from midigen_integrations import api


@pytest.fixture(scope="module")
def song():
    return api.generate(genre="house", key="A", mode="minor", bars=8, seed=7)


def test_generate_returns_spec_summary_critic_and_midi(song):
    assert song.summary["key"] == "A" and song.summary["mode"] == "minor" and song.summary["bars"] == 8
    assert song.summary["genre"] == "house" and song.source == "offline"
    assert 0 <= song.critic["score"] <= 100
    mid = mido.MidiFile(file=io.BytesIO(song.midi))
    assert mid.type == 1 and len(mid.tracks) == 1 + len(song.summary["tracks"])
    json.dumps(song.to_dict())  # everything is JSON-serialisable


def test_generate_is_deterministic(song):
    again = api.generate(api.GenerateParams(genre="house", key="A", mode="minor", bars=8, seed=7))
    assert again.spec == song.spec and again.midi == song.midi
    other = api.generate(genre="house", key="A", mode="minor", bars=8, seed=8)
    assert other.midi != song.midi


def test_prompt_with_explicit_overrides():
    s = api.generate(prompt="a sad lofi hip hop beat in D minor at 82 bpm", key="E", bars=4)
    assert s.summary["genre"] == "lofi_hiphop" and s.summary["key"] == "E" and s.summary["bars"] == 4


@pytest.mark.parametrize("kw,needle", [
    (dict(genre="house", tempo=0), "tempo"), (dict(genre="house", bars=0), "bars"),
    (dict(genre="house", key="H"), "key"), (dict(genre="house", mode="spooky"), "mode"), (dict(), "prompt or a genre"),
])
def test_invalid_params(kw, needle):
    with pytest.raises(api.MidigenError) as e:
        api.generate(**kw)
    assert e.value.kind == "invalid_params" and needle in str(e.value)


def test_unknown_genre():
    with pytest.raises(api.MidigenError) as e:
        api.generate(genre="polka-from-mars")
    assert e.value.kind == "unknown_genre"


def test_edit_with_ops_is_pure(song):
    before = json.dumps(song.spec, sort_keys=True)
    e = api.edit(song.spec, ops=[{"op": "set_tempo", "bpm": 100}, {"op": "transpose", "semitones": 2}])
    assert e.summary["tempo"] == 100 and e.summary["key"] == "B" and e.ops[0]["op"] == "set_tempo"
    assert json.dumps(song.spec, sort_keys=True) == before


def test_edit_with_instruction(song):
    e = api.edit(song.spec, instruction="make it faster")
    assert e.summary["tempo"] > song.summary["tempo"] and e.ops


def test_edit_errors(song):
    with pytest.raises(api.MidigenError) as e:
        api.edit(song.spec, ops=[{"op": "no_such_op"}])
    assert e.value.kind == "invalid_ops"
    with pytest.raises(api.MidigenError) as e:
        api.edit(song.spec, instruction="qwertyuiop zxcv")
    assert e.value.kind == "not_understood"
    with pytest.raises(api.MidigenError) as e:
        api.edit(song.spec)
    assert e.value.kind == "invalid_params"
    with pytest.raises(api.MidigenError) as e:
        api.edit({"tempo": "fast"}, ops=[])
    assert e.value.kind == "invalid_spec"


def test_render_notes_matches_midi(song):
    notes = api.render_notes(song.spec)
    assert notes["tempo_qpm"] == song.summary["tempo"] and notes["total_beats"] == 32
    counts = [len(t["notes"]) for t in notes["tracks"]]
    assert counts == [t["notes"] for t in song.summary["tracks"]]
    mid = mido.MidiFile(file=io.BytesIO(song.midi))
    note_ons = sum(1 for tr in mid.tracks for m in tr if m.type == "note_on" and m.velocity > 0)
    assert note_ons <= sum(counts) and note_ons > 0.95 * sum(counts)  # same-pitch overlaps may be dropped
    drums = [t for t in notes["tracks"] if t["is_drums"]]
    assert drums and drums[0]["channel"] == 9


def test_options_and_genres():
    o = api.list_options()
    assert o["api_version"] == api.API_VERSION and "house" in o["genres"] and "minor" in o["modes"]
    assert "transpose" in o["ops"] and o["limits"]["bars"] == [1, 256]
    json.dumps(o)
    g = {x["id"]: x for x in api.list_genres()}
    assert set(g) == set(o["genres"]) and g["house"]["tempo"]["default"] > 0
    assert api.genre_profile("Drum and Bass")["name"] == "drum_and_bass"
    with pytest.raises(api.MidigenError):
        api.genre_profile("nope-nope")


def test_critique(song):
    rep = api.critique(song.spec)
    assert rep["score"] == song.critic["score"] and "findings" in rep
