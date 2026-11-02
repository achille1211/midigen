"""Extra theory metrics on top of tools/analyze.py (same song files: <name>.mid + <name>.json).

Usage: python tools/theory_metrics.py DIR [DIR2]   -> prints per-metric means (and DIR2 side by side)
Metrics (all over the whole song unless noted):
  clash_pct            analyze.py strong-beat m2/b9 clash % (same definition)
  clash_harm_harm      share of strong-beat onsets in chord/pad/arp parts clashing with another chord/pad/arp part
  clash_bass_harm      share of strong-beat bass onsets clashing with a sounding chord/pad/arp note
  bass_inversion_pct   chord spans whose first bass note is a chord tone other than the root
  bass_step_change_pct chord changes where the bass moves by <= 2 semitones
  bass_change_motion   mean |bass interval| at chord changes (semitones)
  chord_root_low_pct   chord-part onsets (>=2 notes) whose lowest note is the chord root
  chord_motion         mean summed voice motion between successive chord-part voicings (analyze.py def)
  lead_arc_pct         4-bar lead phrases whose highest note is neither the first nor the last note
  lead_climax_unique   4-bar lead phrases whose highest pitch occurs exactly once
  lead_climax_pos      mean relative position (0..1) of the phrase climax
  lead_dur_kinds       mean number of distinct note lengths per 4-bar phrase
  lead_bar_rhythm_var  distinct bar rhythms / lead bars (0..1)
  chords_per_bar       mean chords per bar (spec harmony)
  varied_harm_rhythm   sections whose chords do not all have the same length, or change mid-bar
  secdom_pct           chord symbols that are secondary dominants (V/x, V7/x, or a chromatic dominant 7th)
  borrowed_pct         chord symbols classified borrowed/modal-interchange by midigen.harmony.function_of
"""
import glob, json, os, re, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A  # noqa: E402
from midigen.theory import parse_chord, scale_pcs  # noqa: E402
from midigen import harmony as H  # noqa: E402

HARM = ("chords", "pad", "arp", "other")


def song_metrics(base):
    spec = json.load(open(base + ".json")); mid = A.load_mid(base + ".mid")
    key, mode = spec["key"], spec["mode"]; bb = spec["time_signature"][0] * 4 / spec["time_signature"][1]
    roles = {t["name"]: t["role"] for t in spec["tracks"]}
    tracks = {t["name"]: t for t in mid["tracks"][1:]}
    secs = {s["name"]: s for s in spec["sections"]}
    harm = []; t0 = 0.0
    for nm in spec["arrangement"]:
        s = secs[nm]; L = s["bars"] * bb; per = float(s.get("beats_per_chord") or bb)
        durs = [float(x) for x in s.get("chord_beats") or []]; t = 0; i = 0
        while t < L - 1e-6:
            d = min(durs[i % len(durs)] if durs else per, L - t)
            sym = s["chords"][i % len(s["chords"])]
            harm.append((t0 + t, t0 + t + d, parse_chord(sym, key, mode), sym)); t += d; i += 1
        t0 += L

    def chord_at(b):
        for h in harm:
            if h[0] <= b + 1e-6 < h[1]:
                return h
        return harm[-1]
    R = {}
    # clashes by category
    allp = [(n, nm) for nm, t in tracks.items() if roles.get(nm) != "drums" for n in t["notes"]]
    by = defaultdict(lambda: [0, 0])
    for n, nm in allp:
        if abs(n[0] % 1) > 0.02:
            continue
        r = roles.get(nm)
        cat = "harm" if r in HARM else r
        hit = {"harm": False, "bass": False, "lead": False}
        for m, mm in allp:
            if mm == nm:
                continue
            if m[0] <= n[0] + 0.01 < m[0] + m[1] and abs(n[2] - m[2]) % 12 == 1:
                rr = roles.get(mm); hit["harm" if rr in HARM else rr if rr in hit else "harm"] = True
        by[(cat, "harm")][1] += 1; by[(cat, "harm")][0] += hit["harm"]
    R["clash_harm_harm"] = 100 * by[("harm", "harm")][0] / max(1, by[("harm", "harm")][1])
    R["clash_bass_harm"] = 100 * by[("bass", "harm")][0] / max(1, by[("bass", "harm")][1])
    # bass
    bass = [nm for nm in tracks if roles.get(nm) == "bass" and tracks[nm]["notes"]]
    if bass:
        bn = tracks[bass[0]]["notes"]
        inv = tot = 0; firsts = []
        for h in harm:
            first = [n for n in bn if h[0] - 0.13 <= n[0] < h[0] + 0.6]
            if not first:
                firsts.append(None); continue
            tot += 1; p = first[0][2]
            if p % 12 in h[2].pcs and p % 12 != h[2].root:
                inv += 1
            firsts.append(p)
        R["bass_inversion_pct"] = 100 * inv / max(1, tot)
        # motion into each chord change: last bass note before the change -> first note of the new chord
        mv = []
        for h, f in zip(harm[1:], firsts[1:]):
            if f is None:
                continue
            before = [n for n in bn if n[0] < h[0] - 0.13]
            if before and h[0] - (before[-1][0] + before[-1][1]) < 1.0:
                mv.append(abs(f - before[-1][2]))
        R["bass_step_change_pct"] = 100 * sum(1 for x in mv if x <= 2) / max(1, len(mv))
        R["bass_change_motion"] = st.mean(mv) if mv else 0
    # chord parts
    rl = tot = 0; motions = []
    for nm, t in tracks.items():
        if roles.get(nm) not in ("chords", "pad"):
            continue
        groups = [sorted(n[2] for n in gp) for gp in A.onset_groups(t["notes"]) if len(gp) >= 2]
        gstarts = [gp[0][0] for gp in A.onset_groups(t["notes"]) if len(gp) >= 2]
        for g, s0 in zip(groups, gstarts):
            tot += 1; rl += g[0] % 12 == chord_at(s0)[2].root
        for a, b in zip(groups, groups[1:]):
            if a != b and len(a) == len(b):
                motions.append(sum(abs(x - y) for x, y in zip(a, b)))
    R["chord_root_low_pct"] = 100 * rl / max(1, tot)
    R["chord_motion"] = st.mean(motions) if motions else 0
    # lead phrases (4-bar windows)
    lead = [nm for nm in tracks if roles.get(nm) == "lead" and tracks[nm]["notes"]]
    if lead:
        ln = tracks[lead[0]]["notes"]
        win = defaultdict(list)
        for n in ln:
            win[int(n[0] // (4 * bb))].append(n)
        arc = uniq = cnt = 0; pos = []; kinds = []
        for ns in win.values():
            if len(ns) < 4:
                continue
            cnt += 1; ps = [n[2] for n in ns]; mx = max(ps); i = ps.index(mx)
            arc += 0 < i < len(ps) - 1 and ps[-1] < mx
            uniq += ps.count(mx) == 1
            pos.append(i / (len(ps) - 1))
            kinds.append(len({round(n[1] * 4) for n in ns}))
        R["lead_arc_pct"] = 100 * arc / max(1, cnt)
        R["lead_climax_unique"] = 100 * uniq / max(1, cnt)
        R["lead_climax_pos"] = st.mean(pos) if pos else 0
        R["lead_dur_kinds"] = st.mean(kinds) if kinds else 0
        bars = defaultdict(list)
        for n in ln:
            bars[int(n[0] // bb)].append(round(n[0] % bb, 2))
        R["lead_bar_rhythm_var"] = len({tuple(v) for v in bars.values()}) / max(1, len(bars))
    # harmony
    cpb, varied, syms = [], 0, []
    for s in spec["sections"]:
        per = float(s.get("beats_per_chord") or bb); durs = [float(x) for x in s.get("chord_beats") or []] or [per]
        n = 0; t = 0; i = 0; L = s["bars"] * bb; lens = set(); midbar = False
        while t < L - 1e-6:
            d = min(durs[i % len(durs)], L - t); lens.add(round(d, 3))
            if abs(t % bb) > 1e-6:
                midbar = True
            t += d; i += 1; n += 1
        cpb.append(n / s["bars"]); varied += (len(lens) > 1 or midbar)
        syms += s["chords"]
    R["chords_per_bar"] = st.mean(cpb)
    R["varied_harm_rhythm"] = 100 * varied / max(1, len(spec["sections"]))
    scale = set(scale_pcs(key, mode))

    def secdom(sym):
        if "/" in sym and sym.split("/")[0].lstrip("b#")[:1].upper() == "V" and sym.split("/")[1][:1] in "ivIVb#":
            return True
        c = parse_chord(sym, key, mode)
        ints = set(c.intervals)
        return {4, 10} <= ints and c.root != (scale_pcs(key, mode)[4]) and not set(c.pcs) <= scale and 7 in ints
    R["secdom_pct"] = 100 * sum(1 for x in syms if secdom(x)) / max(1, len(syms))
    R["borrowed_pct"] = 100 * sum(1 for x in syms if H.function_of(x, key, mode) == "borrowed") / max(1, len(syms))
    an = A.analyze(base)
    R["clash_pct"] = an["strong_clash_pct"]
    R["in_key_pct"] = an["in_key_pct"]
    m = an.get("melody") or {}
    for k in ("strong_beat_chord_tone_pct", "phrase_end_chord_tone_pct", "repeated_bar_pct", "distinct_bar_rhythms", "max_leap", "step_pct"):
        if k in m:
            R["lead_" + k.replace("_pct", "") if not k.startswith("lead") else k] = m[k]
    vl = an.get("voice_leading") or {}
    if vl:
        R["vl_max_voice_leap"] = max(v["max_voice_leap"] for v in vl.values())
        R["vl_parallel_5_8"] = sum(v["parallel_5_8"] for v in vl.values())
    R.update(hook_metrics(spec, tracks, roles, bb))
    R["tech_issues"] = len(an["tech"])
    return R


HOOK_RE = re.compile(r"(^(?!pre|post).*chorus|^hook|^drop|^peak|^climax|^bloom|^loop_b)")


def hook_metrics(spec, tracks, roles, bb):
    """Chorus melody recurrence (critic item: each chorus had a freshly generated lead).
    hook_in_chorus_pct: later chorus-like instances whose first 2 bars of lead equal the first chorus's
    (onsets on a 16th grid + intervals, i.e. transposition allowed). chorus_bar_reuse_pct: bar-aligned share
    of identical bars (same test) over the shorter of the two choruses."""
    secs = {s["name"]: s for s in spec["sections"]}
    lead = [nm for nm in tracks if roles.get(nm) == "lead"]
    if not lead:
        return {}
    ln = sorted(n for nm in lead for n in tracks[nm]["notes"])
    tl, t0 = [], 0.0
    for nm in spec["arrangement"]:
        L = secs[nm]["bars"] * bb
        tl.append((t0, t0 + L, nm)); t0 += L
    ch = [x for x in tl if HOOK_RE.search(x[2])]
    if len(ch) < 2:
        return {}

    def sig(a, b):
        ns = [n for n in ln if a - 1e-6 <= n[0] < b - 1e-6]
        if not ns:
            return None
        return tuple((round((n[0] - a) * 4), n[2] - ns[0][2]) for n in ns)
    first = ch[0]
    hits, bars_same, bars_tot = 0, 0, 0
    for c in ch[1:]:
        hits += sig(c[0], c[0] + 2 * bb) == sig(first[0], first[0] + 2 * bb) and sig(c[0], c[0] + 2 * bb) is not None
        nb = int(min(first[1] - first[0], c[1] - c[0]) // bb)
        for b in range(nb):
            x, y = sig(first[0] + b * bb, first[0] + (b + 1) * bb), sig(c[0] + b * bb, c[0] + (b + 1) * bb)
            if x is None and y is None:
                continue
            bars_tot += 1; bars_same += x == y
    return {"hook_in_chorus_pct": 100 * hits / (len(ch) - 1),
            "chorus_bar_reuse_pct": 100 * bars_same / max(1, bars_tot)}


def run(d):
    out = {}
    for f in sorted(glob.glob(os.path.join(d, "*.mid"))):
        out[os.path.basename(f[:-4])] = song_metrics(f[:-4])
    return out


def mean(res, k):
    xs = [r[k] for r in res.values() if k in r]
    return st.mean(xs) if xs else float("nan")


if __name__ == "__main__":
    dirs = sys.argv[1:]
    res = [run(d) for d in dirs]
    for d, r in zip(dirs, res):
        json.dump(r, open(os.path.join(d, "theory_metrics.json"), "w"), indent=1)
    keys = list(dict.fromkeys(k for r in res for v in r.values() for k in v))
    print("| metric | " + " | ".join(os.path.basename(d.rstrip("/")) for d in dirs) + " |")
    print("|---|" + "---|" * len(dirs))
    for k in keys:
        print(f"| {k} | " + " | ".join(f"{mean(r, k):.1f}" for r in res) + " |")
