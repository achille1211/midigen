"""Per-genre mixing defaults (CC7 volume, CC10 pan, CC91 reverb, CC93 chorus) for MIDI export.

Pure Python (no numpy) because `midigen.midi_io` uses it on every export. The table lives in
`midigen/data/mix_defaults.json` so it can be tuned without touching code."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

ROLES = ("drums", "bass", "chords", "pad", "arp", "lead", "other")
CC_VOLUME, CC_PAN, CC_REVERB, CC_CHORUS = 7, 10, 91, 93
_DATA = Path(__file__).resolve().parent.parent / "data" / "mix_defaults.json"
# roles that stay centred even when a genre has several of them (low end and the main kit stay mono)
_CENTRED = {"drums", "bass"}


@lru_cache(maxsize=1)
def _table() -> dict:
    return json.loads(_DATA.read_text())


def mix_table() -> dict:
    """A deep copy of the whole table (default + genres)."""
    return copy.deepcopy(_table())


def genre_key(genre: str | None) -> str | None:
    """'drum-and-bass', 'Drum and Bass', 'jazz@midigen-knowledge' -> table key ('drum_and_bass', 'jazz')."""
    if not genre:
        return None
    g = str(genre).split("@", 1)[0].strip().lower().replace("-", "_").replace(" ", "_")
    g = {"lofi": "lofi_hiphop", "lofi_hip_hop": "lofi_hiphop", "dnb": "drum_and_bass", "bossa": "bossa_nova",
         "neosoul": "neo_soul"}.get(g, g)
    return g


def genre_mix(genre: str | None) -> dict:
    """Resolved per-role defaults for a genre: {'drums': {...}, ..., 'humanize': {...}}. Unknown genre -> default."""
    t = _table()
    out = copy.deepcopy(t["default"])
    over = t["genres"].get(genre_key(genre) or "", {})
    for role, vals in over.items():
        out.setdefault(role, {}).update(vals)
    return out


def _clamp(v) -> int:
    return int(max(0, min(127, round(v))))


def resolve_track_mix(genre: str | None, tracks: list[dict]) -> list[dict]:
    """Mix settings for each track.

    `tracks` items need 'role' and may carry 'volume'/'pan' (spec values). Returns, per track,
    {'volume', 'pan', 'reverb', 'chorus'} as CC values."""
    from ..spec import DEFAULT_VOLUME
    gm = genre_mix(genre)
    seen: dict[str, int] = {}
    out = []
    for t in tracks:
        role = t.get("role") if t.get("role") in ROLES else "other"
        base = gm.get(role, gm["other"])
        k = seen.get(role, 0)
        seen[role] = k + 1
        # volume: table value scaled by the spec's deviation from its role default (keeps 'louder'/'quieter' edits)
        spec_vol = t.get("volume")
        ref = DEFAULT_VOLUME.get(role, 90)
        vol = base["volume"] * (spec_vol / ref if spec_vol is not None and ref else 1.0)
        if k:  # extra tracks of the same role sit a little lower so the primary part leads
            vol *= 0.92
        # pan: an explicit non-centre spec pan wins; otherwise the table, spread for duplicates
        spec_pan = t.get("pan", 64)
        if spec_pan is not None and int(spec_pan) != 64:
            pan = spec_pan
        else:
            pan = base["pan"]
            if k and role not in _CENTRED:
                off = pan - 64 if pan != 64 else 16
                sign = -1 if k % 2 else 1
                pan = 64 + sign * off * (1 + 0.5 * (k // 2))
        out.append({"volume": _clamp(vol), "pan": _clamp(pan), "reverb": _clamp(base["reverb"]),
                    "chorus": _clamp(base["chorus"])})
    return out


def humanize_defaults(genre: str | None) -> dict:
    return dict(genre_mix(genre).get("humanize", {"timing_ms": 4, "velocity": 5}))
