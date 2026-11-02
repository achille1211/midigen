"""Roadmap step 1: one swing convention (midigen-genres scale), applied at full strength."""
import json
import statistics as st
from pathlib import Path

import pytest

import midigen as mg
from midigen.arranger import swing_time

GENRES = Path(__file__).resolve().parents[2] / "midigen-genres"
HATS = {42, 44, 46, 51}


def _offbeat_ratio(notes, unit):
    d = [(n.start % (2 * unit)) / unit - 1.0 for n in notes if 0.7 < (n.start % (2 * unit)) / unit < 1.55]
    return (1 + st.median(d)) / 2


def test_swing_time_convention():
    # 16th unit: the off-beat sits at 0.5 + s/6 of the pair; beats and pair boundaries never move
    for s in (0.0, 0.25, 0.5, 1.0):
        assert swing_time(0.25, s, 0.25) == pytest.approx(0.5 * (0.5 + s / 6))
        assert swing_time(0.5, s, 0.25) == pytest.approx(0.5)
        assert swing_time(1.0, s, 0.25) == pytest.approx(1.0)
    # full triplet on 8ths: the off-8th lands on the last triplet; in-between 16ths warp proportionally
    assert swing_time(0.5, 1.0, 0.5) == pytest.approx(2 / 3)
    assert swing_time(0.25, 1.0, 0.5) == pytest.approx(1 / 3)
    assert swing_time(0.75, 1.0, 0.5) == pytest.approx(2 / 3 + 1 / 6)
    # off-grid (triplet) times are left alone
    assert swing_time(1 / 3, 1.0, 0.5) == pytest.approx(1 / 3)


@pytest.mark.skipif(not GENRES.is_dir(), reason="midigen-genres pack folder absent")
@pytest.mark.parametrize("gid,fname", [("house", "house"), ("lofi_hiphop", "lofi-hip-hop"), ("jazz", "jazz"),
                                       ("neo_soul", "neo-soul"), ("funk", "funk")])
def test_genre_swing_applied_at_full_strength(gid, fname):
    groove = json.loads((GENRES / f"{fname}.json").read_text())["groove"]
    spec = mg.build_spec(gid, seed=1)
    assert spec["swing"] == pytest.approx(groove["swing"], abs=1e-3)   # same number as the genre file
    perf = mg.render(spec)
    unit = 0.5 if spec["swing_unit"] == "1/8" else 0.25
    hats = [n for t in perf.tracks if t.is_drums for n in t.notes if n.pitch in HATS]
    ratio = _offbeat_ratio(hats, unit)
    assert ratio == pytest.approx(0.5 + groove["swing"] / 6, abs=0.012)


def test_version1_specs_migrate_and_render_identically():
    v2 = mg.build_spec("house", seed=3, bars=8)
    assert v2["version"] == 2
    v1 = json.loads(json.dumps(v2))
    v1["version"] = 1
    v1["swing"] = v2["swing"] / 3
    m = mg.normalize(v1)
    assert m["version"] == 2 and m["swing"] == pytest.approx(v2["swing"], abs=1e-3)
    a = [(round(n.start, 3), n.pitch) for t in mg.render(v1).tracks for n in t.notes]
    b = [(round(n.start, 3), n.pitch) for t in mg.render(v2).tracks for n in t.notes]
    assert a == b


def test_set_swing_full_scale():
    spec = mg.build_spec("pop", seed=1, bars=4)
    s = mg.apply_ops(spec, [{"op": "set_swing", "amount": 1.0, "unit": "1/8"}])
    assert s["swing"] == 1.0
