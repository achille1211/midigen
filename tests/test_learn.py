"""Learning loop: experience store, feedback, bandit policy, reference MIDI, self-training, convergence."""
import json
import sys
from pathlib import Path

import pytest

import midigen as mg
from midigen import learn
from midigen.cli import main as cli

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def test_empty_store_leaves_output_unchanged_and_deterministic():
    a = mg.build_spec("pop", seed=3, bars=16)
    assert a == mg.build_spec("pop", seed=3, bars=16, learn=False)
    learn.record(a)                       # recording alone never changes weights
    assert mg.build_spec("pop", seed=3, bars=16) == a


def test_store_roundtrip_and_find():
    s = mg.build_spec("funk", seed=2, bars=16)
    sid = learn.record(s, source="test")
    st = learn.store()
    row = st.song(sid)
    assert row["genre"] == "funk" and row["spec"] == s and row["critic_score"] > 0 and "total" in row["critic_sub"]
    assert st.find("last")["id"] == sid and st.find(s)["id"] == sid
    assert st.counts()["songs"] == 1


def test_rating_changes_policy_and_learned_output_is_deterministic():
    s = mg.build_spec("pop", seed=3, bars=16)
    sid = learn.record(s)
    out = learn.rate(sid, 5, note="love it, but busier please")
    assert out["user_reward"] == 1.0 and any(sg.get("phrase") == "busier parts" for sg in out["signals"])
    a, b = mg.build_spec("pop", seed=3, bars=16), mg.build_spec("pop", seed=3, bars=16)
    assert a == b and a != s                              # learned, but still seeded
    assert mg.build_spec("pop", seed=3, bars=16, learn=False) == s
    pol = learn.load_policy("pop")
    assert learn.arm_mean(pol, "density", "busier") > learn.arm_mean(pol, "density", "sparser")
    with pytest.raises(ValueError):
        learn.rate(sid, 9)


def test_rerating_replaces_previous_rating():
    sid = learn.record(mg.build_spec("pop", seed=4, bars=16))
    learn.rate(sid, 5)
    assert learn.rate(sid, 1)["user_reward"] == 0.0


def test_targeted_feedback_only_credits_that_track():
    s = mg.build_spec("funk", seed=1, bars=16)
    sid = learn.record(s)
    bass = next(t["name"] for t in s["tracks"] if t["role"] == "bass")
    out = learn.feedback(sid, "boring", target=f"track:{bass}")
    assert out["signals"][0]["dims"] == ["bass_style"]
    pol = learn.load_policy("funk")
    assert pol["arms"]["bass_style"]["auto"]["ub"] > 0 and "drums" not in pol["arms"]


def test_keep_reject_and_few_shot():
    good = learn.record(mg.build_spec("house", seed=1, bars=16))
    learn.keep(good)
    learn.reject(learn.record(mg.build_spec("house", seed=2, bars=16)))
    fs = learn.few_shot("house")
    assert fs and len(fs["examples"]) == 1 and fs["examples"][0]["spec"]["sections"]
    assert learn.few_shot("techno") is None


def test_composer_prompt_includes_user_memory_and_records():
    sid = learn.record(mg.build_spec("pop", seed=1, bars=16))
    learn.rate(sid, 5, note="more swing")
    comp = mg.Composer(use_env=False)
    msgs = comp._messages(mg.build_spec("pop", seed=2, bars=16), "task", "pop")
    assert "user_memory" in msgs[1]["content"] and "more swing" in msgs[1]["content"]
    r = comp.generate("pop song 16 bars", seed=5)
    assert r.song_id and learn.store().song(r.song_id)["source"] == "offline"


def test_cli_new_rate_and_manual_edit_detection(tmp_path, capsys):
    out = tmp_path / "a"
    cli(["new", "--genre", "funk", "--bars", "16", "-o", str(out)])
    assert "song id" in capsys.readouterr().out
    cli(["rate", str(out) + ".json", "4", "--note", "more swing"])
    d = json.loads((tmp_path / "a.json").read_text())
    for t in d["tracks"]:
        t["generator"]["density"] = min(1.0, t["generator"].get("density", 0.5) * 1.5)
    (tmp_path / "a.json").write_text(json.dumps(d))
    cli(["rate", str(out) + ".json", "5"])
    eds = learn.store().edits()
    assert eds and eds[-1]["source"] == "manual" and "density" in eds[-1]["signals"]
    cli(["learn", "status", "--genre", "funk"])
    assert "[funk]" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli(["rate", "nosuchsong", "3"])


def test_learn_from_reference_midi(tmp_path):
    src = mg.build_spec("lofi_hiphop", seed=2, bars=16, learn=False)
    mg.export_midi(src, tmp_path / "ref.mid")
    out = learn.learn_midi(tmp_path / "ref.mid", "lofi_hiphop")
    a = out["analysis"]
    assert a["progressions"] and a["bass"]["rhythm"] and a["lead"]["cells"]
    assert all(sum(abs(x) for x in c) == 16 for c in a["lead"]["cells"])
    prof = learn.load_profile("lofi_hiphop")
    assert prof["references"] and prof["progressions"]
    s = mg.build_spec("lofi_hiphop", seed=3, bars=16)   # learned arms are now in play and the spec is valid
    mg.check(s)
    assert any(str(v).startswith("ref:") for v in learn._LAST[learn.fingerprint(s)].values()) or \
        learn.status("lofi_hiphop")["genres"]["lofi_hiphop"]["learned"]["bass"] == 1


def test_status_export_import_reset(tmp_path):
    learn.rate(learn.record(mg.build_spec("pop", seed=1, bars=16)), 4)
    p = learn.export(tmp_path / "w.json")
    assert "pop" in json.loads(Path(p).read_text())["weights"]
    assert learn.reset("pop") and not learn.has_data("pop")
    assert learn.import_weights(p) == ["pop"] and learn.has_data("pop")
    assert learn.handle({"action": "status", "genre": "pop"})["ok"]
    assert not learn.handle({"action": "rate", "song": "missing", "rating": 3})["ok"]


def test_train_small_run(tmp_path):
    res = learn.train(["pop"], rounds=2, candidates=3, bars=16, eval_seeds=(1, 2), chart=tmp_path / "c.png",
                      log=lambda *_: None)
    assert len(res["curve"]) == 3 and res["ascii_chart"].count("\n") == 2
    assert learn.load_policy("pop")["critic_events"] == 6
    assert res.get("chart") is None or Path(res["chart"]).exists()


def test_simulated_user_converges_to_hidden_preference():
    from simulate_user import HIDDEN, simulate
    res = simulate(rounds=40)
    for dim, arm in HIDDEN.items():
        assert res["top_arm"][dim] == arm, res["arm_means"]
    assert res["last_quarter"]["hit_rate"] > res["first_quarter"]["hit_rate"] + 0.4
    assert res["last_quarter"]["mean_rating"] > res["first_quarter"]["mean_rating"] + 1.0


def test_learning_opt_out(monkeypatch):
    learn.rate(learn.record(mg.build_spec("pop", seed=3, bars=16)), 5, note="busier")
    monkeypatch.setenv("MIDIGEN_LEARN", "0")
    assert mg.build_spec("pop", seed=3, bars=16) == mg.build_spec("pop", seed=3, bars=16, learn=False)
