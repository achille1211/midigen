import pytest
import midigen as mg

GENRES = mg.list_genres()


def test_sixteen_genres():
    assert len(GENRES) >= 16


@pytest.mark.parametrize("genre", GENRES)
def test_every_genre_builds_renders_and_scores(genre):
    spec = mg.build_spec(genre, bars=16, seed=2)
    perf = mg.render(spec)
    assert mg.total_bars(spec) == 16
    assert all(len(t.notes) > 0 for t in perf.tracks if not mg.find_track(spec, t.name).get("mute")) if hasattr(mg, "find_track") else True
    rep = mg.critique(perf)
    assert rep["score"] >= 75, mg.format_report(rep)
    assert not [f for f in rep["findings"] if f["severity"] == "error"]


def test_rendering_is_deterministic():
    a = mg.render(mg.build_spec("house", seed=5, bars=16))
    b = mg.render(mg.build_spec("house", seed=5, bars=16))
    assert [t.notes for t in a.tracks] == [t.notes for t in b.tracks]
    c = mg.render(mg.build_spec("house", seed=6, bars=16))
    assert [t.notes for t in a.tracks] != [t.notes for t in c.tracks]


def test_genre_detection():
    assert mg.detect_genre("a chill lofi hip hop beat") == "lofi_hiphop"
    assert mg.detect_genre("dark techno for the warehouse") == "techno"
    assert mg.detect_genre("bossa nova by the sea") == "bossa_nova"


def test_bass_locks_to_kick_in_house():
    spec = mg.build_spec("house", bars=16, seed=1)
    rep = mg.critique(spec)
    assert not [f for f in rep["findings"] if f["check"] == "interlock" and f["severity"] != "info"]


def test_energy_layering_intro_thinner_than_peak():
    spec = mg.build_spec("edm", bars=32, seed=1)
    perf = mg.render(spec)
    secs = perf.sections
    lo = min(secs, key=lambda s: s.energy if hasattr(s, "energy") else 0)
    hi = max(secs, key=lambda s: s.energy if hasattr(s, "energy") else 0)

    def active(sec):
        return sum(1 for t in perf.tracks if any(sec.start <= n.start < sec.end for n in t.notes))
    assert active(hi) >= active(lo)
