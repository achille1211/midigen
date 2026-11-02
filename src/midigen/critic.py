"""Rule-based critic: renders a spec and checks the result against music-theory and mix rules.

Findings are plain dicts so they can be shown to a user or fed back to an LLM for revision."""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from .arranger import Performance, render
from .generators import DRUM_MAP
from .harmony import detect_cadence, parallel_perfects

SEVERITY_COST = {"error": 25, "warning": 8, "info": 1}
# guitar-band genres where parallel fifths/octaves are idiomatic (reported as info only)
PARALLELS_OK = {"rock", "metal", "emo", "screamo", "punk"}
ROLE_ORDER = {"bass": 0, "chords": 2, "pad": 2, "arp": 3, "lead": 4}


def _f(check, severity, message, suggestion=None, track=None, section=None, bars=None):
    d = {"check": check, "severity": severity, "message": message}
    if suggestion:
        d["suggestion"] = suggestion
    if track:
        d["track"] = track
    if section:
        d["section"] = section
    if bars:
        d["bars"] = bars[:6]
    return d


def critique(spec_or_perf) -> dict:
    perf = spec_or_perf if isinstance(spec_or_perf, Performance) else render(spec_or_perf)
    spec = perf.spec
    bb = perf.bar_beats
    from .theory import scale_pcs
    scale = set(scale_pcs(spec["key"], spec["mode"]))
    genre = None
    if spec.get("genre"):
        from .genres import get_genre
        try:
            genre = get_genre(spec["genre"])
        except KeyError:
            pass
    findings: list[dict] = []
    pitched = [t for t in perf.tracks if not t.is_drums and t.notes]
    drums = [t for t in perf.tracks if t.is_drums and t.notes]
    stats = {}

    def bar_of(beat):
        return int(beat // bb) + 1

    # ---- empty / silent tracks
    for t, st in zip(perf.tracks, spec["tracks"]):
        if not t.notes and not st.get("mute"):
            findings.append(_f("empty_track", "warning", f"{t.name} never plays any notes.",
                               "Check its sections list or density.", track=t.name))

    # non-chord tones that resolve by step within one beat are passing/neighbour/approach tones: allowed
    passing: dict[str, set] = {}
    for t in pitched:
        ps = set()
        for a, b in zip(t.notes, t.notes[1:]):
            if a.pitch % 12 not in perf.chord_at(a.start).chord.pcs and abs(b.pitch - a.pitch) <= 2 \
                    and a.dur <= 1.01 and b.start - a.start <= 1.01:
                ps.add(id(a))
        passing[t.name] = ps

    # ---- out-of-key notes and per-track stats
    for t in pitched:
        bad = [n for n in t.notes if n.pitch % 12 not in scale and n.pitch % 12 not in perf.chord_at(n.start).chord.pcs
               and id(n) not in passing[t.name]]
        pitches = sorted(n.pitch for n in t.notes)
        playing_bars = sum(1 for s in perf.sections for _ in range(int((s.end - s.start) // bb))
                           if any(s.start <= n.start < s.end for n in t.notes))
        stats[t.name] = {"notes": len(t.notes), "low": pitches[0], "high": pitches[-1],
                         "median": median(pitches), "notes_per_bar": round(len(t.notes) / max(1, playing_bars), 2),
                         "role": t.role}
        if bad:
            ratio = len(bad) / len(t.notes)
            findings.append(_f("out_of_key", "warning" if ratio > 0.05 else "info",
                               f"{t.name}: {len(bad)} note(s) ({ratio:.0%}) are outside the key and the current chord.",
                               "Use chord tones/scale tones, or reharmonise so the chord contains those notes.",
                               track=t.name, bars=sorted({bar_of(n.start) for n in bad})))
    if drums:
        st = sum(len(d.notes) for d in drums)
        stats[drums[0].name] = {"notes": st, "role": "drums",
                                "notes_per_bar": round(st / max(1, perf.total_beats / bb), 2)}

    # ---- clashes on strong beats between tracks (semitone / minor 9th where one note is a non-chord tone)
    clash_bars = []
    strong = bb / 2 if bb >= 4 and float(bb).is_integer() and int(bb) % 2 == 0 else bb
    beat = 0.0
    while beat < perf.total_beats:
        sounding = []
        for t in pitched:
            for n in t.notes:
                if n.start <= beat + 1e-6 < n.start + n.dur and n.start > beat - 4:
                    if id(n) not in passing[t.name]:
                        sounding.append((t.name, n.pitch))
        pcs = perf.chord_at(beat).chord.pcs
        for i in range(len(sounding)):
            for j in range(i + 1, len(sounding)):
                (ta, a), (tb, b) = sounding[i], sounding[j]
                if ta == tb:
                    continue
                if abs(a - b) % 12 in (1, 11) and abs(a - b) in (1, 13, 11, 23) and \
                        (a % 12 not in pcs or b % 12 not in pcs):
                    clash_bars.append((bar_of(beat), ta, tb))
        beat += strong
    if clash_bars:
        pairs = sorted({f"{a}/{b}" for _, a, b in clash_bars})
        findings.append(_f("clash", "warning" if len(clash_bars) > 4 else "info",
                           f"{len(clash_bars)} semitone clash(es) on strong beats between {', '.join(pairs)}.",
                           "Move the non-chord tone to a chord tone or separate the parts by register.",
                           bars=sorted({b for b, _, _ in clash_bars})))

    # ---- parallel fifths / octaves in chord voicings, and outer-voice parallels bass vs lead
    for t in pitched:
        if t.role not in ("chords", "pad"):
            continue
        voicings = []
        for h in perf.harmony:
            v = sorted({n.pitch for n in t.notes if h.start - 1e-6 <= n.start < h.start + 0.05})
            if v:
                voicings.append((h, v))
        par = [(h, a, b) for (h0, a), (h, b) in zip(voicings, voicings[1:]) if parallel_perfects(a, b)]
        st = next((x.get("generator", {}).get("style") for x in spec.get("tracks", []) if x.get("name") == t.name), None)
        if par and st == "power":
            continue  # power chords are parallel fifths by definition
        if par:
            idiomatic = str(spec.get("genre", "")).split("@")[0] in PARALLELS_OK
            findings.append(_f("parallels", "warning" if len(par) > 2 and not idiomatic else "info",
                               f"{t.name}: parallel fifths/octaves in {len(par)} chord change(s).",
                               "Use contrary/oblique motion or a different inversion (voice leading).",
                               track=t.name, bars=sorted({bar_of(h.start) for h, _, _ in par})))
    bass = next((t for t in pitched if t.role == "bass"), None)
    lead = next((t for t in pitched if t.role == "lead"), None)
    if bass and lead:
        outer = []
        for h in perf.harmony:
            bn = next((n.pitch for n in bass.notes if abs(n.start - h.start) < 0.05), None)
            ln = next((n.pitch for n in lead.notes if n.start <= h.start + 1e-6 < n.start + n.dur), None)
            outer.append((bn, ln))
        par = 0
        for (b1, l1), (b2, l2) in zip(outer, outer[1:]):
            if None in (b1, l1, b2, l2):
                continue
            if parallel_perfects([b1, l1], [b2, l2]):
                par += 1
        if par > 2:
            findings.append(_f("outer_parallels", "info", f"Bass and lead move in parallel fifths/octaves {par} times.",
                               "Let the melody move against the bass (contrary motion)."))

    # ---- register separation
    roles = sorted([t for t in pitched if t.role in ROLE_ORDER], key=lambda t: ROLE_ORDER[t.role])
    for i in range(len(roles)):
        for j in range(i + 1, len(roles)):
            a, b = stats[roles[i].name], stats[roles[j].name]
            if ROLE_ORDER[roles[i].role] == ROLE_ORDER[roles[j].role]:
                continue
            low_ok = roles[i].role != "bass" or b["median"] - a["median"] >= 4
            if abs(b["median"] - a["median"]) < 4 or not low_ok:
                findings.append(_f("register", "warning",
                                   f"{roles[j].name} (median {b['median']:.0f}) sits on top of {roles[i].name} "
                                   f"(median {a['median']:.0f}); parts will mask each other.",
                                   f"Move {roles[j].name} up an octave or {roles[i].name} down (generator range/octave).",
                                   track=roles[j].name))
    for t in pitched:
        if t.role != "lead":
            continue
        for o in pitched:
            if o.role in ("chords", "pad") and stats[t.name]["median"] < stats[o.name]["median"] - 3:
                findings.append(_f("register", "warning",
                                   f"{t.name} (median {stats[t.name]['median']:.0f}) is below {o.name} "
                                   f"(median {stats[o.name]['median']:.0f}); the melody will be buried.",
                                   f"Raise {t.name}'s range above the {o.role} (e.g. range [64, 84]).", track=t.name))
    if bass and stats[bass.name]["low"] < 24:
        findings.append(_f("range", "warning", f"{bass.name} goes below E0 (MIDI {stats[bass.name]['low']}); inaudible on most systems.",
                           "Raise the bass range.", track=bass.name))
    if lead:
        span = stats[lead.name]["high"] - stats[lead.name]["low"]
        if span > 24:
            findings.append(_f("range", "info", f"{lead.name} spans {span} semitones; singable melodies usually stay within ~an octave and a half.",
                               "Narrow the lead range.", track=lead.name))

    # ---- density vs genre targets
    targets = (genre or {}).get("density_targets", {})
    for t in perf.tracks:
        if t.name not in stats:
            continue
        npb = stats[t.name]["notes_per_bar"]
        role = "drums" if t.is_drums else t.role
        lo, hi = targets.get(role, (0, 64 if role in ("drums", "arp", "chords") else 16))
        if npb > hi * 1.25:
            findings.append(_f("density", "warning", f"{t.name}: {npb} notes/bar is busier than typical for "
                               f"{(genre or {}).get('display', 'this style')} ({lo}-{hi}).",
                               "Lower its density or use a sparser style.", track=t.name))
        elif lo and npb < lo * 0.6:
            findings.append(_f("density", "info", f"{t.name}: {npb} notes/bar is sparser than typical ({lo}-{hi}).",
                               "Raise its density.", track=t.name))

    # ---- bass / kick interlock
    if bass and drums:
        kicks = [n.start for n in drums[0].notes if n.pitch == DRUM_MAP["kick"]]
        bstarts = [n.start for n in bass.notes]
        if kicks and bstarts:
            locked = sum(1 for k in kicks if any(abs(k - b) < 0.06 for b in bstarts)) / len(kicks)
            style = next((s["generator"].get("style") for s in spec["tracks"] if s["name"] == bass.name), "")
            stats[bass.name]["kick_lock"] = round(locked, 2)
            if style in ("kick_lock", "sustained", "syncopated") and locked < 0.5:
                findings.append(_f("interlock", "warning", f"Only {locked:.0%} of kicks have a bass note with them.",
                                   "Lock bass onsets to the kick (style kick_lock) for a tighter low end.", track=bass.name))
            if style in ("rolling", "offbeat") and locked > 0.3:
                findings.append(_f("interlock", "info", f"{locked:.0%} of kicks collide with {style} bass notes.",
                                   "Offbeat/rolling bass should leave the kick alone.", track=bass.name))

    # ---- melody shape
    if lead and len(lead.notes) > 4:
        ps = [n.pitch for n in lead.notes]
        moves = [b - a for a, b in zip(ps, ps[1:])]
        big = sum(1 for m in moves if abs(m) > 12)
        leaps = [(i, m) for i, m in enumerate(moves[:-1]) if abs(m) > 4]
        unresolved = sum(1 for i, m in leaps if moves[i + 1] * m > 0 or abs(moves[i + 1]) > 4)
        stepwise = sum(1 for m in moves if abs(m) <= 2) / len(moves)
        stats[lead.name].update({"stepwise_ratio": round(stepwise, 2), "leaps": len(leaps), "unresolved_leaps": unresolved})
        if big:
            findings.append(_f("melody", "warning", f"{lead.name} has {big} leap(s) larger than an octave.",
                               "Keep melodic leaps within an octave.", track=lead.name))
        if leaps and unresolved / len(leaps) > 0.4:
            findings.append(_f("melody", "info", f"{unresolved}/{len(leaps)} melodic leaps are not resolved by step.",
                               "Follow a leap with stepwise motion in the opposite direction.", track=lead.name))
        if stepwise < 0.35:
            findings.append(_f("melody", "info", f"Only {stepwise:.0%} of melodic motion is stepwise; it may sound random.",
                               "Use more conjunct (stepwise) motion.", track=lead.name))

    # ---- form & harmony
    energies = [s.energy for s in perf.sections]
    if len(set(energies)) == 1 and len(energies) > 1:
        findings.append(_f("form", "info", "Every section has the same energy, so the arrangement will feel flat.",
                           "Give the chorus/drop higher energy than verses/intros."))
    for sec in spec["sections"]:
        want = sec.get("cadence")
        if want and want != "none":
            got = detect_cadence(sec["chords"], spec["key"], spec["mode"])
            if got != want and not (want == "authentic" and got == "plagal"):
                findings.append(_f("cadence", "info", f"Section {sec['name']} is marked {want} cadence but its chords end "
                                   f"{sec['chords'][-2:]} ({got}).", "Use set_cadence or adjust the last chords.",
                                   section=sec["name"]))

    score = max(0, 100 - sum(SEVERITY_COST[f["severity"]] for f in findings))
    return {"score": score, "findings": findings, "stats": stats}


def serious(report: dict) -> list[dict]:
    return [f for f in report["findings"] if f["severity"] in ("warning", "error")]


def format_report(report: dict) -> str:
    lines = [f"critic score: {report['score']}/100"]
    for f in report["findings"]:
        loc = " ".join(x for x in [f.get("track", ""), f.get("section", ""), ("bars " + ",".join(map(str, f["bars"]))) if f.get("bars") else ""] if x)
        lines.append(f"  [{f['severity']}] {f['check']}: {f['message']}" + (f" ({loc})" if loc else "")
                     + (f" -> {f['suggestion']}" if f.get("suggestion") else ""))
    if len(lines) == 1:
        lines.append("  no issues found")
    return "\n".join(lines)
