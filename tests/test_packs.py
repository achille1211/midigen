"""Genre packs, precedence/merge policy, time signatures, exact bar counts and CLI validation."""
import json
import os
import subprocess
import sys
from pathlib import Path

import mido
import pytest

import midigen as mg
from midigen import packs

WS = Path(__file__).resolve().parents[2]
HAVE_PACKS = (WS / "midigen-genres").is_dir() and (WS / "midigen-knowledge").is_dir()
needs_packs = pytest.mark.skipif(not HAVE_PACKS, reason="midigen-genres/midigen-knowledge not next to midigen")


def _all_ids():
    if not HAVE_PACKS:
        return []
    ids = list(mg.available_genres()) + sorted(k for k in mg.genres._all() if "@" in k)
    return ids


def _errors(rep):
    return [f for f in rep["findings"] if f["severity"] == "error"]


@needs_packs
def test_pack_report_clean():
    r = packs.report()
    assert r["failed"] == {}
    assert {"bigband", "emo", "metal", "screamo"} <= set(mg.available_genres())
    assert set(mg.list_genres()) <= set(mg.available_genres())
    assert len(mg.list_genres()) == 16
    # the knowledge pack renamed its overlapping ids (jazz-combo, pop-studio, rock-band), so nothing is shadowed now
    assert {"jazz_combo", "pop_studio", "rock_band"} <= set(mg.available_genres())
    assert not {"jazz@midigen-knowledge", "pop@midigen-knowledge", "rock@midigen-knowledge"} & set(r["shadowed"])


@needs_packs
@pytest.mark.parametrize("gid", _all_ids())
def test_every_pack_genre_builds_exports_and_scores(gid, tmp_path):
    spec = mg.build_spec(gid, bars=16, seed=1)
    assert mg.total_bars(spec) == 16
    p = tmp_path / "x.mid"
    perf = mg.export_midi(spec, p)
    mid = mido.MidiFile(p)
    assert len(mid.tracks) == 1 + len(spec["tracks"])
    back = mg.import_midi(p)
    assert len(back["tracks"]) == len([t for t in perf.tracks if t.notes])
    assert not _errors(mg.critique(spec)), _errors(mg.critique(spec))


@needs_packs
@pytest.mark.parametrize("name,expected", [("Big Band", "bigband"), ("lofi-hip-hop", "lofi_hiphop"),
                                           ("Drum and Bass", "drum_and_bass"), ("jazz-combo", "jazz_combo"), ("Rock Band", "rock_band"),
                                           ("jazz_combo@knowledge", "jazz_combo@midigen-knowledge")])
def test_resolve_display_names_and_pack_ids(name, expected):
    assert mg.resolve_genre(name) == expected


def test_unknown_genre_is_clean_error():
    with pytest.raises(mg.genres.UnknownGenre):
        mg.resolve_genre("polka-step-nope")


@needs_packs
def test_merge_policies(monkeypatch):
    merged = mg.get_genre("pop")
    monkeypatch.setenv("MIDIGEN_GENRE_MERGE", "builtin")
    packs.reload()
    try:
        builtin = mg.get_genre("pop")
        monkeypatch.setenv("MIDIGEN_GENRE_MERGE", "replace")
        packs.reload()
        replaced = mg.get_genre("pop")
    finally:
        monkeypatch.delenv("MIDIGEN_GENRE_MERGE")
        packs.reload()
    names = lambda g: sorted(str(v.get("name")) for v in g["instruments"].values())
    assert names(merged) != names(builtin)
    assert names(replaced) == names(merged)


def test_no_default_packs_env(monkeypatch):
    monkeypatch.setenv("MIDIGEN_NO_DEFAULT_PACKS", "1")
    packs.reload()
    try:
        assert mg.available_genres() == mg.list_genres()
    finally:
        monkeypatch.delenv("MIDIGEN_NO_DEFAULT_PACKS")
        packs.reload()


@needs_packs
def test_genre_path_env_and_set_paths_take_precedence(monkeypatch, tmp_path):
    raw = json.loads((WS / "midigen-genres" / "funk.json").read_text())
    raw["id"] = "testgroove"
    raw["name"] = "Test Groove"
    (tmp_path / "testgroove.json").write_text(json.dumps(raw))
    monkeypatch.setenv("MIDIGEN_NO_DEFAULT_PACKS", "1")
    monkeypatch.setenv("MIDIGEN_GENRE_PATH", str(tmp_path))
    packs.reload()
    try:
        assert "testgroove" in mg.available_genres()
        assert mg.resolve_genre("Test Groove") == "testgroove"
        monkeypatch.delenv("MIDIGEN_GENRE_PATH")
        packs.set_paths([str(tmp_path)])
        assert "testgroove" in mg.available_genres()
    finally:
        packs.set_paths(None)
        monkeypatch.delenv("MIDIGEN_NO_DEFAULT_PACKS")
        packs.reload()
    assert "testgroove" not in mg.available_genres()


@pytest.mark.parametrize("bars", [1, 2, 3, 5, 7])
@pytest.mark.parametrize("genre", ["pop", "rock", "techno", "jazz"])
def test_exact_bar_counts(genre, bars):
    assert mg.total_bars(mg.build_spec(genre, bars=bars, seed=1)) == bars


@pytest.mark.parametrize("kw", [dict(bars=0), dict(bars=-1), dict(bars=500), dict(tempo=0), dict(tempo=1000),
                                dict(key="H"), dict(mode="nonsense")])
def test_build_spec_rejects_bad_args(kw):
    with pytest.raises(ValueError):
        mg.build_spec("pop", **kw)


@pytest.mark.parametrize("ts", [(3, 4), (6, 8), (7, 8), (5, 4), (2, 2)])
def test_time_signature_round_trip_keeps_quarter_pulse(ts, tmp_path):
    spec = mg.build_spec("pop", bars=8, seed=1, tempo=120)
    s = mg.apply_ops(spec, [{"op": "set_time_signature", "numerator": ts[0], "denominator": ts[1]}])
    assert mg.spec.quarter_bpm(s) == pytest.approx(120, abs=0.01)
    p = tmp_path / "ts.mid"
    mg.export_midi(s, p)
    mid = mido.MidiFile(p)
    metas = [m for m in mid.tracks[0] if m.type in ("set_tempo", "time_signature")]
    tsm = [m for m in metas if m.type == "time_signature"][0]
    assert (tsm.numerator, tsm.denominator) == ts
    assert mido.tempo2bpm([m for m in metas if m.type == "set_tempo"][0].tempo) == pytest.approx(120, abs=0.05)
    end = max(sum(m.time for m in tr) for tr in mid.tracks) / mid.ticks_per_beat
    assert end <= 8 * ts[0] * 4 / ts[1] + 4.01
    back = mg.import_midi(p)
    assert tuple(back["time_signature"]) == ts
    assert back["tempo"] == pytest.approx(s["tempo"], abs=0.05)


def test_time_signature_keep_tempo():
    spec = mg.build_spec("pop", bars=8, seed=1, tempo=120)
    s = mg.apply_ops(spec, [{"op": "set_time_signature", "numerator": 6, "denominator": 8, "keep_tempo": True}])
    assert s["tempo"] == 120


def _cli(*a, cwd):
    return subprocess.run([sys.executable, "-m", "midigen.cli", *a], capture_output=True, text=True, cwd=cwd)


@pytest.mark.parametrize("args", [["--tempo", "0"], ["--bars", "0"], ["--bars", "-3"], ["--bars", "500"],
                                  ["--key", "H"], ["--mode", "nonsense"], ["--genre", "polka-step-nope"]])
def test_cli_rejects_bad_args(args, tmp_path):
    r = _cli("new", "--genre", "pop", *args, "-o", "x", "--offline", cwd=tmp_path) if "--genre" not in args else \
        _cli("new", *args, "-o", "x", "--offline", cwd=tmp_path)
    assert r.returncode != 0
    assert "error" in r.stderr.lower() and "Traceback" not in r.stderr
    assert not (tmp_path / "x.mid").exists()


def test_cli_bars_two_and_random_seed(tmp_path):
    r = _cli("new", "--genre", "rock", "--bars", "2", "--random-seed", "-o", "x", "--offline", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "seed" in (r.stdout + r.stderr).lower()
    assert mg.total_bars(mg.load(tmp_path / "x.json")) == 2


@needs_packs
def test_cli_genre_path(tmp_path):
    raw = json.loads((WS / "midigen-genres" / "funk.json").read_text())
    raw["id"] = "clitestgroove"
    (tmp_path / "clitestgroove.json").write_text(json.dumps(raw))
    r = _cli("--genre-path", str(tmp_path), "new", "--genre", "clitestgroove", "--bars", "4", "-o", "x", "--offline",
             cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    r = _cli("new", "--genre", "clitestgroove", "-o", "y", "--offline", cwd=tmp_path)
    assert r.returncode != 0
    r = _cli("--genre-path", str(tmp_path / "missing"), "genres", cwd=tmp_path)
    assert r.returncode != 0
