"""`midigen doctor`: check that midigen works here and explain, in plain English, how to fix what doesn't.

Core checks (Python version, import, genres, MIDI export) decide the exit code; optional pieces only warn.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "FAIL"
MIN_PY = (3, 10)
PIP = f'"{sys.executable}" -m pip install'


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def check_python():
    v = sys.version_info[:3]
    if v >= MIN_PY:
        return OK, f"Python {'.'.join(map(str, v))}", None
    return FAIL, f"Python {'.'.join(map(str, v))} is too old", (
        f"midigen needs Python {MIN_PY[0]}.{MIN_PY[1]} or newer: install it from https://www.python.org/downloads/ "
        "and run the installer again")


def check_import():
    try:
        import midigen
        return OK, f"midigen {getattr(midigen, '__version__', '?')} from {Path(midigen.__file__).parent}", None
    except Exception as e:  # noqa: BLE001
        return FAIL, f"cannot import midigen ({e})", "run the installer again: python3 install.py"


def check_genres():
    try:
        from midigen import packs
        from midigen.genres import available_genres
        names = available_genres()
        failed = packs.report().get("failed") or {}
    except Exception as e:  # noqa: BLE001
        return FAIL, f"genres failed to load ({e})", "reinstall midigen: python3 install.py"
    if not names:
        return FAIL, "no genres found", "reinstall midigen: python3 install.py"
    msg = f"{len(names)} genres ({', '.join(names[:6])}{', ...' if len(names) > 6 else ''})"
    if failed:
        return WARN, msg + f"; {len(failed)} pack file(s) failed to load", (
            "run `midigen genres` to see which files and why; the built-in genres still work")
    return OK, msg, None


def check_midi_export():
    try:
        import mido
        import midigen as mg
        spec = mg.build_spec("pop", bars=2, seed=1)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "doctor.mid"
            mg.export_midi(spec, str(out))
            n = len(mido.MidiFile(str(out)).tracks)
        return OK, f"MIDI export works ({n} tracks)", None
    except Exception as e:  # noqa: BLE001
        return FAIL, f"MIDI export failed ({type(e).__name__}: {e})", "reinstall midigen: python3 install.py"


def _optional():
    out = []
    try:
        from midigen.audio import render as audio
    except Exception:  # noqa: BLE001
        audio = None
    np_ok = _has("numpy") and _has("scipy") and _has("soundfile")
    out.append((OK if np_ok else WARN, "audio extras (numpy, scipy, soundfile)" + ("" if np_ok else " not installed"),
                None if np_ok else f"for WAV/MP3 rendering: python3 install.py --audio  (or {PIP} numpy scipy soundfile)"))
    fs = audio.find_fluidsynth() if audio else shutil.which("fluidsynth")
    out.append((OK if fs else WARN, f"FluidSynth: {fs}" if fs else "FluidSynth not found",
                None if fs else "optional, for realistic instrument sounds: Linux `sudo apt install fluidsynth`, "
                "macOS `brew install fluid-synth`, Windows: download from https://github.com/FluidSynth/fluidsynth/releases "
                "(without it audio uses the built-in synth)"))
    sf = audio.find_soundfont() if audio else None
    out.append((OK if sf else WARN, f"soundfont: {sf}" if sf else "no GM soundfont (.sf2) found",
                None if sf else "optional, used with FluidSynth: Linux `sudo apt install fluid-soundfont-gm`, or "
                "download GeneralUser GS and set MIDIGEN_SOUNDFONT=/path/to/file.sf2"))
    ff = audio.find_ffmpeg() if audio else shutil.which("ffmpeg")
    out.append((OK if ff else WARN, f"ffmpeg: {ff}" if ff else "ffmpeg not found",
                None if ff else "optional, only for MP3 export: install ffmpeg from https://ffmpeg.org/download.html"))
    svc = _has("fastapi") and _has("uvicorn") and _has("midigen_integrations")
    out.append((OK if svc else WARN, "web service (fastapi, uvicorn, midigen-integrations)" + ("" if svc else " not installed"),
                None if svc else "for `midigen ui` without the web UI folder and for apps that call midigen over HTTP: "
                "python3 install.py"))
    rt = _has("rtmidi")
    out.append((OK if rt else WARN, "python-rtmidi" + ("" if rt else " not installed"),
                None if rt else "optional, only for live MIDI streaming to a DAW: python3 install.py --stream"))
    try:
        from midigen.ui import find_ui_dir
        ui = find_ui_dir()
    except Exception:  # noqa: BLE001
        ui = None
    out.append((OK if ui else WARN, f"web UI folder: {ui}" if ui else "web UI folder (midigen-ui) not found",
                None if ui else "optional: put the midigen-ui folder next to the midigen folder or set MIDIGEN_UI_DIR; "
                "`midigen ui` then falls back to the web service page"))
    return out


CORE = [check_python, check_import, check_genres, check_midi_export]


def run(stream=None) -> int:
    stream = stream or sys.stdout
    core_failed = False
    print("midigen doctor\n\nCore:", file=stream)
    for chk in CORE:
        status, msg, fix = chk()
        core_failed |= status == FAIL
        print(f"  [{status:>4}] {msg}" + (f"\n         fix: {fix}" if fix else ""), file=stream)
    print("\nOptional:", file=stream)
    for status, msg, fix in _optional():
        print(f"  [{status:>4}] {msg}" + (f"\n         fix: {fix}" if fix else ""), file=stream)
    print("\n" + ("Something core is broken; see the fixes above." if core_failed else
                  "midigen is ready. Optional items marked 'warn' are only needed for the features named."), file=stream)
    return 1 if core_failed else 0


def add_parser(sub):
    p = sub.add_parser("doctor", help="check the installation and explain how to fix problems")
    p.set_defaults(fn=lambda args: sys.exit(run()))
