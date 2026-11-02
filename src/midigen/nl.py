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


def prompt_to_spec(prompt: str, seed: int = 1) -> dict:
    t = prompt.lower()
    genre = detect_genre(t)
    if genre is None:
        for pat, g in MOOD_GENRE:
            if re.search(pat, t):
                genre = g
                break
    genre = genre or "pop"
    g = get_genre(genre)
    key, mode = _key_mode(prompt)
    if mode is None:
        for m in MODES:
            if re.search(rf"\b{m}\b", t) and m not in ("minor", "major"):
                mode = m.replace(" ", "_")
                break
    if mode is None:
        for pat, md in MOOD_MODE:
            if re.search(pat, t):
                mode = md if md in g["modes"] or md == "minor" else None
                if md == "minor" and "minor" not in g["modes"]:
                    mode = next((x for x in g["modes"] if x in ("minor", "dorian", "phrygian", "aeolian")), "minor")
                if md == "major" and "major" not in g["modes"]:
                    mode = next((x for x in g["modes"] if x in ("major", "lydian", "mixolydian")), "major")
                break
    tempo = None
    m = re.search(r"(\d{2,3})\s*(?:bpm|beats per minute)", t) or re.search(r"\b(?:at|tempo)\s+(\d{2,3})\b", t)
    if m:
        tempo = max(20, min(300, int(m.group(1))))
    elif re.search(r"\b(fast|upbeat|energetic)\b", t):
        tempo = g["tempo"]["max"]
    elif re.search(r"\b(slow|laid.?back)\b", t):
        tempo = g["tempo"]["min"]
    bars = None
    m = re.search(r"(\d+|\w+)[\s-]*bars?\b", t)
    if m and _num(m.group(1)):
        bars = max(1, min(256, _num(m.group(1))))
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
    spec = build_spec(genre, key=key, mode=mode, tempo=tempo, bars=bars, seed=seed, roles=roles,
                      exclude_roles=exclude, title=_title(prompt))
    return spec


def _title(prompt: str) -> str:
    words = re.findall(r"[A-Za-z']+", prompt)[:6]
    return " ".join(w.capitalize() for w in words) or "Untitled"


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
        got = None
        if re.search(r"\band\b", clause, re.I):
            pieces = [x.strip() for x in re.split(r"\band\b", clause, flags=re.I) if x.strip()]
            sub = [_clause_ops(spec, x.lower(), x) for x in pieces]
            if len(pieces) > 1 and all(sub):
                got = [o for g in sub for o in g]
        if got is None:
            got = _clause_ops(spec, clause.lower(), clause)
        if got is None:
            unknown.append(clause)
        else:
            ops += got
    if unknown or not ops:
        raise NotUnderstood(f"could not understand: {unknown or [instruction]}")
    return ops


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
            else [{"op": "reharmonize", "technique": "borrowed"}]
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
