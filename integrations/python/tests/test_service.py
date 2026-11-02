import base64
import io

import mido
import pytest
pytest.importorskip("fastapi", reason="service extra not installed: pip install -e 'integrations/python[service]'")
pytest.importorskip("httpx", reason="TestClient needs httpx: pip install -e 'integrations/python[test]'")
from fastapi.testclient import TestClient  # noqa: E402

from midigen_integrations.service import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="module")
def spec():
    r = client.post("/generate", json={"genre": "techno", "bars": 8, "seed": 2})
    assert r.status_code == 200, r.text
    return r.json()["spec"]


def test_health_and_openapi():
    assert client.get("/health").json()["status"] == "ok"
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/generate", "/edit", "/critique", "/export", "/genres", "/genres/{name}", "/options"} <= set(paths)
    assert client.get("/docs").status_code == 200


def test_genres_and_options():
    ids = [g["id"] for g in client.get("/genres").json()]
    assert "techno" in ids
    assert client.get("/genres/lofi-hip-hop").json()["name"] == "lofi_hiphop"
    r = client.get("/genres/nope")
    assert r.status_code == 404 and r.json()["error"] == "unknown_genre"
    assert "set_tempo" in client.get("/options").json()["ops"]


def test_generate_json_and_midi(spec):
    r = client.post("/generate?format=midi", json={"genre": "techno", "bars": 8, "seed": 2})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/midi"
    assert r.content[:4] == b"MThd" and "attachment" in r.headers["content-disposition"]
    assert "bars=8" in r.headers["x-midigen-summary"]
    mid = mido.MidiFile(file=io.BytesIO(r.content))
    assert len(mid.tracks) >= 3
    j = client.post("/generate?include_midi=true", json={"genre": "techno", "bars": 8, "seed": 2}).json()
    assert base64.b64decode(j["midi_base64"]) == r.content and j["spec"] == spec


def test_generate_prompt():
    j = client.post("/generate", json={"prompt": "dreamy synthwave in E minor", "bars": 4}).json()
    assert j["summary"]["genre"] == "synthwave" and j["summary"]["bars"] == 4


@pytest.mark.parametrize("body", [{"genre": "techno", "tempo": 5}, {"genre": "techno", "bars": 0}, {"genre": "techno", "key": "H"}, {}])
def test_generate_validation(body):
    r = client.post("/generate", json=body)
    assert r.status_code == 422 and r.json()["error"] == "invalid_params" and r.json()["errors"]


def test_llm_disabled_by_default():
    r = client.post("/generate", json={"prompt": "pop", "use_llm": True})
    assert r.status_code == 422 and "use_llm" in r.json()["errors"][0]


def test_edit_ops_instruction_and_errors(spec):
    j = client.post("/edit", json={"spec": spec, "ops": [{"op": "set_tempo", "bpm": 140}]}).json()
    assert j["summary"]["tempo"] == 140
    j = client.post("/edit", json={"spec": spec, "instruction": "slower"}).json()
    assert j["summary"]["tempo"] < spec["tempo"]
    r = client.post("/edit?format=midi", json={"spec": spec, "ops": [{"op": "transpose", "semitones": 1}]})
    assert r.content[:4] == b"MThd"
    r = client.post("/edit", json={"spec": spec, "ops": [{"op": "bogus"}]})
    assert r.status_code == 422 and r.json()["error"] == "invalid_ops"
    r = client.post("/edit", json={"spec": spec, "instruction": "xyzzy plugh"})
    assert r.status_code == 422 and r.json()["error"] == "not_understood"


def test_critique(spec):
    rep = client.post("/critique", json={"spec": spec}).json()
    assert 0 <= rep["score"] <= 100
    r = client.post("/critique", json={"spec": {"tempo": 1}})
    assert r.status_code == 422 and r.json()["error"] == "invalid_spec"


def test_export_formats(spec):
    r = client.post("/export", json={"spec": spec})
    assert r.headers["content-type"] == "audio/midi" and r.content[:4] == b"MThd"
    notes = client.post("/export?format=notes", json={"spec": spec}).json()
    assert notes["total_beats"] == 32 and notes["tracks"]
    lines = client.post("/export?format=lines", json={"spec": spec}).text.splitlines()
    assert lines[0].startswith("H\t")
    assert sum(1 for l in lines if l.startswith("T\t")) == len(notes["tracks"])
    assert sum(1 for l in lines if l.startswith("N\t")) == sum(len(t["notes"]) for t in notes["tracks"])


def test_generate_lines_format():
    r = client.post("/generate?format=lines", json={"genre": "funk", "bars": 2})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.text.startswith("H\t") and "\nT\t" in r.text and "\nN\t" in r.text
