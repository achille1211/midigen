"""`midigen doctor`, `midigen ui --dry-run` and install.py (help, dry run, venv creation, plain-English errors)."""
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from midigen import cli, doctor, ui

ROOT = Path(__file__).resolve().parents[1]


def _install():
    spec = importlib.util.spec_from_file_location("midigen_install", ROOT / "install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_doctor_ok_and_lists_optional_pieces():
    out = io.StringIO()
    assert doctor.run(out) == 0
    text = out.getvalue()
    assert "MIDI export works" in text and "genres" in text
    for piece in ("FluidSynth", "soundfont", "python-rtmidi", "web service"):
        assert piece in text


def test_doctor_core_failure_is_nonzero(monkeypatch):
    monkeypatch.setattr(doctor, "CORE", [lambda: (doctor.FAIL, "broken", "do X")] + doctor.CORE[1:])
    out = io.StringIO()
    assert doctor.run(out) == 1
    assert "fix: do X" in out.getvalue()


def test_doctor_optional_missing_only_warns(monkeypatch):
    monkeypatch.setattr(doctor, "_has", lambda m: False)
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    out = io.StringIO()
    assert doctor.run(out) == 0
    assert "[warn] FluidSynth not found" in out.getvalue()


def test_doctor_cli_exit_code():
    with pytest.raises(SystemExit) as e:
        cli.main(["doctor"])
    assert e.value.code == 0


def test_ui_dry_run(capsys, monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("")
    monkeypatch.setenv("MIDIGEN_UI_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as e:
        cli.main(["ui", "--dry-run", "--port", "8799"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert str(tmp_path / "app.py") in out and "http://localhost:8799/" in out


def test_ui_nothing_available(monkeypatch, capsys):
    monkeypatch.setattr(ui, "find_ui_dir", lambda: None)
    monkeypatch.setattr(ui.importlib.util, "find_spec", lambda m: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["ui", "--dry-run"])
    assert e.value.code == 1 and "No web UI available" in capsys.readouterr().err


def test_install_help():
    r = subprocess.run([sys.executable, str(ROOT / "install.py"), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--audio" in r.stdout and "--dry-run" in r.stdout


def test_install_dry_run_changes_nothing(tmp_path):
    venv = tmp_path / "v"
    r = subprocess.run([sys.executable, str(ROOT / "install.py"), "--dry-run", "--venv", str(venv), "--audio"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "pip install" in r.stdout and ".[ui,service]" in r.stdout and ".[audio]" in r.stdout
    assert "dry run" in r.stdout and not venv.exists()


def test_install_old_python_message(capsys):
    mod = _install()
    with pytest.raises(SystemExit):
        mod.check_python((3, 8, 1))
    err = capsys.readouterr().err
    assert "needs Python 3.10" in err and "python.org" in err


def test_install_creates_venv(tmp_path):
    mod = _install()
    venv = str(tmp_path / "v")
    mod.create_venv(venv)
    assert os.path.exists(mod.venv_python(venv))
    r = subprocess.run([mod.venv_python(venv), "-m", "pip", "--version"], capture_output=True, text=True)
    assert r.returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell wrapper")
def test_install_sh_wrapper_dry_run(tmp_path):
    r = subprocess.run(["sh", str(ROOT / "install.sh"), "--dry-run", "--venv", str(tmp_path / "v")],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "midigen installer" in r.stdout
