"""Opt-in integration of trained artifacts into ``build_spec``.

Enabled with ``build_spec(..., ml=True)`` / ``ml="reward,progression,n=8"`` / ``ml={...}``, the env var
``MIDIGEN_ML=1`` (or an options string), or ``midigen new --ml``. Never on by default.

What it does (each part only if its artifact exists in ``artifact_dir()``):

* **progression**: some candidates get their section progressions resampled from the trained chord n-gram
  (same length per section, conditioned on genre/mode/section; the section's wanted cadence is kept when a
  sample realises it, otherwise the cadence field is set to what the new chords actually do).
* **scorer=critic**: rank candidates by the rule-based critic instead of the reward model (reference / no model).
* **reward**: N candidates (the plain spec for the requested seed + N-1 derived seeds / resampled progressions)
  are scored by the reward model and the best is returned. Without a reward model the critic score is used.

With no artifacts at all the plain theory-engine spec is returned (a warning is emitted once), so enabling the flag
can never break generation. Details of the last call are in ``LAST_INFO``.
"""
from __future__ import annotations

import copy
import random
import warnings

from .paths import artifact_dir

LAST_INFO: dict = {}
_WARNED: set = set()
_CACHE: dict = {}


def parse_options(opt) -> dict:
    o = {"reward": True, "progression": True, "n": 6, "temperature": 0.9, "resample_frac": 0.5, "dir": None}
    if isinstance(opt, dict):
        o.update(opt)
        return o
    if opt in (None, True) or str(opt).strip().lower() in ("1", "true", "yes", "on", "all", ""):
        return o
    parts = [p.strip() for p in str(opt).split(",") if p.strip()]
    flags = {p for p in parts if "=" not in p}
    if flags:
        o["reward"] = "reward" in flags or "best" in flags
        o["progression"] = "progression" in flags or "chords" in flags
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            k = k.strip()
            o[k] = v if k in ("dir", "scorer") else float(v) if "." in v else int(v)
    return o


def _load(kind: str, d):
    key = (kind, str(d))
    if key in _CACHE:
        return _CACHE[key]
    m = None
    try:
        if kind == "reward":
            from . import reward
            m = reward.load(d)
        else:
            from . import seqmodel
            p = d / "chords.json.gz"
            m = seqmodel.load(p) if p.exists() else None
    except Exception as e:  # corrupt / incompatible artifact -> fall back
        _warn(f"midigen.ml: could not load {kind} artifact from {d}: {e}")
        m = None
    _CACHE[key] = m
    return m


def clear_cache():
    _CACHE.clear()


def _warn(msg):
    if msg not in _WARNED:
        _WARNED.add(msg)
        warnings.warn(msg, RuntimeWarning, stacklevel=3)


def resample_progressions(spec: dict, model, rng: random.Random, temperature: float = 0.9) -> dict:
    from ..harmony import detect_cadence
    from ..theory import is_valid_chord
    from .tokens import section_base
    out = copy.deepcopy(spec)
    key, mode, genre = out["key"], out["mode"], out.get("genre") or ""

    def valid(sym):
        return is_valid_chord(sym, key, mode)

    for sec in out["sections"]:
        n = len(sec["chords"])
        want = sec.get("cadence", "none")
        best = None
        for _ in range(6):
            ch = model.sample_progression(genre, mode, section_base(sec["name"]), n, rng, temperature, valid)
            if not ch:
                continue
            best = ch
            if want == "none" or detect_cadence(ch, key, mode) == want:
                break
        if best:
            sec["chords"] = best
            got = detect_cadence(best, key, mode)
            if want != "none" and got != want:
                sec["cadence"] = got
    return out


def apply_ml(args: dict, options=None) -> dict:
    from ..arranger import render
    from ..genres import build_spec
    from ..spec import check
    o = parse_options(options)
    d = artifact_dir() if not o.get("dir") else __import__("pathlib").Path(o["dir"])
    rm = _load("reward", d) if o["reward"] and o.get("scorer") != "critic" else None
    cm = _load("chords", d) if o["progression"] else None
    plain = dict(args, ml=False)
    if rm is None and cm is None and o.get("scorer") != "critic":
        _warn(f"midigen.ml: no trained artifacts in {d}; using the plain theory engine "
              "(train with `midigen train reward` / `midigen train progression`)")
        LAST_INFO.clear()
        LAST_INFO.update({"used": False, "reason": "no artifacts", "dir": str(d)})
        return build_spec(**plain)
    from ..core import stable_seed
    seed = int(args.get("seed") or 1)
    n = max(1, int(o["n"]))
    rng = random.Random(stable_seed("ml-prog", args.get("genre"), seed))
    cands = []
    for k in range(n):
        s = seed if k == 0 else stable_seed("ml-cand", seed, k) % 2 ** 31
        try:
            sp = build_spec(**dict(plain, seed=s))
        except Exception:
            continue
        origin = "plain"
        if cm is not None and k > 0 and (rm is None or rng.random() < float(o["resample_frac"]) or k == 1):
            try:
                sp = check(resample_progressions(sp, cm, rng, float(o["temperature"])))
                origin = "ngram-progression"
            except Exception:
                pass
        cands.append((k, s, origin, sp))
    scored = []
    for k, s, origin, sp in cands:
        try:
            perf = render(sp)
            if rm is not None:
                val = rm.score_spec(sp, perf)
            else:
                from ..critic import critique
                val = float(critique(perf)["score"])
        except Exception:
            continue
        scored.append((val, -k, k, s, origin, sp))
    if not scored:
        LAST_INFO.clear()
        LAST_INFO.update({"used": False, "reason": "no valid candidates"})
        return build_spec(**plain)
    best = max(scored)
    LAST_INFO.clear()
    LAST_INFO.update({"used": True, "scorer": "reward_model" if rm is not None else "critic",
                      "reward_mode": getattr(rm, "mode", None), "progression_model": cm is not None,
                      "candidates": [{"k": k, "seed": s, "origin": org, "score": round(v, 2)}
                                     for v, _, k, s, org, _ in sorted(scored, key=lambda t: t[2])],
                      "chosen": {"k": best[2], "seed": best[3], "origin": best[4]}})
    return best[5]
