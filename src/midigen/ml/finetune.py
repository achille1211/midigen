"""Export chat-format JSONL for OpenAI-compatible fine-tuning of midigen's LLM composer.

Two tasks, matching the runtime contract in :mod:`midigen.llm`:

* **generate**: user prompt -> ``{"action":"spec","spec":{...validated spec...},"explanation":...}``
* **edit**: current spec + instruction -> ``{"action":"ops","ops":[...],"explanation":...}``

Where the labels come from (be honest about it):

* Prompts are built from templates and from the *own-words* knowledge packs (``midigen-songwriting`` genre
  descriptions and song-analysis briefs, ``midigen-genres``/``midigen-knowledge`` genre profiles). Every record carries
  ``provenance`` (source paths, author, kind). Song analyses contribute only style attributes (subgenre, key, tempo,
  theme); song titles/artists are never put in prompts, and no lyrics or MIDI are used (none exist in those packs).
* Assistant answers are produced by midigen's **offline** path: ``nl.prompt_to_spec`` + critic refine for specs,
  ``nl.edit_to_ops`` for ops, plus ``set_chords`` ops whose progressions come from ``midigen-songwriting``. All are
  validated (``spec.check`` / ``ops.apply_ops``) before export. A model fine-tuned on this learns the offline
  parser's behaviour and the validated JSON format; it does not learn taste beyond that.
* Validated prompt/response pairs recorded by ``midigen.learn`` (``llm_pairs`` table) are appended when present.

Formats: ``compact`` (default; short system prompt, the spec only as context) or ``runtime`` (exactly the messages
``Composer`` sends, including vocabulary and op catalogue; much larger). Split is by prompt group (no leakage).
"""
from __future__ import annotations

import json
import random
import zlib
from pathlib import Path

from .paths import data_dir

SYSTEM = ("You are midigen's composer. Reply ONLY with JSON: {\"action\":\"spec\",\"spec\":{...}} for a new song or "
          "{\"action\":\"ops\",\"ops\":[...]} to edit the current spec. Use roman-numeral chords; never write notes.")
TEMPLATES = ["{mood} {genre} track in {key} {mode} at {tempo} bpm", "a {mood} {genre} song", "{genre} beat, {tempo} bpm",
             "write a {genre} tune in {key} {mode}", "make me something {mood}, {genre} style, around {tempo} bpm",
             "{bars} bars of {mood} {genre}", "{genre} instrumental with {inst}, {mood}"]
MOODS = ["dark", "happy", "chill", "sad", "energetic", "dreamy", "moody", "uplifting", "melancholic", "bright"]
INSTS = ["strings", "an arp", "a rhodes", "a pad", "piano"]
EDITS = ["faster", "slower by 10", "transpose up 2", "make it dorian", "sadder", "brighter", "jazzier chords",
         "add 9ths", "secondary dominants", "simpler chords", "reharmonize the chorus", "deceptive cadence in the chorus",
         "busier drums", "sparser lead", "humanize", "more swing", "straight", "extend the chorus by 4 bars",
         "shorten the intro", "repeat the chorus", "add strings", "add an arp", "remove the pad", "mute the lead",
         "louder bass", "new melody", "new idea for the bass", "call it Night Drive", "tempo 100", "drums to halftime"]
TEMPLATE_PROV = {"source": "midigen/ml/finetune.py templates", "author": "midigen ml exporter", "kind": "template"}


def _split(group: str) -> str:
    h = zlib.crc32(("ft|" + group).encode()) % 100
    return "train" if h < 80 else "val" if h < 90 else "test"


def _compact_msgs(user: str, answer: dict, spec: dict | None = None) -> list[dict]:
    from ..spec import dumps
    u = user if spec is None else "Current spec:\n" + dumps(spec) + "\n\nInstruction: " + user
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": u},
            {"role": "assistant", "content": json.dumps(answer, separators=(",", ":"))}]


def _runtime_msgs(user: str, answer: dict, spec: dict, task_kind: str) -> list[dict]:
    from ..llm import Composer
    task = (f"User request: {user!r}\nReturn JSON." if task_kind == "generate"
            else f"User instruction: {user!r}\nReturn {{\"action\":\"ops\",\"ops\":[...],\"explanation\":\"...\"}}.")
    msgs = Composer(provider=None, use_env=False)._messages(spec, task, spec.get("genre"))
    return msgs + [{"role": "assistant", "content": json.dumps(answer, separators=(",", ":"))}]


def _prompts(n_per_genre: int, rng: random.Random) -> list[tuple[str, str, dict]]:
    """(prompt, group, provenance) — templates plus knowledge-derived briefs."""
    from ..genres import available_genres
    from ..theory import MODES
    from .knowledge import genre_descriptions, song_briefs
    keys = ["C", "D", "E", "F", "G", "A", "Bb", "Eb"]
    out = []
    for g in available_genres():
        name = g.replace("_", " ")
        for i in range(n_per_genre):
            t = TEMPLATES[i % len(TEMPLATES)]
            p = t.format(mood=rng.choice(MOODS), genre=name, key=rng.choice(keys), mode=rng.choice(["major", "minor"]),
                         tempo=rng.randrange(70, 175, 5), bars=rng.choice([16, 32]), inst=rng.choice(INSTS))
            out.append((p, f"tpl|{g}|{i}", TEMPLATE_PROV))
    for d in genre_descriptions():
        desc = d["description"].strip().split(". ")[0][:160].rstrip(".")
        if desc:
            out.append((f"{d['genre'].replace('_', ' ')}: {desc.lower()}", "desc|" + d["provenance"]["source"],
                        d["provenance"]))
    for b in song_briefs():
        parts = [b.get("subgenre") or b["genre"].replace("_", " ")]
        if b.get("tonic") and b.get("mode") in MODES:
            parts.append(f"in {b['tonic']} {b['mode']}")
        if b.get("bpm"):
            parts.append(f"at {int(b['bpm'])} bpm")
        p = "a " + " ".join(parts) + (f", about {b['theme']}" if b.get("theme") else "")
        out.append((p[:240], "brief|" + b["provenance"]["source"], b["provenance"]))
    return out


def export(out: str | Path | None = None, n_per_genre: int = 12, edits_per_spec: int = 3, fmt: str = "compact",
           seed: int = 0, include_store: bool = True, log=print) -> dict:
    from .. import nl
    from ..llm import refine
    from ..ops import OpError, apply_ops
    from ..spec import check
    from .knowledge import progressions
    rng = random.Random(seed)
    out = Path(out) if out else data_dir() / "finetune"
    out.mkdir(parents=True, exist_ok=True)
    files = {sp: open(out / f"{sp}.jsonl", "w") for sp in ("train", "val", "test")}
    counts = {"generate": 0, "edit": 0, "edit_knowledge_chords": 0, "store_pairs": 0, "skipped": 0}
    by_split = {"train": 0, "val": 0, "test": 0}
    kprogs: dict = {}
    for r in progressions():
        kprogs.setdefault((r["genre"], r["mode"]), []).append(r)

    def write(group, msgs, task, prov, extra=None):
        sp = _split(group)
        rec = {"messages": msgs, "metadata": {"task": task, "group": group, "split": sp, "provenance": prov,
                                              "label_source": (extra or {}).get("label_source", "midigen offline parser")}}
        files[sp].write(json.dumps(rec, separators=(",", ":")) + "\n")
        by_split[sp] += 1

    def msgs_for(user, ans, spec, kind):
        if fmt == "runtime":
            return _runtime_msgs(user, ans, spec, kind)
        return _compact_msgs(user, ans, None if kind == "generate" else spec)

    try:
        for i, (prompt, group, prov) in enumerate(_prompts(n_per_genre, rng)):
            try:
                spec = nl.prompt_to_spec(prompt, seed=1 + i % 50)
                spec, rep = refine(spec)
                spec = check(spec)
            except Exception:
                counts["skipped"] += 1
                continue
            ans = {"action": "spec", "spec": spec,
                   "explanation": f"{spec.get('genre')} in {spec['key']} {spec['mode']} at {spec['tempo']:g} bpm; critic {rep['score']}"}
            write(group, msgs_for(prompt, ans, spec, "generate"), "generate", prov)
            counts["generate"] += 1
            for ins in rng.sample(EDITS, edits_per_spec):
                try:
                    ops = nl.edit_to_ops(spec, ins)
                    apply_ops(spec, ops)
                except Exception:
                    counts["skipped"] += 1
                    continue
                write(group, msgs_for(ins, {"action": "ops", "ops": ops, "explanation": ins}, spec, "edit"), "edit",
                      TEMPLATE_PROV)
                counts["edit"] += 1
            cands = kprogs.get((spec.get("genre"), spec["mode"]))
            if cands:
                kp = rng.choice(cands)
                sec = next((s for s in spec["sections"] if kp["section"] in s["name"]), spec["sections"][-1])
                op = {"op": "set_chords", "section": sec["name"], "chords": kp["chords"]}
                try:
                    apply_ops(spec, [op])
                except (OpError, ValueError):
                    continue
                ins = f"use {' '.join(kp['chords'])} in the {sec['name']}"
                write(group, msgs_for(ins, {"action": "ops", "ops": [op], "explanation": "progression from the "
                      "songwriting knowledge pack"}, spec, "edit"), "edit", kp["provenance"],
                      {"label_source": "knowledge-pack progression -> set_chords (validated)"})
                counts["edit_knowledge_chords"] += 1
        if include_store:
            try:
                from .. import learn  # experience store of the feedback/bandit loop, if installed
                for p in learn.store().llm_pairs():
                    try:
                        json.loads(p["response"]) if p["response"].strip().startswith("{") else None
                    except ValueError:
                        continue
                    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": p["prompt"]},
                            {"role": "assistant", "content": p["response"]}]
                    write(f"store|{p['id']}", msgs, p.get("kind") or "store",
                          {"source": "midigen.learn experience store (llm_pairs)", "kind": "validated runtime pair"},
                          {"label_source": "validated LLM reply recorded by midigen.learn"})
                    counts["store_pairs"] += 1
            except Exception:
                pass
    finally:
        for f in files.values():
            f.close()
    manifest = {"format": fmt, "counts": counts, "by_split": by_split, "files": {k: str(out / f"{k}.jsonl") for k in files},
                "bytes": {k: (out / f"{k}.jsonl").stat().st_size for k in files},
                "provenance_note": "prompts: templates + midigen-songwriting/midigen-genres/midigen-knowledge (own-words "
                                   "analyses by the songwriting coach / genre-pack agents); answers: midigen offline "
                                   "parser + validator; no third-party lyrics or MIDI"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
