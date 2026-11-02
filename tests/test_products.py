import hashlib
import json
import zipfile

import mido
import pytest

import midigen as mg
from midigen.products import deliver


@pytest.mark.parametrize("product", ["creator", "producer", "rehearsal"])
def test_delivery_pack_contents_lengths_and_no_overwrite(tmp_path, product):
    spec = mg.build_spec("pop", tempo=120, bars=16, seed=19)
    out = tmp_path / product
    result = deliver(spec, out, product=product)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["product"] == product
    assert (out / "preview.png").stat().st_size > 1000
    with zipfile.ZipFile(result["zip"]) as bundle:
        assert set(bundle.namelist()) == {p.name for p in out.iterdir()}
    for entry in manifest["files"]:
        assert hashlib.sha256((out / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    for asset in manifest["assets"]:
        mid = mido.MidiFile(out / (asset["name"] + ".mid"))
        assert mid.length == pytest.approx(asset["seconds"], abs=.02)
        if asset["name"].startswith("part-"):
            assert len(mid.tracks) == 2
    if product == "creator":
        assert {a["name"] for a in manifest["assets"]} >= {"full", "cut-15s", "cut-30s"}
    if product == "rehearsal":
        practice = next(a for a in manifest["assets"] if a["name"] == "practice")
        assert practice["tempo"] == 96
    with pytest.raises(ValueError, match="already exists"):
        deliver(spec, out, product=product)


def test_audio_delivery_includes_valid_wav(tmp_path):
    pytest.importorskip("scipy")
    sf = pytest.importorskip("soundfile")
    out = tmp_path / "audio"
    deliver(mg.build_spec("pop", bars=2, seed=3), out, product="creator", audio=True, backend="numpy")
    info = sf.info(out / "full.wav")
    assert info.frames > 0 and info.channels == 2


def test_compound_meter_cutdowns_use_quarter_note_tempo(tmp_path):
    spec = mg.build_spec("pop", bars=16, tempo=120, seed=5)
    spec["time_signature"] = [6, 8]
    out = tmp_path / "compound"
    deliver(spec, out, product="creator")
    manifest = json.loads((out / "manifest.json").read_text())
    for asset in manifest["assets"]:
        assert mido.MidiFile(out / (asset["name"] + ".mid")).length == pytest.approx(asset["seconds"], abs=.02)
