"""Plain-language usability (FEEDBACK.md items 1-5, 7, 8): prompt interpretation, edit phrases, CLI ergonomics."""
import json
import subprocess
import sys

import pytest

import midigen as mg
from midigen import nl


@pytest.mark.parametrize("prompt,genre,mode", [
    ("sad", "pop", "minor"), ("retro 80s driving music", "synthwave", None),
    ("tense music for a game boss fight", "cinematic", "minor"), ("somthing chil for my vidoe", "lofi_hiphop", None)])
def test_interpret_moods_and_typos(prompt, genre, mode):
    spec, info = nl.interpret(prompt)
    mg.check(spec)
    assert spec["genre"] == genre
    if mode:
        assert spec["mode"] == mode
    assert nl.describe(info).startswith("Interpreted as:")


def test_typo_and_unknown_reported():
    _, info = nl.interpret("somthing chil for my vidoe")
    d = nl.describe(info)
    assert "chil → chill" in d and "didn't understand" in d
    assert nl.parse_seconds("retro 80s music") is None


def test_solo_piano_has_chords_and_melody_and_sane_length():
    spec, info = nl.interpret("a calm piano piece for a meditation app")
    assert {t["role"] for t in spec["tracks"]} == {"chords", "lead"}
    assert all(t["program"] == 0 for t in spec["tracks"])
    assert 60 <= nl.duration_seconds(spec) <= 300
    spec, _ = nl.interpret("gentle piano and soft strings")
    progs = {t["role"]: t["program"] for t in spec["tracks"]}
    assert progs["chords"] == 0 and progs.get("pad") == 48


def test_seconds_in_prompt():
    spec, info = nl.interpret("a 30 second jingle")
    assert abs(nl.duration_seconds(spec) - 30) <= 4


def test_explicit_bars_not_capped():
    spec, _ = nl.interpret("ambient 64 bars")
    assert mg.total_bars(spec) == 64


@pytest.fixture
def pop():
    return mg.build_spec("pop", seed=1)


@pytest.mark.parametrize("ins", ["make it shorter", "make it longer", "make it 30 seconds", "more energy at the end",
                                 "loop it", "fade out", "more intense", "more drums", "fewer instruments", "make it sad",
                                 "make it chill", "jazz", "add violin", "add cello", "fastr", "faster and more drums"])
def test_edit_phrases_apply_and_validate(pop, ins):
    new = mg.apply_ops(pop, nl.edit_to_ops(pop, ins))
    mg.check(new)
    assert new != pop


def test_length_edits_hit_target(pop):
    new = mg.apply_ops(pop, nl.edit_to_ops(pop, "make it 30 seconds"))
    assert abs(nl.duration_seconds(new) - 30) <= 6
    assert mg.total_bars(mg.apply_ops(pop, nl.edit_to_ops(pop, "make it shorter"))) < mg.total_bars(pop)
    assert "intro" not in mg.apply_ops(pop, nl.edit_to_ops(pop, "loop it"))["arrangement"]


def test_add_existing_role_is_helpful(pop):
    with pytest.raises(nl.NotUnderstood, match="already a drums track"):
        nl.edit_to_ops(pop, "add drums")
    ops = nl.edit_to_ops(pop, "add flute")
    assert ops[0]["name"] == "Flute"


def test_not_understood_suggests(pop):
    with pytest.raises(nl.NotUnderstood, match="Did you mean"):
        nl.edit_to_ops(pop, "blorp the zibble")


def _cli(*a, cwd):
    return subprocess.run([sys.executable, "-m", "midigen.cli", *a], capture_output=True, text=True, cwd=cwd)


def test_cli_ergonomics(tmp_path):
    r = _cli("new", "a calm piano piece for a meditation app", "--offline", "--seconds", "40", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Interpreted as" in r.stdout and (tmp_path / "a-calm-piano-piece.json").exists()
    r = _cli("new", "a calm piano piece for a meditation app", "--offline", cwd=tmp_path)
    assert (tmp_path / "a-calm-piano-piece-1.json").exists()
    assert r.stdout.count("\n  [info]") == 0
    r = _cli("edit", "a-calm-piano-piece.json", "faster", "--offline", cwd=tmp_path)
    assert r.returncode == 0 and (tmp_path / "a-calm-piano-piece.prev.json").exists() and "previous version" in r.stdout
    (tmp_path / "bad.json").write_text("{oops")
    r = _cli("edit", "bad.json", "faster", "--offline", cwd=tmp_path)
    assert r.returncode != 0 and "not valid JSON" in r.stderr and "Traceback" not in r.stderr
    r = _cli("edit", "missing.json", "faster", "--offline", cwd=tmp_path)
    assert "file not found" in r.stderr and "Traceback" not in r.stderr
    (tmp_path / "x.json").write_bytes(b"\xff\xfe\x00MThd")
    r = _cli("edit", "x.json", "faster", "--offline", cwd=tmp_path)
    assert r.returncode != 0 and "Traceback" not in r.stderr
