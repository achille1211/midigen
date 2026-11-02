"""Synthetic dataset builder.

Every example is produced by midigen itself: ``build_spec(genre, seed=..)`` for every available genre and a range
of seeds, plus a few seeded edit-op variants per (genre, seed) so critic scores vary. Each row records the exact
build arguments and ops, so any example can be regenerated bit-for-bit with the same midigen version and genre packs.

Outputs (under ``<data>/synthetic/``):

    songs.jsonl     one row per song: build args, ops, spec, critic score + findings, features, compact tokens, split
    sections.jsonl  one row per section: roman chords, energy, cadence wanted/detected, critic findings in it
    tracks.jsonl    one row per track: role, program, generator, critic stats, findings, ok label
    drums.jsonl     one row per song with drums: per-bar 16th-step drum-grid tokens
    remi.jsonl      (optional, --remi) REMI-like multitrack event tokens per song
    manifest.json   build parameters, versions, genre sources, counts, wall time

Split: by group = (genre, seed) via crc32 -> 80/10/10. All variants of a group land in the same split, so no
song in val/test shares a seed (and hence harmony/motifs) with a training song of the same genre.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .paths import data_dir

SPLITS = (("train", 80), ("val", 90), ("test", 100))
REHARM = ["secondary_dominants", "borrowed", "7ths", "9ths", "triads", "functional"]


def split_of(group: str) -> str:
    b = zlib.crc32(group.encode()) % 100
    return next(name for name, hi in SPLITS if b < hi)


def group_of(genre: str, seed: int) -> str:
    return f"{genre}|{seed}"


def _random_ops(spec: dict, rng: random.Random) -> list[dict]:
    secs = [s["name"] for s in spec["sections"]]
    tracks = [t for t in spec["tracks"]]
    ops = []
    for _ in range(rng.randint(1, 3)):
        kind = rng.choice(["reharm", "density", "humanize", "reseed", "cadence", "swing", "tempo", "add", "drop"])
        if kind == "reharm":
            ops.append({"op": "reharmonize", "technique": rng.choice(REHARM), "section": rng.choice(secs),
                        "seed": rng.randrange(1000)})
        elif kind == "density" and tracks:
            ops.append({"op": "set_density", "track": rng.choice(tracks)["name"], "density": round(rng.uniform(0.1, 1.0), 2)})
        elif kind == "humanize" and tracks:
            ops.append({"op": "humanize", "track": rng.choice(tracks)["name"], "timing": round(rng.uniform(0, 0.04), 3),
                        "velocity": rng.randint(0, 20)})
        elif kind == "reseed" and tracks:
            ops.append({"op": "reseed", "track": rng.choice(tracks)["name"], "seed": rng.randrange(10 ** 6)})
        elif kind == "cadence":
            ops.append({"op": "set_cadence", "section": rng.choice(secs),
                        "cadence": rng.choice(["authentic", "half", "plagal", "deceptive"])})
        elif kind == "swing":
            ops.append({"op": "set_swing", "amount": round(rng.choice([0, 0.25, 0.5, 0.7]), 2)})
        elif kind == "tempo":
            ops.append({"op": "change_tempo", "delta": rng.choice([-20, -10, 10, 20])})
        elif kind == "add":
            ops.append({"op": "add_instrument", "role": rng.choice(["pad", "arp", "lead", "chords"])})
        elif kind == "drop" and len(tracks) > 2:
            ops.append({"op": "remove_track", "track": rng.choice(tracks[1:])["name"]})
    return ops


def group_params(genre: str, seed: int) -> dict:
    """Per-(genre, seed) structural choices (shared by all variants of the group)."""
    from ..genres import get_genre
    g = get_genre(genre)
    rng = random.Random(zlib.crc32(f"group|{genre}|{seed}".encode()))
    return {"genre": genre, "seed": seed, "mode": rng.choice(g["modes"]),
            "bars": rng.choice([None, None, 8, 16, 32])}


ANALYZE = os.environ.get("MIDIGEN_DATASET_ANALYZE", "1") != "0"


def make_example(genre: str, seed: int, variant: int, remi: bool = False) -> dict | None:
    """Build one song and all its labels. Pure function of (genre, seed, variant) + midigen version + packs."""
    from ..arranger import render
    from ..critic import critique
    from ..genres import build_spec, get_genre
    from ..harmony import detect_cadence
    from ..ops import OpError, apply_ops
    from ..spec import total_bars
    from .features import spec_features
    from .tokens import (chord_tokens, compact_song_tokens, drum_grid_tokens, encode_score, roman_chords,
                         score_from_performance, section_base)
    gp = group_params(genre, seed)
    base = build_spec(genre, mode=gp["mode"], bars=gp["bars"], seed=seed, **_plain_kwargs())
    ops: list[dict] = []
    spec = base
    if variant:
        rng = random.Random(zlib.crc32(f"variant|{genre}|{seed}|{variant}".encode()))
        for _ in range(4):
            ops = _random_ops(base, rng)
            try:
                spec = apply_ops(base, ops)
                break
            except (OpError, ValueError, KeyError):
                ops, spec = [], None
        if spec is None or not ops:
            return None
    t0 = time.perf_counter()
    perf = render(spec)
    rep = critique(perf)
    group = group_of(genre, seed)
    sid = f"{genre}-s{seed}-v{variant}"
    split = split_of(group)
    bb = perf.bar_beats
    feats = spec_features(spec, perf)
    findings = [{k: f[k] for k in ("check", "severity", "track", "section", "bars") if k in f} for f in rep["findings"]]
    sources = get_genre(genre).get("_sources", ["builtin"])
    song = {"id": sid, "group": group, "split": split, "genre": genre, "seed": seed, "variant": variant,
            "build_args": {"genre": genre, "mode": gp["mode"], "bars": gp["bars"], "seed": seed}, "ops": ops,
            "genre_sources": [s if s == "builtin" else Path(s).parent.name + "/" + Path(s).name for s in sources],
            "key": spec["key"], "mode": spec["mode"], "tempo": spec["tempo"], "bars": total_bars(spec),
            "n_tracks": len(spec["tracks"]), "critic_score": rep["score"],
            "n_warnings": sum(1 for f in findings if f["severity"] in ("warning", "error")),
            "findings": findings, "features": {k: round(v, 5) for k, v in feats.items()},
            "tokens": compact_song_tokens(spec), "spec": spec,
            "label": "good" if rep["score"] >= 90 else "ok" if rep["score"] >= 75 else "poor"}
    if ANALYZE:
        from .critic_bridge import analyze_labels
        song["analyze"] = analyze_labels(spec)  # independent critic (tools/analyze.py); {} if genre unsupported
    # sections: findings that name the section or fall in its bars
    sec_rows = []
    spans = {}
    for s in perf.sections:
        spans.setdefault(s.name, []).append((int(s.start // bb) + 1, int(s.end // bb)))
    for sec, ct in zip(spec["sections"], chord_tokens(spec)):
        rng_bars = spans.get(sec["name"], [])
        in_sec = [f for f in findings if f.get("section") == sec["name"] or
                  any(lo <= b <= hi for b in f.get("bars", []) for lo, hi in rng_bars)]
        want = sec.get("cadence", "none")
        got = detect_cadence(sec["chords"], spec["key"], spec["mode"])
        sec_rows.append({"song_id": sid, "group": group, "split": split, "genre": genre, "mode": spec["mode"],
                         "name": sec["name"], "section": section_base(sec["name"]), "bars": sec["bars"],
                         "energy": ct["energy"], "chords": roman_chords(sec["chords"], spec["key"], spec["mode"]),
                         "chords_raw": sec["chords"], "cadence_wanted": want, "cadence_detected": got,
                         "cadence_ok": want in ("none", got) or (want == "authentic" and got == "plagal"),
                         "n_findings": len(in_sec),
                         "n_warnings": sum(1 for f in in_sec if f["severity"] in ("warning", "error")),
                         "variant": variant})
    trk_rows = []
    for t in spec["tracks"]:
        st = rep["stats"].get(t["name"], {})
        tf = [f for f in findings if f.get("track") == t["name"]]
        g = t["generator"]
        trk_rows.append({"song_id": sid, "group": group, "split": split, "genre": genre, "name": t["name"],
                         "role": t["role"], "program": t["program"], "generator": g.get("type"),
                         "style": g.get("style") or g.get("pattern"), "density": g.get("density"), "stats": st,
                         "findings": [f["check"] + ":" + f["severity"] for f in tf],
                         "ok": not any(f["severity"] in ("warning", "error") for f in tf), "variant": variant})
    score = score_from_performance(perf)
    grid = drum_grid_tokens(score)
    out = {"song": song, "sections": sec_rows, "tracks": trk_rows,
           "drums": {"song_id": sid, "group": group, "split": split, "genre": genre, "variant": variant,
                     "steps_per_bar": len(grid[0]) if grid else 0, "bars": grid} if grid else None,
           "seconds": time.perf_counter() - t0}
    if remi:
        out["remi"] = {"song_id": sid, "group": group, "split": split, "genre": genre, "variant": variant,
                       "tokens": " ".join(encode_score(score))}
    return out


def _plain_kwargs() -> dict:
    """Keyword args that force the plain theory engine (no ML hooks, no learned bandit policy if present)."""
    import inspect
    from ..genres import build_spec
    params = inspect.signature(build_spec).parameters
    return {k: False for k in ("ml", "learn") if k in params}


_ISO = {"MIDIGEN_LEARN": "0", "MIDIGEN_NO_RECORD": "1", "MIDIGEN_ML": "0"}


def _isolate_env() -> dict:
    """Dataset builds must not read or write the user's experience store. Returns the previous values."""
    import os
    old = {k: os.environ.get(k) for k in _ISO}
    os.environ.update(_ISO)
    return old


def _restore_env(old: dict) -> None:
    import os
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _job(args):
    _isolate_env()
    genre, seed, variants, remi = args
    rows = []
    for v in range(variants):
        try:
            r = make_example(genre, seed, v, remi)
        except Exception as e:  # record, never silently drop
            r = {"error": f"{genre}-s{seed}-v{v}: {type(e).__name__}: {e}"}
        if r:
            rows.append(r)
    return rows


def _analyzer_info() -> dict:
    try:
        from .critic_bridge import analyzer_info
        return analyzer_info()
    except Exception as e:  # never fail a build over provenance
        return {"available": False, "error": str(e)}


def _git_rev() -> str | None:
    try:
        return subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, timeout=5).stdout.strip() or None
    except Exception:
        return None


def build_synthetic(out: str | Path | None = None, genres: list[str] | None = None, seeds: int | list[int] = 30,
                    variants: int = 3, remi: bool = False, workers: int = 0, log=print) -> dict:
    """Build the synthetic dataset. Returns the manifest dict."""
    old_env = _isolate_env()
    try:
        return _build(out, genres, seeds, variants, remi, workers, log)
    finally:
        _restore_env(old_env)


def _build(out, genres, seeds, variants, remi, workers, log) -> dict:
    from .. import __version__
    from ..genres import available_genres
    out = Path(out) if out else data_dir() / "synthetic"
    out.mkdir(parents=True, exist_ok=True)
    genres = genres or available_genres()
    seed_list = list(range(1, seeds + 1)) if isinstance(seeds, int) else list(seeds)
    jobs = [(g, s, variants, remi) for g in genres for s in seed_list]
    t0 = time.time()
    files = {k: open(out / f"{k}.jsonl", "w") for k in ("songs", "sections", "tracks", "drums")}
    if remi:
        files["remi"] = open(out / "remi.jsonl", "w")
    elif (out / "remi.jsonl").exists():
        (out / "remi.jsonl").unlink()
    counts = {"songs": 0, "sections": 0, "tracks": 0, "drums": 0, "remi": 0}
    by_split, by_genre, errors, sources = {}, {}, [], {}
    scores, aq = [], []
    workers = workers or min(8, max(1, (__import__("os").cpu_count() or 2)))

    def consume(rows):
        for r in rows:
            if "error" in r:
                errors.append(r["error"])
                continue
            s = r["song"]
            files["songs"].write(json.dumps(s, separators=(",", ":")) + "\n")
            counts["songs"] += 1
            by_split[s["split"]] = by_split.get(s["split"], 0) + 1
            by_genre[s["genre"]] = by_genre.get(s["genre"], 0) + 1
            sources[s["genre"]] = s["genre_sources"]
            scores.append(s["critic_score"])
            if s.get("analyze", {}).get("analyze_quality") is not None:
                aq.append(s["analyze"]["analyze_quality"])
            for k in ("sections", "tracks"):
                for x in r[k]:
                    files[k].write(json.dumps(x, separators=(",", ":")) + "\n")
                    counts[k] += 1
            if r.get("drums"):
                files["drums"].write(json.dumps(r["drums"], separators=(",", ":")) + "\n")
                counts["drums"] += 1
            if remi and r.get("remi"):
                files["remi"].write(json.dumps(r["remi"], separators=(",", ":")) + "\n")
                counts["remi"] += 1

    done = 0
    if workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            for rows in ex.map(_job, jobs, chunksize=4):
                consume(rows)
                done += 1
                if done % 100 == 0:
                    log(f"  {done}/{len(jobs)} groups")
    else:
        for j in jobs:
            consume(_job(j))
    for f in files.values():
        f.close()
    groups_by_split = {}
    for g in genres:
        for s in seed_list:
            sp = split_of(group_of(g, s))
            groups_by_split[sp] = groups_by_split.get(sp, 0) + 1
    manifest = {
        "kind": "midigen-synthetic", "midigen_version": __version__, "git_rev": _git_rev(),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "wall_seconds": round(time.time() - t0, 1),
        "params": {"genres": genres, "seeds": [seed_list[0], seed_list[-1]] if seed_list == list(range(seed_list[0], seed_list[-1] + 1)) else seed_list,
                   "variants": variants, "remi": remi},
        "split_rule": "crc32('genre|seed') % 100: <80 train, <90 val, else test (variants share their group's split)",
        "counts": counts, "songs_by_split": by_split, "groups_by_split": groups_by_split, "songs_by_genre": by_genre,
        "analyze_quality": {"songs_labelled": len(aq), "mean": round(sum(aq) / max(1, len(aq)), 2),
                            "min": min(aq, default=None), "max": max(aq, default=None),
                            "source": "tools/analyze.py (independent critic); genres it maps only",
                            "analyzer": _analyzer_info()},
        "critic_score": {"mean": round(sum(scores) / max(1, len(scores)), 2), "min": min(scores, default=None),
                         "max": max(scores, default=None)},
        "genre_sources": sources, "errors": errors[:50], "n_errors": len(errors),
        "license": "Generated by midigen from its own genre profiles; no third-party recordings or MIDI files.",
        "bytes": {p.name: p.stat().st_size for p in out.glob("*.jsonl")},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def iter_jsonl(path, split: str | None = None):
    path = Path(path)
    if not path.exists():
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if split is None or r.get("split") == split:
                yield r
