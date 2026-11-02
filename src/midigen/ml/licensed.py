"""Optional importer for openly licensed / public-domain MIDI folders.

Nothing is downloaded. You point the importer at a local folder you obtained yourself, and say what license it is
under and where it came from — either for the whole folder (``--license``/``--source``) or per file in a sidecar
``LICENSES.json``/``licenses.csv`` (``file, license, source, author``). Each accepted file is recorded in
``<data>/licensed/manifest.jsonl`` with its license, source URL, author/attribution, sha256 and import time, and is
tokenised (REMI + drum grid) into ``<data>/licensed/remi.jsonl``.

Files whose license is missing, unknown or not on the allowlist are **rejected** (listed in the report, not copied,
not tokenised). The allowlist is deliberately conservative: public-domain dedications and CC licenses that permit
reuse in a training set with attribution. Non-commercial and no-derivatives licenses are rejected.
"""
from __future__ import annotations

import csv
import hashlib
import json
import time
import zlib
from pathlib import Path

from .paths import data_dir

ALLOWED = {
    "cc0-1.0": "CC0 1.0 Universal (public-domain dedication)",
    "pddl-1.0": "Open Data Commons Public Domain Dedication and License",
    "public-domain": "Public domain (must give a source that states it)",
    "cc-by-3.0": "Creative Commons Attribution 3.0", "cc-by-4.0": "Creative Commons Attribution 4.0",
    "cc-by-sa-3.0": "Creative Commons Attribution-ShareAlike 3.0",
    "cc-by-sa-4.0": "Creative Commons Attribution-ShareAlike 4.0",
    "mit": "MIT License", "odc-by-1.0": "Open Data Commons Attribution License",
}
ALIASES = {"cc0": "cc0-1.0", "pd": "public-domain", "publicdomain": "public-domain", "public domain": "public-domain",
           "cc-by": "cc-by-4.0", "cc by 4.0": "cc-by-4.0", "cc-by-sa": "cc-by-sa-4.0", "cc by-sa 4.0": "cc-by-sa-4.0"}
NEEDS_ATTRIBUTION = {k for k in ALLOWED if k.startswith(("cc-by", "mit", "odc-by"))}


def normalize_license(lic) -> str | None:
    if not lic:
        return None
    s = str(lic).strip().lower().replace("_", "-")
    s = ALIASES.get(s, s)
    return s if s in ALLOWED else None


def _sidecar(folder: Path) -> dict:
    out = {}
    j = folder / "LICENSES.json"
    if j.exists():
        d = json.loads(j.read_text())
        items = d.items() if isinstance(d, dict) else ((x.get("file"), x) for x in d)
        for k, v in items:
            out[str(k)] = v if isinstance(v, dict) else {"license": v}
    c = folder / "licenses.csv"
    if c.exists():
        with open(c, newline="") as fh:
            for row in csv.DictReader(fh):
                out[row.get("file", "")] = row
    return out


def import_folder(folder, license: str | None = None, source: str | None = None, author: str | None = None,
                  out: str | Path | None = None, genre: str | None = None) -> dict:
    """Import ``*.mid``/``*.midi`` under ``folder``. Returns a report with accepted/rejected lists."""
    from .tokens import drum_grid_tokens, encode_score, score_from_midi
    folder = Path(folder)
    out = Path(out) if out else data_dir() / "licensed"
    out.mkdir(parents=True, exist_ok=True)
    side = _sidecar(folder)
    man, remi = open(out / "manifest.jsonl", "a"), open(out / "remi.jsonl", "a")
    seen = {json.loads(line)["sha256"] for line in open(out / "manifest.jsonl") if line.strip()} \
        if (out / "manifest.jsonl").stat().st_size else set()
    acc, rej = [], []
    try:
        for f in sorted(list(folder.rglob("*.mid")) + list(folder.rglob("*.midi"))):
            rel = str(f.relative_to(folder))
            meta = side.get(rel) or side.get(f.name) or {}
            lic_raw = meta.get("license") or license
            lic = normalize_license(lic_raw)
            src = meta.get("source") or source
            who = meta.get("author") or meta.get("attribution") or author
            if lic is None:
                rej.append({"file": rel, "reason": f"license {lic_raw!r} missing/unknown/not allowed"})
                continue
            if not src:
                rej.append({"file": rel, "reason": "no source/provenance given"})
                continue
            if lic in NEEDS_ATTRIBUTION and not who:
                rej.append({"file": rel, "reason": f"{lic} requires attribution; no author given"})
                continue
            data = f.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha in seen:
                rej.append({"file": rel, "reason": "duplicate (already imported)"})
                continue
            try:
                score = score_from_midi(f)
                toks = encode_score(score)
            except Exception as e:
                rej.append({"file": rel, "reason": f"unreadable MIDI: {type(e).__name__}: {e}"})
                continue
            seen.add(sha)
            split_h = zlib.crc32(sha.encode()) % 100
            split = "train" if split_h < 80 else "val" if split_h < 90 else "test"
            rec = {"file": rel, "folder": str(folder.resolve()), "sha256": sha, "bytes": len(data), "license": lic,
                   "license_name": ALLOWED[lic], "source": src, "author": who, "genre": meta.get("genre") or genre,
                   "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "split": split, "n_tokens": len(toks)}
            man.write(json.dumps(rec) + "\n")
            grid = drum_grid_tokens(score)
            remi.write(json.dumps({"sha256": sha, "split": split, "genre": rec["genre"], "license": lic,
                                   "tokens": " ".join(toks), "drum_grid": grid}) + "\n")
            acc.append(rec)
    finally:
        man.close()
        remi.close()
    return {"accepted": acc, "rejected": rej, "manifest": str(out / "manifest.jsonl")}
