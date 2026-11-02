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

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

WORKDIR = Path(os.environ.get("MIDIGEN_WORKDIR", "midigen_songs"))
_SONGS: dict[str, Editor] = {}

server = _Server(name="midigen", instructions=(
    "Theory-driven multitrack MIDI. Use generate_song to create a song (from a genre or free text), edit_song with a natural "
    "language instruction or JSON ops (see list_ops), export_midi to write a .mid, inspect to read a song or a .mid file. "
    "You decide structure (sections, chords as roman numerals, energy, instruments); the engine writes the notes."))


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
        spec, _ = refine(build_spec(genre, key=key or None, mode=mode or None, tempo=tempo or None, bars=bars or None, seed=seed))
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
    ed.apply(ops, label=instruction or "ops")
    return {"applied_ops": ops, **_summary(song_id)}


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


def main():
    server.run("stdio")


if __name__ == "__main__":
    main()
