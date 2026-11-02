"""Roadmap fix list step 2: no semitone / b9 rubs between parts on strong beats."""
import copy

import midigen as mg


def _strong_clash_pct(spec, perf):
    roles = [t["role"] for t in spec["tracks"]]
    allp = [(n, i) for i, t in enumerate(perf.tracks) if roles[i] != "drums" for n in t.notes]
    checks = clashes = 0
    for n, i in allp:
        if abs(n.start % 1) > 0.02:
            continue
        checks += 1
        if any(j != i and m.start <= n.start + 0.01 < m.start + m.dur and abs(n.pitch - m.pitch) % 12 == 1 for m, j in allp):
            clashes += 1
    return 100 * clashes / max(1, checks)


def test_jazz_and_neo_soul_strong_beats_are_clean():
    for genre in ("jazz", "neo_soul", "bossa_nova"):
        spec = mg.build_spec(genre, seed=1)
        assert _strong_clash_pct(spec, mg.render(spec)) < 3.0, genre


def test_declash_can_be_switched_off_and_only_touches_a_few_notes():
    spec = mg.build_spec("jazz", seed=1)
    off = copy.deepcopy(spec)
    for t in off["tracks"]:
        t["generator"]["declash"] = False
    mg.check(off)
    a, b = mg.render(spec), mg.render(off)
    assert _strong_clash_pct(off, b) > _strong_clash_pct(spec, a)
    na = sum(len(t.notes) for t in a.tracks)
    nb = sum(len(t.notes) for t in b.tracks)
    assert nb - na < 0.02 * nb                     # almost nothing is dropped
    drums = [i for i, t in enumerate(spec["tracks"]) if t["role"] == "drums"]
    assert all([n.as_list() for n in a.tracks[i].notes] == [n.as_list() for n in b.tracks[i].notes] for i in drums)
