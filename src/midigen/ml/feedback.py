"""User feedback / ratings loader.

User ratings live in the experience store of ``midigen.learn`` (SQLite, ``songs.user_reward`` 0..1 computed from
ratings/thumbs/keep/reject feedback), which is developed alongside this package. This module does not duplicate it:
it is a thin, tolerant adapter that

1. reads ``midigen.learn.store().songs()`` rows that have a ``user_reward`` (if ``midigen.learn`` is installed), and
2. additionally (or additionally) reads JSONL files: ``$MIDIGEN_FEEDBACK`` (file or folder, ``:``-separated) or
   ``<data>/feedback/*.jsonl``.

Accepted record shape (extra keys ignored; first matching key wins)::

    {"id": "...", "ts": "2026-11-01T23:00:00Z",
     "spec": {...full spec...}            | "spec_path": "song.json"   | "candidate": {"spec": {...}},
     "rating": 4, "scale": [1, 5]         | "reward": 0.8 (0..1)       | "score": 80 (0..100) | "thumbs": "up"/"down",
     "user": "...", "source": "cli|ui|mcp", "notes": "..."}

Ratings are normalised to 0..100. Records without a usable spec or rating are skipped and counted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .paths import data_dir


def _norm_rating(r: dict) -> float | None:
    if r.get("thumbs") is not None or r.get("liked") is not None:
        v = r.get("thumbs", r.get("liked"))
        return 100.0 if v in (True, "up", 1, "like", "yes") else 0.0
    for k in ("rating", "stars"):
        if r.get(k) is not None:
            lo, hi = r.get("scale") or ([1, 5] if float(r[k]) > 1 else [0, 1])
            return max(0.0, min(100.0, (float(r[k]) - lo) / max(1e-9, hi - lo) * 100))
    for k in ("reward", "value"):
        if r.get(k) is not None:
            v = float(r[k])
            return max(0.0, min(100.0, v * 100 if -1e-9 <= v <= 1 + 1e-9 else v))
    if r.get("score") is not None and r.get("score_kind", "user") == "user":
        return max(0.0, min(100.0, float(r["score"])))
    return None


def _spec_of(r: dict, base: Path | None):
    for k in ("spec", "song", "final_spec"):
        if isinstance(r.get(k), dict) and r[k].get("sections"):
            return r[k]
    if isinstance(r.get("candidate"), dict) and isinstance(r["candidate"].get("spec"), dict):
        return r["candidate"]["spec"]
    p = r.get("spec_path")
    if p:
        p = Path(p) if Path(p).is_absolute() or base is None else base / p
        if p.exists():
            return json.loads(p.read_text())
    return None


def _files() -> list[Path]:
    env = os.environ.get("MIDIGEN_FEEDBACK")
    roots = [Path(x) for x in env.split(os.pathsep) if x] if env else [data_dir() / "feedback"]
    out = []
    for r in roots:
        if r.is_file():
            out.append(r)
        elif r.is_dir():
            out += sorted(r.glob("*.jsonl"))
    return out


def _store_records() -> list[dict]:
    try:
        import importlib
        mod = importlib.import_module("midigen.learn")
    except Exception:
        return []
    if callable(getattr(mod, "store", None)):
        try:
            rows = mod.store().songs(limit=10 ** 7)
            return [{"id": r["id"], "spec": r["spec"], "reward": r["user_reward"], "ts": r.get("ts"),
                     "source_kind": r.get("source")} for r in rows if r and r.get("user_reward") is not None]
        except Exception:
            return []
    for fn in ("iter_records", "load_records", "records"):
        if callable(getattr(mod, fn, None)):
            try:
                return [dict(x) for x in getattr(mod, fn)()]
            except Exception:
                return []
    store_cls = getattr(mod, "ExperienceStore", None)
    if store_cls is not None:
        try:
            st = store_cls()
            recs = st.records() if callable(getattr(st, "records", None)) else []
            return [dict(x) if not hasattr(x, "__dict__") else dict(vars(x)) for x in recs]
        except Exception:
            return []
    return []


def load_feedback() -> tuple[list[dict], dict]:
    """Returns (records with 'spec' and 'rating' in 0..100, stats)."""
    from ..spec import check
    raw: list[tuple[dict, Path | None, str]] = [(r, None, "midigen.learn experience store") for r in _store_records()]
    for f in _files():
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        raw.append((json.loads(line), f.parent, str(f)))
                    except ValueError:
                        raw.append(({}, None, str(f)))
    out, skipped = [], 0
    for r, base, src in raw:
        rating = _norm_rating(r)
        spec = _spec_of(r, base)
        if rating is None or spec is None:
            skipped += 1
            continue
        try:
            spec = check(spec)
        except Exception:
            skipped += 1
            continue
        out.append({"id": r.get("id") or f"fb{len(out)}", "spec": spec, "rating": rating, "source": src,
                    "user": r.get("user"), "ts": r.get("ts")})
    return out, {"records": len(out), "skipped": skipped, "files": [str(f) for f in _files()]}
