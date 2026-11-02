"""Numeric features of a song for the reward model.

``spec_features`` needs only the spec and its render (no critic). ``critic_features`` adds the critic's score and
finding counts; it is used only when the training target is *not* the critic score itself (i.e. when real user
ratings are available), so the model cannot trivially copy its own label.
"""
from __future__ import annotations

from collections import Counter

from ..theory import parse_chord, scale_pcs

ROLES = ("drums", "bass", "chords", "pad", "arp", "lead")
CHECKS = ("out_of_key", "clash", "parallels", "outer_parallels", "register", "range", "density", "interlock", "melody",
          "form", "cadence")


def _chord_stats(spec: dict) -> dict:
    sc = set(scale_pcs(spec["key"], spec["mode"]))
    syms = [c for s in spec["sections"] for c in s["chords"]]
    n_out, ext, sizes = 0, 0, []
    for c in syms:
        try:
            ch = parse_chord(c, spec["key"], spec["mode"])
        except Exception:
            n_out += 1
            continue
        pcs = set(ch.pcs)
        sizes.append(len(pcs))
        if not pcs <= sc:
            n_out += 1
        if len(pcs) > 3:
            ext += 1
    n = max(1, len(syms))
    return {"chords_total": len(syms), "chords_unique": len(set(syms)), "chord_nondiatonic_ratio": n_out / n,
            "chord_extended_ratio": ext / n, "chord_mean_size": sum(sizes) / max(1, len(sizes))}


def perf_stats(perf) -> dict:
    """Musical sanity metrics of a render: pitch-in-key ratio, density, register, drum share."""
    spec = perf.spec
    sc = set(scale_pcs(spec["key"], spec["mode"]))
    bars = max(1.0, perf.total_beats / perf.bar_beats)
    pitched = [n for t in perf.tracks if not t.is_drums for n in t.notes]
    drums = [n for t in perf.tracks if t.is_drums for n in t.notes]
    in_key = sum(1 for n in pitched if n.pitch % 12 in sc) / max(1, len(pitched))
    in_chord = sum(1 for n in pitched if n.pitch % 12 in perf.chord_at(n.start).chord.pcs) / max(1, len(pitched))
    out = {"pitch_in_key_ratio": in_key, "pitch_in_chord_ratio": in_chord,
           "notes_per_bar": (len(pitched) + len(drums)) / bars, "pitched_notes_per_bar": len(pitched) / bars,
           "drum_notes_per_bar": len(drums) / bars, "n_notes": len(pitched) + len(drums)}
    if pitched:
        ps = sorted(n.pitch for n in pitched)
        out.update({"pitch_low": ps[0], "pitch_high": ps[-1], "pitch_median": ps[len(ps) // 2],
                    "vel_mean": sum(n.vel for n in pitched) / len(pitched)})
    return out


def mix_features(spec: dict) -> dict:
    """Numeric features of the optional ``mix`` block (added by the audio side; its schema may evolve).

    Absent/invalid ``mix`` -> ``{"has_mix": 0}``. Otherwise every numeric/bool scalar up to two levels deep becomes
    ``mix_<path>`` (e.g. ``mix_master_lufs``, ``mix_tracks_bass_gain_db``), lists of dicts are keyed by their
    ``role``/``name``. Unknown keys are harmless: a trained model only reads the feature names it was fitted on.
    """
    mix = spec.get("mix") if isinstance(spec, dict) else None
    if not isinstance(mix, dict):
        return {"has_mix": 0.0}
    out = {"has_mix": 1.0}

    def walk(v, path, depth):
        if isinstance(v, bool):
            out[path] = float(v)
        elif isinstance(v, (int, float)):
            out[path] = float(v)
        elif depth >= 3:
            return
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{path}_{k}", depth + 1)
        elif isinstance(v, list):
            for i, x in enumerate(v[:32]):
                name = x.get("role") or x.get("name") or i if isinstance(x, dict) else i
                walk(x, f"{path}_{name}", depth + 1)
    walk(mix, "mix", 0)
    return out


def spec_features(spec: dict, perf=None) -> dict:
    from ..arranger import render
    from ..spec import total_bars
    if perf is None:
        perf = render({k: v for k, v in spec.items() if k != "mix"} if "mix" in spec else spec)
    f: dict[str, float] = mix_features(spec)
    f["genre_" + str(spec.get("genre") or "unknown").split("@")[0]] = 1.0
    f["tempo"] = float(spec["tempo"])
    f["bars"] = float(total_bars(spec))
    f["n_sections"] = float(len(spec["sections"]))
    f["n_tracks"] = float(len(spec["tracks"]))
    f["swing"] = float(spec.get("swing", 0))
    f["mode_minorish"] = float(spec["mode"] in ("minor", "aeolian", "dorian", "phrygian", "harmonic_minor", "locrian"))
    en = [float(s.get("energy", .5)) for s in spec["sections"]]
    f["energy_range"] = max(en) - min(en)
    f["energy_mean"] = sum(en) / len(en)
    f.update({k: float(v) for k, v in _chord_stats(spec).items()})
    roles = Counter(t["role"] for t in spec["tracks"])
    for r in ROLES:
        f[f"has_{r}"] = float(roles.get(r, 0))
    for t in spec["tracks"]:
        g = t["generator"]
        if "density" in g:
            f[f"density_{t['role']}"] = float(g["density"])
        if g.get("humanize"):
            f["humanized"] = 1.0
    ps = perf_stats(perf)
    f.update({k: float(v) for k, v in ps.items()})
    bars = max(1.0, perf.total_beats / perf.bar_beats)
    for t in perf.tracks:
        r = "drums" if t.is_drums else t.role
        if r in ROLES:
            f[f"npb_{r}"] = f.get(f"npb_{r}", 0.0) + len(t.notes) / bars
            if t.notes and not t.is_drums:
                f[f"median_{r}"] = float(sorted(n.pitch for n in t.notes)[len(t.notes) // 2])
    return f


def critic_features(report: dict) -> dict:
    f = {"critic_score": float(report["score"])}
    cnt = Counter((x["check"], x["severity"]) for x in report["findings"])
    for c in CHECKS:
        f[f"crit_{c}_warn"] = float(cnt.get((c, "warning"), 0) + cnt.get((c, "error"), 0))
        f[f"crit_{c}_info"] = float(cnt.get((c, "info"), 0))
    return f


def vectorize(rows: list[dict], names: list[str] | None = None):
    """dict rows -> (numpy matrix, feature names). Missing features are 0."""
    import numpy as np
    if names is None:
        names = sorted({k for r in rows for k in r})
    X = np.zeros((len(rows), len(names)), dtype=float)
    idx = {n: i for i, n in enumerate(names)}
    for i, r in enumerate(rows):
        for k, v in r.items():
            j = idx.get(k)
            if j is not None:
                X[i, j] = v
    return X, names
