"""Pure numpy/scipy fallback synthesizer: MIDI -> stereo float32, used when FluidSynth or a soundfont is missing.

It is deliberately simple (subtractive/additive voices per GM program family, synthetic drum kit, Schroeder
reverb for CC91, modulated-delay chorus for CC93). It honours CC7/CC11 volume, CC10 pan, CC91/CC93 sends and
velocity, so level/pan/masking measurements are meaningful in relative terms, but its timbres are crude and
were never checked by ear (nobody working on this could listen)."""
from __future__ import annotations

import math
from collections import defaultdict

import mido
import numpy as np
from scipy.signal import butter, sosfilt

MASTER_GAIN = 0.22
_MAX_NOTE_S = 8.0


def _midi_hz(p):
    return 440.0 * 2 ** ((p - 69) / 12)


def _env(n, sr, a, d, s, r_len, hold_n):
    """ADSR-like envelope for a note held for hold_n samples followed by an r_len-sample release."""
    env = np.empty(n, dtype=np.float32)
    an, dn = max(1, int(a * sr)), max(1, int(d * sr))
    t = np.arange(n)
    env[:] = s
    ai = t < an
    env[ai] = t[ai] / an
    di = (t >= an) & (t < an + dn)
    env[di] = 1 - (1 - s) * (t[di] - an) / dn
    if hold_n < n:
        start_level = env[min(hold_n, n - 1)]
        rel = t >= hold_n
        env[rel] = start_level * np.exp(-5.0 * (t[rel] - hold_n) / max(1, r_len))
    return env


def _saw(ph):
    return 2.0 * (ph % 1.0) - 1.0


def _lp(x, cutoff, sr, order=2):
    cutoff = min(cutoff, sr * 0.45)
    sos = butter(order, cutoff, btype="low", fs=sr, output="sos")
    return sosfilt(sos, x).astype(np.float32)


def _hp(x, cutoff, sr, order=2):
    sos = butter(order, cutoff, btype="high", fs=sr, output="sos")
    return sosfilt(sos, x).astype(np.float32)


def _bp(x, lo, hi, sr, order=2):
    sos = butter(order, [lo, min(hi, sr * 0.45)], btype="band", fs=sr, output="sos")
    return sosfilt(sos, x).astype(np.float32)


def _pitched(program, pitch, dur, sr, rng):
    f = _midi_hz(pitch)
    fam = program // 8
    rel = {0: 0.25, 1: 0.6, 2: 0.08, 3: 0.3, 4: 0.12, 5: 0.35, 6: 0.15, 7: 0.15, 8: 0.2, 9: 0.15, 10: 0.6, 11: 0.5}.get(fam, 0.2)
    if fam in (0, 1, 3):           # piano / chromatic perc / guitar: decaying, the note may end early
        tail = min(dur, 2.5 if fam != 3 else 1.6) + rel
    else:
        tail = dur + rel
    n = max(16, int(min(tail, _MAX_NOTE_S + rel) * sr))
    hold = int(min(dur, _MAX_NOTE_S) * sr)
    t = np.arange(n, dtype=np.float32) / sr
    ph = f * t
    if fam == 0:      # piano
        x = sum((0.6 / k) * np.sin(2 * np.pi * k * ph * (1 + 0.0004 * k * k)) * np.exp(-t * (1.5 + 0.9 * k)) for k in range(1, 7))
        env = _env(n, sr, 0.003, 0.05, 1.0, rel * sr, hold)
    elif fam == 1:    # bells, vibes, marimba
        x = np.sin(2 * np.pi * ph) * np.exp(-t * 2.5) + 0.3 * np.sin(2 * np.pi * ph * 3.99) * np.exp(-t * 6)
        env = _env(n, sr, 0.002, 0.05, 1.0, rel * sr, hold)
    elif fam == 2:    # organ
        x = sum(a * np.sin(2 * np.pi * k * ph) for k, a in ((1, 0.6), (2, 0.4), (3, 0.25), (4, 0.15), (6, 0.08)))
        env = _env(n, sr, 0.01, 0.02, 1.0, rel * sr, hold)
    elif fam == 3:    # guitars (29/30 overdriven/distorted)
        x = _lp(_saw(ph + 0.002 * rng.standard_normal()), 2500 if program < 29 else 4000, sr) * np.exp(-t * 1.2)
        if program in (29, 30):
            x = np.tanh(4 * x) * 0.6
        env = _env(n, sr, 0.004, 0.08, 0.8, rel * sr, hold)
    elif fam == 4:    # basses
        sub = np.sin(2 * np.pi * ph)
        saw = _lp(_saw(ph), 900 if program >= 38 else 600, sr)
        x = 0.7 * sub + (0.5 if program >= 38 else 0.3) * saw
        env = _env(n, sr, 0.004, 0.15, 0.75, rel * sr, hold)
    elif fam in (5, 6) and program not in range(56, 64) or fam == 11 or program in (88, 89, 90, 91, 92, 93, 94, 95):
        # strings, ensemble, choir, pads: detuned saws, slow attack
        x = sum(_saw(ph * d + rng.random()) for d in (0.996, 1.0, 1.004)) / 3
        x = _lp(x, 2200 if fam != 11 else 1800, sr)
        env = _env(n, sr, 0.18, 0.3, 0.85, rel * sr, hold)
    elif fam in (7, 8):  # brass (56-63) / reeds (64-71)
        x = _lp(_saw(ph), 2800, sr) if fam == 7 or program < 64 else _lp(np.sign(np.sin(2 * np.pi * ph)) * 0.6 + 0.4 * _saw(ph), 2400, sr)
        env = _env(n, sr, 0.03, 0.1, 0.85, rel * sr, hold)
    elif fam == 9:    # flute / pipe
        x = np.sin(2 * np.pi * ph) + 0.08 * _lp(rng.standard_normal(n).astype(np.float32), 3000, sr)
        env = _env(n, sr, 0.05, 0.1, 0.9, rel * sr, hold)
    elif fam == 10:   # synth leads: 80 square, others saw
        x = np.sign(np.sin(2 * np.pi * ph)) * 0.6 if program == 80 else _saw(ph)
        x = _lp(x, 3500, sr)
        env = _env(n, sr, 0.005, 0.1, 0.8, rel * sr, hold)
    else:
        x = np.sin(2 * np.pi * ph)
        env = _env(n, sr, 0.01, 0.1, 0.8, rel * sr, hold)
    return (np.asarray(x, dtype=np.float32) * env).astype(np.float32)


def _drum(note, sr, rng):
    def noise(sec):
        return rng.standard_normal(int(sec * sr)).astype(np.float32)
    if note in (35, 36):                      # kick
        n = int(0.45 * sr)
        t = np.arange(n) / sr
        f = 48 + 110 * np.exp(-t * 28)
        x = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 7.0)
        x[: int(0.004 * sr)] += 0.4 * noise(0.004)[: int(0.004 * sr)]
        return 1.3 * x.astype(np.float32)
    if note in (38, 40):                      # snare
        nz = _bp(noise(0.25), 1200, 9000, sr) * np.exp(-np.arange(int(0.25 * sr)) / sr * 18)
        t = np.arange(len(nz)) / sr
        return (0.9 * nz + 0.5 * np.sin(2 * np.pi * 185 * t) * np.exp(-t * 30)).astype(np.float32)
    if note == 39:                            # clap
        x = _bp(noise(0.2), 900, 5000, sr) * np.exp(-np.arange(int(0.2 * sr)) / sr * 22)
        return x.astype(np.float32)
    if note == 37:                            # rim
        t = np.arange(int(0.05 * sr)) / sr
        return (0.6 * np.sin(2 * np.pi * 1700 * t) * np.exp(-t * 90)).astype(np.float32)
    if note in (42, 44, 46):                  # hats
        sec = 0.06 if note != 46 else 0.35
        x = _hp(noise(sec), 7000, sr) * np.exp(-np.arange(int(sec * sr)) / sr * (60 if note != 46 else 9))
        return 0.45 * x.astype(np.float32)
    if note in (49, 52, 55, 57):              # crash
        x = _hp(noise(1.6), 4000, sr) * np.exp(-np.arange(int(1.6 * sr)) / sr * 2.8)
        return 0.35 * x.astype(np.float32)
    if note in (51, 53, 59):                  # ride
        t = np.arange(int(0.8 * sr)) / sr
        x = _hp(noise(0.8), 5000, sr) * 0.6 + 0.3 * np.sin(2 * np.pi * 3200 * t)
        return 0.3 * (x * np.exp(-t * 5)).astype(np.float32)
    if 41 <= note <= 50:                      # toms
        n = int(0.4 * sr)
        t = np.arange(n) / sr
        base = 80 + (note - 41) * 18
        x = np.sin(2 * np.pi * np.cumsum(base * (1 + 0.5 * np.exp(-t * 20))) / sr) * np.exp(-t * 8)
        return 0.8 * x.astype(np.float32)
    sec = 0.12                                # everything else: shaker-ish noise
    x = _bp(noise(sec), 2000, 10000, sr) * np.exp(-np.arange(int(sec * sr)) / sr * 30)
    return 0.3 * x.astype(np.float32)


def _comb(x, d, g):
    """y[n] = x[n] + g*y[n-d], computed block-wise (d samples at a time) so it stays fast in numpy."""
    y = np.array(x, dtype=np.float64)
    for i in range(d, len(y), d):
        j = min(len(y), i + d)
        y[i:j] += g * y[i - d:j - d]
    return y


def _allpass(x, d, g):
    """Schroeder allpass y[n] = -g*x[n] + x[n-d] + g*y[n-d] (block-wise)."""
    x = np.asarray(x, dtype=np.float64)
    y = -g * x
    y[d:] += x[:-d]
    for i in range(d, len(y), d):
        j = min(len(y), i + d)
        y[i:j] += g * y[i - d:j - d]
    return y


def _reverb(x, sr):
    """Small stereo Schroeder reverb (4 combs + 2 allpasses per side)."""
    out = np.zeros((len(x), 2), dtype=np.float32)
    for side, spread in ((0, 0), (1, 23)):
        acc = np.zeros(len(x), dtype=np.float64)
        for ms, g in ((29.7, 0.80), (37.1, 0.79), (41.1, 0.78), (43.7, 0.77)):
            acc += _comb(x[:, side], int(ms * sr / 1000) + spread, g)
        y = _lp(acc / 4, 6000, sr, order=1)
        for ms, g in ((5.0, 0.7), (1.7, 0.7)):
            y = _allpass(y, int(ms * sr / 1000) + spread // 4, g)
        out[:, side] = y
    return out


def _chorus(x, sr):
    n = len(x)
    t = np.arange(n) / sr
    out = np.zeros_like(x)
    for side, phase in ((0, 0.0), (1, math.pi / 2)):
        delay = (0.012 + 0.003 * np.sin(2 * np.pi * 0.8 * t + phase)) * sr
        idx = np.arange(n) - delay
        out[:, side] = np.interp(idx, np.arange(n), x[:, side], left=0.0, right=0.0)
    return out


def _cc_gain(v):
    return (max(0, min(127, v)) / 127.0) ** 2


def synth_midi(mid: mido.MidiFile, sample_rate: int = 44100, channels: set[int] | None = None,
               notes_filter=None, seed: int = 0) -> np.ndarray:
    """Render a mido.MidiFile to a (n, 2) float32 array. `channels`/`notes_filter(ch, note)` select what plays
    (used for stems). Deterministic for a given seed."""
    sr = sample_rate
    rng = np.random.default_rng(seed)
    state = defaultdict(lambda: {"program": 0, 7: 100, 10: 64, 11: 127, 91: 40, 93: 0})
    notes = []
    open_n = defaultdict(list)
    now = 0.0
    for msg in mid:  # merged tracks, msg.time in seconds (tempo map applied by mido)
        now += msg.time
        if msg.is_meta:
            continue
        st = state[msg.channel]
        if msg.type == "program_change":
            st["program"] = msg.program
        elif msg.type == "control_change" and msg.control in (7, 10, 11, 91, 93):
            st[msg.control] = msg.value
        elif msg.type == "note_on" and msg.velocity > 0:
            open_n[(msg.channel, msg.note)].append((now, msg.velocity, dict(st)))
        elif msg.type in ("note_on", "note_off"):
            lst = open_n.get((msg.channel, msg.note))
            if lst:
                t0, vel, snap = lst.pop(0)
                notes.append((msg.channel, msg.note, t0, now - t0, vel, snap))
    total = max([now] + [t0 + d for _, _, t0, d, _, _ in notes])
    n_total = int((total + 2.5) * sr)
    dry = np.zeros((n_total, 2), dtype=np.float32)
    rev = np.zeros((n_total, 2), dtype=np.float32)
    cho = np.zeros((n_total, 2), dtype=np.float32)
    cache: dict = {}
    for ch, note, t0, dur, vel, snap in notes:
        if channels is not None and ch not in channels:
            continue
        if notes_filter is not None and not notes_filter(ch, note):
            continue
        if ch == 9:
            key = ("d", note)
            if key not in cache:
                cache[key] = _drum(note, sr, rng)
            x = cache[key]
        else:
            x = _pitched(snap["program"], note, max(0.02, dur), sr, rng)
        amp = MASTER_GAIN * (vel / 127.0) ** 1.6 * _cc_gain(snap[7]) * (snap[11] / 127.0) ** 2
        theta = snap[10] / 127.0 * math.pi / 2
        gl, gr = math.cos(theta), math.sin(theta)  # constant-power pan
        i0 = int(t0 * sr)
        i1 = min(n_total, i0 + len(x))
        seg = x[: i1 - i0] * amp
        dry[i0:i1, 0] += seg * gl
        dry[i0:i1, 1] += seg * gr
        if snap[91]:
            s = seg * (snap[91] / 127.0)
            rev[i0:i1, 0] += s * gl
            rev[i0:i1, 1] += s * gr
        if snap[93]:
            s = seg * (snap[93] / 127.0)
            cho[i0:i1, 0] += s * gl
            cho[i0:i1, 1] += s * gr
    out = dry
    if np.any(rev):
        out = out + 0.35 * _reverb(rev, sr)
    if np.any(cho):
        out = out + 0.5 * _chorus(cho, sr)
    # trim trailing near-silence beyond the musical end + tail
    end = int((total + 0.2) * sr)
    nz = np.nonzero(np.max(np.abs(out[end:]), axis=1) > 1e-4)[0]
    cut = end + (nz[-1] + 1 if len(nz) else 0)
    return out[: max(cut, 1)].astype(np.float32)
