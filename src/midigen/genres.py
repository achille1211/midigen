"""Genre knowledge base (data/genres/*.json) and the theory-driven song builder."""
from __future__ import annotations

import copy
import json
import random
import re
from functools import lru_cache
from importlib import resources

from . import harmony
from .core import stable_seed
from .spec import check, make_track

ROLE_ORDER = ["drums", "bass", "chords", "pad", "arp", "lead"]
DEFAULT_CADENCE = {"chorus": "authentic", "hook": "authentic", "drop": "authentic", "verse": "half",
                   "pre": "half", "build": "half", "bridge": "half", "outro": "authentic", "climax": "authentic"}


@lru_cache(maxsize=1)
def _builtin() -> dict:
    out = {}
    for f in resources.files("midigen").joinpath("data/genres").iterdir():
        if f.name.endswith(".json"):
            g = json.loads(f.read_text())
            g["_sources"] = ["builtin"]
            out[g["name"]] = g
    return out


_CACHE: dict = {"key": None, "data": {}}


def _clear_cache() -> None:
    _CACHE["key"] = None


def _all() -> dict:
    """Builtin profiles merged with genre packs (see packs.py for precedence and the duplicate-id policy)."""
    from . import packs
    profs = packs.ensure_loaded()
    key = packs._STATE["key"]
    if _CACHE["key"] != key or not _CACHE["data"]:
        out = copy.deepcopy(_builtin())
        for gid, p in profs.items():
            out[gid] = p if "@" in gid else packs.merge(out.get(gid), p)
        _CACHE.update(key=key, data=out)
    return _CACHE["data"]


def list_genres(include_packs: bool = False) -> list[str]:
    """Builtin genre ids (their profiles include any pack data merged into them).

    include_packs=True also lists genres that only exist in packs (e.g. metal, bigband). Shadowed
    pack versions (``id@pack``) are never listed but resolve with get_genre."""
    if not include_packs:
        return sorted(_builtin())
    return sorted(k for k in _all() if "@" not in k)


def available_genres() -> list[str]:
    return list_genres(include_packs=True)


def _loose(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


class UnknownGenre(KeyError):
    def __str__(self):
        return str(self.args[0])


def resolve_genre(name: str) -> str:
    """Map an id, kebab id, alias, display/index name or ``id@pack`` to a genre id; raises UnknownGenre."""
    from .packs import norm_id
    g = _all()
    raw = str(name or "").strip()
    if not raw:
        raise UnknownGenre(f"empty genre name; choose from {available_genres()}")
    key = norm_id(raw)
    if key in g:
        return key
    if "@" in key:
        base, pack = key.split("@", 1)
        base = norm_id(base)
        for cand in (f"{base}@{pack}", f"{base}@midigen-{pack}", f"{base}@midigen_{pack}"):
            for k in g:
                if norm_id(k) == norm_id(cand):
                    return k
        raise UnknownGenre(f"unknown genre {name!r}; pack versions: {sorted(k for k in g if '@' in k)}")
    lo = _loose(raw)
    plain = {k: v for k, v in g.items() if "@" not in k}
    # loose id ("Big Band" -> bigband), then display name, then aliases: an id or display name always
    # beats another genre's alias (jazz lists "big band" as an alias, but bigband is its own genre)
    for pick in (lambda k, v: [k], lambda k, v: [v.get("display", "")], lambda k, v: list(v.get("aliases", []))):
        for k, v in plain.items():
            if any(_loose(a) == lo for a in pick(k, v) if a):
                return k
    raise UnknownGenre(f"unknown genre {name!r}; choose from {available_genres()}")


def get_genre(name: str) -> dict:
    return copy.deepcopy(_all()[resolve_genre(name)])


EXTRA_HINTS = {  # words that map to a genre even if no profile alias contains them
    "heavy metal": ["metal", "rock"], "metal": ["metal", "rock"], "thrash": ["metal", "rock"], "metalcore": ["metal", "screamo", "rock"],
    "djent": ["metal", "rock"], "doom": ["metal", "rock"], "emo": ["emo", "rock"], "screamo": ["screamo", "emo", "rock"],
    "post hardcore": ["screamo", "emo", "rock"], "hardcore": ["screamo", "rock"], "punk": ["emo", "rock"],
    "big band": ["bigband", "jazz"], "bigband": ["bigband", "jazz"], "swing band": ["bigband", "jazz"],
}


def detect_genre(text: str) -> str | None:
    """Find a genre named in free text (longest alias match wins)."""
    t = " " + re.sub(r"[^a-z0-9&]+", " ", text.lower()) + " "
    best, best_len = None, 0
    g = _all()
    for k, v in g.items():
        if "@" in k:
            continue
        for alias in [k.replace("_", " "), v["display"].lower()] + [a.lower() for a in v.get("aliases", [])]:
            a = " " + re.sub(r"[^a-z0-9&]+", " ", alias).strip() + " "
            if a.strip() and a in t and len(a) > best_len:
                best, best_len = k, len(a)
    for hint, targets in EXTRA_HINTS.items():
        a = f" {hint} "
        if a in t and len(a) > best_len:
            tgt = next((x for x in targets if x in g), None)
            if tgt:
                best, best_len = tgt, len(a)
    return best


def profile_summary(name: str) -> dict:
    """Compact genre profile for LLM prompts."""
    g = get_genre(name)
    out = {k: g[k] for k in ("name", "display", "description", "tempo", "swing", "swing_unit", "modes", "layers",
                             "density_targets", "register") if k in g}
    h = g.get("harmony", {})
    out["harmony"] = {k: h[k] for k in ("extensions", "beats_per_chord", "progressions") if k in h}
    out["form"] = [(s["name"], s["bars"], s["energy"]) for s in g["form"]]
    out["instruments"] = {k: {"role": v.get("role", k), "instrument": v.get("instrument") or v.get("name"),
                              **{p: v.get("generator", {}).get(p) for p in ("type", "style", "pattern") if v.get("generator", {}).get(p)}}
                          for k, v in g["instruments"].items()}
    out["sources"] = g.get("_sources", ["builtin"])
    return out


def _base(name: str) -> str:
    return re.sub(r"(_?\d+)$", "", name)


_GENERIC = {"progression", "prog", "style", "the", "and", "with", "loop", "vamp", "a", "b", "c"}


def _pack_progression(g: dict, mode: str, sec: dict, rng: random.Random) -> str:
    """Choose a pack progression for a section: by name match, else a seeded weighted pick per section type."""
    h = g["harmony"]
    progs = h["progressions"][mode]
    picks = g.setdefault("_picks", {})
    base = _base(sec["name"])
    if (mode, base) in picks:
        return picks[(mode, base)]
    words = [w for w in re.split(r"[_\W]+", sec["name"].lower()) if w and not w.isdigit()]
    best = None
    for pname in progs:
        pw = [w for w in re.split(r"[^a-z0-9]+", pname.lower()) if w]
        letters = [w for w in re.split(r"[^A-Za-z0-9]+", pname) if len(w) == 1 and w.isupper()]
        if any(w in pw and w not in _GENERIC and len(w) > 1 for w in words) or \
                any(w.upper() in letters for w in words if len(w) == 1):
            best = pname
            break
    if best is None:
        names = list(progs)
        w = [h.get("weights", {}).get(mode, {}).get(n, 1.0) for n in names]
        used = set(picks.values())
        fresh = [(n, x) for n, x in zip(names, w) if n not in used]
        if fresh and len(names) > 1:
            names, w = [n for n, _ in fresh], [x for _, x in fresh]
        best = rng.choices(names, weights=[max(1e-3, x) for x in w])[0]
    picks[(mode, base)] = best
    return best


def section_harmony(g: dict, mode: str, sec: dict, rng: random.Random) -> tuple[list[str], list[float] | None]:
    """(chords, chord_beats or None) for a section. Pack progressions keep their own durations (barsPerChord)."""
    h = g.get("harmony", {})
    progs = h.get("progressions", {}).get(mode)
    cbs = h.get("chord_beats", {}).get(mode, {})
    if progs and cbs:
        name = sec.get("progression") if sec.get("progression") in progs else _pack_progression(g, mode, sec, rng)
        return list(progs[name]), list(cbs[name]) if name in cbs else None
    return section_progression(g, mode, sec, rng), None


def _builtin_choice(g: dict, mode: str, sec: dict, rng: random.Random) -> str | None:
    """Seed-varied choice among the curated progressions: repeats of a section keep their pick and two
    section types never share one, so different seeds give different harmony."""
    progs = g.get("harmony", {}).get("progressions", {}).get(mode) or {}
    key = sec.get("progression")
    if key not in progs:
        return None
    picks = g.setdefault("_picks", {})
    base = (mode, _base(sec["name"]), key)
    if base in picks:
        return picks[base]
    used = {v for k, v in picks.items() if k[2] != key}
    choice = key
    others = [k for k in progs if k != key and k not in used]
    if others and key in used:
        choice = rng.choice(others)
    elif others and rng.random() < 0.4:
        choice = rng.choice(others)
    picks[base] = choice
    return choice


def section_progression(g: dict, mode: str, sec: dict, rng: random.Random) -> list[str]:
    """Pick the genre's curated progression for this section, or compose one functionally."""
    h = g.get("harmony", {})
    progs = h.get("progressions", {}).get(mode)
    key = _builtin_choice(g, mode, sec, rng) if progs else None
    if progs and key in progs:
        chords = list(progs[key])
        if sec.get("cadence") and harmony.CADENCES.get(sec["cadence"]):
            chords = _apply_cadence(chords, mode, sec["cadence"])
        return chords
    # not curated for this mode: generate with functional harmony + the genre's colour rules
    base = sec["name"].rstrip("0123456789")
    cad = sec.get("cadence") or DEFAULT_CADENCE.get(base, "authentic")
    n = 4 if sec["bars"] * 4 / h.get("beats_per_chord", 4) >= 4 else 2
    chords = harmony.generate_progression(mode, n, rng, cad)
    if h.get("secondary_dominants"):
        chords = harmony.add_secondary_dominants(chords, mode, rng, h["secondary_dominants"])
    if h.get("borrowed"):
        chords = harmony.borrow_chords(chords, mode, rng, h["borrowed"])
    return harmony.add_extensions(chords, mode, h.get("extensions", "triads"))


def _ends_in(chords: list[str], cadence: str) -> bool:
    d = [harmony.degree_of_symbol(c) for c in chords[-2:]]
    if len(d) < 2:
        return False
    return {"authentic": d == [4, 0], "half": d[1] == 4, "plagal": d == [3, 0],
            "deceptive": d[0] == 4 and d[1] == 5}.get(cadence, False)


def _apply_cadence(chords: list[str], mode: str, cadence: str) -> list[str]:
    if _ends_in(chords, cadence):
        return list(chords)  # already cadences that way: keep the curated progression
    fam = harmony._family(mode)
    tonic = "I" if fam == "major" else "i"
    dom = "V7" if any(c.endswith("7") or c.endswith("9") for c in chords) else "V"
    if cadence == "authentic":
        tail = [dom, tonic + ("maj7" if fam == "major" and "7" in dom and any("maj" in c for c in chords) else "")]
    elif cadence == "half":
        tail = [chords[-2] if len(chords) > 2 else harmony.diatonic_chord(3, mode), dom]
    elif cadence == "plagal":
        tail = [harmony.diatonic_chord(3, mode), tonic]
    elif cadence == "deceptive":
        tail = [dom, "vi" if fam == "major" else "bVI"]
    else:
        return chords
    if len(chords) <= 2:
        return tail[-len(chords):]
    if cadence in ("authentic", "deceptive") and len(chords) >= 4 and harmony.degree_of_symbol(chords[-2]) == 1:
        out = chords[:-3] + [chords[-2]] + tail  # keep a ii right before the cadence (ii-V-I)
    else:
        out = chords[:-2] + tail
    # avoid a static repeat right before the cadence (e.g. V V I): use a predominant instead
    pre = [harmony.diatonic_chord(1, mode, seventh=dom.endswith("7")),
           harmony.diatonic_chord(3, mode, seventh=dom.endswith("7"))]
    for i in range(len(out) - 3, -1, -1):
        if harmony.degree_of_symbol(out[i]) == harmony.degree_of_symbol(out[i + 1]):
            out[i] = next((p for p in pre if harmony.degree_of_symbol(p) != harmony.degree_of_symbol(out[i + 1])), out[i])
    return out


MAX_BARS = 256


def build_spec(genre: str = "pop", key: str | None = None, mode: str | None = None, tempo: float | None = None,
               bars: int | None = None, seed: int = 1, roles: list[str] | None = None,
               title: str | None = None, exclude_roles: list[str] | None = None) -> dict:
    """Build a complete song spec from a genre profile using the theory engine."""
    g = get_genre(genre)
    if bars is not None and not 1 <= int(bars) <= MAX_BARS:
        raise ValueError(f"bars must be between 1 and {MAX_BARS} (got {bars})")
    if tempo is not None and not 20 <= float(tempo) <= 300:
        raise ValueError(f"tempo must be between 20 and 300 BPM (got {tempo})")
    from .theory import MODES, note_to_pc
    if key is not None:
        try:
            note_to_pc(str(key))
            if not re.fullmatch(r"[A-Ga-g][#b]?", str(key).strip()):
                raise ValueError
        except ValueError:
            raise ValueError(f"bad key {key!r}; use a note name: C C# Db D D# Eb E F F# Gb G G# Ab A A# Bb B") from None
    if mode is not None and mode not in MODES:
        raise ValueError(f"bad mode {mode!r}; choose from {sorted(MODES)}")
    rng = random.Random(stable_seed("build", g["name"], seed))
    mode = mode or g["modes"][0]
    key = key or g["keys"][seed % len(g["keys"])]
    bb = g.get("time_signature", [4, 4])[0] * 4 / g.get("time_signature", [4, 4])[1]
    form = copy.deepcopy(g["short_form"] if bars and bars <= 24 else g["form"])
    if bars:
        form = _fit_form(form, int(bars))
    sections = []
    for s in form:
        chords, cb = section_harmony(g, mode, s, rng)
        sec = {"name": s["name"], "bars": s["bars"], "chords": chords, "energy": s["energy"],
               "beats_per_chord": g["harmony"].get("beats_per_chord", bb)}
        if cb:
            sec["chord_beats"] = cb
        cad = s.get("cadence") or harmony.detect_cadence(chords, key, mode)
        if cad and cad != "none":
            sec["cadence"] = cad
        sections.append(sec)
    plays = {s["name"]: s for s in form if "play" in s}
    roles_avail = [r for r in ROLE_ORDER if r in g["instruments"]]
    extras = [k for k in g["instruments"] if k not in ROLE_ORDER]
    lead_at = len(roles_avail) - (1 if roles_avail and roles_avail[-1] == "lead" else 0)
    wanted = list(roles) if roles else roles_avail[:lead_at] + extras + roles_avail[lead_at:]  # lead stays last
    for r in exclude_roles or []:
        wanted = [w for w in wanted if w != r and g["instruments"].get(w, {}).get("role") != r]
    tracks, names = [], set()
    for role in wanted:
        if not roles and plays and role in g["instruments"] and not any(role in s.get("play", []) for s in plays.values()):
            continue  # an instrument the (possibly shortened) form never uses
        t = genre_track(g, role, sections, form=form)
        while t["name"] in names:
            t["name"] += " 2"
        names.add(t["name"])
        tracks.append(t)
    spec = {
        "version": 2, "title": title or f"{g['display']} sketch", "genre": g["name"],
        "tempo": g["tempo"]["default"] if tempo is None else tempo, "time_signature": g.get("time_signature", [4, 4]),
        "key": key, "mode": mode, "seed": seed, "swing": g.get("swing", 0.0), "swing_unit": g.get("swing_unit", "1/16"),
        "sections": sections, "arrangement": [s["name"] for s in sections], "tracks": tracks,
    }
    return check(spec)


def genre_track(g: dict, role: str, sections: list[dict], name: str | None = None, instrument: str | None = None,
                form: list[dict] | None = None) -> dict:
    """A track for an instrument key (a role such as 'lead', or a pack instrument id) of genre profile `g`."""
    key = role
    inst = g["instruments"].get(key)
    if inst is None:  # a role name for which the profile keys its instrument by id
        key = next((k for k, v in g["instruments"].items() if v.get("role") == role), role)
        inst = g["instruments"].get(key, {})
    real_role = inst.get("role", role if role in ROLE_ORDER else "lead")
    t = make_track(real_role, name=name or inst.get("name"), instrument=instrument if instrument else
                   (inst.get("instrument") if isinstance(inst.get("instrument"), str) else None))
    if instrument is None and isinstance(inst.get("program"), int):
        t["program"] = inst["program"]
    if inst.get("generator"):
        t["generator"] = copy.deepcopy(inst["generator"])
    if "humanize" not in t["generator"] and g.get("humanize") and t["generator"]["type"] != "clip":
        t["generator"]["humanize"] = dict(g["humanize"])
    if "laid_back" not in t["generator"] and g.get("laid_back") and t["generator"]["type"] == "drums":
        t["generator"]["laid_back"] = g["laid_back"]
    if key != real_role:
        t["generator"]["seed"] = stable_seed("extra", key) % 1000
    reg = g.get("register", {}).get(key) if key == real_role else None
    if reg and t["generator"]["type"] not in ("drums", "clip"):
        t["generator"]["range"] = list(reg)
        t["generator"].pop("octave", None)
    names = [s["name"] for s in sections]
    fmap = {f["name"]: f for f in (form or g.get("form", [])) if "play" in f}
    if fmap and any(n in fmap for n in names):
        plays = [n for n in names if n in fmap and key in fmap[n]["play"]] + [n for n in names if n not in fmap]
        for n in plays:
            dp = fmap.get(n, {}).get("drum_pattern")
            if t["generator"]["type"] == "drums" and dp and dp != t["generator"].get("pattern"):
                t["overrides"].setdefault(n, {})["pattern"] = dp
        if t["generator"]["type"] == "bass" and t["generator"].get("style") == "template":
            _bass_variations(g, t, [s for s in sections if s["name"] in plays])
    else:
        thr = g.get("layers", {}).get(real_role, 0.0)
        plays = [s["name"] for s in sections if s["energy"] >= thr - 1e-9]
    if not plays:
        plays = [max(sections, key=lambda s: s["energy"])["name"]]
    t["sections"] = None if len(plays) == len(sections) else plays
    return t


def _bass_variations(g: dict, t: dict, sections: list[dict]) -> None:
    vars_ = g.get("bass_variations") or []
    if not vars_:
        return
    for s in sections:
        words = set(re.split(r"[_\W]+", s["name"].lower()))
        v = next((v for v in vars_ if words & set(re.split(r"[_\W]+", v["name"])) - {""}), None)
        if v is None and s["energy"] >= 0.8:
            v = vars_[0]
        if v:
            t["overrides"].setdefault(s["name"], {}).update(rhythm=v["rhythm"], tokens=v["tokens"])


def _fit_form(form: list[dict], bars: int) -> list[dict]:
    """Scale a form to exactly `bars` bars, keeping 2/4/8-bar phrase lengths where possible."""
    form = copy.deepcopy(form)
    bars = max(1, int(bars))
    if not form:
        return [{"name": "verse", "bars": bars, "energy": 0.6}]
    total = sum(s["bars"] for s in form)
    if total == bars:
        return form
    if bars < total:
        # drop numbered repeats first, then halve long sections, then drop intro/outro and low-energy sections
        kept = [s for s in form if not re.search(r"\d$", s["name"])]
        form = kept or form[:1]
        while sum(s["bars"] for s in form) > bars and len(form) > 1:
            biggest = max(form, key=lambda s: s["bars"])
            if biggest["bars"] > 4:
                biggest["bars"] //= 2
                continue
            edge = [s for s in form if _base(s["name"]) in ("intro", "outro")]
            form.remove(edge[-1] if edge else min(form, key=lambda s: s["energy"]))
        while sum(s["bars"] for s in form) > bars:
            max(form, key=lambda s: s["bars"])["bars"] -= 1
        diff = bars - sum(s["bars"] for s in form)
        if diff > 0:
            max(form, key=lambda s: s["energy"])["bars"] += diff
        return form
    out = form
    while sum(s["bars"] for s in out) < bars:
        peak = max(out, key=lambda s: s["energy"])
        dup = copy.deepcopy(peak)
        base = peak["name"].rstrip("0123456789")
        n = sum(1 for s in out if s["name"].rstrip("0123456789") == base)
        while any(s["name"] == f"{base}{n + 1}" for s in out):
            n += 1
        dup["name"] = f"{base}{n + 1}"
        out.insert(max(0, len(out) - 1), dup)
    while sum(s["bars"] for s in out) > bars:
        max(out, key=lambda s: s["bars"])["bars"] -= 1
    return out
