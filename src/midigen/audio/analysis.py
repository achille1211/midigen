"""Automated listening check: objective measurements of rendered audio, turned into critic-style findings.

Nothing here is (or can be) checked by ear. Every number is a measurement; the reference ranges and thresholds
are engineering heuristics (documented below and in AUDIO_NOTES.md), not taste judgements calibrated on
reference tracks. Needs numpy + scipy (+ soundfile to read files)."""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import numpy as np
from scipy.signal import lfilter, resample_poly, welch

SEVERITY_COST = {"error": 25, "warning": 8, "info": 1}
EPS = 1e-12

BANDS = {"sub": (20, 60), "bass": (60, 250), "low_mid": (250, 500), "mid": (500, 2000), "high_mid": (2000, 6000),
         "air": (6000, 20000)}
# band power relative to total power (dB). Heuristic ranges for a finished-sounding mix (wide on purpose).
REFERENCE_BANDS = {
    "default": {"sub": (-18, -4), "bass": (-8, -1), "low_mid": (-14, -4), "mid": (-16, -5), "high_mid": (-26, -10),
                "air": (-38, -16)},
    "electronic": {"sub": (-12, -2), "bass": (-8, -1), "low_mid": (-16, -5), "mid": (-18, -6), "high_mid": (-28, -10),
                   "air": (-38, -16)},
    "acoustic": {"sub": (-26, -6), "bass": (-10, -1), "low_mid": (-13, -3), "mid": (-14, -4), "high_mid": (-26, -9),
                 "air": (-40, -16)},
}
_GENRE_FAMILY = {g: "electronic" for g in ("house", "techno", "edm", "trap", "drum_and_bass", "synthwave")}
_GENRE_FAMILY.update({g: "acoustic" for g in ("jazz", "bossa_nova", "ambient", "cinematic", "neo_soul")})

FOCUS = {"low": (30, 250), "mid": (200, 2000)}
LOUD_SILENCE_DB = -60.0


# ------------------------------------------------------------------ io / basics

def load_audio(path) -> tuple[np.ndarray, int]:
    import soundfile as sf
    x, sr = sf.read(str(path), dtype="float64", always_2d=True)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2], sr


def db(p, ref=1.0):
    return 10 * math.log10(max(p, EPS) / ref)


def _k_filter(sr):
    """BS.1770 K-weighting (pre-filter shelf + RLB high-pass) as two biquads for any sample rate."""
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh, Vb = 10 ** (G / 20), (10 ** (G / 20)) ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a0 = 1 + K / Q + K * K
    b2 = [1, -2, 1]
    a2 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    return (b1, a1), (b2, a2)


def k_weight(x, sr):
    (b1, a1), (b2, a2) = _k_filter(sr)
    return lfilter(b2, a2, lfilter(b1, a1, x, axis=0), axis=0)


def _block_ms(y, sr, block_s, overlap):
    """Mean square per block per channel, blocks of block_s with the given overlap."""
    n = int(block_s * sr)
    hop = max(1, int(n * (1 - overlap)))
    if len(y) < n:
        return np.mean(y ** 2, axis=0, keepdims=True)
    c = np.cumsum(np.vstack([np.zeros((1, y.shape[1])), y ** 2]), axis=0)
    starts = np.arange(0, len(y) - n + 1, hop)
    return (c[starts + n] - c[starts]) / n


def loudness(x, sr) -> dict:
    """Integrated loudness (BS.1770-4 gating), max momentary (400 ms) and max short-term (3 s), in LUFS."""
    y = k_weight(x, sr)
    ms = _block_ms(y, sr, 0.4, 0.75)
    blk = -0.691 + 10 * np.log10(np.maximum(ms.sum(axis=1), EPS))
    gated = blk > -70
    if not gated.any():
        integ = -math.inf
    else:
        rel = -0.691 + 10 * math.log10(ms[gated].sum(axis=1).mean()) - 10
        g2 = gated & (blk > rel)
        integ = -0.691 + 10 * math.log10(ms[g2].sum(axis=1).mean()) if g2.any() else -math.inf
    st = _block_ms(y, sr, 3.0, 2.0 / 3.0)
    st_l = -0.691 + 10 * np.log10(np.maximum(st.sum(axis=1), EPS))
    return {"integrated_lufs": round(integ, 2) if math.isfinite(integ) else None,
            "max_momentary_lufs": round(float(blk.max()), 2), "max_short_term_lufs": round(float(st_l.max()), 2)}


def true_peak(x, oversample=4, chunk=441000) -> float:
    """Approximate true peak (dBTP) by 4x polyphase oversampling, chunked to bound memory."""
    peak = 0.0
    pad = 64
    for i in range(0, len(x), chunk):
        seg = x[max(0, i - pad): i + chunk + pad]
        up = resample_poly(seg, oversample, 1, axis=0)
        peak = max(peak, float(np.max(np.abs(up))) if len(up) else 0.0)
    return round(20 * math.log10(max(peak, EPS)), 2)


def clipping(x, threshold=0.999) -> dict:
    a = np.abs(x)
    at = a >= threshold
    # clip events: runs of >= 2 consecutive samples at/over threshold in any channel
    any_c = at.any(axis=1).astype(np.int8)
    edges = np.diff(np.concatenate([[0], any_c, [0]]))
    starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
    events = int(np.sum((ends - starts) >= 2))
    return {"clipped_samples": int(at.sum()), "over_full_scale_samples": int((a > 1.0).sum()), "clip_events": events,
            "threshold": threshold}


def silence(x, sr, block_s=0.05, floor_db=LOUD_SILENCE_DB) -> dict:
    ms = _block_ms(x, sr, block_s, 0.0).mean(axis=1)
    lv = 10 * np.log10(np.maximum(ms, EPS))
    quiet = lv < floor_db
    loud_idx = np.nonzero(~quiet)[0]
    lead = (loud_idx[0] if len(loud_idx) else len(quiet)) * block_s
    trail = (len(quiet) - 1 - loud_idx[-1] if len(loud_idx) else len(quiet)) * block_s
    return {"silence_ratio": round(float(quiet.mean()), 4), "leading_silence_s": round(float(lead), 2),
            "trailing_silence_s": round(float(trail), 2), "floor_db": floor_db}


def spectrum_bands(x, sr) -> dict:
    """Share of total power per band (dB rel. total) of the mid (L+R)/2 signal, Welch PSD."""
    mono = x.mean(axis=1)
    f, p = welch(mono, fs=sr, nperseg=min(8192, max(256, len(mono))), average="mean")
    tot = p[(f >= 20) & (f <= 20000)].sum() + EPS
    return {k: round(db(p[(f >= lo) & (f < hi)].sum() / tot), 2) for k, (lo, hi) in BANDS.items()}


def stereo(x) -> dict:
    l, r = x[:, 0], x[:, 1]
    el, er = float(np.mean(l ** 2)), float(np.mean(r ** 2))
    corr = float(np.sum(l * r) / math.sqrt(max(np.sum(l ** 2) * np.sum(r ** 2), EPS)))
    mid, side = (l + r) / 2, (l - r) / 2
    return {"balance_db": round(db(el) - db(er), 2), "correlation": round(corr, 3),
            "side_to_mid_db": round(db(float(np.mean(side ** 2))) - db(float(np.mean(mid ** 2))), 2),
            "rms_left_db": round(db(el), 2), "rms_right_db": round(db(er), 2)}


def ffmpeg_loudness(path) -> dict:
    """Cross-check with ffmpeg's ebur128 filter (integrated LUFS, true peak dBTP, LRA). {} if ffmpeg is missing."""
    from .render import find_ffmpeg
    ff = find_ffmpeg()
    if not ff:
        return {}
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=900)
    txt = r.stderr[r.stderr.rfind("Summary:"):] if "Summary:" in r.stderr else r.stderr
    def grab(pat):
        m = re.search(pat, txt)
        return float(m.group(1)) if m else None
    return {"integrated_lufs": grab(r"I:\s+(-?[\d.]+|-inf) LUFS"), "lra_lu": grab(r"LRA:\s+(-?[\d.]+) LU"),
            "true_peak_dbtp": grab(r"True peak:\s+Peak:\s+(-?[\d.]+) dBFS")}


# ------------------------------------------------------------------ stems / masking

def _third_octave_edges(lo=25.0, hi=16000.0):
    centers = [1000 * 2 ** (k / 3) for k in range(-16, 13)]
    centers = [c for c in centers if lo <= c <= hi]
    return [(c / 2 ** (1 / 6), c * 2 ** (1 / 6)) for c in centers]


THIRD_OCT = _third_octave_edges()


def band_spectrogram(x, sr, n_fft=4096, hop=2048) -> np.ndarray:
    """Power per (frame, 1/3-octave band, channel) -> array [frames, bands, 2] (float32)."""
    n = len(x)
    frames = max(1, 1 + (n - n_fft) // hop) if n >= n_fft else 1
    if n < n_fft:
        x = np.vstack([x, np.zeros((n_fft - n, 2))])
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    idx = [np.nonzero((freqs >= lo) & (freqs < hi))[0] for lo, hi in THIRD_OCT]
    out = np.zeros((frames, len(THIRD_OCT), 2), dtype=np.float32)
    step = 256
    for c in range(2):
        xc = x[:, c].astype(np.float32)
        for f0 in range(0, frames, step):
            f1 = min(frames, f0 + step)
            seg = np.stack([xc[i * hop: i * hop + n_fft] for i in range(f0, f1)]) * win
            p = np.abs(np.fft.rfft(seg, axis=1)) ** 2
            for b, ix in enumerate(idx):
                if len(ix):
                    out[f0:f1, b, c] = p[:, ix].sum(axis=1)
    return out


def _focus_mask(lo, hi):
    return np.array([(a >= lo * 0.89 and b <= hi * 1.12) for a, b in THIRD_OCT])


def masking_pair(sa: np.ndarray, sb: np.ndarray, focus: tuple[float, float]) -> dict:
    """Band/time masking metrics between two stems' band spectrograms (computed per stereo side, so panning helps).

    overlap  = sum(min(Pa,Pb)) / min(sum Pa, sum Pb) in the focus range: 0 = never in the same band at the same
               time, 1 = the weaker part is completely matched by the other.
    masked_a = share of A's focus energy in cells where B is louder (and vice versa)."""
    n = min(len(sa), len(sb))
    m = _focus_mask(*focus)
    a, b = sa[:n, m, :].astype(np.float64), sb[:n, m, :].astype(np.float64)
    ta, tb = a.sum(), b.sum()
    if ta < EPS or tb < EPS:
        return {"overlap": 0.0, "masked_a": 0.0, "masked_b": 0.0, "level_diff_db": None, "contested_diff_db": None}
    mn = np.minimum(a, b)
    contested = (a > 0.1 * b) & (b > 0.1 * a)  # within 10 dB of each other
    ca, cb = a[contested].sum(), b[contested].sum()
    return {"overlap": round(float(mn.sum() / min(ta, tb)), 3),
            "masked_a": round(float(a[b > a].sum() / ta), 3), "masked_b": round(float(b[a > b].sum() / tb), 3),
            "level_diff_db": round(db(ta) - db(tb), 2),
            "contested_diff_db": round(db(ca) - db(cb), 2) if ca > EPS and cb > EPS else None}


# priority: which part should win a contested band (higher wins)
_PRIORITY = {"lead": 5, "kick": 4, "bass": 4, "drums": 3, "chords": 2, "arp": 2, "other": 1, "pad": 1}


def _cc7_after(cc7: int, delta_db: float) -> int:
    """CC7 value after a gain change, using the GM/FluidSynth curve gain = (cc/127)^2 (40*log10)."""
    return int(max(0, min(127, round(cc7 * 10 ** (delta_db / 40)))))


# ------------------------------------------------------------------ main analysis

def _f(check, severity, message, suggestion=None, track=None, **extra):
    d = {"check": check, "severity": severity, "message": message}
    if suggestion:
        d["suggestion"] = suggestion
    if track:
        d["track"] = track
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


def analyze_mix(wav_path, *, genre: str | None = None, stems: list[dict] | None = None,
                use_ffmpeg: bool = True) -> dict:
    """Measure a rendered mix (and optionally its stems from render.render_stems) and return the mix report."""
    x, sr = load_audio(wav_path)
    rep = analyze_array(x, sr, genre=genre, stems=stems)
    rep["file"] = str(wav_path)
    if use_ffmpeg:
        ff = ffmpeg_loudness(wav_path)
        if ff:
            rep["metrics"]["ffmpeg_ebur128"] = ff
    rep["summary"] = summary_text(rep)
    return rep


def analyze_array(x: np.ndarray, sr: int, *, genre: str | None = None, stems: list[dict] | None = None) -> dict:
    from .mix import genre_key
    g = genre_key(genre)
    fam = _GENRE_FAMILY.get(g or "", "default")
    ref = REFERENCE_BANDS[fam]
    findings: list[dict] = []
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    dur = len(x) / sr
    loud = loudness(x, sr)
    tp = true_peak(x)
    sp = round(20 * math.log10(max(float(np.max(np.abs(x))) if len(x) else 0.0, EPS)), 2)
    clip = clipping(x)
    dc = [round(float(v), 5) for v in x.mean(axis=0)]
    sil = silence(x, sr)
    bands = spectrum_bands(x, sr) if len(x) > 256 else {k: -120.0 for k in BANDS}
    st = stereo(x)
    metrics = {"duration_s": round(dur, 2), "sample_rate": sr, "loudness": loud, "true_peak_dbtp": tp,
               "sample_peak_dbfs": sp, "plr_db": round(tp - loud["integrated_lufs"], 2) if loud["integrated_lufs"] is not None else None,
               "clipping": clip, "dc_offset": dc, "silence": sil, "spectrum": {"bands_db_rel_total": bands,
               "reference_family": fam, "reference_db": {k: list(v) for k, v in ref.items()}}, "stereo": st}

    # ---- level / peak / clipping
    il = loud["integrated_lufs"]
    if il is None:
        findings.append(_f("mix_silent", "error", "The render is silent (no block above -70 LUFS).",
                           "Check that tracks are unmuted and the backend produced audio."))
    if clip["over_full_scale_samples"] or clip["clip_events"]:
        over = max(sp, tp)
        findings.append(_f("mix_clipping", "error",
                           f"{clip['clipped_samples']} samples at/over full scale ({clip['clip_events']} clip events); "
                           f"sample peak {sp} dBFS, true peak {tp} dBTP.",
                           f"Lower the master/render gain by at least {max(1.0, over + 1.0):.1f} dB, or lower the loudest "
                           "tracks' CC7."))
    elif tp > -1.0:
        findings.append(_f("mix_true_peak", "warning", f"True peak {tp} dBTP is above -1 dBTP (inter-sample overs after MP3 encoding).",
                           f"Lower the render gain by {tp + 1.0:.1f} dB or normalise with a -1 dBTP ceiling."))
    if il is not None and il < -28:
        findings.append(_f("mix_loudness", "info", f"Integrated loudness {il} LUFS is quiet (streaming targets are about -14 LUFS).",
                           f"Normalise on export (+{min(-14 - il, -1 - tp):.1f} dB keeps true peak <= -1 dBTP), e.g. --normalize -14."))
    elif il is not None and il > -9:
        findings.append(_f("mix_loudness", "warning", f"Integrated loudness {il} LUFS is very hot; dynamics will suffer.",
                           "Lower the render gain or the busiest tracks' CC7."))
    if max(abs(v) for v in dc) > 0.002:
        findings.append(_f("mix_dc_offset", "warning", f"DC offset {dc} (L, R) exceeds 0.002.",
                           "Apply a high-pass at ~10-20 Hz; check the synth/soundfont patch that adds it."))
    if sil["silence_ratio"] > 0.25:
        findings.append(_f("mix_silence", "warning", f"{sil['silence_ratio']:.0%} of the render is below {LOUD_SILENCE_DB:.0f} dBFS.",
                           "Check for muted/empty sections or long gaps in the arrangement."))
    if sil["leading_silence_s"] > 2.0:
        findings.append(_f("mix_silence", "info", f"{sil['leading_silence_s']} s of silence before the first sound.",
                           "Start the first section with at least one sounding part."))

    # ---- stereo
    if abs(st["balance_db"]) > 1.5:
        side = "left" if st["balance_db"] > 0 else "right"
        findings.append(_f("mix_stereo_balance", "warning", f"The mix leans {side} by {abs(st['balance_db']):.1f} dB.",
                           f"Pan the heaviest {side}-panned part back toward centre or balance it with a part on the other side."))
    if st["correlation"] < 0:
        findings.append(_f("mix_phase", "warning", f"Negative L/R correlation ({st['correlation']}): mono playback will cancel.",
                           "Reduce chorus (CC93) / stereo widening on the widest parts."))
    elif st["correlation"] > 0.97 and il is not None:
        findings.append(_f("mix_stereo_width", "info", f"The mix is nearly mono (L/R correlation {st['correlation']}).",
                           "Pan chords/pads/arps away from centre (CC10) and add some chorus (CC93) to pads."))

    # ---- stems: levels, band ownership, masking
    stem_rows, mask_rows = [], []
    if stems:
        stem_rows, mask_rows, sfind = _analyze_stems(stems, x, sr, loud)
        findings += sfind

    # ---- spectral balance vs reference (attribute to the stem that owns the band if we have stems)
    for band, val in bands.items():
        lo, hi = ref[band]
        if il is None or lo <= val <= hi:
            continue
        dev = val - hi if val > hi else val - lo
        owner = None
        if stem_rows:
            owner = max(stem_rows, key=lambda r: r["band_power_db"].get(band, -200))
        sev = "warning" if abs(dev) > 4 else "info"
        word = "too much" if dev > 0 else "too little"
        amt = round(min(6.0, max(1.0, abs(dev))), 1)
        sug = (f"{'Lower' if dev > 0 else 'Raise'} {owner['name']} (dominates {band}) by about {amt:g} dB"
               f" (CC7 {owner['volume']} -> {_cc7_after(owner['volume'], -amt if dev > 0 else amt)})"
               if owner else f"{'Cut' if dev > 0 else 'Boost'} the {band} band by ~{amt:g} dB.")
        findings.append(_f("mix_spectral_balance", sev, f"{word.capitalize()} {band} ({BANDS[band][0]}-{BANDS[band][1]} Hz): "
                           f"{val} dB of total power, reference {lo}..{hi} dB ({fam}).", sug,
                           track=owner["name"] if owner else None))

    findings = _dedupe(findings)
    score = max(0, 100 - sum(SEVERITY_COST[f["severity"]] for f in findings))
    mask_score = None
    rel = [m for m in mask_rows if m["relevant"]]
    if rel:
        mask_score = round(float(np.average([m["overlap"] for m in rel],
                                            weights=[max(m["weight"], 1e-6) for m in rel])), 3)
    return {"version": 1, "genre": g, "metrics": metrics, "stems": stem_rows,
            "masking": {"pairs": mask_rows, "score": mask_score,
                        "note": "overlap 0..1 per pair in its focus range (low 30-250 Hz / mid 200-2000 Hz); score = "
                                "energy-weighted mean over musically relevant pairs; lower is clearer"},
            "findings": findings, "score": score, "verified_by_ear": False,
            "disclaimer": "Objective measurements only; nobody listened to this render."}


def _dedupe(fs):
    seen, out = set(), []
    for f in fs:
        k = (f["check"], f["message"])
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def _analyze_stems(stems, mix, sr, mix_loud):
    rows, specs = [], []
    mix_ms = float(np.mean(k_weight(mix, sr) ** 2)) * 2
    for s in stems:
        y, ssr = load_audio(s["wav"])
        bs = band_spectrogram(y, ssr)
        specs.append(bs)
        kw = float(np.mean(k_weight(y, ssr) ** 2)) * 2
        tot = bs.sum() + EPS
        band_db = {}
        for band, (lo, hi) in BANDS.items():
            m = np.array([(a + b) / 2 >= lo and (a + b) / 2 < hi for a, b in THIRD_OCT])
            band_db[band] = round(db(float(bs[:, m, :].sum()) + EPS), 2)
        el, er = float(bs[:, :, 0].sum()), float(bs[:, :, 1].sum())
        rows.append({"name": s["name"], "role": s.get("role", "other"), "channel": s.get("channel"),
                     "volume": s.get("volume", 100), "pan": s.get("pan", 64), "reverb": s.get("reverb"),
                     "chorus": s.get("chorus"), "level_db_rel_mix": round(db(kw) - db(mix_ms), 2),
                     "balance_db": round(db(el) - db(er), 2),
                     "focus_share": {k: round(float(bs[:, _focus_mask(*v), :].sum() / tot), 3) for k, v in FOCUS.items()},
                     "band_power_db": band_db})
    findings, pairs = [], []
    # level sanity
    pitched = [r for r in rows if r["role"] not in ("drums", "kick")]
    for r in rows:
        if r["level_db_rel_mix"] < -30:
            findings.append(_f("mix_level", "info", f"{r['name']} is {r['level_db_rel_mix']} dB below the mix: practically inaudible.",
                               f"Raise {r['name']} by ~6 dB (CC7 {r['volume']} -> {_cc7_after(r['volume'], 6)}) or remove it.",
                               track=r["name"]))
    leads = [r for r in pitched if r["role"] == "lead"]
    others = [r for r in pitched if r["role"] in ("chords", "pad", "arp", "other")]
    if leads and others:
        top_lead = max(leads, key=lambda r: r["level_db_rel_mix"])
        loudest = max(others, key=lambda r: r["level_db_rel_mix"])
        gap = loudest["level_db_rel_mix"] - top_lead["level_db_rel_mix"]
        if gap > 0:
            n = min(8.0, gap + 2)
            findings.append(_f("mix_lead_level", "warning" if gap > 3 else "info",
                               f"Lead {top_lead['name']} is {gap:.1f} dB quieter than {loudest['name']}.",
                               f"Lower {loudest['name']} by {n:.0f} dB (CC7 {loudest['volume']} -> "
                               f"{_cc7_after(loudest['volume'], -n)}) or raise the lead.", track=top_lead["name"]))
    # masking
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            for fname, rng in FOCUS.items():
                if a["focus_share"][fname] < 0.15 or b["focus_share"][fname] < 0.15:
                    continue
                m = masking_pair(specs[i], specs[j], rng)
                roles = {a["role"], b["role"]}
                relevant = (fname == "low" and bool(roles & {"bass", "kick"})) or \
                           (fname == "mid" and bool(roles & {"lead"}) and len(roles) > 1) or \
                           (fname == "mid" and roles <= {"chords", "pad", "arp", "other"})
                w = min(a["focus_share"][fname], b["focus_share"][fname])
                row = {"a": a["name"], "b": b["name"], "focus": fname, "range_hz": list(rng), **m,
                       "relevant": relevant, "weight": round(w, 3)}
                pairs.append(row)
                if not relevant:
                    continue
                findings += _masking_findings(a, b, row)
    return rows, pairs, findings


def _masking_findings(a, b, m):
    out = []
    pa, pb = _PRIORITY.get(a["role"], 1), _PRIORITY.get(b["role"], 1)
    # the part with lower priority yields (ties: the louder-in-band one yields)
    if pa > pb or (pa == pb and (m["level_diff_db"] or 0) < 0):
        keep, yield_, masked_keep = a, b, m["masked_a"]
    else:
        keep, yield_, masked_keep = b, a, m["masked_b"]
    lo, hi = m["range_hz"]
    ov = m["overlap"]
    thr_warn, thr_info = (0.6, 0.45) if m["focus"] == "low" else (0.55, 0.4)
    if ov < thr_info and masked_keep < 0.5:
        return out
    sev = "warning" if (ov >= thr_warn or masked_keep >= 0.6) else "info"
    diff = m["contested_diff_db"]
    n = 3.0 if diff is None else float(min(6.0, max(2.0, 3.0 + (diff if yield_ is a else -diff))))
    sug = [f"lower {yield_['name']} by {n:.0f} dB (CC7 {yield_['volume']} -> {_cc7_after(yield_['volume'], -n)})"]
    if m["focus"] == "low":
        sug.append(f"or EQ-dip {yield_['name']} around the kick fundamental / sidechain it" if "kick" in (a["role"], b["role"])
                   else f"or move {yield_['name']} up an octave / high-pass it above ~150 Hz")
    elif abs((a.get("pan") or 64) - (b.get("pan") or 64)) < 20:
        side = 40 if (yield_.get("pan") or 64) <= 64 else 88
        sug.append(f"or pan {yield_['name']} to {side} (CC10) away from {keep['name']}")
    else:
        sug.append(f"or voice {yield_['name']} an octave away from {keep['name']}")
    out.append(_f("mix_masking", sev,
                  f"{a['name']} and {b['name']} compete in {lo}-{hi} Hz (overlap {ov:.2f}; {masked_keep:.0%} of "
                  f"{keep['name']}'s energy there is covered by {yield_['name']}).",
                  "; ".join(sug), track=yield_["name"], pair=[a["name"], b["name"]], focus=m["focus"]))
    return out


# ------------------------------------------------------------------ outputs for the critic

def critic_findings(mix_report: dict) -> list[dict]:
    """Mix findings in midigen.critic's finding shape: {check, severity, message, suggestion?, track?}."""
    keep = ("check", "severity", "message", "suggestion", "track")
    return [{k: f[k] for k in keep if k in f} for f in mix_report.get("findings", [])]


def summary_text(rep: dict) -> str:
    m = rep["metrics"]
    L = m["loudness"]
    lines = [f"mix score: {rep['score']}/100 (measured, not listened to)",
             f"  loudness {L['integrated_lufs']} LUFS integrated, max short-term {L['max_short_term_lufs']} LUFS; "
             f"true peak {m['true_peak_dbtp']} dBTP, sample peak {m['sample_peak_dbfs']} dBFS; "
             f"clipped samples {m['clipping']['clipped_samples']}",
             f"  silence {m['silence']['silence_ratio']:.0%}; DC {m['dc_offset']}; stereo balance {m['stereo']['balance_db']} dB, "
             f"correlation {m['stereo']['correlation']}",
             "  bands (dB rel. total): " + ", ".join(f"{k} {v}" for k, v in m["spectrum"]["bands_db_rel_total"].items())]
    if rep["masking"]["score"] is not None:
        lines.append(f"  masking score {rep['masking']['score']} (0 = clear, 1 = fully overlapping)")
    for f in rep["findings"]:
        lines.append(f"  [{f['severity']}] {f['check']}: {f['message']}" + (f" -> {f['suggestion']}" if f.get("suggestion") else ""))
    if not rep["findings"]:
        lines.append("  no mix issues measured")
    return "\n".join(lines)
