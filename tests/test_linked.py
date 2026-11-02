"""Linked tracks: role-dependency follow-ups."""
import subprocess
import sys

import midigen as mg
from midigen import linked


def _spec():
    return mg.build_spec("pop", seed=1)


def test_suggestions_depend_on_the_edit():
    s = _spec()
    drums = linked.suggest_followups(s, "busier drums")
    tempo = linked.suggest_followups(s, "faster by 20")
    assert drums and tempo
    assert {x["follower"] for x in drums} >= {"bass"}
    assert all(x["leader"] == "tempo" for x in tempo)
    assert [x["ops"] for x in drums] != [x["ops"] for x in tempo]
    bass = next(x for x in drums if x["follower"] == "bass")
    assert bass["ops"][0]["delta"] > 0 and "kick" in bass["reason"]
    lead = [x for x in drums if x["follower"] == "lead"]
    assert all(x["ops"][0]["delta"] < 0 for x in lead)
    sparser = linked.suggest_followups(s, "sparser drums")
    assert next(x for x in sparser if x["follower"] == "bass")["ops"][0]["delta"] < 0


def test_accepting_keeps_or_improves_critic():
    s = _spec()
    for edit in ("busier drums", "faster by 20", "remove the drums", "jazzier chords", "add violin"):
        after = mg.apply_ops(s, mg.edit_to_ops(s, edit))
        base = mg.critique(after)["score"]
        for sug in linked.suggest_followups(s, edit):
            mg.check(mg.apply_ops(after, sug["ops"]))
            assert sug["score_after"] >= base == sug["score_before"]
            assert sug["reason"] and sug["rank"] >= 1
        new, ops, acc = linked.follow(s, edit)
        assert mg.critique(new)["score"] >= base


def test_ranked_and_explained():
    sugg = linked.suggest_followups(_spec(), [{"op": "set_density", "track": "Pop kit / electronic", "delta": 0.2}])
    assert [x["rank"] for x in sugg] == list(range(1, len(sugg) + 1))
    assert "no follow-ups" in linked.format_suggestions([])


def test_composer_auto_follow():
    c = mg.Composer(use_env=False, record=False)
    plain = c.edit(_spec(), "busier drums")
    auto = c.edit(_spec(), "busier drums", auto_follow=True)
    assert not plain.followups and auto.followups
    assert len(auto.ops) > len(plain.ops) and "followed up" in auto.explanation
    assert auto.report["score"] >= plain.report["score"]


def test_mcp_and_cli(tmp_path, monkeypatch):
    from midigen import mcp_server as m
    monkeypatch.setattr(m, "WORKDIR", tmp_path)
    sid = m.generate_song(genre="pop", bars=16)["song_id"]
    out = m.suggest_followups_tool(sid, instruction="busier drums")
    assert out["suggestions"] and out["suggestions"][0]["ops"]
    mg.save(_spec(), tmp_path / "s.json")
    r = subprocess.run([sys.executable, "-m", "midigen.cli", "edit", "s.json", "busier drums", "--offline", "--suggest"],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "suggested follow-ups" in r.stdout and "kick" in r.stdout
    r = subprocess.run([sys.executable, "-m", "midigen.cli", "edit", "s.json", "busier drums", "--offline", "--auto-follow"],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0 and "followed up" in r.stdout


def test_chat_suggest(tmp_path):
    mg.save(_spec(), tmp_path / "c.json")
    r = subprocess.run([sys.executable, "-m", "midigen.cli", "chat", "--spec", "c", "--offline"],
                       input="busier drums\n/suggest\n/follow 1\n/quit\n", capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "apply with /follow" in r.stdout and "applied:" in r.stdout
