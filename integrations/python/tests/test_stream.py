import mido
import pytest

from midigen_integrations import api, stream


class FakePort:
    def __init__(self):
        self.sent = []

    def send(self, m):
        self.sent.append(m)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, d):
        self.t += d


@pytest.fixture(scope="module")
def song():
    return api.generate(genre="rock", bars=2, seed=1)


def test_events_are_ordered_and_timed(song):
    ev = list(stream.events(song.midi))
    times = [t for t, _ in ev]
    assert times == sorted(times) and times[0] == 0
    secs = song.summary["duration_seconds"]
    assert abs(times[-1] - secs) < 0.6  # last note-off near the end of the song
    assert all(not m.is_meta for _, m in ev)


def test_play_sends_everything_then_notes_off(song):
    port, clk = FakePort(), FakeClock()
    n = stream.play(song.spec, port, clock=clk, sleep=clk.sleep)
    ev = list(stream.events(song.midi))
    assert n == len(ev)
    assert [m for m in port.sent[:n]] == [m for _, m in ev]
    tail = port.sent[n:]
    assert len(tail) == 32 and all(m.type == "control_change" and m.control in (120, 123) for m in tail)
    assert abs(clk.t - ev[-1][0]) < 0.06  # waited (virtually) for the real duration


def test_speed_and_stop(song):
    port, clk = FakePort(), FakeClock()
    stream.play(song.midi, port, speed=4, clock=clk, sleep=clk.sleep)
    full = list(stream.events(song.midi))[-1][0]
    assert abs(clk.t - full / 4) < 0.06
    port, clk = FakePort(), FakeClock()
    n = stream.play(song.midi, port, clock=clk, sleep=clk.sleep, stop=lambda: clk.t > 1.0)
    assert 0 < n < len(full_list(song)) and port.sent[-1].control == 120
    with pytest.raises(ValueError):
        stream.play(song.midi, FakePort(), speed=0)


def full_list(song):
    return list(stream.events(song.midi))


def test_cli_dry_run(capsys):
    assert stream.main(["--genre", "rock", "--bars", "1", "--dry-run", "--speed", "50"]) == 0
    out = capsys.readouterr()
    assert "note_on" in out.out and "done" in out.err


def test_open_output_failure_is_readable(monkeypatch):
    def boom(*a, **k):
        raise SystemError("no ALSA sequencer")
    monkeypatch.setattr(mido.Backend, "open_output", boom)
    with pytest.raises(RuntimeError, match="could not open MIDI output"):
        stream.open_output("x", virtual=True)
