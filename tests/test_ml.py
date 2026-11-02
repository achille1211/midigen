"""midigen.ml: dataset builder, splits, reward model, opt-in build_spec hook (needs the [ml] extra)."""
import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from midigen.genres import build_spec
from midigen.spec import check


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    from midigen.ml.dataset import build_synthetic
    d = tmp_path_factory.mktemp("syn")
    m = build_synthetic(d, ["pop", "funk", "techno"], list(range(1, 13)), variants=2, workers=1, log=lambda *a: None)
    return d, m


def test_dataset_build_and_labels(tiny):
    d, m = tiny
    assert m["n_errors"] == 0 and m["counts"]["songs"] >= 60
    songs = [json.loads(x) for x in (d / "songs.jsonl").read_text().splitlines()]
    s = songs[0]
    assert 0 <= s["critic_score"] <= 100 and s["features"] and s["tokens"].startswith("<G:")
    check(s["spec"])
    assert any("analyze_quality" in (x.get("analyze") or {}) for x in songs) or not __import__(
        "midigen.ml.critic_bridge", fromlist=["x"])._analyzer()
    assert (d / "sections.jsonl").stat().st_size and (d / "tracks.jsonl").stat().st_size


def test_split_by_group_no_leakage(tiny):
    d, _ = tiny
    songs = [json.loads(x) for x in (d / "songs.jsonl").read_text().splitlines()]
    by_group = {}
    for s in songs:
        by_group.setdefault(s["group"], set()).add(s["split"])
    assert all(len(v) == 1 for v in by_group.values())


def test_dataset_does_not_touch_env(tiny, monkeypatch):
    import os
    from midigen.ml.dataset import _isolate_env, _restore_env
    monkeypatch.setenv("MIDIGEN_LEARN", "1")
    old = _isolate_env()
    assert os.environ["MIDIGEN_LEARN"] == "0"
    _restore_env(old)
    assert os.environ["MIDIGEN_LEARN"] == "1"


def test_reward_train_and_hook(tiny, tmp_path, monkeypatch):
    from midigen.ml import hooks, reward
    monkeypatch.setenv("MIDIGEN_FEEDBACK", str(tmp_path / "none.jsonl"))
    d, _ = tiny
    meta = reward.train(d, tmp_path, kind="ridge", min_ratings=10 ** 9, log=lambda *a: None)
    assert meta["mode"] == "critic_surrogate"
    assert not any(n.startswith("critic") for n in reward.load(tmp_path).names)
    assert meta["metrics"]["train"]["model"]["n"] > 0
    rm = reward.load(tmp_path)
    sp = build_spec("pop", seed=2, ml=False)
    assert isinstance(rm.score_spec(sp), float)
    assert isinstance(rm.score_spec(dict(sp, mix={"master": {"lufs": -14}, "tracks": [{"role": "bass", "gain_db": -3}]})), float)
    out = build_spec("pop", seed=2, ml={"n": 3, "dir": str(tmp_path), "progression": False}, learn=False)
    check(out)
    assert hooks.LAST_INFO["used"] and hooks.LAST_INFO["scorer"] == "reward_model"


def test_hook_fallback_without_artifacts(tmp_path):
    with pytest.warns(RuntimeWarning):
        out = build_spec("rock", seed=4, ml={"dir": str(tmp_path / "empty")}, learn=False)
    assert out == build_spec("rock", seed=4, ml=False, learn=False)


def test_ml_off_by_default(monkeypatch):
    monkeypatch.delenv("MIDIGEN_ML", raising=False)
    assert build_spec("house", seed=3) == build_spec("house", seed=3, ml=False)


def test_mix_features_optional():
    from midigen.ml.features import mix_features
    assert mix_features({}) == {"has_mix": 0.0}
    f = mix_features({"mix": {"master": {"lufs": -14}, "tracks": [{"role": "bass", "gain_db": -3}], "x": "s"}})
    assert f["has_mix"] == 1 and f["mix_master_lufs"] == -14 and f["mix_tracks_bass_gain_db"] == -3


def test_cli_routes_train(tmp_path):
    from midigen.ml.cli import route
    assert route(["train", "reward", "--out", "x"])[0] == "ml-train"
    assert route(["train", "--rounds", "2"]) == ["train", "--rounds", "2"]


def test_licensed_rejects_unknown(tmp_path, monkeypatch):
    from midigen.ml.licensed import import_folder, normalize_license
    assert normalize_license("CC0-1.0") and normalize_license("all rights reserved") is None
    monkeypatch.setenv("MIDIGEN_DATASET_DIR", str(tmp_path / "data"))
    src = tmp_path / "mids"
    src.mkdir()
    from midigen.midi_io import export_midi
    export_midi(build_spec("pop", seed=1, bars=4, ml=False), src / "a.mid")
    r = import_folder(src, "proprietary", "http://example.invalid", "x")
    assert not r["accepted"] and r["rejected"]
