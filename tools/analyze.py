"""Independent critic analysis of midigen output (reads .mid + .json spec; uses midigen only for chord parsing)."""
import json, sys, statistics as st, glob, os, math
from collections import defaultdict, Counter
import mido
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
GENRES_DIR = os.environ.get("MIDIGEN_CRITIC_GENRES", os.path.join(os.path.dirname(ROOT), "midigen-genres"))
from midigen.theory import parse_chord, scale_pcs, MODES, note_to_pc

GENRE_FILES = {"ambient":"ambient","bossa_nova":"bossa-nova","cinematic":"cinematic","drum_and_bass":"drum-and-bass","edm":"edm",
 "funk":"funk","house":"house","jazz":"jazz","lofi_hiphop":"lofi-hip-hop","neo_soul":"neo-soul","pop":"pop","reggae":"reggae",
 "rock":"rock","synthwave":"synthwave","techno":"techno","trap":"trap"}

def load_mid(path):
    mid = mido.MidiFile(path); tpb = mid.ticks_per_beat
    info = {"tempo": None, "ts": None, "tracks": [], "issues": []}
    for i, tr in enumerate(mid.tracks):
        now = 0; name = None; prog = {}; chans = Counter(); open_ = defaultdict(list); notes = []; overl = 0
        for m in tr:
            now += m.time
            if m.is_meta:
                if m.type == "set_tempo" and info["tempo"] is None: info["tempo"] = mido.tempo2bpm(m.tempo)
                if m.type == "time_signature": info["ts"] = (m.numerator, m.denominator)
                if m.type == "track_name": name = m.name
                continue
            if m.type == "program_change": prog[m.channel] = m.program
            if m.type == "note_on" and m.velocity > 0:
                chans[m.channel] += 1
                if open_[(m.channel, m.note)]: overl += 1
                open_[(m.channel, m.note)].append((now, m.velocity))
            elif m.type in ("note_off", "note_on"):
                if open_[(m.channel, m.note)]:
                    s, v = open_[(m.channel, m.note)].pop(0)
                    notes.append((s / tpb, (now - s) / tpb, m.note, v, m.channel))
        stuck = sum(len(v) for v in open_.values())
        info["tracks"].append({"name": name, "channels": dict(chans), "programs": prog, "notes": sorted(notes),
                               "stuck": stuck, "same_pitch_overlaps": overl, "end": now / tpb})
    return info

def onset_groups(notes, tol=0.04):
    groups = []
    for n in sorted(notes):
        if groups and abs(n[0] - groups[-1][0][0]) < tol: groups[-1].append(n)
        else: groups.append([n])
    return groups

def analyze(base):
    spec = json.load(open(base + ".json")); mid = load_mid(base + ".mid")
    g = spec["genre"]; gfile = json.load(open(os.path.join(GENRES_DIR, GENRE_FILES[g] + ".json")))
    key, mode = spec["key"], spec["mode"]; scale = set(scale_pcs(key, mode)); bb = 4.0
    secs = {s["name"]: s for s in spec["sections"]}
    # harmony timeline + section timeline
    harm, sect = [], []; t0 = 0
    for nm in spec["arrangement"]:
        s = secs[nm]; L = s["bars"] * bb; per = float(s.get("beats_per_chord") or bb); t = 0; i = 0
        sect.append((t0, t0 + L, nm, s["energy"]))
        while t < L - 1e-6:
            d = min(per, L - t); harm.append((t0 + t, t0 + t + d, parse_chord(s["chords"][i % len(s["chords"])], key, mode), s["chords"][i % len(s["chords"])])); t += d; i += 1
        t0 += L
    total = t0
    def chord_at(b):
        for h in harm:
            if h[0] <= b + 1e-6 < h[1]: return h
        return harm[-1]
    roles = {t["name"]: t["role"] for t in spec["tracks"]}
    tracks = {t["name"]: t for t in mid["tracks"][1:]}
    R = {"name": os.path.basename(base), "genre": g, "key": f"{key} {mode}", "tempo": round(mid["tempo"], 1), "ts": mid["ts"], "bars": total / bb}
    tech = []
    for t in mid["tracks"][1:]:
        if not t["notes"]: tech.append(f"empty track {t['name']}")
        if t["stuck"]: tech.append(f"{t['stuck']} stuck notes in {t['name']}")
        if t["same_pitch_overlaps"]: tech.append(f"{t['same_pitch_overlaps']} same-pitch overlaps in {t['name']}")
        if len(t["channels"]) > 1: tech.append(f"{t['name']} uses multiple channels {t['channels']}")
        role = roles.get(t["name"])
        ch = next(iter(t["channels"]), None)
        if role == "drums" and ch != 9: tech.append(f"drums on ch {ch}")
        if role != "drums" and ch == 9: tech.append(f"{t['name']} pitched on ch10")
        if t["end"] > total + 0.01: tech.append(f"{t['name']} ends past song end ({t['end']:.2f}>{total})")
        # polyphonic overlaps in monophonic roles
        if role in ("bass", "lead"):
            ns = t["notes"]; ov = sum(1 for a, b in zip(ns, ns[1:]) if a[0] + a[1] > b[0] + 0.02 and b[0] > a[0] + 0.02)
            chords_ = sum(1 for gp in onset_groups(ns) if len(gp) > 1)
            if ov or chords_: tech.append(f"{t['name']} ({role}) not monophonic: {ov} overlaps, {chords_} simultaneous onsets")
    progs = {t["name"]: t["programs"] for t in mid["tracks"][1:]}
    R["programs"] = {n: list(p.values())[0] if p else None for n, p in progs.items()}
    R["tech"] = tech
    pitched = [n for t in mid["tracks"][1:] if roles.get(t["name"]) != "drums" for n in t["notes"]]
    R["in_key_pct"] = round(100 * sum(1 for n in pitched if n[2] % 12 in scale) / max(1, len(pitched)), 1)
    # chord-tone conformance per role
    ct = {}
    for nm, t in tracks.items():
        role = roles.get(nm)
        if role in ("drums", None) or not t["notes"]: continue
        ok = sum(1 for n in t["notes"] if n[2] % 12 in chord_at(n[0])[2].pcs)
        ct[nm] = round(100 * ok / len(t["notes"]), 1)
    R["chord_tone_pct"] = ct
    # chord progression summary
    R["progressions"] = {s["name"]: " ".join(s["chords"]) for s in spec["sections"]}
    # clashes: semitone/b9 between sounding notes of different tracks at onsets on strong beats
    allp = [(n, nm) for nm, t in tracks.items() if roles.get(nm) != "drums" for n in t["notes"]]
    clashes = 0; checks = 0
    for (n, nm) in allp:
        if abs(n[0] % 1) > 0.02: continue
        checks += 1
        for (m, mm) in allp:
            if mm == nm: continue
            if m[0] <= n[0] + 0.01 < m[0] + m[1] and (abs(n[2] - m[2]) % 12) == 1:
                clashes += 1; break
    R["strong_clash_pct"] = round(100 * clashes / max(1, checks), 1)
    # bass analysis
    bass = [nm for nm in tracks if roles.get(nm) == "bass"]
    drums = [nm for nm in tracks if roles.get(nm) == "drums"]
    kicks = sorted({round(n[0], 3) for n in tracks[drums[0]]["notes"] if n[2] == 36}) if drums else []
    if bass and tracks[bass[0]]["notes"]:
        bn = tracks[bass[0]]["notes"]
        bons = [n[0] for n in bn]
        tol = 0.06
        k_hit = sum(1 for k in kicks if any(abs(k - b) < tol for b in bons))
        b_hit = sum(1 for b in bons if any(abs(k - b) < tol for k in kicks))
        R["kick_with_bass_pct"] = round(100 * k_hit / max(1, len(kicks)), 1) if kicks else None
        R["bass_on_kick_pct"] = round(100 * b_hit / max(1, len(bons)), 1) if kicks else None
        # root at chord change
        rc = 0; tot = 0
        for h in harm:
            first = [n for n in bn if h[0] - 0.13 <= n[0] < h[0] + 0.6]
            tot += 1
            if first and first[0][2] % 12 == h[2].bass: rc += 1
        R["bass_root_on_change_pct"] = round(100 * rc / max(1, tot), 1)
        R["bass_range"] = (min(n[2] for n in bn), max(n[2] for n in bn))
        R["bass_notes_per_bar"] = round(len(bn) / (total / bb), 2)
        R["bass_downbeat_pct"] = round(100 * sum(1 for n in bn if abs(n[0] % 4) < 0.05) / len(bn), 1)
        bl = [abs(b[2] - a[2]) for a, b in zip(bn, bn[1:])]
        R["bass_big_leaps"] = sum(1 for x in bl if x > 12)
    else:
        R["kick_with_bass_pct"] = None
    # drums: kick step histogram (16ths), swing, velocity
    if drums:
        dn = tracks[drums[0]]["notes"]
        kh = Counter(int(round((k % 4) * 4)) % 16 for k in kicks)
        nb = total / bb
        R["kick_steps"] = "".join("x" if kh[i] / nb > 0.5 else ("o" if kh[i] / nb > 0.15 else ".") for i in range(16))
        sn = [n for n in dn if n[2] in (38, 39, 40)]
        shh = Counter(int(round((n[0] % 4) * 4)) % 16 for n in sn if n[3] > 60)
        R["snare_steps"] = "".join("x" if shh[i] / nb > 0.5 else ("o" if shh[i] / nb > 0.15 else ".") for i in range(16))
        R["drum_pitches"] = dict(Counter(n[2] for n in dn).most_common(8))
        R["drum_vel_sd"] = round(st.pstdev([n[3] for n in dn]), 1)
    # timing deviation from 16th grid & swing estimate (pitched + drums)
    def offgrid(ns, grid=0.25):
        return [((n[0] / grid) - round(n[0] / grid)) * grid for n in ns]
    allnotes = [n for t in tracks.values() for n in t["notes"]]
    devs = [abs(x) for x in offgrid(allnotes)]
    R["timing_dev_ms_mean"] = round(1000 * 60 / mid["tempo"] * st.mean(devs), 1) if devs else 0
    # swing: position of notes near 8th offbeats / 16th offbeats
    def swing_est(ns, unit):
        pos = []
        for n in ns:
            ph = (n[0] % (2 * unit)) / unit  # 0..2
            if 0.6 < ph < 1.6: pos.append(ph)
        return round(st.median(pos) / 2, 3) if pos else None  # 0.5 = straight, .667 triplet
    R["swing_ratio_8th"] = swing_est(allnotes, 0.5)
    R["swing_ratio_16th"] = swing_est(allnotes, 0.25)
    R["spec_swing"] = (spec.get("swing"), spec.get("swing_unit"))
    R["genre_file_swing"] = gfile["groove"].get("swing"), gfile["groove"].get("swingSubdivision")
    R["genre_file_tempo"] = (gfile["tempo"]["min"], gfile["tempo"]["max"], gfile["tempo"]["default"])
    gp = gfile["drums"]["patterns"][0]["voices"].get("kick")
    R["genre_file_kick"] = "".join("x" if v else "." for v in gp[:16]) if gp else None
    gs = gfile["drums"]["patterns"][0]["voices"].get("snare") or gfile["drums"]["patterns"][0]["voices"].get("clap")
    R["genre_file_snare"] = "".join("x" if v else "." for v in gs[:16]) if gs else None
    # velocity variation per track
    R["vel_sd"] = {nm: round(st.pstdev([n[3] for n in t["notes"]]), 1) for nm, t in tracks.items() if len(t["notes"]) > 1}
    # syncopation: fraction of onsets not on 8th grid-beat? (off-beat 8th and 16th positions)
    syn = {}
    for nm, t in tracks.items():
        if not t["notes"] or roles.get(nm) == "drums": continue
        offb = sum(1 for n in t["notes"] if abs((n[0] % 1)) > 0.05)
        syn[nm] = round(100 * offb / len(t["notes"]), 1)
    R["offbeat_onset_pct"] = syn
    # voice leading in chord tracks
    vl = {}
    for nm, t in tracks.items():
        if roles.get(nm) not in ("chords", "pad", "other"): continue
        groups = [sorted(n[2] for n in gp) for gp in onset_groups(t["notes"]) if len(gp) >= 2]
        moves, maxleap, par, same, top = [], 0, 0, 0, []
        for a, b in zip(groups, groups[1:]):
            if a == b: same += 1; continue
            if len(a) == len(b):
                mv = [abs(x - y) for x, y in zip(a, b)]; moves.append(sum(mv)); maxleap = max(maxleap, max(mv))
                for i in range(len(a)):
                    for j in range(i + 1, len(a)):
                        ia, ib = (a[j] - a[i]) % 12, (b[j] - b[i]) % 12
                        if ia == ib and ia in (0, 7) and (b[i] - a[i]) * (b[j] - a[j]) > 0: par += 1
            top.append(b[-1])
        lo = min(n[2] for n in t["notes"]); hi = max(n[2] for n in t["notes"])
        vl[nm] = {"chord_changes": len(moves), "mean_motion": round(st.mean(moves), 1) if moves else 0, "max_voice_leap": maxleap,
                  "parallel_5_8": par, "range": (lo, hi), "voices": round(st.mean(len(gp) for gp in groups), 1) if groups else 0,
                  "low_cluster": sum(1 for gp in groups if gp[0] < 52 and gp[1] - gp[0] <= 4)}
    R["voice_leading"] = vl
    # lead / melody
    lead = [nm for nm in tracks if roles.get(nm) == "lead"]
    if lead and tracks[lead[0]]["notes"]:
        ln = tracks[lead[0]]["notes"]; ps = [n[2] for n in ln]
        iv = [b - a for a, b in zip(ps, ps[1:])]
        steps = sum(1 for x in iv if abs(x) <= 2); leaps = sum(1 for x in iv if abs(x) > 7)
        dirch = sum(1 for a, b in zip(iv, iv[1:]) if a * b < 0)
        rep = sum(1 for x in iv if x == 0)
        # phrase ends: note followed by a gap >= 0.5 beat (or last)
        ends = [n for n, m in zip(ln, ln[1:] + [None]) if m is None or m[0] - (n[0] + n[1]) >= 0.45]
        ends_ct = sum(1 for n in ends if n[2] % 12 in chord_at(n[0])[2].pcs)
        strong = [n for n in ln if abs(n[0] % 2) < 0.03]
        strong_ct = sum(1 for n in strong if n[2] % 12 in chord_at(n[0])[2].pcs)
        sounding = sum(n[1] for n in ln)
        # motif repetition: proportion of bars whose (rhythm,interval) signature reappears
        bars = defaultdict(list)
        for n in ln: bars[int(n[0] // 4)].append((round(n[0] % 4, 2), n[2]))
        sigs = [tuple((r, p - b[0][1]) for r, p in b) for b in bars.values()]
        c = Counter(sigs); repeated = sum(v for v in c.values() if v > 1)
        rh = Counter(tuple(r for r, _ in b) for b in bars.values())
        # short-note fragments
        R["melody"] = {"notes": len(ln), "range": (min(ps), max(ps)), "span": max(ps) - min(ps), "step_pct": round(100 * steps / max(1, len(iv)), 1),
                       "leap_gt5th_pct": round(100 * leaps / max(1, len(iv)), 1), "max_leap": max(abs(x) for x in iv) if iv else 0,
                       "dir_change_pct": round(100 * dirch / max(1, len(iv) - 1), 1), "repeated_pitch_pct": round(100 * rep / max(1, len(iv)), 1),
                       "phrase_ends": len(ends), "phrase_end_chord_tone_pct": round(100 * ends_ct / max(1, len(ends)), 1),
                       "strong_beat_chord_tone_pct": round(100 * strong_ct / max(1, len(strong)), 1),
                       "rest_pct": round(100 * (1 - min(1, sounding / (len(bars) * 4))), 1) if bars else None,
                       "bars_with_lead": len(bars), "repeated_bar_pct": round(100 * repeated / max(1, len(sigs)), 1),
                       "distinct_bar_rhythms": len(rh), "notes_per_bar": round(len(ln) / max(1, len(bars)), 2),
                       "short_notes_pct": round(100 * sum(1 for n in ln if n[1] < 0.2) / len(ln), 1),
                       "mean_dur": round(st.mean(n[1] for n in ln), 2)}
    # energy per section
    en = []
    for (a, b, nm, e) in sect:
        ns = [(n, tn) for tn, t in tracks.items() for n in t["notes"] if a <= n[0] < b]
        act = sorted({tn for n, tn in ns})
        pitchedv = [n[3] for n, tn in ns]
        en.append({"section": nm, "spec_energy": e, "bars": (b - a) / 4, "notes_per_bar": round(len(ns) / ((b - a) / 4), 1),
                   "mean_vel": round(st.mean(pitchedv), 1) if pitchedv else 0, "active": len(act)})
    R["energy"] = en
    # register vs genre file instrumentation register
    regv = []
    for inst in gfile.get("instrumentation", []):
        pass
    return R

if __name__ == "__main__":
    SAMPLES = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "review", "after")
    out = {}
    for f in sorted(glob.glob(os.path.join(SAMPLES, "*.mid"))):
        b = f[:-4]
        out[os.path.basename(b)] = analyze(b)
    json.dump(out, open(os.path.join(SAMPLES, "analysis.json"), "w"), indent=1, default=str)
    print("ok", len(out))
