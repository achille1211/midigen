"""Audio: mixing CCs at export, playback humanize, renderer backends, and the measurement-based listening check.

Nothing here listens to audio; the analyzer is tested on synthetic signals with known properties."""
import io
import json
import math
from pathlib import Path

import mido
import pytest

import midigen as mg
from midigen.audio import mix as mixmod
from midigen.audio.humanize import humanize_midi
from midigen.midi_io import performance_to_midi

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
sf = pytest.importorskip("soundfile")

from midigen.audio import analysis as an  # noqa: E402
from midigen.audio import render as rd  # noqa: E402

SR = 44100


def _ccs(mid):
    out = {}
    for tr in mid.tracks:
        name = next((m.name for m in tr if m.type == "track_name"), None)
        cc = {m.control: m.value for m in tr if m.type == "control_change" and m.control in (7, 10, 91, 93)}
        if cc:
            out[name] = cc
    return out


def _bytes(mid):
    b = io.BytesIO()
    mid.save(file=b)
    return b.getvalue()


# ------------------------------------------------------------------ mixing defaults

def test_mix_table_has_every_role_and_valid_values():
    t = mixmod.mix_table()
    for role in mixmod.ROLES:
        assert role in t["default"]
    for g in [None, "house", "jazz", "unknown-genre"]:
        gm = mixmod.genre_mix(g)
        for role in mixmod.ROLES:
            for k in ("volume", "pan", "reverb", "chorus"):
                assert 0 <= gm[role][k] <= 127


def test_genre_key_normalisation():
    assert mixmod.genre_key("Drum-and-Bass") == "drum_and_bass"
    assert mixmod.genre_key("jazz@midigen-knowledge") == "jazz"
    assert mixmod.genre_key("lofi-hip-hop") == "lofi_hiphop"
    assert mixmod.genre_mix("nope") == mixmod.genre_mix(None)


def test_export_applies_genre_mix_ccs(pop):
    perf = mg.render(pop)
    mid = performance_to_midi(perf)
    ccs = _ccs(mid)
    expected = mixmod.resolve_track_mix("pop", [{"role": t.role, "volume": t.volume, "pan": t.pan} for t in perf.tracks])
    for t, m in zip(perf.tracks, expected):
        cc = ccs[t.name]
        assert (cc[7], cc[10], cc[91], cc[93]) == (m["volume"], m["pan"], m["reverb"], m["chorus"])
    # chords/pads are spread off-centre, bass and drums stay centred
    roles = {t.name: t.role for t in perf.tracks}
    assert all(ccs[n][10] == 64 for n, r in roles.items() if r in ("bass", "drums"))
    assert any(ccs[n][10] != 64 for n, r in roles.items() if r in ("chords", "pad"))


def test_export_without_mix_is_the_old_behaviour(pop):
    perf = mg.render(pop)
    ccs = _ccs(performance_to_midi(perf, mix=False))
    for t in perf.tracks:
        assert ccs[t.name] == {7: t.volume, 10: t.pan}


def test_explicit_pan_and_volume_edits_survive_mix(pop):
    name = next(t["name"] for t in pop["tracks"] if t["role"] == "chords")
    louder = mg.apply_ops(pop, [{"op": "set_track", "track": name, "pan": 20, "volume": 110}])
    base = _ccs(performance_to_midi(mg.render(pop)))[name]
    edited = _ccs(performance_to_midi(mg.render(louder)))[name]
    assert edited[10] == 20
    assert edited[7] > base[7]


def test_duplicate_roles_are_spread():
    res = mixmod.resolve_track_mix("default", [{"role": "pad"}, {"role": "pad"}, {"role": "pad"}])
    pans = [r["pan"] for r in res]
    assert len(set(pans)) == 3 and pans[1] < 64 < pans[0]


def test_export_midi_writes_mix(tmp_path, pop):
    p = tmp_path / "a.mid"
    mg.export_midi(pop, p)
    assert any(m.type == "control_change" and m.control == 91 for tr in mido.MidiFile(str(p)).tracks for m in tr)


# ------------------------------------------------------------------ humanize

def _notes(mid):
    out = []
    for ti, tr in enumerate(mid.tracks):
        now, open_ = 0, {}
        for m in tr:
            now += m.time
            if m.type == "note_on" and m.velocity:
                open_.setdefault((m.channel, m.note), []).append((now, m.velocity))
            elif m.type in ("note_on", "note_off") and open_.get((m.channel, m.note)):
                on, v = open_[(m.channel, m.note)].pop(0)
                out.append((ti, m.channel, m.note, on, now, v))
    return out


def test_humanize_is_deterministic_and_leaves_original_untouched(pop):
    mid = performance_to_midi(mg.render(pop))
    before = _bytes(mid)
    a = humanize_midi(mid, seed=5, timing_ms=6, velocity=8)
    b = humanize_midi(mid, seed=5, timing_ms=6, velocity=8)
    c = humanize_midi(mid, seed=6, timing_ms=6, velocity=8)
    assert _bytes(mid) == before
    assert _bytes(a) == _bytes(b)
    assert _bytes(a) != _bytes(c)


def test_humanize_amounts_are_small_and_bounded(pop):
    mid = performance_to_midi(mg.render(pop))
    tpb = mid.ticks_per_beat
    tempo = next(m.tempo for tr in mid.tracks for m in tr if m.type == "set_tempo")
    max_ticks = 6 * tpb / (tempo / 1000) + 1
    orig = sorted(_notes(mid), key=lambda n: (n[0], n[1], n[2], n[3]))
    hum = sorted(_notes(humanize_midi(mid, seed=1, timing_ms=6, velocity=8)), key=lambda n: (n[0], n[1], n[2], n[3]))
    assert len(orig) == len(hum)
    # match by order within (track, channel, pitch)
    from collections import defaultdict
    go, gh = defaultdict(list), defaultdict(list)
    for n in orig:
        go[n[:3]].append(n)
    for n in hum:
        gh[n[:3]].append(n)
    moved = 0
    anchor_dev, hat_dev = [], []
    for k in go:
        for o, h in zip(go[k], gh[k]):
            assert abs(h[3] - o[3]) <= max_ticks
            assert h[3] >= 0 and h[4] > h[3]
            assert abs(h[5] - o[5]) <= 8
            moved += h[3] != o[3]
            if k[1] == 9:
                (anchor_dev if k[2] in (36, 38) else hat_dev).append(abs(h[3] - o[3]))
    assert moved > len(orig) * 0.5
    if anchor_dev and hat_dev:
        assert sum(anchor_dev) / len(anchor_dev) < sum(hat_dev) / len(hat_dev)


def test_humanize_zero_is_identity_on_notes(pop):
    mid = performance_to_midi(mg.render(pop))
    assert sorted(_notes(humanize_midi(mid, seed=1, timing_ms=0, velocity=0))) == sorted(_notes(mid))


# ------------------------------------------------------------------ renderer

@pytest.fixture
def short_spec():
    return mg.build_spec("house", key="A", mode="minor", bars=2, seed=2)


def test_numpy_backend_renders_spec(tmp_path, short_spec):
    res = rd.render_audio(short_spec, tmp_path / "x.wav", backend="numpy")
    x, sr = sf.read(res.wav)
    assert res.backend == "numpy" and sr == SR and x.shape[1] == 2
    assert 3.0 < res.seconds < 10.0
    assert np.max(np.abs(x)) > 0.01


def test_renderer_accepts_mid_path(tmp_path, short_spec):
    p = tmp_path / "s.mid"
    mg.export_midi(short_spec, p)
    res = rd.render_audio(p, tmp_path / "m.wav", backend="numpy", humanize=3)
    assert Path(res.wav).exists() and res.humanize["seed"] == 3


def test_missing_fluidsynth_gives_clear_error(tmp_path, short_spec, monkeypatch):
    monkeypatch.setenv("MIDIGEN_FLUIDSYNTH", str(tmp_path / "nope"))
    with pytest.raises(rd.AudioBackendError, match="FluidSynth backend unavailable"):
        rd.render_audio(short_spec, tmp_path / "x.wav", backend="fluidsynth")
    # auto degrades to the numpy synth
    assert rd.render_audio(short_spec, tmp_path / "y.wav").backend == "numpy"


def test_no_backend_at_all_raises(tmp_path, short_spec, monkeypatch):
    monkeypatch.setenv("MIDIGEN_FLUIDSYNTH", str(tmp_path / "nope"))
    monkeypatch.setattr(rd, "numpy_available", lambda: False)
    with pytest.raises(rd.AudioBackendError, match="No audio backend available"):
        rd.render_audio(short_spec, tmp_path / "x.wav")


needs_fs = pytest.mark.skipif(not rd.available_backends()["fluidsynth"]["available"],
                              reason="fluidsynth binary or GM soundfont not installed (set MIDIGEN_FLUIDSYNTH/MIDIGEN_SOUNDFONT)")
needs_ff = pytest.mark.skipif(not rd.find_ffmpeg(), reason="ffmpeg not installed")


@needs_fs
def test_fluidsynth_backend_renders(tmp_path, short_spec):
    res = rd.render_audio(short_spec, tmp_path / "f.wav", backend="fluidsynth")
    x, sr = sf.read(res.wav)
    assert res.backend == "fluidsynth" and res.soundfont and sr == SR
    assert np.max(np.abs(x)) > 0.005


@needs_ff
def test_mp3_export(tmp_path, short_spec):
    res = rd.render_audio(short_spec, tmp_path / "x.wav", backend="numpy", mp3=True, normalize_lufs=-16)
    assert Path(res.mp3).exists() and Path(res.mp3).stat().st_size > 1000


def test_stems_split_kick(tmp_path, short_spec):
    stems = rd.render_stems(short_spec, tmp_path / "st", backend="numpy")
    names = [s["name"] for s in stems]
    assert "Kick" in names and any(s["role"] == "bass" for s in stems)
    assert (tmp_path / "st" / "stems.json").exists()


def test_cli_backends_and_render(tmp_path, short_spec, capsys):
    from midigen.audio.__main__ import main
    assert main(["backends"]) == 0
    mg.save(short_spec, tmp_path / "s.json")
    assert main(["render", str(tmp_path / "s.json"), str(tmp_path / "o.wav"), "--backend", "numpy"]) == 0
    assert (tmp_path / "o.wav").exists()
    assert main(["render", str(tmp_path / "nope.txt"), str(tmp_path / "o2.wav")]) == 1


# ------------------------------------------------------------------ analyzer on synthetic signals

def _sine(freq, dbfs, sec=5.0, sr=SR, channels=(1, 1)):
    t = np.arange(int(sec * sr)) / sr
    s = 10 ** (dbfs / 20) * np.sin(2 * np.pi * freq * t)
    return np.stack([s * channels[0], s * channels[1]], axis=1)


def test_sine_levels():
    x = _sine(997, -20)
    rep = an.analyze_array(x, SR)
    m = rep["metrics"]
    assert abs(m["sample_peak_dbfs"] + 20) < 0.05
    assert abs(m["true_peak_dbtp"] + 20) < 0.1
    # BS.1770: a 997 Hz sine at -20 dBFS peak in both channels reads about -20 LUFS (stereo sum +3, RMS -3)
    assert abs(m["loudness"]["integrated_lufs"] + 20.0) < 0.3
    assert m["clipping"]["clipped_samples"] == 0
    assert m["silence"]["silence_ratio"] == 0
    assert abs(m["stereo"]["balance_db"]) < 0.01 and m["stereo"]["correlation"] > 0.99
    assert m["spectrum"]["bands_db_rel_total"]["mid"] > -0.5
    assert not [f for f in rep["findings"] if f["severity"] == "error"]


@needs_ff
def test_loudness_matches_ffmpeg(tmp_path):
    x = _sine(440, -12) * np.linspace(0.2, 1, int(5 * SR))[:, None]
    p = tmp_path / "s.wav"
    sf.write(p, x, SR, subtype="FLOAT")
    ours = an.loudness(x, SR)["integrated_lufs"]
    theirs = an.ffmpeg_loudness(p)["integrated_lufs"]
    assert abs(ours - theirs) < 0.3


def test_clipped_signal_is_flagged():
    x = np.clip(_sine(200, 6), -1.0, 1.0)
    rep = an.analyze_array(x, SR)
    assert rep["metrics"]["clipping"]["clip_events"] > 100
    assert any(f["check"] == "mix_clipping" and f["severity"] == "error" for f in rep["findings"])


def test_silence_is_flagged():
    rep = an.analyze_array(np.zeros((SR * 3, 2)), SR)
    assert rep["metrics"]["silence"]["silence_ratio"] == 1.0
    assert any(f["check"] == "mix_silent" for f in rep["findings"])


def test_partial_silence_and_dc():
    x = _sine(440, -10)
    x[: len(x) // 2] = 0
    rep = an.analyze_array(x, SR)
    assert 0.45 < rep["metrics"]["silence"]["silence_ratio"] < 0.55
    assert any(f["check"] == "mix_silence" for f in rep["findings"])
    rep = an.analyze_array(_sine(440, -10) + 0.01, SR)
    assert rep["metrics"]["dc_offset"][0] == pytest.approx(0.01, abs=1e-3)
    assert any(f["check"] == "mix_dc_offset" for f in rep["findings"])


def test_stereo_imbalance_flagged():
    rep = an.analyze_array(_sine(440, -12, channels=(1.0, 0.5)), SR)
    assert rep["metrics"]["stereo"]["balance_db"] == pytest.approx(6.02, abs=0.05)
    assert any(f["check"] == "mix_stereo_balance" for f in rep["findings"])


def _write_stems(tmp_path, parts):
    stems = []
    for i, (name, role, sig) in enumerate(parts):
        p = tmp_path / f"{i}.wav"
        sf.write(p, sig, SR, subtype="FLOAT")
        stems.append({"name": name, "role": role, "wav": str(p), "volume": 100, "pan": 64})
    return stems


def test_masked_tones_are_detected(tmp_path):
    lead = _sine(440, -20)
    pad = _sine(440, -14) + _sine(660, -16)    # same band, louder: covers the lead
    stems = _write_stems(tmp_path, [("Lead", "lead", lead), ("Pad", "pad", pad)])
    rep = an.analyze_array(lead + pad, SR, stems=stems)
    pair = next(p for p in rep["masking"]["pairs"] if p["focus"] == "mid")
    assert pair["overlap"] > 0.8 and pair["masked_a"] > 0.9
    f = next(f for f in rep["findings"] if f["check"] == "mix_masking")
    assert f["severity"] == "warning" and f["track"] == "Pad" and "lower Pad" in f["suggestion"]
    lead_lvl = next(f for f in rep["findings"] if f["check"] == "mix_lead_level")
    assert "Lead" in lead_lvl["message"]


def test_separated_tones_are_not_masked(tmp_path):
    bass = _sine(55, -14)
    kick = np.zeros_like(bass)
    kick[: SR // 2] = _sine(120, -10, sec=0.5)   # different band and mostly different time
    lead = _sine(1500, -18)
    stems = _write_stems(tmp_path, [("Bass", "bass", bass), ("Kick", "kick", kick), ("Lead", "lead", lead)])
    rep = an.analyze_array(bass + kick + lead, SR, stems=stems)
    assert rep["masking"]["score"] is not None and rep["masking"]["score"] < 0.2
    assert not [f for f in rep["findings"] if f["check"] == "mix_masking"]


def test_panning_reduces_masking(tmp_path):
    a = _sine(500, -14, channels=(1, 0.05))
    b = _sine(500, -14, channels=(0.05, 1))
    c = _sine(500, -14)
    stems_apart = _write_stems(tmp_path, [("Chords", "chords", a), ("Pad", "pad", b)])
    apart = an.analyze_array(a + b, SR, stems=stems_apart)["masking"]["score"]
    (tmp_path / "x").mkdir()
    stems_centre = _write_stems(tmp_path / "x", [("Chords", "chords", c), ("Pad", "pad", c)])
    centre = an.analyze_array(c + c, SR, stems=stems_centre)["masking"]["score"]
    assert apart < 0.2 < 0.9 < centre


# ------------------------------------------------------------------ critic integration

def test_critic_gets_mix_section(short_spec):
    rep = an.analyze_array(np.clip(_sine(200, 6), -1, 1), SR)
    plain = mg.critique(short_spec)
    with_mix = mg.critique(short_spec, mix=rep)
    assert with_mix["score"] == plain["score"]
    assert with_mix["mix"]["findings"] and with_mix["mix"]["verified_by_ear"] is False
    assert set(with_mix["mix"]["findings"][0]) <= {"check", "severity", "message", "suggestion", "track"}
    txt = mg.format_report(with_mix)
    assert "mix (measured" in txt and "mix_clipping" in txt
    from midigen.critic import serious
    assert len(serious(with_mix, include_mix=True)) > len(serious(with_mix))
    assert an.critic_findings(rep) == with_mix["mix"]["findings"]


def test_listening_check_end_to_end(tmp_path, short_spec):
    from midigen.audio.check import listening_check
    rep = listening_check(short_spec, tmp_path, name="s", backend="numpy", mp3=False)
    for ext in ("wav", "mid", "mix.json", "mix.txt", "critic.txt"):
        assert (tmp_path / f"s.{ext}").exists()
    data = json.loads((tmp_path / "s.mix.json").read_text())
    assert data["verified_by_ear"] is False and data["stems"] and data["render"]["backend"] == "numpy"
    assert math.isfinite(data["metrics"]["loudness"]["integrated_lufs"])


# ------------------------------------------------------------------ QA: CC ranges and channels on every genre

@pytest.mark.parametrize("genre", sorted(mixmod.mix_table()["genres"]))
def test_mix_ccs_in_range_on_valid_channels(genre):
    spec = mg.build_spec(genre, bars=4, seed=1)
    perf = mg.render(spec)
    mid = performance_to_midi(perf)
    seen_tracks = 0
    for tr, rt in zip(mid.tracks[1:], perf.tracks):
        chans = {m.channel for m in tr if hasattr(m, "channel")}
        assert chans == {rt.channel} and 0 <= rt.channel <= 15
        if rt.is_drums:
            assert rt.channel == 9  # GM channel 10
            assert not any(m.type == "program_change" for m in tr)
        else:
            assert rt.channel != 9
        cc = {m.control: m.value for m in tr if m.type == "control_change" and m.control in (7, 10, 91, 93)}
        assert set(cc) == {7, 10, 91, 93}
        assert all(0 <= v <= 127 for v in cc.values())
        seen_tracks += 1
    assert seen_tracks == len(perf.tracks)
    assert any(t.is_drums for t in perf.tracks) or genre in ("ambient", "cinematic")


def test_mix_clamps_extreme_spec_values():
    res = mixmod.resolve_track_mix("rock", [{"role": "lead", "volume": 127, "pan": 127},
                                            {"role": "lead", "volume": 0, "pan": 0},
                                            {"role": "drums", "volume": 127}])
    for r in res:
        assert all(0 <= r[k] <= 127 and isinstance(r[k], int) for k in ("volume", "pan", "reverb", "chorus"))


def test_mix_and_expression_flags_are_independent(pop):
    perf = mg.render(pop)
    a = performance_to_midi(perf, expression=False, mix=True)
    b = performance_to_midi(perf, expression=True, mix=False)
    assert any(m.type == "control_change" and m.control == 91 for tr in a.tracks for m in tr)
    assert not any(m.type == "control_change" and m.control == 91 for tr in b.tracks for m in tr)
    assert _notes(a) == _notes(b)
