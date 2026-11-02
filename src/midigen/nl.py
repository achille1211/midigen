"""Offline (no-LLM) natural-language layer: rule-based prompt->spec and edit-phrase->ops.

Covers the common phrases; anything it does not understand raises NotUnderstood so callers can
fall back to the LLM (or tell the user)."""
from __future__ import annotations

import re

from .genres import available_genres, build_spec, detect_genre, get_genre, list_genres
from .schema import DRUM_PATTERNS
from .spec import INSTRUMENTS, find_section, find_track, SpecError

MOOD_GENRE = [
    (r"\b(chill|study|relax|cozy|mellow)\b", "lofi_hiphop"), (r"\b(epic|film|score|trailer|orchestral)\b", "cinematic"),
    (r"\b(club|dance|dancefloor)\b", "house"), (r"\b(retro|80s|outrun)\b", "synthwave"),
    (r"\b(dreamy|atmospheric|calm|meditat\w*)\b", "ambient"), (r"\b(groovy|groove)\b", "funk"),
    (r"\b(rap|hip ?hop|beat)\b", "lofi_hiphop"), (r"\b(guitar|band)\b", "rock"),
]
MOOD_MODE = [(r"\b(sad|dark|melanchol\w*|moody|minor|sombre|somber|tense)\b", "minor"),
             (r"\b(happy|bright|uplifting|major|joyful|sunny)\b", "major")]
MODES = ["harmonic minor", "major", "minor", "dorian", "phrygian", "lydian", "mixolydian", "locrian", "ionian", "aeolian"]
ROLE_WORDS = {
    "drums": ["drums", "drum", "beat", "percussion", "kit"],
    "bass": ["bass", "bassline", "808", "sub", "low end"],
    "chords": ["chords", "keys", "piano", "guitar", "comping", "rhythm guitar", "organ", "stabs", "electric piano", "rhodes"],
    "pad": ["pad", "pads", "strings", "string", "choir", "atmosphere", "texture", "warm pad"],
    "arp": ["arp", "arpeggio", "arpeggiator", "ostinato", "sequence"],
    "lead": ["lead", "melody", "hook", "topline", "solo", "flute", "sax", "synth lead", "vocal"],
}
INSTR_ALIASES = {"rhodes": "electric_piano", "electric piano": "electric_piano", "ep": "electric_piano",
                 "keys": "electric_piano", "string": "strings", "synth lead": "saw_lead", "808": "synth_bass",
                 "sub": "synth_bass", "acoustic guitar": "guitar", "nylon guitar": "guitar", "saxophone": "sax",
                 "horns": "brass", "horn": "brass", "vibes": "vibraphone", "bells": "bell", "plucks": "pluck"}


class NotUnderstood(ValueError):
    pass


NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "twelve": 12,
             "sixteen": 16, "a": 1, "an": 1}


def _num(s: str) -> int | None:
    if s is None:
        return None
    s = s.strip().lower()
    return int(s) if s.lstrip("-").isdigit() else NUM_WORDS.get(s)


def _key_mode(text: str) -> tuple[str | None, str | None]:
    m = re.search(r"\b(?:in|key of|key)\s+([A-Ga-g])(#|b|sharp|flat)?(?:\s*(m(?:in(?:or)?)?|maj(?:or)?|"
                  + "|".join(x.replace(" ", r"\s") for x in MODES) + r"))?\b", text)
    if not m:
        return None, None
    key = m.group(1).upper() + {"sharp": "#", "flat": "b", "#": "#", "b": "b"}.get(m.group(2) or "", "")
    md = (m.group(3) or "").lower().strip()
    mode = None
    if md in ("m", "min", "minor"):
        mode = "minor"
    elif md in ("maj", "major"):
        mode = "major"
    elif md:
        mode = md.replace(" ", "_")
    return key, mode


# Mood / use-case vocabulary for people who don't name a genre. Each entry: regex, then what it implies.
# genre is used only when the prompt names no genre; tempo is "min", "max" or a factor of the genre default.
PRESETS = [
    (r"\b(horror|scary|spooky|creepy|haunted|halloween|eerie|sinister|nightmare|zombie|ghost\w*)\b",
     {"label": "dark/scary", "genre": "ambient", "mode": "minor", "tempo": "min"}),
    (r"\b(lullaby|lullabies|bedtime|sleep\w*|nursery|baby)\b",
     {"label": "lullaby", "genre": "ambient", "mode": "major", "tempo": "min", "exclude": ["drums"]}),
    (r"\b(peaceful|relax\w*|meditat\w*|spa|yoga|serene|tranquil|soothing|gentle)\b",
     {"label": "calm", "genre": "ambient", "mode": "major", "tempo": "min", "exclude": ["drums"]}),
    (r"\b(calm|dreamy|atmospheric|floating|ethereal)\b", {"label": "calm", "genre": "ambient", "tempo": "min"}),
    (r"\b(boss|battle|fight\w*|combat|war|chase|action|intense|aggressive)\b",
     {"label": "intense/action", "genre": "cinematic", "mode": "minor", "tempo": "max"}),
    (r"\b(mysterious|mystery|suspense\w*|stealth|sneak\w*|detective|tense|tension)\b",
     {"label": "tense/mysterious", "genre": "cinematic", "mode": "minor", "tempo": 0.9}),
    (r"\b(workout|gym|running|hype|energetic|pump\w*)\b", {"label": "energetic", "genre": "edm", "tempo": "max"}),
    (r"\b(heroic|hero|victory|victorious|fanfare|triumph\w*|adventure\w*|winner|wins?|celebrat\w*|epic|trailer)\b",
     {"label": "heroic/triumphant", "genre": "cinematic", "mode": "major"}),
    (r"\b(8[ -]?bit|chiptune|chip tune|retro game|video ?games?|game music|game menu|arcade|pixel|nes|gameboy)\b",
     {"label": "retro video game", "genre": "synthwave", "tempo": "max", "lead": "square_lead"}),
    (r"\b(christmas|xmas|holiday|festive|winter wonderland|sleigh)\b",
     {"label": "festive", "genre": "pop", "mode": "major", "lead": "bell"}),
    (r"\b(beach|tropical|summer|island|vacation)\b", {"label": "sunny/tropical", "genre": "reggae", "mode": "major"}),
    (r"\b(party|celebration)\b", {"label": "party", "genre": "house", "tempo": "max"}),
    (r"\b(corporate|presentation|commercial|advert\w*|ad|jingle|vlog|youtube|explainer|podcast|background)\b",
     {"label": "light background", "genre": "pop", "mode": "major"}),
    (r"\b(romantic|romance|love|dinner|candle\w*|wedding)\b", {"label": "romantic", "genre": "neo_soul", "tempo": "min"}),
    (r"\b(rain\w*|lonely|loneliness|nostalgi\w*|melancholy|melancholic|rainy)\b",
     {"label": "wistful", "genre": "lofi_hiphop", "mode": "minor", "tempo": 0.9}),
    (r"\b(sad|sadness|breakup|heartbreak\w*|grief|crying|depress\w*|emotional|somber|sombre|moody)\b",
     {"label": "sad", "mode": "minor", "tempo": 0.85}),
    (r"\b(happy|cheerful|joyful|bright|sunny|uplifting|playful|fun)\b", {"label": "happy", "mode": "major", "tempo": 1.05}),
    (r"\b(chill|study|cozy|mellow|coffee|cafe|lofi)\b", {"label": "chill", "genre": "lofi_hiphop"}),
]
# instrument words a non-musician uses -> (GM program, track name); used for "sad piano", "violin solo", "piano piece"
SOLO_INSTRUMENTS = {"piano": (0, "Piano"), "violin": (40, "Violin"), "cello": (42, "Cello"), "guitar": (24, "Acoustic guitar"),
                    "acoustic guitar": (24, "Acoustic guitar"), "flute": (73, "Flute"), "harp": (46, "Harp"),
                    "music box": (10, "Music box"), "strings": (48, "Strings"), "sax": (65, "Sax"), "saxophone": (65, "Sax"),
                    "organ": (19, "Organ"), "marimba": (12, "Marimba")}
STOPWORDS = set("""a an the for my our your of to in on at with and or but some something like music song songs track tune
piece pieces sound sounds kind sort type style vibe vibes feel feeling i me we want need make made please would could that this
it its is be really very bit little more less long short length about around just only plus also as from into by when
where while player players scene video videos app apps level levels theme themes loop loops intro outro clip minute minutes
second seconds sec secs min mins s new give get create write compose play playing played one two three four five six seven
eight nine ten fifteen twenty thirty sixty half quarter mood vibe ish so too menu soft warm nice good cool some
driving drive night day time people kids family friends work project film movie short game games""".split())
NUM_WORDS_BIG = {"ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50,
                 "sixty": 60, "ninety": 90, "half a": 0.5, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def _vocab() -> set[str]:
    words = set()
    for pat, _ in PRESETS + MOOD_GENRE + MOOD_MODE:
        words |= set(re.findall(r"[a-z]{3,}", re.sub(r"\\w\*|\\b|\[.*?\]|\?", " ", pat)))
    for ws in ROLE_WORDS.values():
        for w in ws:
            words |= set(w.split())
    words |= set(SOLO_INSTRUMENTS) | {w for k in available_genres() for w in k.split("_")}
    words |= {"slow", "fast", "upbeat", "laid", "back", "jazz", "jazzy", "rock", "house", "techno", "trap", "funk", "reggae",
              "ambient", "cinematic", "synthwave", "pop", "metal", "solo", "major", "minor", "bars", "bpm", "tempo", "key",
              "without", "drums", "beat", "beats", "orchestral", "electronic", "dance", "violin", "cello", "harp", "bells"}
    return {w for w in words if len(w) >= 3}


_VOCAB: set[str] | None = None


def correct_typos(text: str, extra: set[str] | None = None) -> tuple[str, dict]:
    """Fix likely typos ("chil" -> "chill", "vidoe" -> "video", "sader" -> "sadder") against the known vocabulary."""
    import difflib
    global _VOCAB
    if _VOCAB is None:
        _VOCAB = _vocab()
    vocab = _VOCAB | (extra or set())
    fixed = {}

    def sub(m):
        w = m.group(0)
        lw = w.lower()
        if len(lw) < 4 or lw in vocab or lw in STOPWORDS or lw.isdigit() or any(
                re.search(pat, lw) for pat, _ in PRESETS + MOOD_GENRE + MOOD_MODE):
            return w
        c = difflib.get_close_matches(lw, vocab | STOPWORDS, n=1, cutoff=0.8)
        if c and c[0] != lw and c[0] not in STOPWORDS:
            fixed[lw] = c[0]
            return c[0]
        return w
    return re.sub(r"[A-Za-z]+", sub, text), fixed


def parse_seconds(text: str) -> float | None:
    """'15 seconds', '30-second', '1 minute', 'a minute and a half', '1:30', '2 mins' -> seconds."""
    t = text.lower()
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    if re.search(r"\b(a|one) minute and a half\b|\b1\.5 ?min", t):
        return 90.0
    num = r"(\d+(?:\.\d+)?|half an?|half a|an?|one|two|three|four|five|ten|fifteen|twenty|thirty|forty-five|forty|fifty|sixty|ninety)"
    m = re.search(rf"\b{num}[\s-]*(seconds?|secs?|s|minutes?|mins?|m)\b", t)
    if not m:
        return None
    n = m.group(1)
    if m.group(2) == "s" and n.isdigit() and int(n) in (20, 30, 40, 50, 60, 70, 80, 90) and not re.search(rf"\b{n}\s+s\b", t):
        return None  # "80s" = the decade
    v = float(n) if n[0].isdigit() else NUM_WORDS_BIG.get(n.replace("half an", "half a"), 1)
    unit = m.group(2)
    if unit in ("m",) and v > 10:  # "30m" is ambiguous; treat >10 as seconds is wrong, so skip
        return None
    return v * (60 if unit.startswith("m") else 1)


def duration_seconds(spec: dict) -> float:
    from .spec import total_bars
    num = spec.get("time_signature", [4, 4])[0]
    return round(total_bars(spec) * num * 60.0 / float(spec["tempo"]), 1)


def fmt_duration(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}" if sec >= 60 else f"{sec} s"


MAX_DEFAULT_SECONDS = 300.0  # longer default arrangements are shortened
CAPPED_SECONDS = 150.0


def bars_for_seconds(seconds: float, tempo: float, beats_per_bar: int = 4) -> int:
    return max(2, min(256, int(round(seconds * tempo / 60.0 / beats_per_bar))))


def _solo_instruments(t: str) -> list[str]:
    found = []
    for name in sorted(SOLO_INSTRUMENTS, key=len, reverse=True):
        m = re.search(rf"\b{name}s?\b", t)
        if m and not any(name in f for f, _ in found):
            found.append((name, m.start()))
    return [f for f, _ in sorted(found, key=lambda x: x[1])]


def interpret(prompt: str, seed: int = 1) -> tuple[dict, dict]:
    """prompt -> (spec, info). info says what was understood: genre (and why), mood, key, tempo, length, solo
    instruments, typo corrections and the words that were not used, so callers can print "Interpreted as: ..."."""
    raw = prompt.strip()
    if not raw:
        raise ValueError('empty prompt; describe the music, e.g. midigen new "calm piano for a meditation app"')
    fixed_text, typos = correct_typos(raw)
    t = fixed_text.lower()
    info: dict = {"typos": typos, "moods": [], "genre_reason": None}
    genre = detect_genre(t)
    if genre:
        info["genre_reason"] = "named"
    presets = [(pat, p) for pat, p in PRESETS if re.search(pat, t)]
    info["moods"] = list(dict.fromkeys(p["label"] for _, p in presets))
    if genre is None:
        for pat, g in MOOD_GENRE:
            if re.search(pat, t):
                genre, info["genre_reason"] = g, f"from '{re.search(pat, t).group(0)}'"
                break
    if genre is None:
        for pat, p in presets:
            if p.get("genre"):
                genre, info["genre_reason"] = p["genre"], f"from '{re.search(pat, t).group(0)}'"
                break
    solo = _solo_instruments(t)
    if genre is None and solo and "cinematic" in available_genres() and any(x in solo for x in ("violin", "cello", "strings")):
        genre, info["genre_reason"] = "cinematic", f"from '{solo[0]}'"
    if genre is None:
        genre, info["genre_reason"] = "pop", "default"
    g = get_genre(genre)
    key, mode = _key_mode(fixed_text)
    if mode is None:
        for m in MODES:
            if re.search(rf"\b{m}\b", t) and m not in ("minor", "major"):
                mode = m.replace(" ", "_")
                break
    want = None
    if mode is None:
        want = next((p["mode"] for _, p in presets if p.get("mode")), None)
        if want is None:
            for pat, md in MOOD_MODE:
                if re.search(pat, t):
                    want = md
                    break
    if want:
        if want in g["modes"]:
            mode = want
        elif want == "minor":
            mode = next((x for x in g["modes"] if x in ("minor", "dorian", "phrygian", "aeolian", "harmonic_minor")), "minor")
        else:
            mode = next((x for x in g["modes"] if x in ("major", "lydian", "mixolydian", "ionian")), "major")
    tr = g["tempo"]
    tempo = None
    m = re.search(r"(\d{2,3})\s*(?:bpm|beats per minute)", t) or re.search(r"\b(?:at|tempo)\s+(\d{2,3})\b", t)
    if m:
        tempo = max(20, min(300, int(m.group(1))))
    elif re.search(r"\b(fast|upbeat|energetic|quick|driving)\b", t):
        tempo = tr["max"]
    elif re.search(r"\b(slow|laid.?back|slower)\b", t):
        tempo = tr["min"]
    else:
        tw = next((p["tempo"] for _, p in presets if p.get("tempo")), None)
        if tw == "min":
            tempo = tr["min"]
        elif tw == "max":
            tempo = tr["max"]
        elif isinstance(tw, float):
            tempo = round(max(tr["min"], min(tr["max"], tr["default"] * tw)), 1)
            if tw < 1 and tempo >= tr["default"]:
                tempo = tr["min"]
    bars = None
    m = re.search(r"(\d+|\w+)[\s-]*bars?\b", t)
    if m and _num(m.group(1)):
        bars = max(1, min(256, _num(m.group(1))))
    seconds = parse_seconds(t)
    m = re.search(r"seed\s*(\d+)", t)
    if m:
        seed = int(m.group(1))
    roles = None
    exclude = []
    for role, words in ROLE_WORDS.items():
        for w in words:
            if re.search(rf"\b(no|without)\s+(?:\w+\s+)?{re.escape(w)}\b", t):
                exclude.append(role)
    only = re.search(r"\bjust\s+([\w\s,and]+)", t)
    if only:
        roles = [r for r, ws in ROLE_WORDS.items() if any(re.search(rf"\b{re.escape(w)}\b", only.group(1)) for w in ws)] or None
    drum_words = re.search(r"\b(drums?|beats?|percussion|groove|kit)\b", t) and not re.search(r"\b(no|without)\s+(?:\w+\s+)?(drums?|beats?|percussion)\b", t)
    solo_mode = bool(solo) and not drum_words and (
        info["genre_reason"] in ("default",) or re.search(r"\b(solo|only|just|piece|alone|unaccompanied)\b", t)
        or any(re.search(rf"\b{x}\b", t) for x in ("lullaby", "meditat", "sad", "calm", "gentle")) or len(solo) >= 1 and genre in ("ambient", "cinematic", "pop"))
    if solo_mode:
        roles = ["chords", "lead"] + (["pad"] if "strings" in solo and len(solo) > 1 else [])
        info["solo"] = solo
    for _, p in presets:
        for r in p.get("exclude", []):
            if r not in exclude and not (drum_words and r == "drums"):
                exclude.append(r)
    if roles:
        roles = [r for r in roles if r not in exclude] or roles
        roles = [r for r in roles if r in {v.get("role", k) for k, v in g["instruments"].items()} | set(g["instruments"])] or roles
    kw = dict(key=key, mode=mode, tempo=tempo, bars=bars, seed=seed, roles=roles, exclude_roles=exclude, title=_title(raw))
    spec = build_spec(genre, **kw)
    if seconds and not bars:
        nb = bars_for_seconds(seconds, float(spec["tempo"]), spec.get("time_signature", [4, 4])[0])
        spec = build_spec(genre, **{**kw, "bars": nb})
        info["seconds_requested"] = seconds
    elif not bars and duration_seconds(spec) > MAX_DEFAULT_SECONDS:
        # slow genres (ambient at 55 BPM) default to 10+ minutes; cap prompt defaults to a usable length
        nb = bars_for_seconds(CAPPED_SECONDS, float(spec["tempo"]), spec.get("time_signature", [4, 4])[0])
        spec = build_spec(genre, **{**kw, "bars": nb})
        info["length_capped"] = True
    if solo_mode:
        spec = _apply_solo(spec, solo)
    lead_inst = next((p["lead"] for _, p in presets if p.get("lead")), None)
    if lead_inst and not solo_mode:
        from .spec import INSTRUMENTS
        for trk in spec["tracks"]:
            if trk["role"] == "lead" and lead_inst in INSTRUMENTS:
                trk["program"] = INSTRUMENTS[lead_inst]
                trk["name"] = lead_inst.replace("_", " ").capitalize() + " lead"
                break
    used = set()
    for pat, _ in PRESETS + MOOD_GENRE + MOOD_MODE:
        used |= {w for mm in re.finditer(pat, t) for w in re.findall(r"[a-z0-9]+", mm.group(0))}
    used |= set(solo) | {w for x in solo for w in x.split()} | set(genre.split("_"))
    gm = detect_genre(t)
    unknown = [w for w in re.findall(r"[a-z]+", t) if w not in STOPWORDS and w not in used and len(w) > 2
               and w not in _vocab_cached() and not (gm and w in gm)]
    info.update(genre=genre, key=spec["key"], mode=spec["mode"], tempo=spec["tempo"], bars=_total(spec),
                seconds=duration_seconds(spec), unknown=list(dict.fromkeys(unknown)),
                instruments=[trk["name"] for trk in spec["tracks"]])
    return spec, info


def _vocab_cached() -> set[str]:
    global _VOCAB
    if _VOCAB is None:
        _VOCAB = _vocab()
    return _VOCAB


def _total(spec: dict) -> int:
    from .spec import total_bars
    return total_bars(spec)


def _apply_solo(spec: dict, solo: list[str]) -> dict:
    """Make the chords and lead tracks play the requested instrument(s): "sad piano" = piano chords + piano melody,
    "violin solo" = violin melody over soft strings, "piano and strings" = piano chords/melody + a strings pad."""
    from .ops import apply_ops
    first = solo[0]
    lead = SOLO_INSTRUMENTS[first]
    chords = SOLO_INSTRUMENTS["strings"] if first in ("violin", "cello", "strings") else (
        lead if first not in ("flute", "sax") else SOLO_INSTRUMENTS["piano"])
    want_pad = "strings" in solo and first != "strings"
    ops = []
    roles = {t["role"] for t in spec["tracks"]}
    if "chords" not in roles:
        ops.append({"op": "add_instrument", "role": "chords", "name": chords[1] + " (chords)"})
    if "lead" not in roles:
        ops.append({"op": "add_instrument", "role": "lead", "name": lead[1] + " (melody)"})
    if want_pad and "pad" not in roles:
        ops.append({"op": "add_instrument", "role": "pad", "name": "Strings"})
    if ops:
        spec = apply_ops(spec, ops)
    for trk in spec["tracks"]:
        if trk["role"] == "chords":
            trk["program"], trk["name"] = chords[0], chords[1] + " (chords)"
        elif trk["role"] == "lead":
            trk["program"], trk["name"] = lead[0], lead[1] + " (melody)"
        elif trk["role"] == "pad" and want_pad:
            trk["program"], trk["name"] = 48, "Strings"
    return spec


def describe(info: dict) -> str:
    """One plain line: 'Interpreted as: sad pop (default genre) · G minor · 98 BPM · 16 bars ≈ 39 s · ...'"""
    gr = info.get("genre_reason")
    why = (" (default genre — no genre word recognised)" if info.get("moods") else
           " (default — no genre or mood word recognised)") if gr == "default" else (f" ({gr})" if gr and gr != "named" else "")
    mood = (", ".join(info["moods"]) + " ") if info.get("moods") else ""
    parts = [f"Interpreted as: {mood}{info['genre'].replace('_', ' ')}{why}", f"{info['key']} {info['mode'].replace('_', ' ')}",
             f"{info['tempo']:g} BPM", f"{info['bars']} bars ≈ {fmt_duration(info['seconds'])}"]
    if info.get("seconds_requested"):
        parts[-1] += f" (asked for {fmt_duration(info['seconds_requested'])})"
    if info.get("solo"):
        parts.append("solo " + " + ".join(info["solo"]) + ", no drums")
    s = " · ".join(parts)
    if info.get("typos"):
        s += "\n  fixed typos: " + ", ".join(f"{a} → {b}" for a, b in info["typos"].items())
    if info.get("unknown"):
        s += "\n  didn't understand: " + ", ".join(f"'{w}'" for w in info["unknown"])
    return s


LAST_INTERPRETATION: dict = {}


def prompt_to_spec(prompt: str, seed: int = 1) -> dict:
    spec, info = interpret(prompt, seed=seed)
    LAST_INTERPRETATION.clear()
    LAST_INTERPRETATION.update(info)
    return spec


def _title(prompt: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", prompt)[:6]
    while len(words) > 1 and words[-1].lower() in ("a", "an", "the", "for", "to", "of", "with", "and", "in", "my", "at"):
        words.pop()
    return " ".join(w[:1].upper() + w[1:].lower() if not w[0].isdigit() else w.title() for w in words) or "Untitled"


def _role_of(phrase: str) -> tuple[str | None, str | None]:
    """Return (role, instrument) for a phrase like 'strings' or 'a rhodes'."""
    p = phrase.lower().strip()
    p = re.sub(r"^(a|an|some|the|more)\s+", "", p)
    inst = INSTR_ALIASES.get(p) or (p.replace(" ", "_") if p.replace(" ", "_") in INSTRUMENTS else None)
    for role, words in ROLE_WORDS.items():
        if p in words or any(re.search(rf"\b{re.escape(w)}\b", p) for w in words):
            return role, inst if inst != role else None
    if inst:
        return ("pad" if inst in ("strings", "choir", "pad", "warm_pad", "space_pad") else
                "lead" if inst in ("flute", "sax", "square_lead", "saw_lead", "lead", "bell") else "chords"), inst
    return None, None


def _track_ref(spec: dict, phrase: str) -> str | None:
    phrase = re.sub(r"^(the|a|an)\s+", "", phrase.strip().lower())
    for t in spec["tracks"]:
        if t["name"].lower() == phrase:
            return t["name"]
    try:
        return find_track(spec, phrase)["name"]
    except (SpecError, KeyError, ValueError):
        pass
    role, _ = _role_of(phrase)
    if role:
        for t in spec["tracks"]:
            if t["role"] == role:
                return t["name"]
    return None


def _section_ref(spec: dict, phrase: str) -> str | None:
    phrase = re.sub(r"^(the|a|an)\s+", "", phrase.strip().lower())
    for s in spec["sections"]:
        if s["name"].lower() == phrase:
            return s["name"]
    for s in spec["sections"]:
        if s["name"].lower().startswith(phrase) or phrase.startswith(s["name"].lower()):
            return s["name"]
    syn = {"drop": ["chorus", "hook", "peak", "climax"], "chorus": ["drop", "hook", "peak", "climax"],
           "hook": ["chorus", "drop"], "verse": ["a", "groove"], "breakdown": ["break", "bridge", "b"], "bridge": ["b", "break"]}
    for alt in syn.get(phrase, []):
        for s in spec["sections"]:
            if s["name"].lower().rstrip("0123456789") == alt:
                return s["name"]
    secs = spec["sections"]
    if phrase in ("chorus", "drop", "hook", "peak", "climax", "loudest part", "main part"):
        return max(secs, key=lambda s: s.get("energy", 0.5))["name"]
    if phrase in ("verse", "groove"):
        mid = [s for s in secs if s["name"] not in ("intro", "outro")] or secs
        return min(mid, key=lambda s: s.get("energy", 0.5))["name"]
    if phrase in ("breakdown", "bridge", "break") and len(secs) > 2:
        inner = secs[1:-1]
        return min(inner, key=lambda s: s.get("energy", 0.5))["name"]
    if phrase in ("start", "beginning", "opening"):
        return secs[0]["name"]
    if phrase in ("end", "ending", "last section"):
        return secs[-1]["name"]
    return None


def _split(text: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;|\bthen\b|\band then\b|\balso\b|\band\b(?=\s+(?:make|add|remove|drop|mute|unmute|set|change|"
                     r"transpose|use|turn|put|give|extend|lengthen|repeat|humani[sz]e|swing|reharmoni[sz]e|speed|slow)))\s*", text, flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


def edit_to_ops(spec: dict, instruction: str) -> list[dict]:
    ops: list[dict] = []
    unknown = []
    for clause in _split(instruction.strip().rstrip(".!")):
        got = _clause_any(spec, clause)
        if got is None:  # retry once with typos fixed ("fastr" -> "faster")
            fixed, changes = correct_typos(clause, EDIT_WORDS)
            if changes:
                got = _clause_any(spec, fixed)
        if got is None:
            unknown.append(clause)
        else:
            ops += got
    if unknown or not ops:
        raise NotUnderstood(_not_understood(unknown or [instruction]))
    return _check_adds(spec, ops)


def _clause_any(spec: dict, clause: str) -> list[dict] | None:
    got = None
    if re.search(r"\band\b", clause, re.I):
        pieces = [x.strip() for x in re.split(r"\band\b", clause, flags=re.I) if x.strip()]
        sub = [_clause_ops(spec, x.lower(), x) or _usability_ops(spec, x.lower()) for x in pieces]
        if len(pieces) > 1 and all(sub):
            got = [o for g in sub for o in g]
    if got is None:
        got = _usability_ops(spec, clause.lower()) if _USABILITY_FIRST.search(clause.lower()) else None
    if got is None:
        got = _clause_ops(spec, clause.lower(), clause)
    if got is None:
        got = _usability_ops(spec, clause.lower())
    return got


def _clause_ops(spec: dict, c: str, orig: str | None = None) -> list[dict] | None:
    orig = orig or c
    # tempo
    m = re.search(r"(?:tempo|bpm|speed)\s*(?:to|=|at|of)?\s*(\d{2,3})|(\d{2,3})\s*bpm", c)
    if m:
        return [{"op": "set_tempo", "bpm": int(m.group(1) or m.group(2))}]
    m = re.search(r"\b(faster|speed (?:it )?up|quicker|slower|slow (?:it )?down)\b(?:\s*by\s*(\d+))?", c)
    if m:
        n = int(m.group(2) or 10)
        return [{"op": "change_tempo", "delta": n if m.group(1) in ("faster", "quicker") or "up" in m.group(1) else -n}]
    # transpose
    m = re.search(r"(?:transpose|shift|move|pitch)\s*(?:it\s*)?(up|down)?\s*(?:by\s*)?(\d+|an?|one|two|three|four|five|seven|twelve)?\s*"
                  r"(semitones?|half.?steps?|steps?|whole.?steps?|tones?|octaves?)?\s*(up|down)?", c)
    if m and (m.group(1) or m.group(4)) and re.search(r"transpose|shift|pitch|move (it )?(up|down)", c):
        n = _num(m.group(2)) or 1
        unit = m.group(3) or "semitone"
        n *= 12 if unit.startswith("octave") else 2 if (unit.startswith("whole") or unit.startswith("tone") or unit == "step" or unit == "steps") else 1
        return [{"op": "transpose", "semitones": n if (m.group(1) or m.group(4)) == "up" else -n}]
    # genre restyle
    m = re.search(r"(?:make it|turn (?:it )?into|as|in the style of|restyle (?:it )?as|switch to|more like)\s+(?:a |an )?(.+?)(?:\s+(?:song|track|tune|version|style|vibe))?$", c)
    if m:
        g = detect_genre(m.group(1))
        if g:
            return [{"op": "apply_genre", "genre": g, "keep_harmony": "keep" in c or "same chords" in c}]
    # key / mode
    key, mode = _key_mode(c)
    if key:
        return [{"op": "set_key", "key": key, **({"mode": mode} if mode else {})}]
    m = re.search(r"\b(?:make it|in|to|switch to|change to)\s+(?:a\s+)?(harmonic minor|major|minor|dorian|phrygian|lydian|mixolydian|locrian)\b", c)
    if m:
        return [{"op": "set_key", "key": spec["key"], "mode": m.group(1).replace(" ", "_")}]
    if re.search(r"\b(sadder|darker|more melancholic)\b", c) and "chord" not in c:
        return [{"op": "set_key", "key": spec["key"], "mode": "minor"}] if spec["mode"] not in ("minor", "aeolian", "dorian", "phrygian") \
            else [{"op": "set_key", "key": spec["key"], "mode": "minor"},
                  {"op": "change_tempo", "delta": -8}]
    if re.search(r"\b(happier|brighter)\b", c):
        return [{"op": "set_key", "key": spec["key"], "mode": "major"}]
    # harmony
    sec = None
    ms = re.search(r"\b(?:in|for|of|on)\s+the\s+(\w+)", c)
    if ms:
        sec = _section_ref(spec, ms.group(1))
    tech = None
    if re.search(r"secondary dominant", c):
        tech = "secondary_dominants"
    elif re.search(r"borrow|modal mixture|darker chords|minor iv", c):
        tech = "borrowed"
    elif re.search(r"9ths|ninths|lusher|richer|neo.?soul chords", c):
        tech = "9ths"
    elif re.search(r"7ths|sevenths|jazzier|jazzy chords|extensions|more colou?r", c):
        tech = "7ths"
    elif re.search(r"simpler chords|triads|plain chords|simplify (?:the )?chords", c):
        tech = "triads"
    elif re.search(r"reharmoni[sz]e|new chords|different chords|change the chords", c):
        tech = "functional"
    if tech:
        return [{"op": "reharmonize", "technique": tech, **({"section": sec} if sec else {})}]
    m = re.search(r"(authentic|plagal|half|deceptive)\s+cadence", c)
    if m:
        target = sec or spec["arrangement"][-1]
        return [{"op": "set_cadence", "section": target, "cadence": m.group(1)}]
    m = re.search(r"chords?\s*(?:to|:)\s*([A-Ga-gIViv#b0-9/ømajdinsu°+\- ]+?)(?:\s+(?:in|for) the (\w+))?$", orig, re.I)
    if m and len(m.group(1).split()) >= 1:
        target = _section_ref(spec, m.group(2).lower()) if m.group(2) else None
        chords = m.group(1).replace("-", " ").split()
        return [{"op": "set_chords", "section": s["name"], "chords": chords} for s in spec["sections"]
                if target is None or s["name"] == target]
    # drums
    for p in sorted(DRUM_PATTERNS, key=len, reverse=True):
        pw = p.replace("_", "[ -]?")
        if re.search(rf"\b{pw}\b\s*(?:beat|drums|groove|pattern|rhythm)|(?:drums?|beat|groove|pattern)\s*(?:to|like)?\s*(?:a\s+)?\b{pw}\b", c):
            return [{"op": "set_drum_pattern", "pattern": p, **({"section": sec} if sec else {})}]
    # density
    m = re.search(r"\b(busier|more busy|denser|more notes|more active|sparser|less busy|simpler|fewer notes|calmer|thinner)\b", c)
    if m:
        up = m.group(1) in ("busier", "more busy", "denser", "more notes", "more active")
        ref = re.sub(r"\b(make|the|more|less|busier|denser|sparser|simpler|calmer|thinner|busy|notes|fewer|active|a bit|it|in the \w+)\b", " ", c)
        tr = _track_ref(spec, ref.strip()) if ref.strip() else None
        return [{"op": "set_density", "delta": 0.2 if up else -0.2, **({"track": tr} if tr else {}), **({"section": sec} if sec else {})}]
    if re.search(r"humani[sz]e|more human|loosen|less robotic|feel more natural", c):
        return [{"op": "humanize", "timing": 0.02, "velocity": 12}]
    if re.search(r"\b(straight|quantis|quantiz|no swing|remove swing)", c):
        return [{"op": "set_swing", "amount": 0.0}]
    if re.search(r"\bswing", c):
        return [{"op": "set_swing", "amount": 0.6 if "more" not in c else round(min(1.0, spec.get("swing", 0) + 0.3), 3), "unit": "1/8" if "8" in c else "1/16"}]
    # structure
    m = re.search(r"(?:extend|lengthen|longer)\s+(?:the\s+)?(\w+)(?:\s+by\s+(\d+|\w+)\s*bars?)?|make\s+the\s+(\w+)\s+longer(?:\s+by\s+(\d+|\w+)\s*bars?)?", c)
    if m:
        target = _section_ref(spec, m.group(1) or m.group(3))
        if target:
            return [{"op": "extend_section", "section": target, "bars": _num(m.group(2) or m.group(4)) or 4}]
    m = re.search(r"(?:shorten|shorter)\s+(?:the\s+)?(\w+)(?:\s+by\s+(\d+|\w+)\s*bars?)?|make\s+the\s+(\w+)\s+shorter", c)
    if m:
        target = _section_ref(spec, m.group(1) or m.group(3))
        if target:
            sec_d = find_section(spec, target)
            return [{"op": "extend_section", "section": target, "bars": -min(sec_d["bars"] - 1, _num(m.group(2)) or sec_d["bars"] // 2)}]
    m = re.search(r"(?:repeat|another|double)\s+(?:the\s+)?(\w+)", c)
    if m and _section_ref(spec, m.group(1)):
        return [{"op": "repeat_section", "section": _section_ref(spec, m.group(1))}]
    # tracks
    m = re.search(r"\b(?:remove|delete|drop|lose|get rid of|take out)\s+(?:the\s+)?(.+)", c)
    if m:
        tr = _track_ref(spec, m.group(1))
        if tr:
            return [{"op": "remove_track", "track": tr}]
    m = re.search(r"\b(un)?mute\s+(?:the\s+)?(.+)", c)
    if m:
        tr = _track_ref(spec, m.group(2))
        if tr:
            return [{"op": "set_track", "track": tr, "mute": not m.group(1)}]
    m = re.search(r"\b(?:add|put in|bring in|include|layer)\s+(?:a\s+|an\s+|some\s+)?(.+?)(?:\s+(?:in|to|on) the (\w+))?$", c)
    if m:
        role, inst = _role_of(m.group(1))
        if role:
            from .schema import STYLES
            gtype = {"lead": "lead", "bass": "bass", "chords": "chords", "pad": "pad", "arp": "arp"}.get(role)
            style = next((st for st in (STYLES.get(gtype) or []) if re.search(rf"\b{st.replace('_', '[ _-]?')}\b", c)), None)
            existing = [t for t in spec["tracks"] if t["role"] == role and t["generator"]["type"] == gtype]
            if style and existing:
                return [{"op": "set_generator", "track": existing[0]["name"], "params": {"style": style}}]
            op = {"op": "add_instrument", "role": role}
            if inst:
                op["instrument"] = inst
            if m.group(2) and _section_ref(spec, m.group(2)):
                op["sections"] = [_section_ref(spec, m.group(2))]
            return [op]
    m = re.search(r"\b(?:new|different|another|fresh)\s+(?:idea|version|take|melody|bassline|variation)(?:\s+(?:for|of)\s+the\s+(.+))?|\b(?:regenerate|reroll|reseed|shuffle)\s*(?:the\s+)?(.*)", c)
    if m:
        ref = m.group(1) or m.group(2) or ""
        if "melody" in c and not ref:
            ref = "lead"
        if "bassline" in c and not ref:
            ref = "bass"
        tr = _track_ref(spec, ref) if ref.strip() else None
        return [{"op": "reseed", **({"track": tr} if tr else {})}]
    m = re.search(r"(?:call it|title|name it|rename (?:it|the song) to)\s+[\"']?(.+?)[\"']?$", c)
    if m:
        return [{"op": "set_title", "title": m.group(1).strip().title()}]
    m = re.search(r"\b(louder|quieter|softer)\s+(.+)|\b(?:make\s+)?(?:the\s+)?(.+?)\s+(louder|quieter|softer)\b", c)
    if m:
        ref = m.group(2) or m.group(3)
        tr = _track_ref(spec, ref)
        if tr:
            t = find_track(spec, tr)
            up = (m.group(1) or m.group(4)) == "louder"
            return [{"op": "set_track", "track": tr, "volume": max(0, min(127, t.get("volume", 100) + (15 if up else -15)))}]
    return None


HELP = """Things the offline parser understands (combine with 'and' / commas):
  faster / slower [by N] · tempo 120 · transpose up 2 · in D minor · make it dorian · sadder / brighter
  make it house / turn it into jazz [keep chords] · jazzier chords / add 9ths / secondary dominants / borrowed chords
  simpler chords · reharmonize the chorus · chords to Am F C G · deceptive cadence in the chorus
  funk beat / drums to halftime · busier drums / sparser lead · humanize · more swing / straight
  extend the chorus by 4 bars · shorten the intro · repeat the chorus
  add strings / add an arp / add a rhodes in the verse · remove the pad · mute the lead · louder bass
  new melody · new idea for the bass · call it Night Drive
Genres: """ + ", ".join(available_genres())



# ---------------------------------------------------------------- usability phrases (feedback items 1-5, 7)
EDIT_EXAMPLES = ["faster", "slower", "tempo 120", "transpose up 2", "make it minor", "sadder", "happier", "make it jazz",
                 "make it shorter", "make it longer", "make it 30 seconds", "more energy at the end", "loop it", "fade out",
                 "more intense", "more drums", "fewer drums", "fewer instruments", "add strings", "add violin", "add an arp",
                 "remove the pad", "busier bass", "simpler chords", "jazzier chords", "add a bridge", "swing it"]
EDIT_WORDS = {w for e in EDIT_EXAMPLES for w in re.findall(r"[a-z]{4,}", e)} | {
    "faster", "slower", "louder", "quieter", "softer", "shorter", "longer", "busier", "sparser", "drums", "intense",
    "energy", "transpose", "reharmonize", "instruments", "violin", "cello", "piano", "guitar", "strings", "remove", "minor",
    "major", "darker", "brighter", "happier", "sadder", "chorus", "verse", "bridge", "intro", "outro", "melody", "chords"}

# phrases that would otherwise be grabbed by a broader rule in _clause_ops ("make it 30 seconds" is not a genre)
_USABILITY_FIRST = re.compile(r"\b(seconds?|secs?|minutes?|mins?|shorter|longer|loop|fade|intense|violin|cello|viola)\b"
                              r"|\b(?:more|fewer|less)\s+(?:drums|percussion|instruments|tracks)\b|^\s*(?:make it\s+)?(?:sad|happy|chill|calm|dark|upbeat|energetic|epic)\s*$")

_CORE_ROLES = ("drums", "bass", "chords", "lead")


def _not_understood(clauses: list[str]) -> str:
    import difflib
    sugg = []
    for c in clauses:
        sugg += difflib.get_close_matches(c.lower(), EDIT_EXAMPLES, n=3, cutoff=0.45)
    sugg = list(dict.fromkeys(sugg))[:3] or ["faster", "more drums", "make it 30 seconds"]
    return (f"could not understand: {clauses}. Did you mean: " + ", ".join(repr(x) for x in sugg)
            + "? (ops reference: README 'Edit phrases'; or configure an LLM provider for free-form edits)")


def _sec_names(spec: dict) -> list[str]:
    return list(dict.fromkeys(spec["arrangement"]))


def _resize_to(spec: dict, target_bars: int) -> list[dict]:
    cur = _total(spec)
    f = max(0.05, target_bars / max(1, cur))
    ops = []
    for sec in spec["sections"]:
        if sec["name"] not in spec["arrangement"]:
            continue
        nb = max(1, min(64, int(round(sec["bars"] * f))))
        if nb >= 4 and nb % 2:
            nb += 1 if sec["bars"] * f > nb else -1
        if nb != sec["bars"]:
            ops.append({"op": "set_section", "section": sec["name"], "bars": nb})
    return ops


def _end_section(spec: dict) -> str:
    arr = spec["arrangement"]
    for name in reversed(arr):
        if not re.search(r"outro|end|tag", name, re.I) or len(arr) == 1:
            return name
    return arr[-1]


def _drum_tracks(spec: dict) -> list[dict]:
    return [t for t in spec["tracks"] if t["role"] in ("drums", "perc", "percussion") or t["generator"]["type"] == "drums"]


def _usability_ops(spec: dict, c: str) -> list[dict] | None:
    c = c.strip()
    # length: "make it 30 seconds", "2 minutes long", "shorter", "much longer"
    secs = parse_seconds(c)
    if secs and re.search(r"\b(make|long|length|last|seconds?|secs?|minutes?|mins?)\b", c):
        tgt = bars_for_seconds(secs, float(spec["tempo"]), spec.get("time_signature", [4, 4])[0])
        return _resize_to(spec, tgt) or None
    m = re.search(r"\b(a lot |much |a bit |slightly |a little )?(shorter|longer)\b", c)
    if m and not re.search(r"\b(notes?|chords?)\b", c):
        f = {"a lot ": .5, "much ": .5, "a bit ": .8, "slightly ": .85, "a little ": .8}.get(m.group(1) or "", .65)
        f = f if m.group(2) == "shorter" else 1 / f
        sec = _section_ref(spec, c.split(m.group(2))[0].split()[-1]) if c.split(m.group(2))[0].split() else None
        if sec:
            b = next(x["bars"] for x in spec["sections"] if x["name"] == sec)
            return [{"op": "set_section", "section": sec, "bars": max(1, min(64, int(round(b * f))))}]
        return _resize_to(spec, max(2, int(round(_total(spec) * f)))) or None
    # build at the end
    if re.search(r"\b(more energy|louder|bigger|build|climax|lift|stronger)\b.*\b(end|ending|finish|last)\b", c):
        sec = _end_section(spec)
        e = next(x.get("energy", .5) for x in spec["sections"] if x["name"] == sec)
        return [{"op": "set_section", "section": sec, "energy": round(min(1.0, e + .25), 2)},
                {"op": "set_density", "section": sec, "delta": 0.15}]
    # loopable: drop intro/outro so the body repeats seamlessly
    if re.search(r"\b(loop(?:able)?|seamless(?:ly)?|repeat forever)\b", c):
        keep = [a for a in spec["arrangement"] if not re.search(r"intro|outro|ending|tag", a, re.I)]
        if keep and keep != spec["arrangement"]:
            return [{"op": "set_arrangement", "order": keep}]
        return [{"op": "set_title", "title": spec.get("title", "Loop")}] if keep else None
    # fade out: last section quiet and sparse (MIDI has no master fader in the spec; this lowers energy/density)
    if re.search(r"\bfade(?:s|d)?\s*(?:it\s*)?out\b|\bfade at the end\b", c):
        sec = spec["arrangement"][-1]
        return [{"op": "set_section", "section": sec, "energy": 0.15}, {"op": "set_density", "section": sec, "delta": -0.3}]
    if re.search(r"\b(more intense|intense|harder|heavier|punchier|more aggressive|more epic|epic)\b", c):
        return [{"op": "set_section", "section": n,
                 "energy": round(min(1.0, next(x.get("energy", .5) for x in spec["sections"] if x["name"] == n) + .15), 2)}
                for n in _sec_names(spec)] + [{"op": "set_density", "delta": 0.1}]
    m = re.search(r"\b(more|busier|fewer|less|sparser|quieter|softer|louder)\s+(drums|percussion|beat)\b", c)
    if m:
        dr = _drum_tracks(spec)
        up = m.group(1) in ("more", "busier", "louder")
        if not dr:
            return [{"op": "add_instrument", "role": "drums"}] if up else None
        if m.group(1) in ("quieter", "softer", "louder"):
            return [{"op": "set_track", "track": t["name"], "volume": max(0, min(127, t.get("volume", 100) + (15 if up else -20)))}
                    for t in dr]
        return [{"op": "set_density", "track": t["name"], "delta": 0.2 if up else -0.2} for t in dr]
    if re.search(r"\b(fewer|less) (instruments|tracks|parts)\b|\bstrip (?:it )?(?:back|down)\b|\b(sparser|more minimal|thinner)\b$", c):
        extra = [t for t in spec["tracks"] if t["role"] not in _CORE_ROLES]
        n = max(1, len(extra) // 2)
        if extra:
            return [{"op": "remove_track", "track": t["name"]} for t in extra[-n:]]
        return [{"op": "set_density", "delta": -0.15}]
    if re.search(r"\b(more instruments|fuller|thicker)\b", c):
        roles = {t["role"] for t in spec["tracks"]}
        for r, inst in (("pad", "warm_pad"), ("arp", "pluck"), ("counter", "strings")):
            if r not in roles:
                return [{"op": "add_instrument", "role": r, "instrument": inst}]
        return [{"op": "set_density", "delta": 0.1}]
    m = re.search(r"\b(?:add|with|bring in)\s+(?:a |an |some )?(violin|viola|cello)s?\b", c)
    if m:
        name, prog, role = {"violin": ("Violin", 40, "lead"), "viola": ("Viola", 41, "other"),
                            "cello": ("Cello", 42, "other")}[m.group(1)]
        if any(t["name"].lower() == name.lower() for t in spec["tracks"]):
            raise NotUnderstood(f"there is already a {name} track — try 'louder {name.lower()}' or 'busier {name.lower()}'")
        if role == "lead" and any(t["role"] == "lead" for t in spec["tracks"]):
            role = "other"
        rng = {"Violin": [62, 86], "Viola": [53, 74], "Cello": [36, 62]}[name]
        return [{"op": "add_instrument", "role": role, "instrument": "strings", "name": name},
                {"op": "set_track", "track": name, "program": prog},
                {"op": "set_generator", "track": name, "params": {"range": rng}}]
    # bare mood / genre words: "sad", "make it chill", "jazz"
    m = re.fullmatch(r"(?:make it|more|a bit|go|now)?\s*(?:more\s+)?([a-z][a-z &'-]*?)\s*(?:please)?", c)
    if m:
        w = m.group(1).strip()
        if re.fullmatch(r"sad|melancholic|dark|moody|gloomy|spooky|tense|mysterious", w):
            return [{"op": "set_key", "key": spec["key"], "mode": "minor"}] if spec["mode"] in ("major", "lydian", "mixolydian") \
                else [{"op": "reharmonize", "technique": "borrowed"}]
        if re.fullmatch(r"happy|uplifting|bright|cheerful|hopeful|sunny", w):
            return [{"op": "set_key", "key": spec["key"], "mode": "major"}] if spec["mode"] != "major" \
                else [{"op": "change_tempo", "delta": 6}]
        if re.fullmatch(r"chill|calm|relaxed|mellow|laid.?back|gentle|soft|peaceful", w):
            return [{"op": "change_tempo", "delta": -10}, {"op": "set_density", "delta": -0.15}]
        if re.fullmatch(r"upbeat|energetic|driving|hype|exciting|lively", w):
            return [{"op": "change_tempo", "delta": 10}, {"op": "set_density", "delta": 0.1}]
        if len(w.split()) <= 3:
            g = detect_genre(w)
            if g:
                return [{"op": "apply_genre", "genre": g, "keep_harmony": False}]
    return None


def _check_adds(spec: dict, ops: list[dict]) -> list[dict]:
    """'add drums' when there are drums already -> helpful message; new tracks are named after their instrument."""
    from .spec import INSTRUMENTS
    out = []
    for op in ops:
        if op.get("op") == "add_instrument":
            op = dict(op)
            same = [t for t in spec["tracks"] if t["role"] == op["role"]]
            inst = (op.get("instrument") or "").lower().replace(" ", "_")
            if same and (not inst or any(t.get("program") == INSTRUMENTS.get(inst) for t in same)):
                t = same[0]
                hint = {"drums": "busier drums", "bass": "busier bass", "lead": "reseed the melody",
                        "chords": "jazzier chords"}.get(op["role"], f"busier {op['role']}")
                raise NotUnderstood(f"there is already a {op['role']} track ({t['name']}) — try '{hint}', "
                                    f"'louder {t['name'].lower()}', or 'replace the {op['role']} with <instrument>'")
            if inst and not op.get("name"):
                op["name"] = inst.replace("_", " ").title()
        out.append(op)
    return out
