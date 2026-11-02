"""Roadmap fix list step 1: later instances of a section family restate the first instance's lead (hook reuse)."""
import copy

import midigen as mg
from midigen.arranger import section_family


def _lead_bars(perf, spec, sec_name):
    ti = next(i for i, t in enumerate(spec["tracks"]) if t["generator"]["type"] == "lead" and sec_name in (t.get("sections") or [sec_name]))
    sec = next(s for s in perf.sections if s.name == sec_name)
    notes = [n for n in perf.tracks[ti].notes if sec.start <= n.start < sec.end]
    bars = {}
    for n in notes:
        bars.setdefault(int((n.start - sec.start + 0.05) // 4), []).append((round(n.start - sec.start - 4 * int((n.start - sec.start + 0.05) // 4)), n.pitch))
    return bars


def test_section_family():
    assert section_family("chorus2") == section_family("final_chorus") == section_family("chorus") == "chorus"
    assert section_family("verse2") == "verse"
    assert section_family("head_a3") == "head_a"
    assert section_family("pre_chorus") != "chorus"


def test_chorus2_restates_chorus1_lead():
    spec = mg.build_spec("pop", seed=2)
    perf = mg.render(spec)
    a, b = _lead_bars(perf, spec, "chorus1"), _lead_bars(perf, spec, "chorus2")
    shared = [k for k in a if k in b and k < max(a)]   # last bar is allowed a fresh ending
    same = sum(1 for k in shared if [p for _, p in a[k]] == [p for _, p in b[k]])
    assert shared and same / len(shared) >= 0.6


def test_restate_param_switches_it_off():
    spec = mg.build_spec("pop", seed=2)
    off = copy.deepcopy(spec)
    for t in off["tracks"]:
        if t["generator"]["type"] == "lead":
            t["generator"]["restate"] = False
    mg.check(off)
    a = mg.render(spec)
    b = mg.render(off)
    assert [n.pitch for t in a.tracks for n in t.notes] != [n.pitch for t in b.tracks for n in t.notes]
