"""Reuse of the independent critic's work instead of re-implementing it.

* ``analyze_features(spec)`` runs ``tools/analyze.py`` (the critic's analyzer: in-key %, chord-tone %, strong clashes,
  kick/bass lock, voice leading, ...) on a spec rendered to a temporary .mid and returns its numeric top-level
  metrics prefixed ``an_``. It returns ``{}`` when the analyzer, its genre files (``midigen-genres``) or the genre
  mapping are unavailable, so callers must treat it as optional.
* ``review_scores()`` parses the per-song human-style 0-10 scores table in ``midigen-critic/REVIEW.md``
  (28 songs) together with the reviewed specs in ``midigen-critic/samples``. ``evaluate`` uses it as a tiny external
  sanity label for the reward model. It is NOT training data.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

from .paths import REPO

_AN = None


def _analyzer():
    global _AN
    if _AN is not None:
        return _AN or None
    cands = [os.environ.get("MIDIGEN_ANALYZE"), REPO / "tools" / "analyze.py", Path("/workspace/midigen-critic/analyze.py")]
    for c in cands:
        if c and Path(c).is_file():
            try:
                spec = importlib.util.spec_from_file_location("_midigen_critic_analyze", str(c))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if Path(mod.GENRES_DIR).is_dir():
                    _AN = mod
                    return mod
            except Exception:
                continue
    _AN = False
    return None


def analyzer_info() -> dict:
    """Which analyzer produced the ``analyze`` labels: path, sha256 of the file, and its genre count. The analyzer
    may change (output keys come and go); labels only use keys that are present, so record this in the dataset card."""
    import hashlib
    an = _analyzer()
    if an is None:
        return {"available": False}
    f = Path(an.__file__)
    return {"available": True, "path": str(f), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()[:16],
            "version": getattr(an, "__version__", None), "genres_mapped": len(getattr(an, "GENRE_FILES", {}) or {})}


def _flat(d: dict, prefix: str, out: dict, depth: int = 0) -> None:
    for k, v in d.items():
        if isinstance(v, bool):
            out[f"{prefix}{k}"] = float(v)
        elif isinstance(v, (int, float)):
            out[f"{prefix}{k}"] = float(v)
        elif isinstance(v, dict) and depth < 1:
            _flat(v, f"{prefix}{k}_", out, depth + 1)


def analyze_features(spec: dict) -> dict:
    an = _analyzer()
    g = str(spec.get("genre") or "").split("@")[0]
    if an is None or g not in getattr(an, "GENRE_FILES", {}):
        return {}
    from ..arranger import render
    from ..midi_io import export_midi
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "song")
        s = dict(spec, genre=g)
        Path(base + ".json").write_text(json.dumps(s))
        try:
            export_midi(render(s), base + ".mid")
            res = an.analyze(base)
        except Exception:
            return {}
    out: dict = {}
    if isinstance(res, dict):
        _flat(res, "an_", out)
    return out


def critic_root() -> Path | None:
    for c in (os.environ.get("MIDIGEN_CRITIC_ROOT"), REPO.parent / "midigen-critic", Path("/workspace/midigen-critic")):
        if c and (Path(c) / "REVIEW.md").is_file():
            return Path(c)
    return None


def review_scores() -> list[dict]:
    """[{song, avg, scores{...}, spec_path}] from REVIEW.md's table; [] if not available."""
    root = critic_root()
    if root is None:
        return []
    lines = (root / "REVIEW.md").read_text(encoding="utf-8").splitlines()
    head, rows = None, []
    for ln in lines:
        cells = [c.strip().strip("*") for c in ln.strip().strip("|").split("|")]
        if cells and cells[0] == "Song" and "Avg" in cells:
            head = cells
            continue
        if head and len(cells) == len(head) and re.fullmatch(r"[a-z_]+_s\d+", cells[0]):
            try:
                sc = {h: float(c) for h, c in zip(head[1:], cells[1:])}
            except ValueError:
                continue
            p = root / "samples" / f"{cells[0]}.json"
            rows.append({"song": cells[0], "avg": sc.pop("Avg"), "scores": sc, "spec_path": str(p) if p.is_file() else None})
        elif head and rows and not ln.strip().startswith("|"):
            head = None
    return rows


LABEL_KEYS = ("an_in_key_pct", "an_strong_clash_pct", "an_kick_with_bass_pct", "an_bass_on_kick_pct",
              "an_bass_root_on_change_pct", "an_melody_strong_beat_chord_tone_pct", "an_melody_phrase_end_chord_tone_pct",
              "an_melody_repeated_bar_pct", "an_melody_step_pct", "an_timing_dev_ms_mean")


def analyze_labels(spec: dict) -> dict:
    """Selected ``tools/analyze.py`` metrics plus ``analyze_quality``, a 0-100 composite of the harmony / bass /
    melody checks that REVIEW.md scores (in-key %, mean chord-tone %, strong-beat melody chord tones, bass root on
    chord change, 100 - 10 x strong-clash %). Returns ``{}`` if the analyzer can't handle this genre."""
    f = analyze_features(spec)
    if not f:
        return {}
    out = {k[3:]: round(f[k], 3) for k in LABEL_KEYS if k in f}
    ct = [v for k, v in f.items() if k.startswith("an_chord_tone_pct_")]
    if ct:
        out["chord_tone_pct_mean"] = round(sum(ct) / len(ct), 3)
    parts = [f.get("an_in_key_pct"), out.get("chord_tone_pct_mean"), f.get("an_melody_strong_beat_chord_tone_pct"),
             f.get("an_bass_root_on_change_pct"),
             None if "an_strong_clash_pct" not in f else max(0.0, 100 - 10 * f["an_strong_clash_pct"])]
    parts = [p for p in parts if p is not None]
    if parts:
        out["analyze_quality"] = round(sum(parts) / len(parts), 3)
    return out
