"""Minimal REST service for midigen (FastAPI). Stateless: every request carries the song ``spec`` it works on.

Run::

    pip install -e "integrations/python[service]"
    midigen-service --host 127.0.0.1 --port 8765      # or: uvicorn midigen_integrations.service:app
    open http://127.0.0.1:8765/docs                    # interactive OpenAPI docs (/openapi.json for the schema)

Environment:
    MIDIGEN_SERVICE_ALLOW_LLM=1   let requests set use_llm=true (uses MIDIGEN_API_KEY/BASE_URL/MODEL). Default off.
    MIDIGEN_CORS_ORIGINS=a,b      comma-separated origins allowed by CORS (e.g. http://localhost:5173). Default none.
"""
from __future__ import annotations

import base64
import os
import re
from enum import Enum
from typing import Any, Optional

try:
    from fastapi import FastAPI, Query, Request
    from fastapi.responses import JSONResponse, PlainTextResponse, Response
    from pydantic import BaseModel, Field
except ImportError as e:  # pragma: no cover
    raise ImportError('the REST service needs FastAPI: pip install "midigen-integrations[service]"') from e

from . import api

MIDI_MEDIA_TYPE = "audio/midi"


class OutFormat(str, Enum):
    json = "json"
    midi = "midi"
    lines = "lines"


class ExportFormat(str, Enum):
    midi = "midi"
    notes = "notes"
    lines = "lines"


class GenerateRequest(BaseModel):
    prompt: Optional[str] = Field(None, description="Free text, e.g. 'moody lofi in D minor, 16 bars'", examples=["dreamy synthwave"])
    genre: Optional[str] = Field(None, description="Genre id/name/alias (GET /genres)", examples=["house"])
    key: Optional[str] = Field(None, examples=["A"])
    mode: Optional[str] = Field(None, examples=["minor"])
    tempo: Optional[float] = Field(None, ge=20, le=300)
    bars: Optional[int] = Field(None, ge=1, le=256)
    seed: int = 1
    refine: bool = True
    title: Optional[str] = None
    use_llm: bool = False


class SpecRequest(BaseModel):
    spec: dict[str, Any] = Field(..., description="A midigen song spec (as returned by /generate)")


class EditRequest(SpecRequest):
    ops: Optional[list[dict[str, Any]]] = Field(None, description="JSON edit ops (GET /options -> ops)",
                                                examples=[[{"op": "transpose", "semitones": 2}]])
    instruction: Optional[str] = Field(None, description="Plain English, e.g. 'faster and add strings'")
    use_llm: bool = False


class SongResponse(BaseModel):
    spec: dict[str, Any]
    summary: dict[str, Any]
    critic: dict[str, Any]
    source: str
    ops: list[dict[str, Any]] = []
    midi_base64: Optional[str] = Field(None, description="Present when include_midi=true")


class ErrorResponse(BaseModel):
    error: str
    errors: list[str]


ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}

app = FastAPI(
    title="midigen service", version=api.API_VERSION,
    description="Theory-driven multitrack MIDI generation over HTTP. Stateless: generate returns a `spec`; send it back "
                "to /edit, /critique and /export. MIDI responses are `audio/midi` Standard MIDI Files (type 1).",
)

_origins = [o.strip() for o in os.environ.get("MIDIGEN_CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["Content-Disposition", "X-Midigen-Summary"])


@app.exception_handler(api.MidigenError)
async def _midigen_error(_: Request, exc: api.MidigenError):
    return JSONResponse(status_code=404 if exc.kind == "unknown_genre" else 422,
                        content={"error": exc.kind, "errors": exc.errors})


from fastapi.exceptions import RequestValidationError  # noqa: E402


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    errs = [f"{'.'.join(str(x) for x in e.get('loc', ()) if x != 'body')}: {e.get('msg')}" for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "invalid_params", "errors": errs})


def _llm_allowed(flag: bool) -> bool:
    if flag and os.environ.get("MIDIGEN_SERVICE_ALLOW_LLM") != "1":
        raise api.MidigenError("invalid_params", ["use_llm is disabled on this server (set MIDIGEN_SERVICE_ALLOW_LLM=1)"])
    return flag


def _filename(spec: dict) -> str:
    return (re.sub(r"[^A-Za-z0-9]+", "_", str(spec.get("title") or "song")).strip("_").lower() or "song") + ".mid"


def _midi_response(spec: dict, summary: Optional[dict] = None) -> Response:
    headers = {"Content-Disposition": f'attachment; filename="{_filename(spec)}"'}
    if summary is not None:
        headers["X-Midigen-Summary"] = (f"bars={summary['bars']};tempo={summary['tempo']};key={summary['key']};"
                                        f"mode={summary['mode']};tracks={len(summary['tracks'])}")
    return Response(content=api.to_midi_bytes(spec), media_type=MIDI_MEDIA_TYPE, headers=headers)


def _song_out(song: api.Song, fmt: OutFormat, include_midi: bool):
    if fmt is OutFormat.midi:
        return _midi_response(song.spec, song.summary)
    if fmt is OutFormat.lines:
        return PlainTextResponse(notes_to_lines(api.render_notes(song.spec)))
    out = song.to_dict()
    if include_midi:
        out["midi_base64"] = base64.b64encode(song.midi).decode()
    return out


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Liveness check and versions."""
    import midigen
    return {"status": "ok", "api_version": api.API_VERSION, "midigen_version": midigen.__version__}


@app.get("/options", tags=["meta"])
def options() -> dict:
    """Genres, keys, modes, limits, instruments, styles, drum patterns, the edit-op JSON schemas and the spec schema."""
    return api.list_options()


@app.get("/genres", tags=["genres"])
def genres() -> list[dict]:
    """All genres: id, display name, description, tempo range, modes."""
    return api.list_genres()


@app.get("/genres/{name}", tags=["genres"], responses=ERRORS)
def genre(name: str) -> dict:
    """One genre's profile (accepts ids, display names, aliases and id@pack)."""
    return api.genre_profile(name)


@app.post("/generate", tags=["songs"], response_model=None, responses={**ERRORS, 200: {
    "description": "Song JSON (format=json), a .mid file (format=midi) or tab-separated notes (format=lines, see /export)",
    "content": {"application/json": {"schema": SongResponse.model_json_schema()}, MIDI_MEDIA_TYPE: {}, "text/plain": {}}}})
def generate(req: GenerateRequest, format: OutFormat = Query(OutFormat.json),
             include_midi: bool = Query(False, description="add midi_base64 to the JSON response")):
    """Generate a song from a genre and/or prompt. Deterministic for the same parameters and seed."""
    d = req.model_dump()
    d["use_llm"] = _llm_allowed(d["use_llm"])
    return _song_out(api.generate(api.GenerateParams(**d)), format, include_midi)


@app.post("/edit", tags=["songs"], response_model=None, responses={**ERRORS, 200: {
    "description": "Edited song JSON (format=json) or a .mid file (format=midi)",
    "content": {"application/json": {"schema": SongResponse.model_json_schema()}, MIDI_MEDIA_TYPE: {}}}})
def edit(req: EditRequest, format: OutFormat = Query(OutFormat.json), include_midi: bool = Query(False)):
    """Apply JSON edit ops or a plain-English instruction to a spec. The response carries the new spec."""
    song = api.edit(req.spec, ops=req.ops, instruction=req.instruction, use_llm=_llm_allowed(req.use_llm))
    return _song_out(song, format, include_midi)


@app.post("/critique", tags=["songs"], responses=ERRORS)
def critique(req: SpecRequest) -> dict:
    """Music-theory critic report: score 0-100, findings, stats."""
    return api.critique(req.spec)


@app.post("/export", tags=["songs"], response_model=None, responses={**ERRORS, 200: {
    "description": "midi: .mid bytes; notes: JSON note lists; lines: tab-separated text for simple clients (ReaScript)",
    "content": {MIDI_MEDIA_TYPE: {}, "application/json": {}, "text/plain": {}}}})
def export(req: SpecRequest, format: ExportFormat = Query(ExportFormat.midi)):
    """Render a spec. `lines` format: `H tempo_qpm num den total_beats`, then per track
    `T channel program is_drums volume pan name` followed by its `N start dur pitch velocity` rows (tab-separated,
    times in quarter-note beats)."""
    if format is ExportFormat.midi:
        return _midi_response(req.spec)
    notes = api.render_notes(req.spec)
    if format is ExportFormat.notes:
        return notes
    return PlainTextResponse(notes_to_lines(notes))


def notes_to_lines(notes: dict) -> str:
    num, den = notes["time_signature"]
    rows = [f"H\t{notes['tempo_qpm']:g}\t{num}\t{den}\t{notes['total_beats']:g}"]
    for t in notes["tracks"]:
        name = str(t["name"]).replace("\t", " ").replace("\n", " ")
        rows.append(f"T\t{t['channel']}\t{t['program']}\t{int(t['is_drums'])}\t{t['volume']}\t{t['pan']}\t{name}")
        rows.extend(f"N\t{s:g}\t{d:g}\t{p}\t{v}" for s, d, p, v in t["notes"])
    return "\n".join(rows) + "\n"


def main(argv: Optional[list] = None) -> None:
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(description="midigen REST service")
    ap.add_argument("--host", default=os.environ.get("MIDIGEN_SERVICE_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MIDIGEN_SERVICE_PORT", "8765")))
    a = ap.parse_args(argv)
    uvicorn.run(app, host=a.host, port=a.port)


if __name__ == "__main__":
    main()
