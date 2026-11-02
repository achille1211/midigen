"""Stream a midigen song in real time to a MIDI output port (e.g. a virtual port a DAW or synth listens to).

Prototype. Port I/O goes through mido's python-rtmidi backend (``pip install "midigen-integrations[stream]"``), which
supports ALSA and JACK on Linux, CoreMIDI on macOS (virtual ports supported) and WinMM on Windows (no virtual ports
there: use loopMIDI and pass ``--port``).

    midigen-stream --genre house --bars 8 --virtual midigen     # create port "midigen" and play into it
    midigen-stream --list                                       # list output ports
    midigen-stream --genre jazz --port "loopMIDI Port 1"        # play into an existing port
    midigen-stream --file song.mid --dry-run                    # print events, no MIDI system needed

The scheduler is independent of the port: any object with ``send(mido.Message)`` works (see :func:`play`).
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Callable, Iterator, Optional, Union

import mido

from . import api


def midi_file(source: Union[bytes, dict, mido.MidiFile]) -> mido.MidiFile:
    """Accept MIDI bytes, a midigen spec dict or a MidiFile and return a MidiFile."""
    if isinstance(source, mido.MidiFile):
        return source
    if isinstance(source, dict):
        source = api.to_midi_bytes(source)
    import io
    return mido.MidiFile(file=io.BytesIO(source))


def events(source: Union[bytes, dict, mido.MidiFile]) -> Iterator[tuple[float, mido.Message]]:
    """Yield ``(absolute_seconds, message)`` for every channel message, honouring tempo changes (meta events dropped)."""
    t = 0.0
    for msg in midi_file(source):  # mido merges tracks and converts delta ticks to seconds using set_tempo
        t += msg.time
        if not msg.is_meta:
            yield t, msg.copy(time=0)


def all_notes_off(port) -> None:
    """Send All Notes Off + All Sound Off on every channel (used on stop/interrupt)."""
    for ch in range(16):
        port.send(mido.Message("control_change", channel=ch, control=123, value=0))
        port.send(mido.Message("control_change", channel=ch, control=120, value=0))


def play(source, port, speed: float = 1.0, clock: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep, stop: Optional[Callable[[], bool]] = None) -> int:
    """Send ``source`` to ``port`` in real time. Returns the number of messages sent (excluding the final notes-off).

    ``speed`` 2.0 plays twice as fast. ``clock``/``sleep`` are injectable for tests; ``stop()`` returning true aborts.
    Always finishes with :func:`all_notes_off` so no notes hang, even on KeyboardInterrupt.
    """
    if speed <= 0:
        raise ValueError("speed must be > 0")
    t0, sent = clock(), 0
    try:
        for at, msg in events(source):
            due = t0 + at / speed
            while True:
                if stop and stop():
                    return sent
                wait = due - clock()
                if wait <= 0:
                    break
                sleep(min(wait, 0.05))
            port.send(msg)
            sent += 1
    finally:
        all_notes_off(port)
    return sent


class PrintPort:
    """A stand-in port that prints messages (``--dry-run``); no MIDI system required."""

    def __init__(self, out=None, quiet: bool = False):
        self.out, self.quiet, self.count, self.t0 = out, quiet, 0, time.monotonic()

    def send(self, msg: mido.Message) -> None:
        self.count += 1
        if not self.quiet:
            print(f"{time.monotonic() - self.t0:8.3f}s  {msg}", file=self.out or sys.stdout)

    def close(self) -> None:
        pass


def open_output(name: Optional[str] = None, virtual: bool = False):
    """Open a real MIDI output through mido's rtmidi backend. Raises RuntimeError with a readable reason."""
    try:
        backend = mido.Backend("mido.backends.rtmidi")
        return backend.open_output(name, virtual=virtual)
    except ImportError as e:
        raise RuntimeError('python-rtmidi is not installed: pip install "midigen-integrations[stream]"') from e
    except Exception as e:  # rtmidi raises SystemError/RuntimeError when no MIDI system is available
        raise RuntimeError(f"could not open MIDI output {name!r} (virtual={virtual}): {e}") from e


def list_outputs() -> list[str]:
    try:
        return list(mido.Backend("mido.backends.rtmidi").get_output_names())
    except Exception as e:
        raise RuntimeError(f"could not list MIDI outputs: {e}") from e


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="stream a midigen song to a MIDI port in real time")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--genre")
    src.add_argument("--prompt")
    src.add_argument("--file", help="play an existing .mid")
    ap.add_argument("--key"); ap.add_argument("--mode")
    ap.add_argument("--tempo", type=float); ap.add_argument("--bars", type=int); ap.add_argument("--seed", type=int, default=1)
    dst = ap.add_mutually_exclusive_group()
    dst.add_argument("--virtual", metavar="NAME", help="create a virtual output port (ALSA/JACK/CoreMIDI)")
    dst.add_argument("--port", metavar="NAME", help="open an existing output port")
    dst.add_argument("--dry-run", action="store_true", help="print events instead of sending")
    ap.add_argument("--list", action="store_true", help="list output ports and exit")
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args(argv)
    try:
        if a.list:
            print("\n".join(list_outputs()) or "(no output ports)")
            return 0
        if a.file:
            source = open(a.file, "rb").read()
        else:
            source = api.generate(genre=a.genre or (None if a.prompt else "pop"), prompt=a.prompt, key=a.key,
                                  mode=a.mode, tempo=a.tempo, bars=a.bars, seed=a.seed).midi
        if a.dry_run:
            port = PrintPort()
        else:
            port = open_output(a.virtual or a.port, virtual=bool(a.virtual))
            print(f"streaming to {getattr(port, 'name', a.virtual or a.port)!r} (Ctrl-C to stop)", file=sys.stderr)
        try:
            n = play(source, port, speed=a.speed)
        except KeyboardInterrupt:
            n = -1
        finally:
            port.close()
        print(f"done ({n} messages)" if n >= 0 else "stopped", file=sys.stderr)
        return 0
    except (RuntimeError, api.MidigenError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
