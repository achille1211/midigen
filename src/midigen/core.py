"""Shared data types for rendering."""
from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field

from .theory import Chord


@dataclass
class Note:
    start: float   # beats (quarter notes)
    dur: float     # beats
    pitch: int
    vel: int

    def as_list(self):
        return [round(self.start, 4), round(self.dur, 4), self.pitch, self.vel]


@dataclass
class ChordSpan:
    start: float
    dur: float
    chord: Chord

    @property
    def end(self):
        return self.start + self.dur


@dataclass
class Ctx:
    """Everything a generator needs to render one track for one section instance."""
    spec: dict
    track: dict
    section: dict
    params: dict
    bars: int
    bar_beats: float
    chords: list            # list[ChordSpan], relative to section start
    key: str
    mode: str
    scale: list             # pitch classes of the key
    energy: float
    prev_energy: float | None
    next_energy: float | None
    next_section: str | None
    occurrence: int         # how many times this section has been played before
    rng: random.Random
    kick_onsets: list = field(default_factory=list)   # beats relative to section start
    genre: dict | None = None

    @property
    def length(self) -> float:
        return self.bars * self.bar_beats

    @property
    def steps_per_bar(self) -> int:
        return max(1, int(round(self.bar_beats * 4)))

    @property
    def density(self) -> float:
        d = float(self.params.get("density", 0.5)) + (self.energy - 0.5) * 0.8
        return max(0.0, min(1.0, d))

    def chord_at(self, beat: float) -> ChordSpan:
        for c in self.chords:
            if c.start <= beat + 1e-6 < c.end:
                return c
        return self.chords[-1]

    def velocity(self, base: int | None = None, accent: float = 0.0) -> int:
        b = base or self.params.get("velocity", 92)
        v = b * (0.72 + 0.4 * self.energy) + accent
        return int(max(1, min(127, v)))


def stable_seed(*parts) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode())


def clamp(v, lo, hi):
    return max(lo, min(hi, v))
