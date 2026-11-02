#!/usr/bin/env python3
"""One-step installer for midigen (standard library only; works on Linux, macOS and Windows).

    python3 install.py              # core + web service, then makes demo.mid to prove it works
    python3 install.py --audio      # also WAV rendering (numpy, scipy, soundfile)
    python3 install.py --stream     # also live MIDI out (python-rtmidi)
    python3 install.py --dry-run    # only show what would happen

Everything goes into a private folder (.venv) inside this project; nothing system-wide is changed.
"""
# Keep this file free of newer syntax so very old Pythons reach the friendly version message below.
import argparse
import os
import subprocess
import sys

MIN = (3, 10)
HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS = os.name == "nt"


def say(msg=""):
    print(msg, flush=True)


def fail(msg, code=1):
    sys.stderr.write("\nmidigen install: PROBLEM\n  " + msg.replace("\n", "\n  ") + "\n")
    sys.exit(code)


def check_python(version=None):
    v = tuple(version or sys.version_info[:3])
    if v < MIN:
        fail("This is Python {0}, but midigen needs Python {1}.{2} or newer.\n"
             "Install a current Python from https://www.python.org/downloads/ (on Windows tick\n"
             "'Add python.exe to PATH'), then run this installer again.".format(".".join(map(str, v)), *MIN))


def venv_python(venv):
    return os.path.join(venv, "Scripts", "python.exe") if WINDOWS else os.path.join(venv, "bin", "python")


def venv_bin(venv, name):
    return os.path.join(venv, "Scripts", name + ".exe") if WINDOWS else os.path.join(venv, "bin", name)


def run(cmd, dry, what, optional=False):
    say("  $ " + " ".join('"{0}"'.format(c) if " " in c else c for c in cmd))
    if dry:
        return True
    r = subprocess.call(cmd, cwd=HERE)
    if r == 0:
        return True
    if optional:
        say("  (skipped: {0} failed; midigen works without it. `midigen doctor` explains how to add it later)".format(what))
        return False
    fail("{0} failed (exit code {1}).\nCheck your internet connection and try again. If it keeps failing, copy the\n"
         "messages above into a bug report.".format(what, r))


def create_venv(venv, dry=False):
    if os.path.exists(venv_python(venv)):
        say("  using the existing private Python at " + venv)
        return
    say("  creating a private Python environment at " + venv)
    if dry:
        return
    try:
        import venv as venvmod
        venvmod.EnvBuilder(with_pip=True, upgrade_deps=False).create(venv)
    except Exception as e:  # e.g. Debian/Ubuntu without python3-venv
        fail("Could not create the private Python environment ({0}).\nOn Debian/Ubuntu run:  sudo apt install python3-venv\n"
             "then run this installer again.".format(e))
    if not os.path.exists(venv_python(venv)):
        fail("The private Python environment was created without pip.\nOn Debian/Ubuntu run:  sudo apt install python3-venv")


def plan(args):
    """The pip installs to run, as (argv, description, optional)."""
    py = venv_python(args.venv)
    pip = [py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    extras = ["ui"] + ([] if args.no_service else ["service"]) + (["dev"] if args.dev else [])
    steps = [(pip + ["-e", ".[{0}]".format(",".join(extras))], "installing midigen", False)]
    integ = os.path.join(HERE, "integrations", "python")
    if not args.no_service and os.path.isfile(os.path.join(integ, "pyproject.toml")):
        steps.append((pip + ["-e", integ + ("[test]" if args.dev else "")], "installing the web service", True))
    if args.audio:
        steps.append((pip + ["-e", ".[audio]"], "installing audio extras (numpy, scipy, soundfile)", True))
    if args.stream:
        steps.append((pip + ["-e", ".[stream]"], "installing python-rtmidi (live MIDI)", True))
    return steps


DEMO = ("import midigen as mg, mido, sys; spec = mg.build_spec('house', bars=8, seed=1); "
        "mg.export_midi(spec, sys.argv[1]); print('  ok:', len(mido.MidiFile(sys.argv[1]).tracks), 'tracks')")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install midigen into a private .venv folder and check that it works.")
    ap.add_argument("--venv", default=os.path.join(HERE, ".venv"), help="where to put the private Python (default .venv)")
    ap.add_argument("--audio", action="store_true", help="also install WAV rendering (numpy, scipy, soundfile)")
    ap.add_argument("--stream", action="store_true", help="also install live MIDI output (python-rtmidi)")
    ap.add_argument("--no-service", action="store_true", help="skip the web service (fastapi, uvicorn)")
    ap.add_argument("--dev", action="store_true", help="also install test tools (pytest, ...)")
    ap.add_argument("--demo", default=os.path.join(HERE, "demo.mid"), help="where to write the test song (default demo.mid)")
    ap.add_argument("--skip-demo", action="store_true", help="don't make the test song")
    ap.add_argument("--dry-run", action="store_true", help="only print what would be done")
    args = ap.parse_args(argv)
    args.venv = os.path.abspath(args.venv)

    check_python()
    say("midigen installer (Python {0}, {1})".format(sys.version.split()[0], sys.platform))
    say("\n1. Private Python environment")
    create_venv(args.venv, args.dry_run)
    say("\n2. Installing (needs internet; can take a minute)")
    for cmd, what, optional in plan(args):
        run(cmd, args.dry_run, what, optional)
    py = venv_python(args.venv)
    if not args.skip_demo:
        say("\n3. Making a test song")
        run([py, "-c", DEMO, args.demo], args.dry_run, "making the test song")
        if not args.dry_run:
            say("  wrote " + args.demo)
    if not args.dry_run:
        say("\n4. Health check (optional items only warn)")
        subprocess.call([venv_bin(args.venv, "midigen"), "doctor"], cwd=HERE)
    mg = venv_bin(args.venv, "midigen")
    act = (os.path.join(args.venv, "Scripts", "activate") if WINDOWS
           else ". " + os.path.join(args.venv, "bin", "activate"))
    say("\nDone{0}. Next:".format(" (dry run, nothing was changed)" if args.dry_run else ""))
    if not args.skip_demo:
        say("  open the test song in any music app or DAW:  " + args.demo)
    say('  open the app in your browser:   "{0}" ui'.format(mg))
    say('  make a song from the terminal:  "{0}" new "chill lofi beat in D minor" -o mysong'.format(mg))
    say("  (or first run  {0}  and then just type  midigen ...)".format(act))
    return 0


if __name__ == "__main__":
    sys.exit(main())
