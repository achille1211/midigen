"""Genre packs: load external genre files (schema v1.0.0, e.g. /workspace/midigen-genres) as midigen profiles.

Where packs come from (lowest -> highest precedence; a later source wins an id):

  1. builtin profiles (src/midigen/data/genres/*.json)
  2. default folders, if present: ``midigen-knowledge`` then ``midigen-genres`` (searched next to the
     package checkout, in the current directory and in /workspace). midigen-genres is the curated,
     validated base library and wins over midigen-knowledge for ids both define (jazz, pop, rock).
     Disable with ``MIDIGEN_NO_DEFAULT_PACKS=1``.
  3. ``MIDIGEN_GENRE_PATH``: os.pathsep-separated folders or files; later entries win.
  4. ``--genre-path`` on the CLI (repeatable) / ``set_paths([...])`` / ``genres_path=`` keywords; later entries win.

Duplicate-id policy (explicit):
  * ids are normalised (lower case, ``-``/space -> ``_``; ``lofi-hip-hop`` -> ``lofi_hiphop``).
  * between packs the higher-precedence file wins the id. The shadowed version stays reachable as
    ``<id>@<pack>`` (e.g. ``jazz@midigen-knowledge``); its aliases are merged into the winner.
  * against a builtin, ``MIDIGEN_GENRE_MERGE`` decides:
      - ``merge`` (default): the pack's fields win (tempo, groove, harmony, band, form, drums...), aliases
        are unioned and builtin-only keys (extensions, secondary_dominants, borrowed, density_targets) are kept.
      - ``replace``: the pack profile replaces the builtin entirely.
      - ``builtin``: builtins win; the pack version is only reachable as ``<id>@<pack>``.
  * every merged profile records ``_sources`` (files, in the order applied).

Unmapped pack fields: drum ``kit`` programs, ``fx``/``perc`` instruments (skipped and reported),
glide, noteChoices weights, ornaments, phrase lengths and free-text notes/rules.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

SUPPORTED_SCHEMA = "1."
DEFAULT_PACK_DIRS = ("midigen-knowledge", "midigen-genres")  # increasing precedence
ID_ALIASES = {"lofi_hip_hop": "lofi_hiphop"}
MAX_TRACKS = 10

SCALE_MODE = {"major": "major", "ionian": "major", "natural_minor": "minor", "minor": "minor", "aeolian": "minor",
              "harmonic_minor": "harmonic_minor", "melodic_minor": "minor", "dorian": "dorian",
              "mixolydian": "mixolydian", "phrygian": "phrygian", "lydian": "lydian", "locrian": "locrian",
              "pentatonic_major": "major", "pentatonic_minor": "minor", "bebop_dominant": "mixolydian",
              "phrygian_dominant": "phrygian"}
VOICE_NOTES = {"kick": 36, "snare": 38, "closed_hat": 42, "open_hat": 46, "pedal_hat": 44, "ride": 51, "crash": 49,
               "rim": 37, "clap": 39, "tom_floor": 41, "tom_low": 45, "tom_mid": 47, "tom_high": 50, "shaker": 82,
               "tambourine": 54, "conga_high": 62, "china": 52, "cowbell": 56, "ride_bell": 53, "splash": 55}
ROLE_MAP = {"lead": ("lead", None), "chords": ("chords", None), "drums": ("drums", None), "bass": ("bass", None),
            "sub": ("bass", None), "pad": ("pad", None), "strings": ("pad", None), "keys": ("chords", None),
            "brass": ("chords", "stabs"), "arp": ("arp", None)}
SKIPPED_ROLES = ("fx", "perc")
TOKEN_MAP = {"chromatic_approach": "approach"}

_STATE: dict = {"key": None, "extra": [], "profiles": {}, "patterns": {}, "report": {}}


# ------------------------------------------------------------------ configuration

def norm_id(s: str) -> str:
    k = re.sub(r"[\s\-]+", "_", str(s).strip().lower())
    return ID_ALIASES.get(k, k)


def set_paths(paths: list[str] | None) -> None:
    """Extra pack folders/files with the highest precedence (the CLI's --genre-path, alias genres_path)."""
    _STATE["extra"] = [str(p) for p in (paths or [])]
    reload()


genres_path = set_paths  # keyword-friendly alias


def reload() -> None:
    _STATE["key"] = None
    from . import genres
    genres._clear_cache()


def _default_dirs() -> list[Path]:
    if os.environ.get("MIDIGEN_NO_DEFAULT_PACKS", "").strip() not in ("", "0", "false"):
        return []
    roots = [Path(__file__).resolve().parents[2].parent, Path(__file__).resolve().parents[2], Path.cwd(), Path("/workspace")]
    out = []
    for name in DEFAULT_PACK_DIRS:
        for r in roots:
            p = r / name
            if p.is_dir() and p.resolve() not in [q.resolve() for q in out]:
                out.append(p)
                break
    return out


def pack_sources() -> list[Path]:
    """All pack folders/files in increasing precedence."""
    env = [p for p in os.environ.get("MIDIGEN_GENRE_PATH", "").split(os.pathsep) if p.strip()]
    return _default_dirs() + [Path(p).expanduser() for p in env] + [Path(p).expanduser() for p in _STATE["extra"]]


def _files(src: Path) -> list[Path]:
    if src.is_file():
        return [src] if src.suffix == ".json" else []
    if not src.is_dir():
        return []
    return sorted(f for f in src.glob("*.json"))  # sub-folders (arranging/, tools/) are not genres


def _label(path: Path) -> str:
    return path.parent.name if path.is_file() else path.name


# ------------------------------------------------------------------ loading

def ensure_loaded() -> dict:
    """Load (or reuse) all packs for the current configuration. Returns {id: profile}."""
    srcs = pack_sources()
    key = (tuple(str(s) for s in srcs), os.environ.get("MIDIGEN_GENRE_MERGE", "merge"),
           tuple(f.stat().st_mtime for s in srcs for f in _files(s)))
    if _STATE["key"] == key:
        return _STATE["profiles"]
    from . import generators
    generators.clear_external_patterns()
    profiles: dict = {}
    shadowed: dict = {}
    patterns: dict = {}
    report = {"loaded": {}, "failed": {}, "skipped": [], "shadowed": {}, "warnings": {}}
    index_names: dict = {}
    for src in srcs:
        if not src.exists():
            report["failed"][str(src)] = "path does not exist"
            continue
        for f in _files(src):
            try:
                raw = json.loads(f.read_text())
            except (OSError, ValueError) as e:
                report["failed"][str(f)] = f"unreadable JSON: {e}"
                continue
            if isinstance(raw, dict) and isinstance(raw.get("genres"), list):
                # index.json: its display names become aliases ("Jazz (Swing / Bebop)")
                for e in raw["genres"]:
                    if isinstance(e, dict) and e.get("id") and e.get("name"):
                        index_names.setdefault(norm_id(e["id"]), []).append(str(e["name"]))
                report["skipped"].append(str(f))
                continue
            if not isinstance(raw, dict) or "id" not in raw or "drums" not in raw:
                report["skipped"].append(str(f))
                continue
            try:
                prof, pats, warns = convert(raw, f)
            except Exception as e:  # noqa: BLE001 - a broken pack must not break midigen
                report["failed"][str(f)] = f"{type(e).__name__}: {e}"
                continue
            gid = prof["name"]
            if gid in profiles:
                old = profiles[gid]
                tag = f"{gid}@{old['_pack']}"
                shadowed[tag] = old
                report["shadowed"][tag] = str(f)
                prof["aliases"] = _union(prof["aliases"], [a for a in old["aliases"]])
            profiles[gid] = prof
            patterns.update(pats)
            report["loaded"][gid] = str(f)
            if warns:
                report["warnings"][gid] = warns
    for gid, names in index_names.items():
        if gid in profiles:
            profiles[gid]["aliases"] = _union(profiles[gid]["aliases"], names)
    for tag, p in shadowed.items():
        q = copy.deepcopy(p)
        q["name"] = tag
        q["aliases"] = []
        profiles[tag] = q
    for gid, p in list(profiles.items()):
        if "@" not in gid:
            q = copy.deepcopy(p)
            q["name"] = f"{gid}@{p['_pack']}"
            q["aliases"] = []
            profiles.setdefault(q["name"], q)
    generators.register_drum_patterns(patterns)
    _STATE.update(key=key, profiles=profiles, patterns=patterns, report=report)
    return profiles


def report() -> dict:
    ensure_loaded()
    return copy.deepcopy(_STATE["report"])


def merge_policy() -> str:
    m = os.environ.get("MIDIGEN_GENRE_MERGE", "merge").strip().lower()
    return m if m in ("merge", "replace", "builtin") else "merge"


def _union(a: list, b: list) -> list:
    out, seen = [], set()
    for x in list(a) + list(b):
        k = str(x).lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


BUILTIN_KEEP = ("density_targets",)
BUILTIN_HARMONY_KEEP = ("extensions", "secondary_dominants", "borrowed")


def merge(builtin: dict | None, pack: dict) -> dict:
    """Combine a builtin profile with a pack profile under the current MIDIGEN_GENRE_MERGE policy."""
    if builtin is None:
        return pack
    pol = merge_policy()
    if pol == "replace":
        return pack
    if pol == "builtin":
        out = copy.deepcopy(builtin)
        out["aliases"] = _union(builtin.get("aliases", []), [a for a in pack["aliases"] if norm_id(a) != builtin["name"]])
        return out
    out = copy.deepcopy(pack)
    out["display"] = builtin.get("display", pack["display"])
    out["aliases"] = _union(builtin.get("aliases", []) + [pack["display"]], pack["aliases"])
    for k in BUILTIN_KEEP:
        if k in builtin and k not in out:
            out[k] = copy.deepcopy(builtin[k])
    for k in BUILTIN_HARMONY_KEEP:
        if k in builtin.get("harmony", {}):
            out["harmony"].setdefault(k, builtin["harmony"][k])
    # keep builtin generator styles where the pack only gives a rhythm (they encode idioms like walking/power)
    for role, inst in out["instruments"].items():
        b = builtin.get("instruments", {}).get(role)
        if b and inst.get("role", role) == role and "style" in b.get("generator", {}) and not inst.get("_style_fixed"):
            inst["generator"]["style"] = b["generator"]["style"]
            if inst["generator"]["type"] == "bass" and b["generator"]["style"] != "walking":
                inst["generator"]["style"] = "template" if inst["generator"].get("rhythm") else b["generator"]["style"]
    out["_sources"] = ["builtin"] + pack["_sources"]
    return out


# ------------------------------------------------------------------ conversion

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _reg(r, default):
    try:
        lo, hi = int(r["low"]), int(r["high"])
    except (TypeError, KeyError, ValueError):
        return list(default)
    lo, hi = _clamp(min(lo, hi), 0, 127), _clamp(max(lo, hi), 0, 127)
    if hi - lo < 12:
        hi = min(127, lo + 12)
        lo = hi - 12
    return [lo, hi]


def _mode_for(scale: str, numerals: list[str] | None = None) -> str | None:
    s = (scale or "").lower()
    if s in SCALE_MODE:
        return SCALE_MODE[s]
    if s in ("blues", "chromatic", "minor_blues", "major_blues"):
        first = (numerals or ["i"])[0].lstrip("b#")
        return "mixolydian" if first[:1].isupper() and s != "minor_blues" else "minor"
    return None


def _fix_numeral(n: str, mode: str) -> str | None:
    from .theory import is_valid_chord
    n = n.replace("ø7", "m7b5").replace("ø", "m7b5")
    if is_valid_chord(n, "C", mode):
        return n
    m = re.match(r"^([b#]?[ivIV]+)(.*?)(/.*)?$", n)
    if not m:
        return None
    base, suf = m.group(1), m.group(2)
    for cand in (base + suf, base + re.sub(r"(add\d+|sus\d|#\d+|b\d+)", "", suf), base + ("7" if "7" in suf else ""), base):
        if cand and is_valid_chord(cand, "C", mode):
            return cand
    return None


# register slots keep the pack's range width but separate the roles so parts don't mask each other
# (pack files often give overlapping registers, e.g. a flute melody on top of a nylon guitar)
SLOTS = {"chords": 61, "pad": 58, "arp": 68}


def _slot(role: str, reg: list[int]) -> list[int]:
    lo, hi = reg
    width = max(12, min(24 if role == "lead" else 22, hi - lo))
    centre = (lo + hi) / 2
    if role == "bass":
        centre = min(centre, 44)
        width = min(width, 24)
    elif role in SLOTS:
        centre = SLOTS[role]
    elif role == "lead":
        centre = max(centre, 75)
    lo = int(round(centre - width / 2))
    if role == "arp":
        lo = 61  # gen_arp voices within lo..lo+14, so its median sits near lo+7
    lo = max(0, min(127 - width, lo))
    return [lo, lo + width]


def _velocity_grid(vals, steps) -> list[int]:
    g = [int(_clamp(round(float(v or 0)), 0, 127)) for v in vals][:steps]
    return g + [0] * (steps - len(g))


def _section_name(name: str, used: set) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", str(name).lower()).strip("_")[:28] or "section"
    n, out = 1, base
    while out in used:
        n += 1
        out = f"{base}_{n}"
    used.add(out)
    return out


PEAK_RE = re.compile(r"^(drop|peak|climax|chorus|hook)")


def _hits_per_step(pat: dict | None) -> float:
    if not pat:
        return 0.0
    return sum(1 for v in pat["voices"] for x in v["grid"] if x) / max(1, pat["steps"])


def _arc_fixups(form: list[dict], patterns: dict, main: str, warns: list) -> None:
    """Make the form's energy arc audible (roadmap fix 4):
    - a peak section (drop/peak/chorus/hook) never plays a thinner drum pattern than a lower-energy section
      before it (house: the drop used the bare main groove while main_a had the percussion groove);
    - a drop that follows a breakdown gets an 8-bar build (rising energy, the drums' snare roll, the
      breakdown's instruments) when the form has none."""
    dens = lambda s: _hits_per_step(patterns.get(s.get("drum_pattern") or main))
    for i, s in enumerate(form):
        if not PEAK_RE.match(s["name"]) or (s.get("drum_pattern") or main) != main or patterns[main]["variants"].get("chorus"):
            continue  # an explicit or chorus-variant pattern is the pack author's choice
        lower = [f for f in form[:i] if f["energy"] < s["energy"]]
        if not lower:
            continue
        lower = [f for f in lower if f.get("drum_pattern") and patterns[f["drum_pattern"]].get("type") in ("main", "variation")
                 and not re.search(r"intro|outro|break|fill|pre", f["drum_pattern"].split(".", 1)[-1])]
        if not lower:
            continue
        best = max(lower, key=dens)
        if dens(best) > dens(s) * 1.05 and best.get("drum_pattern"):
            s["drum_pattern"] = best["drum_pattern"]
    if any(f["name"].startswith("build") for f in form):
        return
    i = 1
    while i < len(form):
        prev, s = form[i - 1], form[i]
        if s["name"].startswith(("drop", "peak")) and prev["energy"] <= s["energy"] - 0.35 and prev["bars"] >= 8:
            used = {f["name"] for f in form}
            nm = "build" if "build" not in used else f"build{i}"
            play = sorted(set(prev.get("play") or []) | {k for k in s.get("play") or [] if k in ("drums", "chords", "pad", "arp")})
            form.insert(i, {"name": nm, "bars": 8, "energy": round((prev["energy"] + s["energy"]) / 2, 3), "play": play})
            warns.append(f"inserted an 8-bar {nm} before {s['name']}")
            i += 1
        i += 1


def convert(raw: dict, path: Path | str = "?") -> tuple[dict, dict, list[str]]:
    """Map one schema-1.x genre file to (profile, drum patterns, warnings)."""
    path = Path(path)
    warns: list[str] = []
    ver = str(raw.get("schemaVersion", "1.0.0"))
    if not ver.startswith(SUPPORTED_SCHEMA):
        raise ValueError(f"unsupported schemaVersion {ver}")
    gid = norm_id(raw["id"])
    if not re.match(r"^[a-z0-9_]+$", gid):
        raise ValueError(f"bad genre id {raw['id']!r}")
    ts = list((raw.get("timeSignature") or {}).get("default") or [4, 4])
    bar_beats = ts[0] * 4 / ts[1]
    tempo = raw.get("tempo") or {}
    tmin, tmax = float(tempo.get("min", 80)), float(tempo.get("max", 140))
    tdef = float(tempo.get("default", (tmin + tmax) / 2))
    groove = raw.get("groove") or {}
    hum = groove.get("humanize") or {}
    sw = float(groove.get("swing") or 0)
    sub = str(groove.get("swingSubdivision", "16th"))

    # ---------------- harmony
    h = raw.get("harmony") or {}
    progs, chord_beats, weights, mode_w = {}, {}, {}, {}
    for p in h.get("progressions") or []:
        mode = _mode_for(p.get("scale", "major"), p.get("numerals"))
        if not mode:
            warns.append(f"progression {p.get('name')!r}: scale {p.get('scale')!r} not supported")
            continue
        nums = [_fix_numeral(n, mode) for n in p.get("numerals") or []]
        if not nums or any(n is None for n in nums):
            warns.append(f"progression {p.get('name')!r}: unparseable numerals {p.get('numerals')}")
            continue
        bpc = p.get("barsPerChord") or [1] * len(nums)
        if not isinstance(bpc, list):
            bpc = [bpc] * len(nums)
        bpc = [float(b) for b in bpc] + [1.0] * (len(nums) - len(bpc))
        pname = str(p.get("name") or f"prog{len(progs.get(mode, {})) + 1}")
        progs.setdefault(mode, {})[pname] = nums
        chord_beats.setdefault(mode, {})[pname] = [round(b * bar_beats, 4) for b in bpc[:len(nums)]]
        weights.setdefault(mode, {})[pname] = float(p.get("weight", 1.0))
        mode_w[mode] = mode_w.get(mode, 0.0) + float(p.get("weight", 1.0))
    for s in h.get("scales") or []:
        m = _mode_for(s.get("name"))
        if m and m in progs:
            mode_w[m] = mode_w.get(m, 0) + float(s.get("weight", 0))
    modes = sorted(progs, key=lambda m: -mode_w.get(m, 0))
    if not modes:
        warns.append("no usable progressions: harmony falls back to midigen's functional generator")
        modes = [m for m in (_mode_for(s.get("name")) for s in h.get("scales") or []) if m][:2] or ["major"]
    keys = [k for k in h.get("tonics") or [] if re.match(r"^[A-G][#b]?$", str(k))] or ["C"]

    # ---------------- drums
    d = raw.get("drums") or {}
    spb_ext = int(d.get("stepsPerBar", 16))
    if spb_ext != int(round(bar_beats * 4)):
        warns.append(f"stepsPerBar {spb_ext} does not match {ts[0]}/{ts[1]} in 16ths; grids are read as 16ths")
    gm = {**VOICE_NOTES, **{k: int(v) for k, v in (d.get("gmNotes") or {}).items()}}
    patterns, kinds = {}, {}
    for p in d.get("patterns") or []:
        pid = norm_id(p.get("id", "p"))
        name = f"{gid}.{pid}"
        nb = int(p.get("bars", 1) or 1)
        steps = nb * spb_ext
        voices = []
        for vname, vals in (p.get("voices") or {}).items():
            note = gm.get(vname)
            if note is None:
                warns.append(f"drum voice {vname!r} has no GM note; skipped")
                continue
            grid = _velocity_grid(vals, steps)
            if any(grid):
                voices.append({"name": vname, "note": note, "grid": grid, "every": 1})
        rats = []
        for r in p.get("ratchets") or []:
            note = gm.get(r.get("voice"))
            if note is not None:
                rats.append({"note": note, "step": int(r.get("step", 0)) % max(1, steps), "count": int(_clamp(r.get("count", 2), 2, 8)),
                             "curve": r.get("velocityCurve", "flat")})
        patterns[name] = {"name": name, "voices": voices, "ratchets": rats, "steps": steps, "type": p.get("type", "main"),
                          "keep_ghosts": {v["note"] for v in voices}, "comp": None, "variants": {}}
        kinds.setdefault(p.get("type", "main"), []).append(name)
    if not patterns:
        raise ValueError("no drum patterns")
    main = (kinds.get("main") or list(patterns))[0]
    vlist = kinds.get("variation", [])
    for vn in vlist:
        low = vn.split(".", 1)[1]
        if "chorus" in low or "drop" in low or "peak" in low:
            patterns[main]["variants"].setdefault("chorus", vn)
        elif "verse" in low:
            patterns[main]["variants"].setdefault("verse", vn)
    to_beats = tdef / 60.0 / 1000.0
    drum_gen = {"type": "drums", "pattern": main, "density": 0.55, "fill": True}
    if kinds.get("fill"):
        drum_gen["fill_pattern"] = kinds["fill"][0]
    if d.get("fillEveryBars"):
        drum_gen["fill_every"] = int(_clamp(int(d["fillEveryBars"]), 0, 64))
    if kinds.get("transition"):
        drum_gen["transition"] = kinds["transition"][0]

    humanize = {"timing": round(min(0.1, float(hum.get("timingMs", 6)) * to_beats), 4),
                "velocity": int(_clamp(int(hum.get("velocity", 6)), 0, 40))}
    laid = float(_clamp(float(groove.get("laidBackMs", 0) or 0), -40, 60))

    # ---------------- instruments
    insts = raw.get("instrumentation") or []
    mel = raw.get("melody") or {}
    bassd = raw.get("bass") or {}
    prefer = {"lead": mel.get("instrumentId"), "bass": bassd.get("instrumentId")}
    primaries: dict = {}
    extras: list = []
    for inst in insts:
        role_ext = str(inst.get("role", "")).lower()
        if inst.get("isDrum") and role_ext != "drums":
            role_ext = "perc"
        if role_ext in SKIPPED_ROLES or role_ext not in ROLE_MAP:
            warns.append(f"instrument {inst.get('id')!r} (role {role_ext}) not mapped")
            continue
        role, style_hint = ROLE_MAP[role_ext]
        cur = primaries.get(role)
        if cur is None or (prefer.get(role) == inst.get("id") and cur[0].get("id") != prefer.get(role)):
            if cur is not None:
                extras.insert(0, cur)
            primaries[role] = (inst, role_ext, style_hint)
        else:
            extras.append((inst, role_ext, style_hint))
    if len(primaries) + len(extras) > MAX_TRACKS:
        dropped = extras[MAX_TRACKS - len(primaries):]
        extras = extras[:MAX_TRACKS - len(primaries)]
        warns.append(f"{len(dropped)} instruments beyond {MAX_TRACKS} tracks not mapped: {[i[0].get('id') for i in dropped]}")

    def gen_for(inst, role_ext, role, style_hint, primary):
        prog = int(_clamp(int((inst.get("programs") or [0])[0]), 0, 127))
        reg = _reg(inst.get("register"), (28, 52) if role == "bass" else (52, 76) if role in ("chords", "pad") else (60, 84))
        g: dict = {"type": role}
        fixed = False
        if role == "drums":
            g = copy.deepcopy(drum_gen)
            g["laid_back"] = laid
            g["humanize"] = dict(humanize)
        elif role == "bass":
            if primary and bassd.get("walking"):
                g.update(style="walking", density=0.6)
            else:
                rhythm = bassd.get("rhythm") if primary else None
                if rhythm and any(rhythm):
                    toks = [TOKEN_MAP.get(t, t) if t else None for t in (bassd.get("stepNotes") or ["root"])]
                    g.update(style="template", rhythm=[float(_clamp(x or 0, 0, 127)) for x in rhythm][:128],
                             tokens=toks[:128], gate=float(_clamp(float(bassd.get("gate", 0.9)), 0.05, 1.0)), density=0.6)
                else:
                    g.update(style="root_fifth", density=0.5)
            gl = bassd.get("glide") or {}
            if primary and gl.get("enabled") and float(gl.get("probability", 0) or 0) > 0:
                g["glide"] = {"probability": round(float(_clamp(float(gl["probability"]), 0, 1)), 3),
                              "time_ms": float(_clamp(float(gl.get("timeMs", 60) or 60), 0, 1000))}
            reg = _reg(bassd.get("octaveRange"), reg) if primary else reg
        elif role in ("chords", "pad"):
            r = inst.get("rhythm")
            if role == "chords" and prog in (29, 30):
                g.update(style="power", density=0.6)
                fixed = True
            elif role == "pad" or role_ext in ("strings", "pad"):
                g = {"type": "pad", "style": "pad", "density": 0.3}
                role = "pad"
            elif style_hint:
                g.update(style=style_hint, density=0.5)
                fixed = True
            else:
                g.update(style="comp" if r else "block", density=0.5)
            if r and any(r) and g["type"] == "chords":
                g["rhythm"] = [float(_clamp(x or 0, 0, 127)) for x in r][:128]
                fixed = fixed or bool(r)
            if primary and role == "chords" and (h.get("voicing") or {}).get("register"):
                reg = _reg(h["voicing"]["register"], reg)
        elif role == "arp":
            g.update(style="up", rate="1/16", density=0.5)
        elif role == "lead":
            dens = (mel.get("density") or {}).get("value", 0.5)
            g.update(style="melodic", density=float(_clamp(float(dens), 0.05, 1.0)))
            if mel.get("rhythmCells"):
                g["cells"] = [[float(x) for x in c] for c in mel["rhythmCells"] if c][:32]
            if "stepwiseProbability" in mel:
                g["stepwise"] = float(_clamp(float(mel["stepwiseProbability"]), 0, 1))
            if "chordToneOnStrongBeat" in mel:
                g["chord_tone_strong"] = float(_clamp(float(mel["chordToneOnStrongBeat"]), 0, 1))
            if "maxLeapSemitones" in mel:
                g["max_leap"] = int(_clamp(int(mel["maxLeapSemitones"]), 1, 24))
            if primary and mel.get("register"):
                reg = _reg(mel["register"], reg)
            orn = " ".join(str(o).lower() for o in (mel.get("ornaments") or []))
            if primary and any(w in orn for w in ("slide", "scoop", "bend")):
                g["bend"] = 0.2
        if role != "drums":
            g["range"] = _slot(role, reg)
            g["humanize"] = dict(humanize)
            if laid and role in ("bass", "chords", "pad"):
                g["laid_back"] = round(laid * 0.5, 1)
        return {"name": str(inst.get("name") or role.capitalize())[:64], "program": prog, "generator": g,
                "id": str(inst.get("id") or role), "role": role, "_style_fixed": fixed}

    instruments: dict = {}
    for role, (inst, role_ext, hint) in primaries.items():
        instruments[role] = gen_for(inst, role_ext, role, hint, True)
    for inst, role_ext, hint in extras:
        role, _ = ROLE_MAP[role_ext]
        prog0 = int((inst.get("programs") or [0])[0] or 0)
        if role == "lead" and 56 <= prog0 <= 79:
            # a second horn/reed "melody" would improvise against the lead and clash: play section stabs
            role, hint = "chords", "stabs"
            warns.append(f"extra melody instrument {inst.get('id')!r} mapped as a horn-section backing part")
        key = norm_id(inst.get("id") or role)
        if key in instruments or key in ROLE_MAP or key in ("drums", "bass", "chords", "pad", "arp", "lead"):
            key = f"{key}_2"
        while key in instruments:
            key += "_"
        instruments[key] = gen_for(inst, role_ext, role, hint, False)
    if "drums" not in instruments:
        instruments["drums"] = {"name": "Drums", "generator": copy.deepcopy(drum_gen), "id": "drums", "role": "drums",
                                "program": 0, "_style_fixed": False}
    id_to_key = {v["id"]: k for k, v in instruments.items()}

    # ---------------- arrangement
    arr = raw.get("arrangement") or {}
    form, used = [], set()
    for s in arr.get("sections") or []:
        nm = _section_name(s.get("name", "section"), used)
        bars = int(_clamp(int(round(float(s.get("bars", 8) or 8))), 1, 64))
        sec = {"name": nm, "bars": bars, "energy": round(float(_clamp(float(s.get("energy", 0.5)), 0, 1)), 3),
               "play": sorted({id_to_key[i] for i in s.get("instruments") or [] if i in id_to_key})}
        dp = s.get("drumPattern")
        if dp and f"{gid}.{norm_id(dp)}" in patterns:
            sec["drum_pattern"] = f"{gid}.{norm_id(dp)}"
        form.append(sec)
    if not form:
        form = [{"name": "verse", "bars": 8, "energy": 0.5, "play": list(instruments)},
                {"name": "chorus", "bars": 8, "energy": 0.85, "play": list(instruments)}]
        warns.append("no arrangement sections: using verse/chorus")
    _arc_fixups(form, patterns, main, warns)
    short, seen_base, total = [], set(), 0
    for s in form:
        base = re.sub(r"(_?\d+)$", "", s["name"])
        if base in seen_base:
            continue
        seen_base.add(base)
        b = min(8, s["bars"])
        if total + b > 24:
            break
        short.append({**copy.deepcopy(s), "bars": b})
        total += b
    if not any(s.get("play") and "lead" in s["play"] for s in short) and form:
        pass

    # bass variations -> section overrides
    bass_vars = []
    if not bassd.get("walking"):
        for v in bassd.get("variations") or []:
            if v.get("rhythm") and any(v["rhythm"]):
                bass_vars.append({"name": str(v.get("name", "")).lower(),
                                  "rhythm": [float(_clamp(x or 0, 0, 127)) for x in v["rhythm"]][:128],
                                  "tokens": [TOKEN_MAP.get(t, t) if t else None for t in (v.get("stepNotes") or ["root"])][:128]})

    regs = {k: v["generator"]["range"] for k, v in instruments.items() if "range" in v["generator"] and k in ("bass", "chords", "pad", "arp", "lead")}
    dmin, dmax = (mel.get("density") or {}).get("notesPerBarMin"), (mel.get("density") or {}).get("notesPerBarMax")
    prof = {
        "name": gid, "display": str(raw.get("name") or gid), "description": str(raw.get("description", "")),
        "aliases": _union([str(raw["id"]), str(raw.get("name", ""))], [str(a) for a in raw.get("aliases") or []]),
        "tempo": {"min": tmin, "max": tmax, "default": tdef},
        "swing": round(float(_clamp(sw, 0, 1)), 3), "swing_unit": "1/8" if sub.startswith("8") else "1/16",
        "humanize": humanize, "laid_back": laid, "time_signature": ts,
        "modes": modes, "keys": keys,
        "harmony": {"beats_per_chord": bar_beats, "progressions": progs, "chord_beats": chord_beats, "weights": weights,
                    "extensions": "7ths"},
        "instruments": instruments, "layers": {}, "form": form, "short_form": short or form[:1],
        "register": regs, "bass_variations": bass_vars,
        "_pack": _label(path), "_sources": [str(path)], "_warnings": warns,
    }
    if dmin is not None and dmax is not None:
        prof["density_targets"] = {"lead": [float(dmin), float(dmax) * 1.5]}
    return prof, patterns, warns
