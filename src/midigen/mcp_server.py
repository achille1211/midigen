"""MCP stdio server exposing midigen to any MCP client (Claude Desktop, Cursor, ...).

Run:  midigen-mcp   (or python -m midigen.mcp_server)
Songs are kept in memory by song_id and also written to MIDIGEN_WORKDIR (default ./midigen_songs)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import spec as specmod
from .critic import critique
from .genres import available_genres, build_spec, get_genre, list_genres
from .llm import Composer, refine
from .midi_io import export_midi, inspect_midi
from .nl import edit_to_ops
from .ops import Editor, apply_ops
from . import learn

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

WORKDIR = Path(os.environ.get("MIDIGEN_WORKDIR", "midigen_songs"))
_SONGS: dict[str, Editor] = {}

server = _Server(name="midigen", instructions=(
    "Theory-driven multitrack MIDI. Use generate_song to create a song (from a genre or free text), edit_song with a natural "
    "language instruction or JSON ops (see list_ops), export_midi to write a .mid, inspect to read a song or a .mid file. "
    "You decide structure (sections, chords as roman numerals, energy, instruments); the engine writes the notes. "
    "Pass the user's reactions to rate_song / song_feedback: midigen learns per-genre preferences from them."))


def _id(spec: dict) -> str:
    base = "".join(ch if ch.isalnum() else "_" for ch in spec.get("title", "song").lower())[:24] or "song"
    i, sid = 1, base
    while sid in _SONGS:
        i += 1
        sid = f"{base}_{i}"
    return sid


def _summary(sid: str) -> dict:
    s = _SONGS[sid].spec
    rep = critique(s)
    return {"song_id": sid, "title": s.get("title"), "genre": s.get("genre"), "key": s["key"], "mode": s["mode"],
            "tempo": s["tempo"], "bars": specmod.total_bars(s),
            "sections": [{"name": x["name"], "bars": x["bars"], "energy": x.get("energy"), "chords": x["chords"]} for x in s["sections"]],
            "arrangement": s["arrangement"], "tracks": [t["name"] + f" ({t['role']})" for t in s["tracks"]],
            "critic_score": rep["score"], "critic_findings": [f["message"] for f in rep["findings"]][:8]}


@server.tool(name="generate_song", description="Create a song. Give a free-text prompt (e.g. 'moody lofi in D minor, 16 bars') "
             "and/or genre/key/mode/tempo/bars. Returns a song_id and summary.")
def generate_song(prompt: str = "", genre: str = "", key: str = "", mode: str = "", tempo: float = 0, bars: int = 0,
                  seed: int = 1) -> dict:
    if genre:
        raw = build_spec(genre, key=key or None, mode=mode or None, tempo=tempo or None, bars=bars or None, seed=seed)
        ch = learn.choices_for(raw)
        spec, rep = refine(raw)
        learn.record(spec, source="mcp", report=rep, choices=ch)
    else:
        spec = Composer(use_env=False).generate(prompt or "pop song", seed=seed).spec
        if key or mode or tempo:
            ops = []
            if key or mode:
                ops.append({"op": "set_key", "key": key or spec["key"], **({"mode": mode} if mode else {})})
            if tempo:
                ops.append({"op": "set_tempo", "bpm": tempo})
            spec = apply_ops(spec, ops)
    sid = _id(spec)
    _SONGS[sid] = Editor(spec)
    return _summary(sid)


@server.tool(name="edit_song", description="Edit a song. Either `instruction` (plain English, offline parser) or `ops_json` "
             "(a JSON list of edit ops; see list_ops). Use instruction='undo' or 'redo' for history.")
def edit_song(song_id: str, instruction: str = "", ops_json: str = "") -> dict:
    ed = _SONGS[song_id]
    if instruction.strip().lower() in ("undo", "redo"):
        label = ed.undo() if instruction.strip().lower() == "undo" else ed.redo()
        return {"done": label, **_summary(song_id)}
    ops = json.loads(ops_json) if ops_json else edit_to_ops(ed.spec, instruction)
    old = ed.spec
    ed.apply(ops, label=instruction or "ops")
    learn.record_edit(old, ed.spec, ops=ops, source="mcp")
    return {"applied_ops": ops, **_summary(song_id)}


@server.tool(name="suggest_followups", description="Linked tracks: given a proposed edit (`instruction` or `ops_json`) "
             "for a song, return ranked, explained, critic-validated follow-up ops for the other tracks (e.g. busier "
             "drums -> busier bass, sparer lead). Nothing is applied; pass the chosen ops to edit_song.")
def suggest_followups_tool(song_id: str, instruction: str = "", ops_json: str = "", limit: int = 5) -> dict:
    from . import linked
    ed = _SONGS[song_id]
    edit = json.loads(ops_json) if ops_json else instruction
    return {"suggestions": linked.suggest_followups(ed.spec, edit, limit=limit)}


@server.tool(name="export_midi", description="Write the song to a .mid (and its spec .json). Returns file paths and note counts.")
def export_midi_tool(song_id: str, path: str = "", png: bool = False) -> dict:
    spec = _SONGS[song_id].spec
    out = Path(path) if path else WORKDIR / f"{song_id}.mid"
    perf = export_midi(spec, out)
    specmod.save(spec, out.with_suffix(".json"))
    res = {"midi": str(out.resolve()), "spec": str(out.with_suffix(".json").resolve()),
           "tracks": {t.name: len(t.notes) for t in perf.tracks}}
    if png:
        from .preview import piano_roll
        res["png"] = str(piano_roll(perf, out.with_suffix(".png")).resolve())
    return res


@server.tool(name="inspect", description="Inspect a song by song_id (full spec + critic report) or a .mid file by path.")
def inspect_tool(song_id: str = "", path: str = "") -> dict:
    if path:
        return inspect_midi(path)
    s = _SONGS[song_id].spec
    return {"spec": s, "critic": critique(s)}


@server.tool(name="load_song", description="Load a spec .json (or import a .mid) from disk into the session.")
def load_song(path: str) -> dict:
    from .midi_io import import_midi
    spec = import_midi(path) if path.lower().endswith((".mid", ".midi")) else specmod.load(path)
    sid = _id(spec)
    _SONGS[sid] = Editor(spec)
    return _summary(sid)


@server.tool(name="list_ops", description="The edit-op catalogue (JSON schemas) and genre list.")
def list_ops() -> dict:
    from .schema import OP_SCHEMAS
    return {"ops": OP_SCHEMAS, "genres": available_genres()}


@server.tool(name="genre_profile", description="Return the knowledge-base profile for a genre.")
def genre_profile(genre: str) -> dict:
    return get_genre(genre)


def _song_ref(song_id: str, path: str = ""):
    if song_id in _SONGS:
        return _SONGS[song_id].spec
    return path or song_id  # a learning-store id, 'last', or a file path


@server.tool(name="rate_song", description="Record the user's rating (1-5) of a song, optionally with a note and a target "
             "('track:Bass', 'section:chorus'). Ratings dominate what midigen learns for the genre.")
def rate_song(song_id: str, rating: float, note: str = "", target: str = "") -> dict:
    return learn.rate(_song_ref(song_id), rating, note=note or None, target=target or None)


@server.tool(name="song_feedback", description="Record free-text feedback, thumbs up/down, keep or reject for a song "
             "(kind: note | up | down | keep | reject). Words like 'busier', 'more swing', 'bigger chorus' become preferences.")
def song_feedback(song_id: str, note: str = "", kind: str = "note", target: str = "") -> dict:
    ref = _song_ref(song_id)
    if kind in ("up", "down"):
        return learn.thumbs(ref, kind == "up", note=note or None, target=target or None)
    if kind == "keep":
        return learn.keep(ref, note=note or None)
    if kind == "reject":
        return learn.reject(ref, note=note or None)
    return learn.feedback(ref, note, target=target or None)


@server.tool(name="learn_status", description="What midigen has learned (per genre: arm scores, preferences, references).")
def learn_status(genre: str = "") -> dict:
    return learn.status(genre or None)


@server.tool(name="learn_reference", description="Learn from a reference .mid file for a genre: its progressions, bass "
             "pattern, lead rhythm cells, drum feel and energy become options that new songs sample.")
def learn_reference(path: str, genre: str) -> dict:
    out = learn.learn_midi(path, genre)
    return {"genre": out["genre"], "added": out["added"]}


@server.tool(name="train", description="Offline self-training against the critic and the learned user model; returns the "
             "improvement curve. The critic is a proxy: user ratings are the ground truth.")
def train(genres: str = "", rounds: int = 3, candidates: int = 6, bars: int = 16) -> dict:
    res = learn.train([g for g in genres.split(",") if g] or None, rounds=rounds, candidates=candidates,
                      bars=bars or None, log=lambda *_: None)
    return {k: res[k] for k in ("genres", "baseline_mean", "final_mean", "curve", "ascii_chart")}


def main():
    server.run("stdio")


if __name__ == "__main__":
    main()
