"""Own-words knowledge packs as labelled data, with per-record provenance.

Sources (read-only; found next to the checkout, in the cwd, or in /workspace; override with MIDIGEN_KNOWLEDGE_ROOT):

* ``midigen-songwriting/`` harmony.json progressions, genres.json per-genre progressions, song_analyses.json
  per-section progressions. Written by the midigen songwriting-coach agent as original, own-words analysis
  (its README: "All text is original; no lyrics are quoted beyond a word or two"). Song analyses name real songs
  (title/artist/year as factual metadata) and give approximate chord readings; no lyrics or MIDI are ingested.
* ``midigen-knowledge/`` and ``midigen-genres/`` genre profiles (authored for midigen; already used by the generator
  through ``midigen.packs``). Only their textual descriptions are used here, for fine-tuning prompts.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .paths import REPO

PROVENANCE = {"author": "midigen songwriting coach agent", "kind": "own-words analysis",
              "license": "authored for the midigen project (internal); no third-party lyrics or MIDI"}
SCALE_MODE = {"major": "major", "ionian": "major", "natural_minor": "minor", "minor": "minor", "aeolian": "minor",
              "dorian": "dorian", "phrygian": "phrygian", "lydian": "lydian", "mixolydian": "mixolydian",
              "harmonic_minor": "harmonic_minor", "locrian": "locrian"}


def root(name: str) -> Path | None:
    env = os.environ.get("MIDIGEN_KNOWLEDGE_ROOT")
    cands = ([Path(env)] if env else []) + [REPO.parent, Path.cwd(), Path("/workspace")]
    for c in cands:
        if (c / name).is_dir():
            return c / name
    return None


def _gid(x: str) -> str | None:
    from ..genres import resolve_genre
    try:
        return resolve_genre(x)
    except Exception:
        return None


def _section(s: str) -> str:
    from .tokens import section_base
    s = s.lower().replace("pre_chorus", "prechorus").replace("pre-chorus", "prechorus")
    return section_base(s)


_NUM = re.compile(r"^[b#]?(?:VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)")


def _clean(chords) -> list[str] | None:
    out = [str(c).strip() for c in chords or [] if str(c).strip()]
    if not out or not all(_NUM.match(c) for c in out):
        return None
    return out


def progressions() -> list[dict]:
    """Labelled roman-numeral progressions from midigen-songwriting with provenance."""
    sw = root("midigen-songwriting")
    if not sw:
        return []
    rows = []

    def add(chords, genre, mode, section, weight, src, kind):
        ch = _clean(chords)
        if ch and genre and mode:
            rows.append({"genre": genre, "mode": mode, "section": section, "chords": ch, "weight": float(weight or 0.5),
                         "provenance": {"source": src, "record_kind": kind, **PROVENANCE}})

    try:
        h = json.loads((sw / "harmony.json").read_text())
        for p in h.get("progressions", []):
            mode = SCALE_MODE.get(p.get("scale", "major"))
            gens = {_gid(g) for g in p.get("appliesTo", {}).get("genres", [])} - {None}
            secs = p.get("appliesTo", {}).get("sections") or ["any"]
            for g in gens:
                for s in secs:
                    add(p["numerals"], g, mode, _section(s), p.get("weight"), f"midigen-songwriting/harmony.json#{p['id']}",
                        "progression")
    except (OSError, ValueError, KeyError):
        pass
    try:
        gj = json.loads((sw / "genres.json").read_text())
        for g in gj.get("genres", []):
            gid = _gid(g.get("midigenGenreId") or g["id"])
            for p in g.get("progressions", []):
                add(p.get("numerals"), gid, SCALE_MODE.get(p.get("scale", "major")),
                    _section(p.get("usage", "any")), p.get("weight"),
                    f"midigen-songwriting/genres.json#{g['id']}/{p.get('name')}", "genre_progression")
    except (OSError, ValueError, KeyError):
        pass
    try:
        sa = json.loads((sw / "song_analyses.json").read_text())
        for s in sa.get("songs", []):
            gid = _gid(s.get("genre", ""))
            mode = SCALE_MODE.get((s.get("key") or {}).get("mode", "major"))
            for sec, ch in (s.get("progressions") or {}).items():
                add(ch, gid, mode, _section(sec), 0.5, f"midigen-songwriting/song_analyses.json#{s['id']}",
                    "song_analysis_progression (approximate reading)")
    except (OSError, ValueError, KeyError):
        pass
    return rows


def genre_descriptions() -> list[dict]:
    """Short own-words genre descriptions (for fine-tuning prompts), with provenance."""
    out = []
    sw = root("midigen-songwriting")
    if sw and (sw / "genres.json").exists():
        try:
            for g in json.loads((sw / "genres.json").read_text()).get("genres", []):
                gid = _gid(g.get("midigenGenreId") or g["id"])
                if gid:
                    out.append({"genre": gid, "name": g.get("name"), "description": g.get("description", ""),
                                "aliases": g.get("aliases", []), "moods": [],
                                "provenance": {"source": f"midigen-songwriting/genres.json#{g['id']}", **PROVENANCE}})
        except (OSError, ValueError):
            pass
    for pack in ("midigen-genres", "midigen-knowledge"):
        r = root(pack)
        if not r:
            continue
        for f in sorted(r.glob("*.json")):
            if f.name == "index.json":
                continue
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            gid = _gid(d.get("id", f.stem)) if isinstance(d, dict) else None
            if gid and d.get("description"):
                out.append({"genre": gid, "name": d.get("name"), "description": d["description"],
                            "aliases": d.get("aliases", []), "moods": [],
                            "provenance": {"source": f"{pack}/{f.name}", "author": "midigen genre-pack authors (agents)",
                                           "kind": "own-words genre profile",
                                           "license": "authored for the midigen project (internal)"}})
    return out


def song_briefs() -> list[dict]:
    """Song-analysis takeaways as *style briefs* (no titles/artists in the prompt text), with provenance."""
    sw = root("midigen-songwriting")
    if not sw:
        return []
    try:
        sa = json.loads((sw / "song_analyses.json").read_text())
    except (OSError, ValueError):
        return []
    out = []
    for s in sa.get("songs", []):
        gid = _gid(s.get("genre", ""))
        if not gid:
            continue
        key = s.get("key") or {}
        bpm = (s.get("bpm") or {}).get("value")
        out.append({"genre": gid, "subgenre": s.get("subgenre"), "tonic": key.get("tonic"),
                    "mode": SCALE_MODE.get(key.get("mode", ""), None), "bpm": bpm,
                    "theme": (s.get("lyricApproach") or {}).get("theme"),
                    "provenance": {"source": f"midigen-songwriting/song_analyses.json#{s['id']}", **PROVENANCE,
                                   "note": "key/bpm approximate per source; title/artist not used in prompts"}})
    return out
