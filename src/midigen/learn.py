"""Learning loop: an experience store, user feedback, and a per-genre bandit over the generator's choices.

Everything is local and CPU-only. No API keys or GPU are needed.

* **Experience store** (SQLite at ``$MIDIGEN_DATA_DIR/experience.sqlite``, default ``~/.midigen/experience``):
  every recorded song (spec, genre, seed, choices, critic sub-scores), user feedback (ratings, thumbs,
  keep/reject, per-track or per-section notes), edits (old -> new, as preference signals) and validated
  LLM prompt/response pairs.
* **Policy** (``weights/<genre>.json``): Beta statistics per *arm* (a discrete generator choice such as
  "bass style = octave" or "chorus progression = royal road"), kept separately for user rewards and
  critic/training rewards. ``build_spec`` Thompson-samples the arms (seeded, so the output is deterministic for
  a given weights state) whenever the genre has learned data. ``build_spec(..., learn=False)``,
  ``MIDIGEN_LEARN=0`` or ``--no-learn`` turn this off.
* **Reward**: ``0.8 * user + 0.2 * critic`` when the user has given feedback. Otherwise the normalised critic
  score is used. User evidence is weighted ``USER_WEIGHT`` times higher than critic evidence, and critic evidence is
  capped at ``CRITIC_CAP`` pseudo-counts per arm, so a handful of human ratings outweigh any amount of
  self-training.
* **Reference MIDI** (``learn_midi``): extracts progressions, drum grids, bass templates, lead rhythm cells,
  intervals and section energy into ``profiles/<genre>.json``. These become new arms with a head start.
* **Offline self-improvement** (``train``): generate candidates, score with the critic plus a learned
  user-reward model, update, and report a per-round curve.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from .core import stable_seed

USER_WEIGHT = 4.0      # one human rating counts like 4 critic evaluations
CRITIC_CAP = 20.0      # max critic pseudo-counts per arm used when sampling
USER_DECAY = 0.97      # recency decay of user evidence (per user update, per genre)
CRITIC_DECAY = 0.995   # recency decay of critic evidence (per training update)
AUTO_PRIOR = (2.0, 1.0)  # the hand-tuned genre default starts slightly favoured
ARM_PRIOR = (1.0, 1.0)


def data_dir() -> Path:
    d = os.environ.get("MIDIGEN_DATA_DIR")
    return Path(d).expanduser() if d else Path.home() / ".midigen" / "experience"


def learning_enabled(flag: bool | None = None) -> bool:
    if flag is not None:
        return bool(flag)
    return os.environ.get("MIDIGEN_LEARN", "1").strip().lower() not in ("0", "false", "no", "off")


def recording_enabled() -> bool:
    return os.environ.get("MIDIGEN_NO_RECORD", "").strip().lower() not in ("1", "true", "yes", "on")


def _gkey(genre: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@-]+", "_", genre or "unknown")


def fingerprint(spec: dict) -> str:
    return hashlib.sha1(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS songs (
  id TEXT PRIMARY KEY, ts REAL, genre TEXT, seed INTEGER, source TEXT, parent TEXT, fingerprint TEXT,
  path TEXT, prompt TEXT, spec TEXT, params TEXT, choices TEXT, critic_score REAL, critic_sub TEXT,
  user_reward REAL, applied_user REAL, applied_dims TEXT, status TEXT);
CREATE INDEX IF NOT EXISTS songs_fp ON songs(fingerprint);
CREATE INDEX IF NOT EXISTS songs_path ON songs(path);
CREATE INDEX IF NOT EXISTS songs_genre ON songs(genre);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, song_id TEXT, ts REAL, kind TEXT, value REAL, target TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS edits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, song_id TEXT, new_song_id TEXT, ts REAL, ops TEXT, signals TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS llm_pairs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, genre TEXT, kind TEXT, prompt TEXT, response TEXT,
  song_id TEXT, score REAL);
"""


class Experience:
    """SQLite-backed store of songs, feedback, edits and LLM pairs."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser() if root else data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "experience.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA_SQL)

    def close(self):
        self.db.close()

    def add_song(self, spec: dict, source: str = "generate", choices: dict | None = None, params: dict | None = None,
                 path: str | None = None, parent: str | None = None, prompt: str | None = None,
                 report: dict | None = None) -> str:
        fp = fingerprint(spec)
        sid = hashlib.sha1(f"{fp}{time.time_ns()}{random.random()}".encode()).hexdigest()[:10]
        if report is None:
            from .critic import critique
            try:
                report = critique(spec)
            except Exception:  # noqa: BLE001 - recording must never break generation
                report = {"score": 0, "findings": []}
        self.db.execute(
            "INSERT INTO songs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, time.time(), spec.get("genre"), spec.get("seed"), source, parent, fp,
             str(Path(path).resolve()) if path else None, prompt, json.dumps(spec), json.dumps(params or {}),
             json.dumps(choices or {}), report["score"], json.dumps(sub_scores(report)), None, 0.0, "[]", None))
        self.db.commit()
        return sid

    def song(self, sid: str) -> dict | None:
        return _row(self.db.execute("SELECT * FROM songs WHERE id=?", (sid,)).fetchone())

    def find(self, ref) -> dict | None:
        """A song by id, 'last', a spec dict, or a .json/.mid path (by path, then by content)."""
        if isinstance(ref, dict):
            return _row(self.db.execute("SELECT * FROM songs WHERE fingerprint=? ORDER BY ts DESC LIMIT 1",
                                        (fingerprint(ref),)).fetchone())
        ref = str(ref)
        if ref == "last":
            return _row(self.db.execute("SELECT * FROM songs ORDER BY ts DESC LIMIT 1").fetchone())
        s = self.song(ref)
        if s:
            return s
        p = Path(ref).expanduser()
        if p.suffix == ".mid":
            p = p.with_suffix(".json")
        if p.exists():
            r = self.db.execute("SELECT * FROM songs WHERE path=? ORDER BY ts DESC LIMIT 1",
                                (str(p.resolve()),)).fetchone()
            if r:
                return _row(r)
            try:
                spec = json.loads(p.read_text())
            except (OSError, ValueError):
                return None
            return self.find(spec) if isinstance(spec, dict) else None
        return None

    def update_song(self, sid: str, **cols):
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in cols.values()]
        self.db.execute(f"UPDATE songs SET {sets} WHERE id=?", (*vals, sid))
        self.db.commit()

    def songs(self, genre: str | None = None, limit: int = 1000) -> list[dict]:
        if genre:
            rows = self.db.execute("SELECT * FROM songs WHERE genre=? ORDER BY ts DESC LIMIT ?", (genre, limit))
        else:
            rows = self.db.execute("SELECT * FROM songs ORDER BY ts DESC LIMIT ?", (limit,))
        return [_row(r) for r in rows]

    def add_feedback(self, sid: str, kind: str, value: float | None = None, target: str | None = None,
                     note: str | None = None) -> int:
        cur = self.db.execute("INSERT INTO feedback (song_id, ts, kind, value, target, note) VALUES (?,?,?,?,?,?)",
                              (sid, time.time(), kind, value, target, note))
        self.db.commit()
        return cur.lastrowid

    def feedback(self, sid: str | None = None) -> list[dict]:
        q = "SELECT * FROM feedback" + (" WHERE song_id=?" if sid else "") + " ORDER BY id"
        return [dict(r) for r in self.db.execute(q, (sid,) if sid else ())]

    def add_edit(self, sid: str | None, new_sid: str | None, ops, signals, source: str):
        self.db.execute("INSERT INTO edits (song_id, new_song_id, ts, ops, signals, source) VALUES (?,?,?,?,?,?)",
                        (sid, new_sid, time.time(), json.dumps(ops), json.dumps(signals), source))
        self.db.commit()

    def edits(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM edits ORDER BY id")]

    def add_llm_pair(self, genre, kind, prompt, response, song_id=None, score=None):
        self.db.execute("INSERT INTO llm_pairs (ts, genre, kind, prompt, response, song_id, score) VALUES (?,?,?,?,?,?,?)",
                        (time.time(), genre, kind, prompt, response, song_id, score))
        self.db.commit()

    def llm_pairs(self, genre: str | None = None) -> list[dict]:
        q = "SELECT * FROM llm_pairs" + (" WHERE genre=?" if genre else "") + " ORDER BY id"
        return [dict(r) for r in self.db.execute(q, (genre,) if genre else ())]

    def counts(self) -> dict:
        return {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("songs", "feedback", "edits", "llm_pairs")}

    def best(self, genre: str, k: int = 3) -> list[dict]:
        """Highest user-rated songs for a genre (user reward first, then critic)."""
        rows = self.db.execute("SELECT * FROM songs WHERE genre=? AND user_reward IS NOT NULL AND user_reward >= 0.6 "
                               "ORDER BY user_reward DESC, critic_score DESC, ts DESC LIMIT ?", (genre, k))
        return [_row(r) for r in rows]


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("spec", "params", "choices", "critic_sub", "applied_dims"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def sub_scores(report: dict) -> dict:
    """Per-check sub-scores (100 minus that check's penalties) plus the total."""
    from .critic import SEVERITY_COST
    cost = Counter()
    for f in report.get("findings", []):
        cost[f.get("check", "other")] += SEVERITY_COST.get(f.get("severity"), 0)
    return {"total": report["score"], **{k: max(0, 100 - v) for k, v in sorted(cost.items())}}


def critic_reward(score: float) -> float:
    """Critic score -> [0, 1]. Scores cluster in 80-100, so the useful range is stretched."""
    return max(0.0, min(1.0, (float(score) - 60.0) / 40.0))


# ----------------------------------------------------------------------------- policy (weights)

def _wpath(genre: str, root: Path | None = None) -> Path:
    return (root or data_dir()) / "weights" / f"{_gkey(genre)}.json"


def _ppath(genre: str, root: Path | None = None) -> Path:
    return (root or data_dir()) / "profiles" / f"{_gkey(genre)}.json"


def load_policy(genre: str, root: Path | None = None) -> dict:
    p = _wpath(genre, root)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    return {"genre": genre, "version": 1, "updates": 0, "user_events": 0, "critic_events": 0,
            "arms": {}, "prefs": {}, "history": []}


def save_policy(pol: dict, root: Path | None = None) -> None:
    p = _wpath(pol["genre"], root)
    p.parent.mkdir(parents=True, exist_ok=True)
    pol["history"] = pol.get("history", [])[-500:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(pol, indent=1, sort_keys=True))
    tmp.replace(p)


def load_profile(genre: str, root: Path | None = None) -> dict:
    p = _ppath(genre, root)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    return {"genre": genre, "references": [], "progressions": {}, "bass": {}, "cells": {}, "drum_grids": [],
            "energy": [], "tempo": [], "swing": [], "intervals": {}}


def save_profile(prof: dict, root: Path | None = None) -> None:
    p = _ppath(prof["genre"], root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prof, indent=1, sort_keys=True))


def _stats(pol: dict, dim: str, arm: str) -> dict:
    return pol["arms"].setdefault(dim, {}).setdefault(arm, {"ua": 0.0, "ub": 0.0, "ca": 0.0, "cb": 0.0})


def _prior(arm: str) -> tuple[float, float]:
    return AUTO_PRIOR if arm == "auto" else ARM_PRIOR


def _alpha_beta(st: dict, arm: str) -> tuple[float, float]:
    ca, cb = st.get("ca", 0.0), st.get("cb", 0.0)
    n = ca + cb
    if n > CRITIC_CAP:
        ca, cb = ca * CRITIC_CAP / n, cb * CRITIC_CAP / n
    a0, b0 = _prior(arm)
    return a0 + USER_WEIGHT * st.get("ua", 0.0) + ca, b0 + USER_WEIGHT * st.get("ub", 0.0) + cb


def arm_mean(pol: dict, dim: str, arm: str) -> float:
    a, b = _alpha_beta(pol["arms"].get(dim, {}).get(arm, {}), arm)
    return a / (a + b)


def has_data(genre: str, root: Path | None = None) -> bool:
    return load_policy(genre, root).get("updates", 0) > 0 or bool(load_profile(genre, root).get("references"))


def _decay(pol: dict, which: str, gamma: float):
    a, b = ("ua", "ub") if which == "user" else ("ca", "cb")
    for arms in pol["arms"].values():
        for st in arms.values():
            st[a] *= gamma
            st[b] *= gamma


def update(pol: dict, choices: dict, reward: float, source: str = "user", weight: float = 1.0,
           dims: list[str] | None = None, decay: bool = True) -> None:
    """Add a reward in [0, 1] to each chosen arm (optionally only some dimensions)."""
    which = "user" if source == "user" else "critic"
    if decay:
        _decay(pol, which, USER_DECAY if which == "user" else CRITIC_DECAY)
    a, b = ("ua", "ub") if which == "user" else ("ca", "cb")
    reward = max(0.0, min(1.0, float(reward)))
    for dim, arm in choices.items():
        if dims is not None and dim not in dims:
            continue
        st = _stats(pol, dim, str(arm))
        st[a] = max(0.0, st[a] + weight * reward)
        st[b] = max(0.0, st[b] + weight * (1.0 - reward))
    ev = "user_events" if which == "user" else "critic_events"
    pol["updates"] = pol.get("updates", 0) + 1
    pol[ev] = pol.get(ev, 0) + 1
    pol.setdefault("history", []).append({"ts": round(time.time(), 1), "source": source, "reward": round(reward, 3)})


def nudge(pol: dict, dim: str, arm: str, current: str | None, weight: float = 1.0) -> None:
    """A directional preference ("busier", an edit): reward `arm` and penalise the arm that was used."""
    _stats(pol, dim, arm)["ua"] += weight
    if current and current != arm:
        _stats(pol, dim, current)["ub"] += weight
    pol["updates"] = pol.get("updates", 0) + 1
    pol["user_events"] = pol.get("user_events", 0) + 1


# ----------------------------------------------------------------------------- arms

SECTION_WORDS = ("verse", "chorus", "bridge", "pre", "intro", "outro", "break", "breakdown", "build", "drop", "solo",
                 "ending", "fill", "stop", "in", "turn", "head", "shout", "riser")
BASS_STYLES = ["root_fifth", "octave", "syncopated", "kick_lock", "pulse", "walking", "root"]
LEAD_STYLES = ["melodic", "sparse", "riff", "busy"]
REG_SHIFT = {"low": -5, "auto": 0, "high": 5}
DENSITY = {"sparser": 0.8, "auto": 1.0, "busier": 1.2}
ENERGY = {"flat": 0.5, "auto": 1.0, "steep": 1.5}
SWING = {"straight": 0.0, "less": 0.5, "auto": 1.0, "more": 1.5}
HUMANIZE = {"tight": 0.5, "auto": 1.0, "loose": 1.8}
TEMPO = {"slower": 0.94, "auto": 1.0, "faster": 1.06}


def _base(name: str) -> str:
    return re.sub(r"(_?\d+)$", "", name)


def _tracks(spec, role):
    return [t for t in spec["tracks"] if t.get("role") == role and t["generator"].get("type") != "clip"]


def _ref_id(obj) -> str:
    return "ref:" + hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:6]


def _family(mode: str) -> str:
    return "minor" if mode in ("minor", "aeolian", "dorian", "phrygian", "harmonic_minor", "locrian") else "major"


def arm_space(spec: dict, g: dict, prof: dict | None = None, fixed: set | None = None) -> dict[str, list[str]]:
    """Discrete choices available for this spec/genre. Every dimension has an 'auto' arm (the genre default)."""
    from .generators import drum_patterns
    prof = prof or {}
    fixed = fixed or set()
    space: dict[str, list[str]] = {}
    mode = spec["mode"]
    progs = (g.get("harmony", {}).get("progressions", {}) or {}).get(mode) or {}
    learned_p = [k for k, v in prof.get("progressions", {}).items() if v.get("mode_family") == _family(mode)]
    for base in dict.fromkeys(_base(s["name"]) for s in spec["sections"]):
        arms = ["auto"] + list(progs)[:8] + learned_p[:4]
        if len(arms) > 1:
            space[f"prog:{base}"] = arms
    dr = _tracks(spec, "drums")
    if dr:
        pats = drum_patterns()
        cur = dr[0]["generator"].get("pattern", "")
        ns = {g["name"], cur.split(".")[0]} if cur else {g["name"]}
        cands = [k for k, p in pats.items()
                 if (k in ns or k.split(".")[0] in ns) and k != cur
                 and (p.get("type") == "main" or (p.get("type") == "variation"
                      and not any(w in re.split(r"[._]", k.lower()) for w in SECTION_WORDS)))]
        if cands:
            space["drums"] = ["auto"] + sorted(cands)[:6]
    if _tracks(spec, "bass"):
        cur = _tracks(spec, "bass")[0]["generator"].get("style")
        space["bass_style"] = ["auto"] + [s for s in BASS_STYLES if s != cur] + list(prof.get("bass", {}))[:4]
    if _tracks(spec, "lead"):
        cur = _tracks(spec, "lead")[0]["generator"].get("style")
        space["lead_rhythm"] = ["auto"] + [s for s in LEAD_STYLES if s != cur] + list(prof.get("cells", {}))[:3]
        space["register"] = list(REG_SHIFT)
    space["density"] = list(DENSITY)
    if len(spec["sections"]) > 1:
        space["energy"] = list(ENERGY) + (["learned"] if prof.get("energy") else [])
    space["swing"] = list(SWING) if spec.get("swing", 0) > 0 else ["auto", "light"]
    space["humanize"] = list(HUMANIZE)
    if "instrumentation" not in fixed and len(spec["tracks"]) > 3:
        space["instrumentation"] = ["auto", "lean", "no_pad"]
    if "tempo" not in fixed:
        space["tempo"] = list(TEMPO)
    return space


def choose(pol: dict, space: dict, rng: random.Random, greedy: bool = False) -> dict:
    """Thompson sampling per dimension (or the posterior mean when greedy)."""
    out = {}
    for dim, arms in space.items():
        best, bv = "auto", -1.0
        for arm in arms:
            a, b = _alpha_beta(pol["arms"].get(dim, {}).get(arm, {}), arm)
            v = a / (a + b) if greedy else rng.betavariate(a, b)
            if v > bv:
                best, bv = arm, v
        out[dim] = best
    return out


def apply_choices(spec: dict, g: dict, choices: dict, prof: dict | None = None) -> dict:
    """A new spec with the chosen arms applied (spec-native fields only, so the result stays portable)."""
    from . import harmony
    prof = prof or {}
    s = copy.deepcopy(spec)
    mode = s["mode"]
    bb = s["time_signature"][0] * 4 / s["time_signature"][1]
    progs = (g.get("harmony", {}).get("progressions", {}) or {}).get(mode) or {}
    cbs = (g.get("harmony", {}).get("chord_beats", {}) or {}).get(mode) or {}
    for dim, arm in choices.items():
        if arm == "auto":
            continue
        if dim.startswith("prog:"):
            base = dim[5:]
            if arm.startswith("ref:"):
                lp = prof.get("progressions", {}).get(arm)
                if not lp:
                    continue
                chords, cb, bpc = list(lp["chords"]), None, bb
            elif arm in progs:
                chords, cb, bpc = list(progs[arm]), (list(cbs[arm]) if arm in cbs else None), None
            else:
                continue
            for sec in s["sections"]:
                if _base(sec["name"]) != base:
                    continue
                sec["chords"] = chords
                sec.pop("chord_beats", None)
                if cb:
                    sec["chord_beats"] = cb
                if bpc:
                    sec["beats_per_chord"] = bpc
                cad = harmony.detect_cadence(chords, s["key"], mode)
                if cad and cad != "none":
                    sec["cadence"] = cad
                else:
                    sec.pop("cadence", None)
        elif dim == "drums":
            for t in _tracks(s, "drums")[:1]:
                t["generator"]["pattern"] = arm
        elif dim == "bass_style":
            for t in _tracks(s, "bass"):
                gen = t["generator"]
                if arm.startswith("ref:"):
                    tpl = prof.get("bass", {}).get(arm)
                    if not tpl:
                        continue
                    gen.update(style="template", rhythm=list(tpl["rhythm"]), tokens=list(tpl["tokens"]))
                    if tpl.get("gate"):
                        gen["gate"] = tpl["gate"]
                else:
                    gen["style"] = arm
                    gen.pop("rhythm", None)
                    gen.pop("tokens", None)
                for ov in t.get("overrides", {}).values():
                    for k in ("rhythm", "tokens", "style"):
                        ov.pop(k, None)
                t["overrides"] = {k: v for k, v in t.get("overrides", {}).items() if v}
        elif dim == "lead_rhythm":
            for t in _tracks(s, "lead"):
                if arm.startswith("ref:"):
                    cells = prof.get("cells", {}).get(arm)
                    if cells:
                        t["generator"]["cells"] = [list(c) for c in cells["cells"]]
                        t["generator"]["style"] = "melodic"
                else:
                    t["generator"]["style"] = arm
        elif dim == "register":
            sh = REG_SHIFT.get(arm, 0)
            for t in _tracks(s, "lead"):
                lo, hi = t["generator"].get("range", [60, 84])
                lo, hi = max(36, lo + sh), min(100, hi + sh)
                if hi - lo >= 7:
                    t["generator"]["range"] = [lo, hi]
                    t["generator"].pop("octave", None)
        elif dim == "density":
            f = DENSITY.get(arm, 1.0)
            for t in s["tracks"]:
                gen = t["generator"]
                if gen.get("type") != "clip":
                    gen["density"] = round(max(0.05, min(1.0, gen.get("density", 0.5) * f)), 3)
        elif dim == "energy":
            es = [sec.get("energy", 0.6) for sec in s["sections"]]
            if arm == "learned" and prof.get("energy"):
                curve, n = prof["energy"][-1], len(s["sections"])
                for i, sec in enumerate(s["sections"]):
                    sec["energy"] = round(max(0.05, min(1.0, curve[min(len(curve) - 1, int(i * len(curve) / n))])), 3)
            else:
                f, m = ENERGY.get(arm, 1.0), sum(es) / len(es)
                for sec, e in zip(s["sections"], es):
                    sec["energy"] = round(max(0.05, min(1.0, m + (e - m) * f)), 3)
        elif dim == "swing":
            if arm == "light":
                s["swing"] = 0.15
            else:
                s["swing"] = round(max(0.0, min(1.0, s.get("swing", 0.0) * SWING.get(arm, 1.0))), 3)
        elif dim == "humanize":
            f = HUMANIZE.get(arm, 1.0)
            for t in s["tracks"]:
                h = t["generator"].get("humanize")
                if h:
                    t["generator"]["humanize"] = {"timing": round(min(0.1, h.get("timing", 0.006) * f), 4),
                                                  "velocity": int(min(40, round(h.get("velocity", 4) * f)))}
        elif dim == "instrumentation":
            drop_roles = {"pad", "arp"} if arm == "lean" else {"pad"}
            keep, seen = [], set()
            for t in s["tracks"]:
                r = t.get("role")
                if r in drop_roles or (arm == "lean" and r in seen and r != "drums"):
                    continue
                seen.add(r)
                keep.append(t)
            if len(keep) >= 2:
                s["tracks"] = keep
        elif dim == "tempo":
            s["tempo"] = round(max(20.0, min(300.0, s["tempo"] * TEMPO.get(arm, 1.0))), 1)
    return s


_LAST: dict = {}   # fingerprint -> choices, so record() can credit the arms build_spec used


def apply_policy(spec: dict, g: dict, seed: int, fixed: set | None = None, greedy: bool = False,
                 root: Path | None = None) -> tuple[dict, dict]:
    """Called by build_spec: sample arms from the genre's policy and apply them. No data -> unchanged."""
    genre = g["name"]
    pol, prof = load_policy(genre, root), load_profile(genre, root)
    if pol.get("updates", 0) <= 0 and not prof.get("references"):
        return spec, {}
    space = arm_space(spec, g, prof, fixed)
    rng = random.Random(stable_seed("learn", genre, seed, pol.get("updates", 0)))
    choices = choose(pol, space, rng, greedy=greedy)
    new = apply_choices(spec, g, choices, prof)
    _LAST[fingerprint(new)] = choices
    if len(_LAST) > 256:
        _LAST.pop(next(iter(_LAST)))
    return new, choices


def observe(spec: dict, g: dict | None = None, prof: dict | None = None) -> dict:
    """Infer which arm each dimension corresponds to by comparing a spec with the genre's unlearned default.

    Works for any spec (LLM-made, hand-edited, imported), so feedback and edits can always be credited."""
    from .genres import build_spec, get_genre, UnknownGenre
    from .spec import total_bars
    try:
        g = g or get_genre(spec.get("genre") or "")
    except (UnknownGenre, KeyError, ValueError):
        return {}
    prof = prof if prof is not None else load_profile(g["name"])
    try:
        base = build_spec(g["name"], key=spec["key"], mode=spec["mode"], bars=total_bars(spec),
                          seed=spec.get("seed", 1) or 1, learn=False)
    except Exception:  # noqa: BLE001 - observation is best-effort
        return {}
    space = arm_space(base, g, prof)
    out = {}
    progs = (g.get("harmony", {}).get("progressions", {}) or {}).get(spec["mode"]) or {}
    bsec = {_base(x["name"]): x for x in base["sections"]}
    for dim, arms in space.items():
        if not dim.startswith("prog:"):
            continue
        b = dim[5:]
        sec = next((x for x in spec["sections"] if _base(x["name"]) == b), None)
        if not sec:
            continue
        if b in bsec and sec["chords"] == bsec[b]["chords"]:
            out[dim] = "auto"
            continue
        hit = next((a for a in arms if a in progs and list(progs[a]) == sec["chords"]), None)
        hit = hit or next((a for a in arms if a.startswith("ref:")
                           and prof.get("progressions", {}).get(a, {}).get("chords") == sec["chords"]), None)
        out[dim] = hit or "custom"

    def first(sp, role):
        t = _tracks(sp, role)
        return t[0]["generator"] if t else None

    def ratio(a, b):
        return a / b if b else (2.0 if a else 1.0)

    d, bd = first(spec, "drums"), first(base, "drums")
    if "drums" in space and d and bd:
        out["drums"] = "auto" if d.get("pattern") == bd.get("pattern") else (
            d.get("pattern") if d.get("pattern") in space["drums"] else "custom")
    bs, bbs = first(spec, "bass"), first(base, "bass")
    if "bass_style" in space and bs and bbs:
        if bs.get("style") == bbs.get("style") and bs.get("rhythm") == bbs.get("rhythm"):
            out["bass_style"] = "auto"
        else:
            ref = next((k for k, v in prof.get("bass", {}).items() if v["rhythm"] == bs.get("rhythm")), None)
            out["bass_style"] = ref or (bs.get("style") if bs.get("style") in space["bass_style"] else "custom")
    ld, bld = first(spec, "lead"), first(base, "lead")
    if ld and bld:
        if "lead_rhythm" in space:
            ref = next((k for k, v in prof.get("cells", {}).items() if v["cells"] == ld.get("cells")), None)
            out["lead_rhythm"] = ref or ("auto" if ld.get("style") == bld.get("style") else
                                         (ld.get("style") if ld.get("style") in space["lead_rhythm"] else "custom"))
        sh = (ld.get("range") or [60, 84])[0] - (bld.get("range") or [60, 84])[0]
        out["register"] = "low" if sh <= -3 else "high" if sh >= 3 else "auto"
    dens = [ratio(t["generator"].get("density", .5), bt["generator"].get("density", .5))
            for t in spec["tracks"] for bt in base["tracks"]
            if t["name"] == bt["name"] and t["generator"].get("type") != "clip"]
    if dens:
        r = sum(dens) / len(dens)
        out["density"] = "sparser" if r < 0.92 else "busier" if r > 1.08 else "auto"
    if "energy" in space:
        def spread(sp):
            es = [x.get("energy", .6) for x in sp["sections"]]
            m = sum(es) / len(es)
            return math.sqrt(sum((e - m) ** 2 for e in es) / len(es))
        r = ratio(spread(spec), spread(base))
        out["energy"] = "flat" if r < 0.75 else "steep" if r > 1.25 else "auto"
    sw, bsw = spec.get("swing", 0) or 0, base.get("swing", 0) or 0
    if bsw > 0:
        r = sw / bsw
        out["swing"] = "straight" if r < 0.2 else "less" if r < 0.75 else "more" if r > 1.25 else "auto"
    else:
        out["swing"] = "light" if sw > 0.05 else "auto"
    hs = [ratio(t["generator"]["humanize"].get("timing", 0), bt["generator"]["humanize"].get("timing", 0))
          for t in spec["tracks"] for bt in base["tracks"] if t["name"] == bt["name"]
          and t["generator"].get("humanize") and bt["generator"].get("humanize")]
    if hs:
        r = sum(hs) / len(hs)
        out["humanize"] = "tight" if r < 0.75 else "loose" if r > 1.35 else "auto"
    if "instrumentation" in space:
        roles, broles = [t["role"] for t in spec["tracks"]], [t["role"] for t in base["tracks"]]
        if len(roles) < len(broles) and "pad" in broles and "pad" not in roles:
            lean = ("arp" in broles and "arp" not in roles) or len(set(roles)) == len(roles)
            out["instrumentation"] = "lean" if lean else "no_pad"
        else:
            out["instrumentation"] = "auto"
    r = ratio(spec["tempo"], base["tempo"])
    out["tempo"] = "slower" if r < 0.97 else "faster" if r > 1.03 else "auto"
    return out


def choices_for(spec: dict) -> dict:
    """The arms behind a spec: remembered from build_spec if possible, else observed."""
    return dict(_LAST.get(fingerprint(spec)) or observe(spec))


# ----------------------------------------------------------------------------- text preferences

PREF_RULES = [  # (regex, dimension or None, arm, phrase for the LLM prompt)
    (r"\b(busier|more notes|denser|more going on|too sparse|too empty|fuller)\b", "density", "busier", "busier parts"),
    (r"\b(sparser|less busy|too busy|simpler|cluttered|too many notes|calmer|minimal)\b", "density", "sparser",
     "sparser, simpler parts"),
    (r"\b(more swing|swingier|swing more|groovier|shuffle)\b", "swing", "more", "more swing"),
    (r"\b(less swing|straighter|too swung)\b", "swing", "straight", "straighter feel"),
    (r"\b(bigger chorus|busier chorus|chorus (should )?hit harder|more dynamic|more contrast|build more|more drama)\b",
     "energy", "steep", "bigger contrast between sections (busier choruses)"),
    (r"\b(more even|less contrast|flatter|too dramatic|steady energy)\b", "energy", "flat", "steady energy across sections"),
    (r"\b(higher melody|melody (is )?too low|brighter melody|lead too low)\b", "register", "high", "higher melody register"),
    (r"\b(lower melody|melody (is )?too high|lead too high|darker melody)\b", "register", "low", "lower melody register"),
    (r"\b(tighter|too sloppy|quantized|on the grid)\b", "humanize", "tight", "tight, quantised timing"),
    (r"\b(looser|more human|too robotic|too stiff|more feel)\b", "humanize", "loose", "loose, human timing"),
    (r"\b(fewer instruments|less cluttered|stripped|thinner|no pad|drop the pad)\b", "instrumentation", "lean",
     "fewer instruments"),
    (r"\b(faster|too slow|speed up)\b", "tempo", "faster", "faster tempo"),
    (r"\b(slower|too fast|slow down|more laid back)\b", "tempo", "slower", "slower tempo"),
    (r"\b(jazzier|richer chords|more colou?r|extensions|7ths|sevenths)\b", None, None, "jazzier, richer chords"),
    (r"\b(simpler chords|plainer chords|triads)\b", None, None, "plain triads"),
    (r"\b(walking bass)\b", "bass_style", "walking", "walking bass"),
    (r"\b(octave bass|octaves in the bass)\b", "bass_style", "octave", "octave bass"),
    (r"\b(syncopated bass|funkier bass)\b", "bass_style", "syncopated", "syncopated bass"),
    (r"\b(busy lead|busier (lead|melody))\b", "lead_rhythm", "busy", "busy lead lines"),
    (r"\b(sparse (lead|melody)|fewer melody notes)\b", "lead_rhythm", "sparse", "sparse lead lines"),
]
POS = r"\b(love|loved|great|nice|good|awesome|perfect|like|liked|keep)\b"
NEG = r"\b(hate|bad|boring|awful|dislike|annoying|wrong|weak|meh)\b"


def parse_note(note: str) -> list[tuple]:
    """Text -> [(dimension or None, arm or None, phrase)]."""
    n = (note or "").lower()
    return [(d, a, ph) for rx, d, a, ph in PREF_RULES if re.search(rx, n)]


def sentiment(note: str) -> float | None:
    n = (note or "").lower()
    p, q = len(re.findall(POS, n)), len(re.findall(NEG, n))
    if not p and not q:
        return None
    return p / (p + q)


ROLE_DIMS = {"drums": ["drums", "humanize"], "bass": ["bass_style"], "lead": ["lead_rhythm", "register"],
             "chords": ["prog:", "density"], "pad": ["instrumentation", "prog:"], "arp": ["instrumentation"]}


def _target_dims(spec: dict, choices: dict, target: str | None) -> list[str] | None:
    """Dimensions a per-track ('track:Bass', a track name or role) or per-section ('section:chorus') note refers to."""
    if not target:
        return None
    kind, _, name = target.partition(":")
    if not name:
        kind, name = "", target
    name_l = name.lower().strip()
    if kind in ("section", "") and any(_base(s["name"]).lower() == _base(name_l) for s in spec["sections"]):
        return [d for d in choices if d.lower() == f"prog:{_base(name_l)}"] + ["energy"]
    if kind == "section":
        return []
    tr = next((t for t in spec["tracks"] if t["name"].lower() == name_l or t.get("role") == name_l), None)
    if tr is None:
        return []
    pref = ROLE_DIMS.get(tr.get("role"), [])
    return [d for d in choices if any(d == p or (p.endswith(":") and d.startswith(p)) for p in pref)]


# ----------------------------------------------------------------------------- public feedback API

_STORE: dict = {}


def store(root: str | Path | None = None) -> Experience:
    r = str(Path(root).expanduser() if root else data_dir())
    st = _STORE.get(r)
    if st is None or not Path(r, "experience.sqlite").exists():
        st = _STORE[r] = Experience(r)
    return st


def record(spec: dict, source: str = "generate", path: str | None = None, parent: str | None = None,
           prompt: str | None = None, params: dict | None = None, report: dict | None = None,
           choices: dict | None = None) -> str | None:
    """Record a generated or edited song. Returns its id, or None if recording is off (MIDIGEN_NO_RECORD=1)."""
    if not recording_enabled():
        return None
    try:
        ch = choices if choices else choices_for(spec)
        return store().add_song(spec, source=source, choices=ch, params=params, path=path, parent=parent,
                                prompt=prompt, report=report)
    except (sqlite3.Error, OSError):
        return None  # a read-only home dir must not break generation


def _json_path(ref) -> Path | None:
    if not isinstance(ref, (str, Path)):
        return None
    p = Path(str(ref)).expanduser()
    p = p.with_suffix(".json") if p.suffix == ".mid" else p
    return p if p.suffix == ".json" and p.exists() else None


def resolve(ref) -> dict:
    """Find the recorded song for a reference (id, 'last', .json/.mid path or spec dict).

    An unknown file is recorded on the spot. A file edited by hand since it was recorded becomes a new version,
    and the edit is learned from."""
    st = store()
    row = st.find(ref)
    jp = _json_path(ref)
    if row is None and jp is not None:
        spec = json.loads(jp.read_text())
        row = st.song(st.add_song(spec, source="imported", choices=observe(spec), path=str(jp)))
    if row is None and isinstance(ref, dict):
        row = st.song(st.add_song(ref, source="imported", choices=observe(ref)))
    if row is None:
        raise KeyError(f"no recorded song matches {ref!r} (use a song id, 'last', or the song's .json path)")
    if jp is not None:
        try:
            cur = json.loads(jp.read_text())
        except ValueError:
            cur = None
        if isinstance(cur, dict) and fingerprint(cur) != row["fingerprint"]:
            sid = record_edit(row["spec"], cur, ops=None, source="manual", path=str(jp), parent=row["id"])
            row = st.song(sid) if sid else row
    return row


def _user_reward(fb: list[dict]) -> float | None:
    vals = []
    for f in fb:
        if f.get("target"):
            continue
        k, v = f["kind"], f["value"]
        if k == "rating" and v is not None:
            vals += [(v - 1) / 4.0] * 2
        elif k == "thumbs" and v is not None:
            vals.append(1.0 if v > 0 else 0.0)
        elif k == "keep":
            vals.append(0.85)
        elif k == "reject":
            vals.append(0.0)
        elif k == "note" and v is not None:
            vals.append(v)
    return sum(vals) / len(vals) if vals else None


def _apply_song_feedback(row: dict) -> dict:
    """Recompute the song's user reward and move its arms' user statistics by the change."""
    st = store()
    u = _user_reward(st.feedback(row["id"]))
    genre = row.get("genre")
    out = {"song_id": row["id"], "genre": genre, "user_reward": u}
    if not genre:
        return out
    pol = load_policy(genre)
    ch = row.get("choices") or {}
    had = row.get("user_reward") is not None
    if u is not None and ch:
        if had:  # replace this song's previous contribution
            update(pol, ch, row.get("applied_user") or 0.0, "user", weight=-1.0, decay=False)
            pol["updates"] -= 1
            pol["user_events"] -= 1
            pol["history"].pop()
        update(pol, ch, u, "user", decay=not had)
        save_policy(pol)
    st.update_song(row["id"], user_reward=u, applied_user=u if u is not None else 0.0)
    out["reward"] = None if u is None else round(0.8 * u + 0.2 * critic_reward(row.get("critic_score") or 0), 3)
    out["choices"] = ch
    return out


def _apply_targeted(row: dict, value: float | None, target: str | None, note: str | None) -> dict:
    genre = row.get("genre")
    out = {"song_id": row["id"], "genre": genre, "signals": []}
    if not genre:
        return out
    pol = load_policy(genre)
    ch = row.get("choices") or {}
    dims = _target_dims(row["spec"], ch, target) if target else None
    if target and value is not None and dims:
        update(pol, ch, value, "user", dims=dims)
        out["signals"].append({"dims": dims, "reward": round(value, 3)})
    for dim, arm, phrase in parse_note(note or ""):
        pol.setdefault("prefs", {})[phrase] = pol.get("prefs", {}).get(phrase, 0) + 1
        if dim:
            nudge(pol, dim, arm, ch.get(dim))
        out["signals"].append({"nudge": [dim, arm], "phrase": phrase})
    if out["signals"]:
        save_policy(pol)
    return out


def rate(ref, rating: float, note: str | None = None, target: str | None = None) -> dict:
    """Rate a song 1-5 (the whole song, or one track/section with target='track:Bass' / 'section:chorus')."""
    rating = float(rating)
    if not 1 <= rating <= 5:
        raise ValueError(f"rating must be 1-5 (got {rating:g})")
    row = resolve(ref)
    store().add_feedback(row["id"], "rating", rating, target, note)
    if target:
        return _apply_targeted(row, (rating - 1) / 4.0, target, note)
    out = _apply_song_feedback(row)
    if note:
        out["signals"] = _apply_targeted(row, None, None, note)["signals"]
    return out


def thumbs(ref, up: bool, note: str | None = None, target: str | None = None) -> dict:
    row = resolve(ref)
    store().add_feedback(row["id"], "thumbs", 1.0 if up else -1.0, target, note)
    if target:
        return _apply_targeted(row, 1.0 if up else 0.0, target, note)
    out = _apply_song_feedback(row)
    if note:
        out["signals"] = _apply_targeted(row, None, None, note)["signals"]
    return out


def keep(ref, note: str | None = None) -> dict:
    row = resolve(ref)
    store().add_feedback(row["id"], "keep", 1.0, None, note)
    store().update_song(row["id"], status="kept")
    out = _apply_song_feedback(row)
    if note:
        out["signals"] = _apply_targeted(row, None, None, note)["signals"]
    return out


def reject(ref, note: str | None = None) -> dict:
    row = resolve(ref)
    store().add_feedback(row["id"], "reject", 0.0, None, note)
    store().update_song(row["id"], status="rejected")
    out = _apply_song_feedback(row)
    if note:
        out["signals"] = _apply_targeted(row, None, None, note)["signals"]
    return out


def feedback(ref, note: str = "", target: str | None = None, rating: float | None = None,
             thumbs_up: bool | None = None) -> dict:
    """Free-text feedback, optionally per track/section. Keywords ("busier", "more swing") nudge the policy,
    and positive or negative words count as a soft rating."""
    if rating is not None:
        return rate(ref, rating, note=note, target=target)
    if thumbs_up is not None:
        return thumbs(ref, thumbs_up, note=note, target=target)
    row = resolve(ref)
    s = sentiment(note)
    store().add_feedback(row["id"], "note", s, target, note)
    if target:
        return _apply_targeted(row, s, target, note)
    out = _apply_song_feedback(row) if s is not None else {"song_id": row["id"], "genre": row.get("genre")}
    out["signals"] = _apply_targeted(row, None, None, note)["signals"]
    return out


def edit_signals(old: dict, new: dict) -> list[dict]:
    """Preference signals implied by an edit: dimensions whose observed arm changed, and new chords."""
    a, b = observe(old), observe(new)
    out = [{"dim": d, "from": a.get(d), "to": arm} for d, arm in b.items()
           if a.get(d) != arm and arm != "custom" and not d.startswith("prog:")]
    for so in new.get("sections", []):
        prev = next((x for x in old.get("sections", []) if x["name"] == so["name"]), None)
        dim = f"prog:{_base(so['name'])}"
        if prev and prev["chords"] != so["chords"] and not any(o.get("dim") == dim for o in out):
            arm = b.get(dim)
            out.append({"dim": dim, "from": a.get(dim), "to": arm if arm not in (None, "custom") else None,
                        "chords": so["chords"]})
    return out


def record_edit(old: dict, new: dict, ops=None, source: str = "edit", path: str | None = None,
                parent: str | None = None) -> str | None:
    """Record an edit (LLM, offline parser, JSON ops or a hand edit) and learn from what changed."""
    if not recording_enabled():
        return None
    try:
        st = store()
        if parent is None:
            prow = st.find(old)
            parent = prow["id"] if prow else None
        sigs = edit_signals(old, new) if new.get("genre") else []
        sid = st.add_song(new, source=source, choices=observe(new) if new.get("genre") else {}, path=path,
                          parent=parent)
        st.add_edit(parent, sid, ops, sigs, source)
    except (sqlite3.Error, OSError):
        return None
    if sigs:
        genre = new["genre"]
        pol, prof = load_policy(genre), load_profile(genre)
        for sg in sigs:
            arm = sg.get("to")
            if "chords" in sg and not arm:
                arm = _ref_id(sg["chords"])
                prof["progressions"].setdefault(arm, {"chords": sg["chords"], "mode_family": _family(new["mode"]),
                                                      "count": 0, "source": "edit"})["count"] += 1
            if arm:
                nudge(pol, sg["dim"], arm, sg.get("from"), weight=0.75)
        save_profile(prof)
        save_policy(pol)
    return sid


def record_llm_pair(genre, kind, prompt, response, spec=None, score=None) -> None:
    """Keep a validated LLM prompt/response pair (only called after the reply passed the validator)."""
    if not recording_enabled():
        return
    try:
        row = store().find(spec) if spec is not None else None
        store().add_llm_pair(genre, kind, prompt if isinstance(prompt, str) else json.dumps(prompt), response,
                             row["id"] if row else None, score)
    except (sqlite3.Error, OSError):
        pass


# ----------------------------------------------------------------------------- few-shot memory for the LLM

def _compact(spec: dict) -> dict:
    return {"tempo": spec.get("tempo"), "key": spec.get("key"), "mode": spec.get("mode"), "swing": spec.get("swing"),
            "sections": [{k: s[k] for k in ("name", "bars", "chords", "energy") if k in s} for s in spec["sections"]],
            "tracks": [{"role": t["role"], "instrument": t.get("instrument"),
                        **{k: t["generator"][k] for k in ("type", "style", "pattern", "density", "range")
                           if k in t["generator"]}} for t in spec["tracks"]]}


def preferences(genre: str, top: int = 6) -> list[str]:
    """Readable learned preferences for a genre (from notes, edits and the arms' user statistics)."""
    pol = load_policy(genre)
    lines = [f"user asked for {p} ({n}x)" for p, n in sorted(pol.get("prefs", {}).items(), key=lambda x: -x[1])[:top]]
    for dim, arms in sorted(pol.get("arms", {}).items()):
        if sum(s.get("ua", 0) + s.get("ub", 0) for s in arms.values()) < 1.0:
            continue
        best = max(arms, key=lambda a: arm_mean(pol, dim, a))
        if best != "auto" and arm_mean(pol, dim, best) > 0.6 and arms[best].get("ua", 0) >= 0.5:
            lines.append(f"user ratings favour {dim} = {best} (score {arm_mean(pol, dim, best):.2f})")
    return lines[:top * 2]


def few_shot(genre: str | None, k: int = 2) -> dict | None:
    """Best-rated past specs and learned preferences for a genre, for the LLM prompt (None if nothing learned)."""
    if not genre or not learning_enabled():
        return None
    try:
        best = store().best(genre, k)
    except (sqlite3.Error, OSError):
        best = []
    prefs = preferences(genre)
    if not best and not prefs:
        return None
    return {"examples": [{"user_rating": round(1 + 4 * (b["user_reward"] or 0), 1), "spec": _compact(b["spec"])}
                         for b in best], "preferences": prefs}


# ----------------------------------------------------------------------------- reference MIDI

def _bar_steps(ts):
    bb = ts[0] * 4 / ts[1]
    return bb, int(round(bb * 4))


def analyze_midi(path: str | Path) -> dict:
    """Extract progressions, drum grids, a bass template, lead rhythm cells, intervals, energy and swing."""
    from .harmony import to_roman
    from .midi_io import import_midi, read_midi
    from .theory import parse_chord, note_to_pc
    data = read_midi(path)
    spec = import_midi(path)
    key, mode = spec["key"], spec["mode"]
    bb, spb = _bar_steps(spec["time_signature"])
    step = bb / spb
    total = max((n[0] + n[1] for t in data["tracks"] for n in t["notes"]), default=bb)
    nbars = max(1, int(-(-total // bb)))
    bar_chords = []
    for s in spec["sections"]:
        ch = s["chords"]
        for b in range(s["bars"]):
            bar_chords.append(ch[min(len(ch) - 1, b * len(ch) // s["bars"])])
    try:
        roman = to_roman(bar_chords, key, mode)
    except Exception:  # noqa: BLE001
        roman = bar_chords
    windows = Counter()
    for i in range(0, max(0, len(roman) - 3), 4):
        w = tuple(roman[i:i + 4])
        if len(set(w)) >= 2:
            windows[w] += 1
    tracks = data["tracks"]
    drums = [t for t in tracks if t["channel"] == 9 and t["notes"]]
    mel = [t for t in tracks if t["channel"] != 9 and t["notes"]]

    def med(t):
        return sorted(n[2] for n in t["notes"])[len(t["notes"]) // 2]

    def mono(t):
        st = Counter(round(n[0], 3) for n in t["notes"])
        return sum(1 for c in st.values() if c == 1) / max(1, len(st))

    bass = min(mel, key=med) if mel else None
    if bass is not None and med(bass) > 55:
        bass = None
    leads = [t for t in mel if t is not bass]
    lead = max(leads, key=lambda t: (mono(t) > 0.7, med(t))) if leads else None

    def grid(notes, pitches=None):
        occ, bars_with = [0] * spb, set()
        for n in notes:
            if pitches and n[2] not in pitches:
                continue
            b, pos = divmod(n[0], bb)
            occ[int(round(pos / step)) % spb] += 1
            bars_with.add(int(b))
        nb = max(1, len(bars_with))
        return [round(min(1.0, o / nb), 2) for o in occ]

    out = {"file": str(path), "key": key, "mode": mode, "tempo": spec["tempo"], "bars": nbars,
           "time_signature": spec["time_signature"], "progressions": [list(w) for w, _ in windows.most_common(3)],
           "chords_per_bar": roman[:64]}
    if drums:
        dn = [n for t in drums for n in t["notes"]]
        out["drums"] = {"kick": grid(dn, {35, 36}), "snare": grid(dn, {37, 38, 39, 40}),
                        "hat": grid(dn, {42, 44, 46, 51, 53, 59})}
    if bass:
        rhythm = [100 if o >= 0.35 else 0 for o in grid(bass["notes"])]
        if not any(rhythm):
            rhythm[0] = 100
        toks, tonic = Counter(), note_to_pc(key)
        for n in bass["notes"]:
            b, pos = divmod(n[0], bb)
            sym = bar_chords[min(len(bar_chords) - 1, int(b))] if bar_chords else None
            try:
                root = parse_chord(sym, key, mode).root if sym else tonic
            except Exception:  # noqa: BLE001
                root = tonic
            tok = {0: "root", 7: "fifth", 3: "third", 4: "third", 10: "seventh", 11: "seventh"}.get(
                (n[2] - root) % 12, "approach")
            toks[(int(round(pos / step)) % spb, tok)] += 1
        tokens = []
        for i, r in enumerate(rhythm):
            c = [(cnt, t) for (s_, t), cnt in toks.items() if s_ == i]
            tokens.append((max(c)[1] if c else "root") if r else None)
        hits = max(1, sum(1 for r in rhythm if r))
        durs = sorted(n[1] for n in bass["notes"])
        gate = round(max(0.05, min(1.0, durs[len(durs) // 2] / (bb / hits))), 2)
        out["bass"] = {"rhythm": rhythm, "tokens": tokens, "gate": gate}
    if lead:
        cells, bars, ivs, prev = Counter(), {}, Counter(), None
        for n in sorted(lead["notes"]):
            bars.setdefault(int(n[0] // bb), []).append(n)
            if prev is not None and n[0] > prev[0]:
                ivs[max(-12, min(12, n[2] - prev[2]))] += 1
            prev = n
        for b, ns in bars.items():
            on = sorted({int(round((n[0] - b * bb) / step)) for n in ns} - {spb})
            if len(on) < 2:
                continue
            durs = {int(round((n[0] - b * bb) / step)): max(1, int(round(n[1] / step))) for n in ns}
            cell = [-on[0]] if on[0] > 0 else []
            for i, o in enumerate(on):
                gap = (on[i + 1] if i + 1 < len(on) else spb) - o
                d = min(gap, durs.get(o, gap))
                if gap - d >= 2:
                    cell += [d, -(gap - d)]
                else:
                    cell.append(gap)
            cells[tuple(cell)] += 1
        steps = sum(c for i, c in ivs.items() if abs(i) <= 2)
        out["lead"] = {"cells": [list(c) for c, _ in cells.most_common(4)],
                       "intervals": {str(k): v for k, v in sorted(ivs.items())},
                       "stepwise": round(steps / max(1, sum(ivs.values())), 2),
                       "range": [min(n[2] for n in lead["notes"]), max(n[2] for n in lead["notes"])]}
    dens = [sum(1 for t in tracks for n in t["notes"] if s_ * bb <= n[0] < (s_ + 8) * bb) for s_ in range(0, nbars, 8)]
    if dens and max(dens) > 0:
        mx, mn = max(dens), min(dens)
        out["energy"] = [round(0.3 + 0.65 * ((d - mn) / (mx - mn) if mx > mn else 0.7), 2) for d in dens]
    offs = [n[0] % 1.0 for t in mel for n in t["notes"] if 0.45 < n[0] % 1.0 < 0.75]
    out["swing"] = round(min(1.0, max(0.0, (sum(offs) / len(offs) - 0.5) / (2 / 3 - 0.5))), 2) if offs else 0.0
    return out


def _grid_similarity(ref: dict, pat: dict, spb: int) -> float:
    from .generators import KICKS, BACKBEAT, HATS
    cls = {"kick": KICKS, "snare": BACKBEAT, "hat": HATS | {46, 51, 53, 59}}
    inter = union = 0
    for name, notes in cls.items():
        r = [1 if x >= 0.35 else 0 for x in ref.get(name, [0] * spb)]
        p = [0] * spb
        for v in pat.get("voices", []):
            if v.get("note") in notes:
                for i, x in enumerate(v.get("grid", [])[:spb]):
                    if x:
                        p[i] = 1
        inter += sum(1 for a, b in zip(r, p) if a and b)
        union += sum(1 for a, b in zip(r, p) if a or b)
    return inter / union if union else 0.0


def learn_midi(path: str | Path, genre: str) -> dict:
    """Learn from a reference MIDI: add its material to the genre profile as new arms with a head start."""
    from .genres import get_genre, build_spec
    from .generators import drum_patterns
    g = get_genre(genre)
    a = analyze_midi(path)
    prof, pol = load_profile(g["name"]), load_policy(g["name"])
    added = {"progressions": [], "bass": None, "cells": None, "drums": None, "energy": None}
    fam = _family(a["mode"])
    for ch in a["progressions"]:
        k = _ref_id(ch)
        prof["progressions"].setdefault(k, {"chords": ch, "mode_family": fam, "count": 0, "source": Path(path).name})
        prof["progressions"][k]["count"] += 1
        added["progressions"].append({"arm": k, "chords": ch})
    if a.get("bass"):
        k = _ref_id(a["bass"]["rhythm"])
        prof["bass"][k] = a["bass"]
        added["bass"] = k
    if a.get("lead") and a["lead"]["cells"]:
        k = _ref_id(a["lead"]["cells"])
        prof["cells"][k] = {"cells": a["lead"]["cells"], "stepwise": a["lead"]["stepwise"]}
        for iv, c in a["lead"]["intervals"].items():
            prof["intervals"][iv] = prof["intervals"].get(iv, 0) + c
        added["cells"] = k
    if a.get("energy") and len(a["energy"]) > 1:
        prof["energy"].append(a["energy"])
        added["energy"] = a["energy"]
    if a.get("drums"):
        prof["drum_grids"].append(a["drums"])
    prof["tempo"].append(a["tempo"])
    prof["swing"].append(a["swing"])
    prof["references"].append({"file": str(path), "key": a["key"], "mode": a["mode"], "tempo": a["tempo"],
                               "bars": a["bars"], "ts": round(time.time(), 1)})
    save_profile(prof)
    # head start: a reference is a user signal (they chose this music)
    base = build_spec(g["name"], mode=a["mode"] if a["mode"] in g.get("modes", []) else None, seed=1, learn=False)
    space = arm_space(base, g, prof)
    for item in added["progressions"]:
        for dim in space:
            if dim.startswith("prog:") and item["arm"] in space[dim]:
                _stats(pol, dim, item["arm"])["ua"] += 1.0
    if added["bass"]:
        _stats(pol, "bass_style", added["bass"])["ua"] += 1.5
    if added["cells"]:
        _stats(pol, "lead_rhythm", added["cells"])["ua"] += 1.5
    if added["energy"]:
        _stats(pol, "energy", "learned")["ua"] += 1.0
    if a.get("drums") and "drums" in space:
        pats = drum_patterns()
        _, spb = _bar_steps(a["time_signature"])
        sims = {k: _grid_similarity(a["drums"], pats[k], spb) for k in space["drums"] if k in pats}
        cur = _tracks(base, "drums")[0]["generator"].get("pattern")
        if cur in pats:
            sims["auto"] = _grid_similarity(a["drums"], pats[cur], spb)
        if sims:
            best = max(sims, key=sims.get)
            _stats(pol, "drums", best)["ua"] += 1.5 * sims[best]
            added["drums"] = {"nearest": best, "similarity": round(sims[best], 2)}
    if a.get("tempo") and g.get("tempo"):
        r = a["tempo"] / g["tempo"]["default"]
        if r < 0.97 or r > 1.03:
            _stats(pol, "tempo", "slower" if r < 1 else "faster")["ua"] += 0.5
    pol["updates"] = pol.get("updates", 0) + 1
    pol["user_events"] = pol.get("user_events", 0) + 1
    save_policy(pol)
    return {"genre": g["name"], "analysis": a, "added": added}
