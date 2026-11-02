"""Genre knowledge base (data/genres/*.json) and the theory-driven song builder."""
from __future__ import annotations

import copy
import os
import json
import random
import re
from functools import lru_cache
from importlib import resources

from . import harmony
from .core import stable_seed
from .spec import check, make_track
from .theory import is_roman

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
        if (any(w in pw and w not in _GENERIC and len(w) > 1 for w in words) or
                any(w.upper() in letters for w in words if len(w) == 1)) and \
                (g.get("name") not in SEED_VARIETY or rng.random() < 0.6):
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


# Loop genres whose packs offer only two or three progressions per mode: the seed also decides whether a
# section's loop starts on its tonic or on another of its chords (bVImaj7-bVII-i7-bVII instead of
# i7-bVII-bVImaj7-bVII), and section-name matches are only preferred, so the 3 seeds of a genre differ.
SEED_VARIETY = {"house", "funk"}


def _loop_rotation(g: dict, mode: str, sec: dict, chords: list[str], rng: random.Random) -> int:
    rot = g.setdefault("_rotations", {})
    base = (mode, _base(sec["name"]))
    if base not in rot:
        n = len(chords)
        ok = [k for k in range(1, n) if chords[k] != chords[k - 1] and chords[0] != chords[-1]]
        rot[base] = rng.choice(ok) if ok and rng.random() < 0.5 else 0
    return rot[base]


def section_harmony(g: dict, mode: str, sec: dict, rng: random.Random) -> tuple[list[str], list[float] | None]:
    """(chords, chord_beats or None) for a section. Pack progressions keep their own durations (barsPerChord)."""
    h = g.get("harmony", {})
    progs = h.get("progressions", {}).get(mode)
    cbs = h.get("chord_beats", {}).get(mode, {})
    if progs and cbs:
        name = sec.get("progression") if sec.get("progression") in progs else _pack_progression(g, mode, sec, rng)
        chords, beats = list(progs[name]), list(cbs[name]) if name in cbs else None
        if g.get("name") in SEED_VARIETY and not sec.get("progression"):
            k = _loop_rotation(g, mode, sec, chords, rng)
            chords = chords[k:] + chords[:k]
            if beats and len(beats) == len(progs[name]):
                beats = beats[k:] + beats[:k]
        return chords, beats
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


# Per-genre harmonic colour for pack progressions (which arrive without the functional path's
# secondary dominants / modal mixture): (secondary-dominant probability, borrowed-chord probability).
# Applied by *splitting* a long chord so the pack's own progression survives and the harmonic rhythm
# varies (a chord change on beat 3 into the tonicised / borrowed chord).
GENRE_COLOUR = {
    "pop": (0.45, 0.35), "rock": (0.25, 0.5), "synthwave": (0.15, 0.35), "cinematic": (0.25, 0.5),
    "jazz": (0.6, 0.25), "neo_soul": (0.5, 0.4), "lofi_hiphop": (0.45, 0.35), "bossa_nova": (0.55, 0.25),
    "funk": (0.15, 0.1), "reggae": (0.15, 0.15), "ambient": (0.0, 0.3), "edm": (0.15, 0.3),
    "house": (0.15, 0.25), "drum_and_bass": (0.2, 0.3), "techno": (0.0, 0.1), "trap": (0.0, 0.2),
}
_NUMERAL_RE = re.compile(r"[b#]?(?:VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)")


def _numeral(sym: str) -> str | None:
    m = _NUMERAL_RE.match(sym)
    return m.group(0) if m else None


def _colour_split(chords: list[str], beats: list[float], i: int, new: str) -> None:
    """Split chord i: the original keeps the first half, `new` takes the rest (at most one bar)."""
    b = beats[i]
    tail = min(b / 2, 4.0)
    chords.insert(i + 1, new)
    beats[i] = b - tail
    beats.insert(i + 1, tail)


def colour_harmony(g: dict, mode: str, chords: list[str], beats: list[float] | None,
                   rng: random.Random) -> tuple[list[str], list[float] | None]:
    """Genre-weighted secondary dominants and modal interchange for a pack progression.

    Secondary dominant: the second half of a chord of >= 4 beats becomes V7/x of the next chord
    (x diatonic, not the tonic, not diminished). Borrowed: major keys split IV -> IV iv (minor plagal)
    or swap ii -> iiø7 / vi -> bVI; minor keys split iv -> IV iv or i -> i IV (dorian IV). The last chord
    (the cadence goal) is never touched. Returns new (chords, beats)."""
    if not beats or len(beats) != len(chords) or not all(is_roman(c) for c in chords):
        return chords, beats
    sec_p, bor_p = g.get("harmony", {}).get("colour") or GENRE_COLOUR.get(g.get("name", ""), (0.0, 0.0))
    out, bts = list(chords), [float(b) for b in beats]
    rich = sum(1 for c in out if any(x in c for x in ("7", "9", "11", "13"))) * 2 >= len(out)
    fam = harmony._family(mode)
    # secondary dominants: one per progression with probability sec_p, a second with sec_p / 2
    for want in (sec_p, sec_p / 2):
        if rng.random() >= want:
            break
        cands = []
        for j in range(len(out) - 1):
            cur, tgt = out[j], out[j + 1]
            base, d = _numeral(tgt), harmony.degree_of_symbol(tgt)
            if (base is None or d in (None, 0) or "/" in tgt or "/" in cur or tgt.endswith(("°", "ø7", "dim7"))
                    or bts[j] < 4 or base.lower() in ("bii", "#iv") or harmony.degree_of_symbol(cur) == (d + 4) % 7):
                continue  # tonic / diminished targets, short chords, or already its dominant
            cands.append(j)
        if not cands:
            break
        j = rng.choice(cands)
        _colour_split(out, bts, j, "V7/" + _numeral(out[j + 1]))
    # modal interchange (one per progression)
    if rng.random() < bor_p:
        for j in rng.sample(range(len(out) - 1), len(out) - 1):
            c = out[j]
            n = _numeral(c)
            if n is None or "/" in c:
                continue
            if fam == "major" and n == "IV" and bts[j] >= 4:
                _colour_split(out, bts, j, "iv7" if rich else "iv")
                break
            if fam == "major" and n == "ii" and j > 0:
                out[j] = "iiø7"
                break
            if fam == "major" and n == "vi" and j > 0 and not rich:
                out[j] = "bVI"
                break
            if fam == "minor" and mode != "dorian" and n == "iv" and bts[j] >= 4:
                out[j] = "IV" + ("7" if rich else "")
                _colour_split(out, bts, j, c)
                break
            if fam == "minor" and mode not in ("dorian", "phrygian") and n == "i" and bts[j] >= 8:
                _colour_split(out, bts, j, "IV7" if rich else "IV")
                break
    return out, bts


MAX_BARS = 256


def build_spec(genre: str = "pop", key: str | None = None, mode: str | None = None, tempo: float | None = None,
               bars: int | None = None, seed: int = 1, roles: list[str] | None = None,
               title: str | None = None, exclude_roles: list[str] | None = None,
               learn: bool | None = None, ml=None) -> dict:
    """Build a complete song spec from a genre profile using the theory engine.

    learn: apply the genre's learned policy (see midigen.learn). None = on unless MIDIGEN_LEARN=0;
    with no learned data the output is identical to learn=False.

    ml (opt-in): True, an options string such as "reward,progression,n=8" or a dict enables trained artifacts
    from midigen.ml (best-of-N reranking, learned progressions). None reads MIDIGEN_ML (off when unset);
    without trained artifacts the plain spec is returned."""
    if ml or (ml is None and os.environ.get("MIDIGEN_ML", "").strip().lower() not in ("", "0", "false", "no", "off")):
        from .ml.hooks import apply_ml
        return apply_ml(dict(genre=genre, key=key, mode=mode, tempo=tempo, bars=bars, seed=seed, roles=roles,
                             title=title, exclude_roles=exclude_roles, learn=learn),
                        ml if ml is not None else os.environ.get("MIDIGEN_ML"))
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
        if cb:  # pack progressions: genre-weighted colour, the same for every repeat of a section type
            chords, cb = colour_harmony(g, mode, chords, cb, random.Random(stable_seed("colour", g["name"], seed, _base(s["name"]))))
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
    from . import learn as _learn
    if _learn.learning_enabled(learn):
        fixed = ({"instrumentation"} if roles or exclude_roles else set()) | ({"tempo"} if tempo else set())
        spec, _ = _learn.apply_policy(check(spec), g, seed, fixed=fixed)
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
