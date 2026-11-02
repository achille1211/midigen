"""Held-out evaluation of the opt-in ML path against the plain theory engine.

Held-out = seeds 10001.. (never used by ``dataset build``, whose seeds start at 1) for every genre. Per song:

* ``plain``   : ``build_spec(genre, seed, ml=False, learn=False)`` (baseline)
* ``random``  : a random pick among the same N candidates the ML path considers (best-of-N null baseline)
* ``ml``      : ``build_spec(..., ml="reward,progression,n=N")`` (reward model ranks N candidates)
* ``critic``  : the same N candidates ranked by the critic itself. This is an upper reference for the critic metric,
                not a fair competitor, because it optimises the metric directly.

Metrics: critic score (0-100), pitch-in-key ratio, MIDI validity (export + re-read with mido + note count),
seconds per song, and the independent analyzer's metrics (``tools/analyze.py``) when available. When
``midigen-critic/REVIEW.md`` is present, the reward model's Spearman correlation with its 28 human-style scores is
reported too. With n=28 this is only a sanity check.
"""
from __future__ import annotations

import json
import random
import statistics as st
import tempfile
import time
from pathlib import Path

AN_KEYS = ("an_in_key_pct", "an_strong_clash_pct", "an_kick_with_bass_pct", "an_bass_root_on_change_pct",
           "an_melody_strong_beat_chord_tone_pct", "an_melody_repeated_bar_pct")


def _midi_ok(spec) -> tuple[bool, int]:
    import mido
    from ..midi_io import export_midi
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.mid"
        try:
            export_midi(spec, p)
            m = mido.MidiFile(str(p))
            n = sum(1 for tr in m.tracks for msg in tr if msg.type == "note_on" and msg.velocity > 0)
            return n > 0, n
        except Exception:
            return False, 0


def _measure(spec, analyzer: bool) -> dict:
    from ..arranger import render
    from ..critic import critique
    from .features import perf_stats
    perf = render(spec)
    rep = critique(perf)
    ok, n = _midi_ok(spec)
    r = {"critic": float(rep["score"]), "in_key": perf_stats(perf)["pitch_in_key_ratio"], "midi_ok": ok, "notes": n}
    if analyzer:
        from .critic_bridge import analyze_features
        f = analyze_features(spec)
        r.update({k: f[k] for k in AN_KEYS if k in f})
    return r


def _agg(rows: list[dict]) -> dict:
    out = {"n": len(rows)}
    keys = sorted({k for r in rows for k in r if isinstance(r[k], (int, float)) and not isinstance(r[k], bool)})
    for k in keys:
        v = [r[k] for r in rows if k in r]
        out[k] = round(st.mean(v), 4)
    if any("midi_ok" in r for r in rows):
        out["midi_valid_pct"] = round(100 * sum(bool(r.get("midi_ok")) for r in rows) / max(1, len(rows)), 2)
    return out


def _review_check(d) -> dict | None:
    from .critic_bridge import review_scores
    from . import reward
    from .reward import _spearman
    rows = review_scores()
    rm = reward.load(d)
    if not rows or rm is None:
        return None
    from ..arranger import render
    from ..critic import critique
    from ..spec import check
    human, pred, crit, used = [], [], [], []
    for r in rows:
        if not r["spec_path"]:
            continue
        try:
            sp = check(json.loads(Path(r["spec_path"]).read_text()))
            perf = render(sp)
            pred.append(rm.score_spec(sp, perf))
            crit.append(float(critique(perf)["score"]))
            human.append(r["avg"])
            used.append(r["song"])
        except Exception:
            continue
    if len(human) < 5:
        return None
    return {"songs": len(human), "spearman_reward_vs_review": _spearman(pred, human),
            "spearman_critic_vs_review": _spearman(crit, human),
            "note": "28 hand-scored songs from midigen-critic/REVIEW.md (older midigen version); sanity check only"}


def evaluate(seeds: int = 5, genres=None, n: int = 6, d=None, json_out=None, analyzer: bool = True, log=print) -> dict:
    from ..genres import available_genres, build_spec
    from . import hooks
    from .dataset import _isolate_env, _restore_env
    from .paths import artifact_dir
    d = Path(d) if d else artifact_dir()
    genres = genres or available_genres()
    seeds_l = list(range(10001, 10001 + seeds))
    res = {"plain": [], "random": [], "ml": [], "critic": []}
    times = {k: [] for k in res}
    picks = []
    old = _isolate_env()
    try:
        for g in genres:
            for s in seeds_l:
                rng = random.Random(f"eval|{g}|{s}")
                t = time.perf_counter()
                plain = build_spec(g, seed=s, ml=False, learn=False)
                times["plain"].append(time.perf_counter() - t)
                res["plain"].append(dict(_measure(plain, analyzer), genre=g, seed=s))
                t = time.perf_counter()
                mls = build_spec(g, seed=s, learn=False, ml={"n": n, "dir": str(d)})
                times["ml"].append(time.perf_counter() - t)
                info = dict(hooks.LAST_INFO)
                res["ml"].append(dict(_measure(mls, analyzer), genre=g, seed=s))
                picks.append(info.get("chosen", {}).get("origin") if info.get("used") else "fallback")
                t = time.perf_counter()
                crs = build_spec(g, seed=s, learn=False, ml={"n": n, "dir": str(d), "scorer": "critic"})
                times["critic"].append(time.perf_counter() - t)
                cinfo = dict(hooks.LAST_INFO)
                res["critic"].append(dict(_measure(crs, analyzer), genre=g, seed=s))
                # random pick among the critic run's candidate list (same candidate generator as ml)
                cands = cinfo.get("candidates") or []
                if cands:
                    c = rng.choice(cands)
                    res["random"].append({"critic": c["score"], "genre": g, "seed": s})
            log(f"eval {g}: plain {st.mean(r['critic'] for r in res['plain'] if r['genre'] == g):.2f} "
                f"ml {st.mean(r['critic'] for r in res['ml'] if r['genre'] == g):.2f}")
    finally:
        _restore_env(old)
    summary = {k: _agg(v) for k, v in res.items() if v}
    for k, v in times.items():
        if v:
            summary[k]["sec_per_song"] = round(st.mean(v), 4)
    from collections import Counter
    summary["ml_pick_origin"] = dict(Counter(picks))
    diffs = [a["critic"] - b["critic"] for a, b in zip(res["ml"], res["plain"])]
    summary["ml_minus_plain_critic"] = {"mean": round(st.mean(diffs), 3), "wins": sum(x > 0 for x in diffs),
                                        "ties": sum(x == 0 for x in diffs), "losses": sum(x < 0 for x in diffs)}
    summary["setup"] = {"genres": len(genres), "seeds": seeds_l, "n": n, "artifacts": str(d)}
    rv = _review_check(d)
    if rv:
        summary["review_md"] = rv
    out = {"summary": summary, "rows": res}
    p = Path(json_out) if json_out else d / "eval.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    summary["written"] = str(p)
    return out
