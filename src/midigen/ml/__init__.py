"""midigen.ml: datasets, tokenizers and small trainable models for midigen.

Everything here is optional and opt-in. The core generator never imports this package unless a caller asks for
it (``build_spec(..., ml=True)``, ``MIDIGEN_ML=1`` or ``midigen new --ml``), and every hook falls back to the plain
theory engine when no trained artifact is present.

Modules
-------
tokens    REMI-like multitrack event tokens, chord/section tokens, drum-grid tokens
features  numeric features of a spec + its render (critic findings optional)
dataset   synthetic dataset builder (all genres x seeds x variants), split, dataset card
licensed  importer for openly licensed / public-domain MIDI folders (rejects unknown licenses)
feedback  loader for user ratings (adapter over the experience/feedback store)
reward    quality/reward model (gradient boosting or ridge) + best-of-N selection
seqmodel  Witten-Bell n-gram models for chord progressions and drum grids, with sampling hooks
finetune  chat-JSONL exporter (prompt -> spec, instruction -> ops) for OpenAI-compatible fine-tuning
hooks     opt-in integration into build_spec with graceful fallback
evaluate  held-out evaluation against the plain generator
"""
from .paths import artifact_dir, data_dir  # noqa: F401
