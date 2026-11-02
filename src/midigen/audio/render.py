"""MIDI/spec -> WAV (FluidSynth + GM soundfont, or the numpy fallback synth) and WAV -> MP3 (ffmpeg).

Backend discovery (first hit wins):
  FluidSynth binary : $MIDIGEN_FLUIDSYNTH, then `fluidsynth` on PATH
  soundfont (.sf2/.sf3): $MIDIGEN_SOUNDFONT (file), $MIDIGEN_SOUNDFONT_DIR, /usr/share/sounds/sf2, /usr/share/soundfonts,
                     ~/.local/share/soundfonts, ~/.fluidsynth (GeneralUser GS / FluidR3_GM / default.sf2 preferred)
  ffmpeg            : $MIDIGEN_FFMPEG, then `ffmpeg` on PATH (only needed for MP3 export)
backend="auto" uses FluidSynth when both binary and soundfont exist, else the numpy synth (if numpy/scipy
are installed). If nothing works an AudioBackendError explains what is missing."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import mido

SAMPLE_RATE = 44100
FLUIDSYNTH_GAIN = 0.5
_SF_DIRS = ["/usr/share/sounds/sf2", "/usr/share/soundfonts", "/usr/share/sounds/sf3", "~/.local/share/soundfonts",
            "~/.fluidsynth"]
_SF_PREFER = ("generaluser", "fluidr3_gm", "fluidr3", "default", "musescore_general", "timgm")


class AudioBackendError(RuntimeError):
    """No usable audio backend (or a requested one is missing). The message says how to fix it."""


@dataclass
class RenderResult:
    wav: str
    backend: str
    sample_rate: int
    seconds: float
    soundfont: str | None = None
    mp3: str | None = None
    midi: str | None = None
    humanize: dict | None = None
    mix: bool | None = None
    stems: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ discovery

def find_fluidsynth() -> str | None:
    env = os.environ.get("MIDIGEN_FLUIDSYNTH")
    if env:
        return env if Path(env).is_file() and os.access(env, os.X_OK) else None
    return shutil.which("fluidsynth")


def find_soundfont() -> str | None:
    env = os.environ.get("MIDIGEN_SOUNDFONT")
    if env:
        return env if Path(env).is_file() else None
    dirs = ([os.environ["MIDIGEN_SOUNDFONT_DIR"]] if os.environ.get("MIDIGEN_SOUNDFONT_DIR") else []) + _SF_DIRS
    found = []
    for d in dirs:
        p = Path(d).expanduser()
        if p.is_dir():
            found += sorted(x for x in p.iterdir() if x.suffix.lower() in (".sf2", ".sf3") and x.is_file())
    if not found:
        return None
    for pref in _SF_PREFER:
        for f in found:
            if f.name.lower().startswith(pref):
                return str(f)
    return str(found[0])


def find_ffmpeg() -> str | None:
    env = os.environ.get("MIDIGEN_FFMPEG")
    if env:
        return env if Path(env).is_file() else None
    return shutil.which("ffmpeg")


def numpy_available() -> bool:
    try:
        import numpy  # noqa: F401
        import scipy.signal  # noqa: F401
        return True
    except Exception:
        return False


def available_backends() -> dict:
    fs, sf = find_fluidsynth(), find_soundfont()
    return {"fluidsynth": {"available": bool(fs and sf), "binary": fs, "soundfont": sf},
            "numpy": {"available": numpy_available()},
            "ffmpeg": {"available": bool(find_ffmpeg()), "binary": find_ffmpeg()},
            "auto": "fluidsynth" if fs and sf else ("numpy" if numpy_available() else None)}


def _pick_backend(backend: str, soundfont: str | None) -> tuple[str, str | None]:
    if backend not in ("auto", "fluidsynth", "numpy"):
        raise ValueError(f"unknown backend {backend!r}; use auto, fluidsynth or numpy")
    fs = find_fluidsynth()
    sf = soundfont or find_soundfont()
    if soundfont and not Path(soundfont).is_file():
        raise AudioBackendError(f"soundfont not found: {soundfont}")
    if backend in ("auto", "fluidsynth") and fs and sf:
        return "fluidsynth", sf
    if backend == "fluidsynth":
        missing = [x for x, ok in (("the fluidsynth binary", fs), ("a GM soundfont (.sf2/.sf3)", sf)) if not ok]
        raise AudioBackendError(
            "FluidSynth backend unavailable: missing " + " and ".join(missing) + ". Install it (apt-get install "
            "fluidsynth fluid-soundfont-gm, or `micromamba create -p .tools/fs -c conda-forge fluidsynth`) and/or "
            "set MIDIGEN_FLUIDSYNTH / MIDIGEN_SOUNDFONT. Or use backend='numpy' (built-in fallback synth).")
    if numpy_available():
        return "numpy", None
    raise AudioBackendError(
        "No audio backend available: FluidSynth+soundfont not found and numpy/scipy are not installed. "
        "pip install numpy scipy soundfile (fallback synth) or install fluidsynth + a GM soundfont "
        "(see AUDIO_NOTES.md).")


# ------------------------------------------------------------------ inputs

def to_midifile(source, mix: bool = True) -> tuple[mido.MidiFile, dict | None]:
    """Accept a spec dict, a Performance, a .json spec path, a .mid path or a mido.MidiFile.
    Returns (MidiFile, spec-or-None). Specs are exported with the per-genre mix defaults unless mix=False."""
    from ..arranger import Performance, render
    from ..midi_io import performance_to_midi
    if isinstance(source, mido.MidiFile):
        return source, None
    if isinstance(source, Performance):
        return performance_to_midi(source, mix=mix), source.spec
    if isinstance(source, dict):
        perf = render(source)
        return performance_to_midi(perf, mix=mix), perf.spec
    p = Path(source)
    if p.suffix.lower() == ".json":
        from ..spec import load
        spec = load(p)
        perf = render(spec)
        return performance_to_midi(perf, mix=mix), perf.spec
    if p.suffix.lower() in (".mid", ".midi"):
        return mido.MidiFile(str(p)), None
    raise ValueError(f"don't know how to render {source!r} (expected spec dict/.json, .mid or MidiFile)")


def _midi_bytes(mid: mido.MidiFile) -> bytes:
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


# ------------------------------------------------------------------ rendering

def _render_fluidsynth(mid: mido.MidiFile, wav: Path, sf: str, sr: int, gain: float) -> None:
    fs = find_fluidsynth()
    with tempfile.TemporaryDirectory() as td:
        mpath = Path(td) / "in.mid"
        mpath.write_bytes(_midi_bytes(mid))
        cmd = [fs, "-ni", "-q", "-g", str(gain), "-r", str(sr), "-O", "float", "-T", "wav", "-F", str(wav), sf, str(mpath)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not wav.exists():
            raise AudioBackendError(f"fluidsynth failed ({r.returncode}): {r.stderr.strip()[:500]}")


def _render_numpy(mid: mido.MidiFile, wav: Path, sr: int, channels=None, notes_filter=None) -> None:
    import soundfile as sf_mod
    from .synth import synth_midi
    x = synth_midi(mid, sample_rate=sr, channels=channels, notes_filter=notes_filter)
    sf_mod.write(str(wav), x, sr, subtype="FLOAT")


def _wav_seconds(path: Path) -> float:
    import soundfile as sf_mod
    info = sf_mod.info(str(path))
    return info.frames / info.samplerate


def render_audio(source, wav_path, *, mp3: bool | str = False, backend: str = "auto", soundfont: str | None = None,
                 sample_rate: int = SAMPLE_RATE, gain: float = FLUIDSYNTH_GAIN, mix: bool = True,
                 humanize: bool | dict | int | None = None, genre: str | None = None,
                 save_midi: str | Path | None = None, normalize_lufs: float | None = None) -> RenderResult:
    """Render a spec/.json/.mid/MidiFile to a 32-bit float WAV (and optionally MP3).

    humanize: None/False = off; True = per-genre defaults with seed 1; int = seed with genre defaults;
              dict = {"seed", "timing_ms", "velocity"}. Applied to the rendered copy only.
    normalize_lufs: only affects the MP3 (gain change via ffmpeg, capped so true peak stays <= -1 dBTP);
              the WAV is always the raw render that the analyzer measures."""
    mid, spec = to_midifile(source, mix=mix)
    genre = genre or (spec or {}).get("genre")
    hum = None
    if humanize:
        from .humanize import humanize_midi
        from .mix import humanize_defaults
        hum = humanize_defaults(genre)
        hum["seed"] = 1
        if isinstance(humanize, dict):
            hum.update(humanize)
        elif isinstance(humanize, int) and not isinstance(humanize, bool):
            hum["seed"] = humanize
        mid = humanize_midi(mid, seed=hum["seed"], timing_ms=hum["timing_ms"], velocity=hum["velocity"])
    which, sf = _pick_backend(backend, soundfont)
    wav = Path(wav_path)
    wav.parent.mkdir(parents=True, exist_ok=True)
    if save_midi:
        Path(save_midi).parent.mkdir(parents=True, exist_ok=True)
        mid.save(str(save_midi))
    if which == "fluidsynth":
        _render_fluidsynth(mid, wav, sf, sample_rate, gain)
    else:
        _render_numpy(mid, wav, sample_rate)
    res = RenderResult(str(wav), which, sample_rate, round(_wav_seconds(wav), 3), sf, None,
                       str(save_midi) if save_midi else None, hum, mix if spec is not None else None)
    if mp3:
        mp3_path = Path(mp3) if isinstance(mp3, (str, Path)) and not isinstance(mp3, bool) else wav.with_suffix(".mp3")
        res.mp3 = str(export_mp3(wav, mp3_path, normalize_lufs=normalize_lufs))
    return res


def export_mp3(wav_path, mp3_path=None, bitrate: str = "192k", normalize_lufs: float | None = None) -> Path:
    """WAV -> MP3 with ffmpeg (libmp3lame). Optional loudness normalisation (static gain, true peak capped at -1 dBTP)."""
    ff = find_ffmpeg()
    if not ff:
        raise AudioBackendError("MP3 export needs ffmpeg (apt-get install ffmpeg or set MIDIGEN_FFMPEG).")
    wav_path = Path(wav_path)
    mp3_path = Path(mp3_path) if mp3_path else wav_path.with_suffix(".mp3")
    af = []
    if normalize_lufs is not None:
        from .analysis import ffmpeg_loudness
        m = ffmpeg_loudness(wav_path)
        if m.get("integrated_lufs") is not None and m["integrated_lufs"] > -70:
            g = normalize_lufs - m["integrated_lufs"]
            if m.get("true_peak_dbtp") is not None:
                g = min(g, -1.0 - m["true_peak_dbtp"])
            af = ["-af", f"volume={g:.2f}dB"]
    cmd = [ff, "-y", "-loglevel", "error", "-i", str(wav_path), *af, "-codec:a", "libmp3lame", "-b:a", bitrate,
           str(mp3_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise AudioBackendError(f"ffmpeg mp3 export failed: {r.stderr.strip()[:500]}")
    return mp3_path


# ------------------------------------------------------------------ stems

def _track_info(mid: mido.MidiFile, spec: dict | None) -> list[dict]:
    """One entry per note-bearing track: name, channel, program, role, CC7/CC10/CC91/CC93 at start."""
    roles = {}
    if spec:
        roles = {t["name"]: t.get("role", "other") for t in spec.get("tracks", [])}
    out = []
    for i, tr in enumerate(mid.tracks):
        name, ch, prog, cc, has_notes = None, None, 0, {}, False
        for m in tr:
            if m.type == "track_name":
                name = m.name
            elif m.type == "program_change":
                prog = m.program
            elif m.type == "control_change" and m.control not in cc:
                cc[m.control] = m.value
            elif m.type == "note_on" and m.velocity > 0:
                has_notes = True
                ch = m.channel if ch is None else ch
        if not has_notes:
            continue
        name = name or f"Track {i}"
        role = roles.get(name) or ("drums" if ch == 9 else _guess_role_from_program(prog))
        out.append({"index": i, "name": name, "channel": ch, "program": prog, "role": role,
                    "volume": cc.get(7, 100), "pan": cc.get(10, 64), "reverb": cc.get(91), "chorus": cc.get(93)})
    return out


def _guess_role_from_program(p: int) -> str:
    if 32 <= p <= 39:
        return "bass"
    if 88 <= p <= 95 or 48 <= p <= 55:
        return "pad"
    if 80 <= p <= 87 or 56 <= p <= 79:
        return "lead"
    return "chords"


def _solo(mid: mido.MidiFile, keep_index: int, note_filter=None) -> mido.MidiFile:
    """Copy of `mid` keeping the conductor + one track (optionally only notes passing note_filter)."""
    out = mido.MidiFile(type=1, ticks_per_beat=mid.ticks_per_beat)
    for i, tr in enumerate(mid.tracks):
        if i == keep_index or not any(not m.is_meta for m in tr):
            if i == keep_index and note_filter is not None:
                nt, carry = mido.MidiTrack(), 0
                for m in tr:
                    if m.type in ("note_on", "note_off") and not note_filter(m.note):
                        carry += m.time
                        continue
                    nt.append(m.copy(time=m.time + carry))
                    carry = 0
                out.tracks.append(nt)
            else:
                out.tracks.append(tr)
    return out


KICK_NOTES = (35, 36)


def render_stems(source, out_dir, *, backend: str = "auto", soundfont: str | None = None,
                 sample_rate: int = SAMPLE_RATE, gain: float = FLUIDSYNTH_GAIN, mix: bool = True,
                 split_kick: bool = True) -> list[dict]:
    """Render each note-bearing track solo to out_dir/<nn>_<name>.wav. The drum track is split into a 'Kick'
    stem (GM 35/36) and the rest, so bass-vs-kick masking can be measured. Returns track-info dicts + 'wav'."""
    mid, spec = to_midifile(source, mix=mix)
    which, sf = _pick_backend(backend, soundfont)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stems = []
    for info in _track_info(mid, spec):
        parts = [(info, None)]
        if split_kick and info["channel"] == 9:
            parts = [({**info, "name": "Kick", "role": "kick", "source_track": info["name"]}, lambda n: n in KICK_NOTES),
                     ({**info, "name": info["name"] + " (no kick)", "source_track": info["name"]},
                      lambda n: n not in KICK_NOTES)]
        for inf, flt in parts:
            solo = _solo(mid, inf["index"], flt)
            if not any(m.type == "note_on" and m.velocity > 0 for tr in solo.tracks for m in tr):
                continue
            safe = "".join(c if c.isalnum() else "_" for c in inf["name"])[:40].strip("_")
            wav = out_dir / f"{len(stems):02d}_{safe}.wav"
            if which == "fluidsynth":
                _render_fluidsynth(solo, wav, sf, sample_rate, gain)
            else:
                _render_numpy(solo, wav, sample_rate)
            stems.append({**inf, "wav": str(wav), "backend": which})
    (out_dir / "stems.json").write_text(json.dumps(stems, indent=1))
    return stems
