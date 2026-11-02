"""Audio side of midigen: rendering (FluidSynth or numpy fallback), per-genre mixing defaults, playback
humanization and an automated, measurement-only listening check that feeds the critic.

Light imports only (mix/humanize need just mido); the numpy-based parts load on first use."""
from .mix import genre_mix, humanize_defaults, mix_table, resolve_track_mix  # noqa: F401
from .humanize import humanize_midi  # noqa: F401
from .render import (AudioBackendError, RenderResult, available_backends, export_mp3, render_audio,  # noqa: F401
                     render_stems, to_midifile)

_LAZY = {"analyze_mix": "analysis", "analyze_array": "analysis", "critic_findings": "analysis",
         "summary_text": "analysis", "ffmpeg_loudness": "analysis", "listening_check": "check", "synth_midi": "synth"}


def __getattr__(name):
    if name in _LAZY:
        import importlib
        return getattr(importlib.import_module(f".{_LAZY[name]}", __name__), name)
    raise AttributeError(name)
