"""midigen: theory-driven multitrack MIDI generation and arrangement with a conversational layer.

Typical embedding::

    import midigen as mg
    spec = mg.build_spec("house", key="A", mode="minor", bars=16, seed=7)
    spec = mg.apply_ops(spec, [{"op": "reharmonize", "technique": "9ths"}])
    report = mg.critique(spec)
    mg.export_midi(spec, "song.mid")
    sid = mg.learn.record(spec)            # remember it (also done by the CLI, MCP server and Composer)
    mg.rate(sid, 5, note="busier chorus")  # teach the genre policy; the next build_spec samples from it
"""
__version__ = "0.1.0"

from .spec import SpecError, check, validate, normalize, load, save, dumps, make_track, total_bars, INSTRUMENTS  # noqa: F401
from .schema import SPEC_SCHEMA, OP_SCHEMAS, OPS_SCHEMA, DRUM_PATTERNS, STYLES  # noqa: F401
from .theory import parse_chord, scale_pcs, transpose_symbol  # noqa: F401
from . import harmony  # noqa: F401
from .genres import build_spec, list_genres, available_genres, resolve_genre, get_genre, detect_genre, profile_summary  # noqa: F401
from . import packs  # noqa: F401
from .arranger import render, Performance  # noqa: F401
from .critic import critique, format_report  # noqa: F401
from .ops import apply_ops, validate_ops, Editor, OpError  # noqa: F401
from .midi_io import export_midi, import_midi, inspect_midi  # noqa: F401
from .nl import prompt_to_spec, edit_to_ops, NotUnderstood  # noqa: F401
from .llm import Composer, OpenAICompatProvider, refine, Result  # noqa: F401
from . import learn  # noqa: F401
from .learn import rate, feedback, keep, reject, thumbs, learn_midi, train  # noqa: F401
from .learn import status as learn_status  # noqa: F401


def piano_roll(spec_or_perf, path, title=None):
    """Write a piano-roll PNG (requires matplotlib)."""
    from .preview import piano_roll as _pr
    return _pr(spec_or_perf, path, title)
