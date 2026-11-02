"""Write DATASET_CARD.md for a built synthetic dataset from its real manifest (no hand-typed numbers)."""
from __future__ import annotations

import json
from pathlib import Path

from .paths import data_dir

LYRICS_MIDI_NOTE = (
    "The knowledge packs (`midigen-songwriting`, `midigen-knowledge`, `midigen-genres`) were checked before use: "
    "they contain only JSON/Markdown own-words analysis written by the midigen songwriting coach agent (chord "
    "progressions as roman numerals, genre descriptions, song briefs). They contain no `.mid` files and no song "
    "lyrics. Reference songs are mentioned only by title/artist inside the analysis text, and those names are never "
    "copied into training prompts. Nothing was downloaded.")


def _table(d: dict) -> str:
    return "\n".join(f"| {k} | {v} |" for k, v in d.items())


def _an_ver(m: dict) -> str:
    info = (m.get("analyze_quality") or {}).get("analyzer")
    if not info:
        from .critic_bridge import analyzer_info
        info = dict(analyzer_info(), note="recorded at card time, not build time")
    return json.dumps(info)


def write_card(out=None) -> Path:
    d = Path(out) if out else data_dir() / "synthetic"
    m = json.loads((d / "manifest.json").read_text())
    p = m["params"]
    seeds = p["seeds"]
    srange = f"{seeds[0]}..{seeds[-1]}" if isinstance(seeds, list) and seeds else str(seeds)
    files = "\n".join(f"| `{k}` | {v / 1e6:.1f} MB |" for k, v in m.get("bytes", {}).items())
    gs = m.get("genre_sources", {})
    gsrc = "\n".join(f"| {g} | {', '.join(v)} |" for g, v in sorted(gs.items())) if isinstance(gs, dict) else str(gs)
    txt = f"""# midigen synthetic dataset — card

*Generated from `manifest.json` by `midigen dataset card`, built {m.get('built_at')} from midigen
{m.get('midigen_version')} (rev {m.get('git_rev')}) in {m.get('wall_seconds')} s wall time.*

## What it is
Songs produced by midigen itself: `build_spec(genre, seed)` for {len(p['genres'])} genres × seeds {srange}, plus
{p['variants'] - 1} validated edit-op variant(s) per (genre, seed) (`set_tempo`, `transpose`, `set_density`, `swing`,
`set_mode`, ...). Every song is rendered, scored by midigen's rule-based critic (0-100 + findings) and stored at song, section
and track level, together with compact chord/section tokens, a 16-step drum grid and (optional) full REMI note tokens.
User learning (`midigen.learn`) and the ML hooks are disabled during the build (`MIDIGEN_LEARN=0`, `MIDIGEN_NO_RECORD=1`,
`ml=False, learn=False`), so the data reflects the plain theory engine and nothing is written to your experience store.

**Important limitation.** Everything here is midigen's own output, labelled by midigen's own critic. A model trained
on it mostly learns to imitate the generator and its critic. It is no evidence of musical quality to human
listeners. Real user ratings (via `midigen rate` / the experience store) and properly licensed human MIDI
(`midigen dataset import`) are the only ways to learn something the generator doesn't already know.

## Counts
| item | count |
|---|---|
{_table(m['counts'])}

Songs by split: {m['songs_by_split']} · groups by split: {m['groups_by_split']} · errors: {m.get('n_errors')}

Critic score: {m['critic_score']}. The distribution is heavily skewed toward the top, because the generator is tuned to satisfy its own critic.

## Labels
* `critic_score`, `findings`, `label` (good ≥90 / ok ≥75 / poor): midigen's built-in rule-based critic (`midigen.critic`).
* `analyze`: metrics from the independent critic's analyzer (`tools/analyze.py`, the tool behind
  `midigen-critic/REVIEW.md`): in-key %, chord-tone %, strong clashes, kick/bass lock, bass root on chord change,
  melody strong-beat chord tones, timing deviation, plus `analyze_quality`, a 0-100 composite of the harmony, bass and
  melody checks. Stats: {m.get('analyze_quality')}.
  Analyzer version: {_an_ver(m)}. Labels use only the analyzer keys that are present, so a changed analyzer can add or drop
  label columns; compare datasets only when the analyzer sha256 matches. Only genres the analyzer maps get these labels; for the others `analyze` is `{{}}`.
* User ratings are not part of this file. `midigen train reward` reads them from the experience store
  (`midigen.learn`, `songs.user_reward`) and `$MIDIGEN_FEEDBACK` JSONL, and switches to them once there are enough.

## Splits (no leakage)
{m['split_rule']}. A *group* is one (genre, seed); all its variants (and sections, tracks, drum rows) share the
group's split, so no edited copy of a test song is ever in train. Held-out evaluation (`midigen eval`) uses seeds from
10001 upward, which no dataset build uses by default.

## Files
| file | size |
|---|---|
{files}

* `songs.jsonl`: id, group, split, genre, seed, variant, build args, ops, key/mode/tempo/bars, critic score + findings,
  reward features, compact song tokens (`<G:..> <M:..> <T:..> <TRK:..> <S:name:bars:energy> chords ...`), spec.
* `sections.jsonl`: per section: chords (roman), wanted vs detected cadence, findings in that section.
* `tracks.jsonl`: per track: role, program, generator, density, note stats, critic findings for the track.
* `drums.jsonl`: per song, the 16-step drum grid per bar (`.`/K/S/H/O/C/T...); its vocabulary is in `tokens.py`.
* `remi.jsonl`: full REMI note tokens (BAR/POS/TRK/P/DUR/VEL), which round-trip to notes (`tokens.decode_remi`).

## Sources and licenses
* Music: generated by midigen (this repository) from its builtin genre profiles and the genre packs below.
  {m.get('license')}
* Genre packs used (as genre *profiles*, not as music):

| genre | profile sources |
|---|---|
{gsrc}

* Knowledge used by `train progression` / `train export-finetune` (provenance kept per record):
  `midigen-songwriting/`, `midigen-knowledge/`, `midigen-genres/`, author = "midigen songwriting coach agent",
  kind = "own-words analysis". {LYRICS_MIDI_NOTE}
* Licensed third-party MIDI: none. The importer (`data/licensed/`) ships an empty manifest and rejects files whose
  license is unknown or not on the allowlist.

## Intended use
Training/evaluating midigen's small CPU models (reward reranker, chord/drum n-grams) and exporting LLM fine-tune
pairs for the spec/ops JSON interface. Not intended as a general music dataset.
"""
    p_ = d / "DATASET_CARD.md"
    p_.write_text(txt)
    return p_
