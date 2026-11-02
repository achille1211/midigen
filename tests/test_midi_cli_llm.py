import json
import subprocess
import sys

import pytest
import midigen as mg
from midigen.llm import Composer


@pytest.mark.parametrize("genre", ["lofi_hiphop", "techno", "jazz", "rock"])
def test_midi_roundtrip_counts(genre, tmp_path):
    spec = mg.build_spec(genre, bars=16, seed=4)
    p = tmp_path / "x.mid"
    perf = mg.export_midi(spec, p)
    info = mg.inspect_midi(p)
    assert len(info["tracks"]) == len(perf.tracks)
    assert {t["name"]: t["notes"] for t in info["tracks"]} == {t.name: len(t.notes) for t in perf.tracks}
    assert any(t["channel"] == 10 for t in info["tracks"])
    imported = mg.import_midi(p)
    perf2 = mg.render(imported)
    assert sorted(len(t.notes) for t in perf2.tracks) == sorted(len(t.notes) for t in perf.tracks)


def test_offline_nl_prompt_and_edits():
    spec = mg.prompt_to_spec("a sad lofi beat in D minor at 80 bpm, 16 bars")
    assert spec["genre"] == "lofi_hiphop" and spec["key"] == "D" and spec["mode"] == "minor" and spec["tempo"] == 80
    ops = mg.edit_to_ops(spec, "make it faster and add strings and jazzier chords in the chorus")
    assert [o["op"] for o in ops] == ["change_tempo", "add_instrument", "reharmonize"]
    ops = mg.edit_to_ops(spec, "chords to Am F C G in the chorus")
    assert ops[0]["chords"] == ["Am", "F", "C", "G"]
    mg.apply_ops(spec, ops)
    with pytest.raises(mg.NotUnderstood):
        mg.edit_to_ops(spec, "flibbertigibbet")


class FakeProvider:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


def test_llm_retries_on_invalid_reply_then_applies():
    fake = FakeProvider([
        "not json at all",
        json.dumps({"action": "ops", "ops": [{"op": "set_tempo", "bpm": "very fast"}]}),
        "```json\n" + json.dumps({"action": "ops", "ops": [{"op": "set_tempo", "bpm": 140}], "explanation": "faster"}) + "\n```",
        json.dumps({"action": "ops", "ops": []}),
    ])
    r = Composer(provider=fake, critic_rounds=1).edit(mg.build_spec("pop", bars=16, seed=1), "make it much faster")
    assert r.source == "llm" and r.spec["tempo"] == 140
    assert len(fake.calls) >= 3
    assert "rejected by the validator" in fake.calls[2][-1]["content"]


def test_llm_full_spec_reply_and_offline_fallback():
    base = mg.build_spec("jazz", bars=16, seed=1)
    new = json.loads(json.dumps(base))
    new["title"] = "From LLM"
    fake = FakeProvider([json.dumps({"action": "spec", "spec": new})] + [json.dumps({"ops": []})] * 3)
    r = Composer(provider=fake).generate("jazz ballad")
    assert r.source == "llm" and r.spec["title"] == "From LLM"
    bad = FakeProvider(["nope"] * 10)
    r = Composer(provider=bad, max_retries=2).edit(base, "make it faster")
    assert r.source == "offline-fallback" and r.spec["tempo"] > base["tempo"]


def test_critic_feedback_reaches_llm():
    spec = mg.build_spec("pop", bars=16, seed=1)
    bad_ops = [{"op": "set_generator", "track": "Lead", "params": {"range": [36, 50]}}]
    fake = FakeProvider([json.dumps({"ops": bad_ops}), json.dumps({"ops": [{"op": "set_generator", "track": "Lead",
                                                                            "params": {"range": [67, 86]}}]})])
    r = Composer(provider=fake, critic_rounds=1).edit(spec, "lower the melody a lot")
    assert len(fake.calls) == 2 and "critic" in fake.calls[1][-1]["content"]
    assert r.spec["tracks"][-1]["generator"]["range"] == [67, 86]
    assert r.log and r.log[0]["score_after"] >= r.log[0]["score_before"]


def test_env_provider_requires_config(monkeypatch):
    for k in ("MIDIGEN_API_KEY", "MIDIGEN_BASE_URL", "MIDIGEN_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert mg.OpenAICompatProvider.from_env() is None
    monkeypatch.setenv("MIDIGEN_MODEL", "m")
    monkeypatch.setenv("MIDIGEN_BASE_URL", "http://localhost:11434/v1")
    p = mg.OpenAICompatProvider.from_env()
    assert p and p.model == "m" and p.api_key is None


def test_cli_end_to_end(tmp_path):
    run = lambda *a: subprocess.run([sys.executable, "-m", "midigen.cli", *a], capture_output=True, text=True, cwd=tmp_path)
    r = run("new", "uplifting house track in F minor, 16 bars", "-o", "song", "--offline", "--png")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "song.mid").exists() and (tmp_path / "song.png").exists()
    r = run("edit", "song.json", "add an arp and transpose up 2", "--offline")
    assert r.returncode == 0, r.stderr
    spec = mg.load(tmp_path / "song.json")
    assert spec["key"] == "G" and any(t["role"] == "arp" for t in spec["tracks"])
    for args in (["inspect", "song.mid"], ["inspect", "song.json"], ["critique", "song.json"], ["genres"], ["render", "song.json"]):
        r = run(*args)
        assert r.returncode == 0, (args, r.stderr)


def test_mcp_tools(tmp_path, monkeypatch):
    from midigen import mcp_server as m
    monkeypatch.setattr(m, "WORKDIR", tmp_path)
    s = m.generate_song(genre="techno", bars=16)
    sid = s["song_id"]
    e = m.edit_song(sid, instruction="make it faster")
    assert e["tempo"] > s["tempo"]
    e = m.edit_song(sid, ops_json=json.dumps([{"op": "transpose", "semitones": 2}]))
    out = m.export_midi_tool(sid)
    info = m.inspect_tool(path=out["midi"])
    assert {t["name"]: t["notes"] for t in info["tracks"]} == out["tracks"]
    assert m.edit_song(sid, instruction="undo")["done"]


def test_mcp_stdio_protocol(tmp_path):
    import asyncio
    import os
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "midigen.mcp_server"],
                                       env={**os.environ, "MIDIGEN_WORKDIR": str(tmp_path)})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                assert {"generate_song", "edit_song", "export_midi", "inspect"} <= names
                d = json.loads((await s.call_tool("generate_song", {"genre": "house", "bars": 16})).content[0].text)
                out = json.loads((await s.call_tool("export_midi", {"song_id": d["song_id"]})).content[0].text)
                assert os.path.exists(out["midi"])
    asyncio.run(go())
