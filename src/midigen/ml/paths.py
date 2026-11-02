"""Where datasets and trained artifacts live (overridable with env vars)."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _writable_default(sub: str) -> Path:
    if (REPO / "pyproject.toml").exists():  # source checkout
        return REPO / sub
    return Path.home() / ".cache" / "midigen" / sub


def data_dir() -> Path:
    """Dataset root: $MIDIGEN_DATASET_DIR, else <checkout>/data.

    (Not MIDIGEN_DATA_DIR: that names the experience store of ``midigen.learn``.)"""
    return Path(os.environ.get("MIDIGEN_DATASET_DIR") or _writable_default("data"))


def artifact_dir() -> Path:
    """Trained-model root: $MIDIGEN_ML_DIR, else <checkout>/artifacts/ml."""
    return Path(os.environ.get("MIDIGEN_ML_DIR") or _writable_default("artifacts/ml"))
