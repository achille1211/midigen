"""midigen integrations: a stable, typed facade over midigen plus a REST service and MIDI streaming.

Everything here is additive: it only calls midigen's public functions and never changes midigen itself.

    from midigen_integrations import api
    song = api.generate(api.GenerateParams(genre="house", key="A", mode="minor", bars=16, seed=7))
    open("song.mid", "wb").write(song.midi)
"""
from .api import (  # noqa: F401
    API_VERSION, GenerateParams, Song, EditResult, MidigenError, generate, edit, critique, to_midi_bytes,
    render_notes, summarize, list_genres, genre_profile, list_options, validate_spec,
)

__version__ = "0.1.0"
