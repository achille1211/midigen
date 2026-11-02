"""Exercise midigen_generate.lua outside REAPER: a real Lua 5.4 runtime (via `lupa`), a stubbed `reaper` table that
records every call, and real HTTP (curl) to a midigen service.

    pytest -q integrations/reaper                    # starts its own service on a free port; skips if deps missing
    python integrations/reaper/test_reascript.py     # starts its own service too
    python integrations/reaper/test_reascript.py http://127.0.0.1:8765    # use an already running service

Needs: `pip install -e integrations/python lupa` plus the service extra (fastapi, uvicorn) and `curl` on PATH.

This proves the script compiles, builds valid requests, parses the response and makes a coherent sequence of
ReaScript calls. It does NOT prove REAPER accepts them; that needs a run inside REAPER.
"""
import contextlib
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent

STUB = r"""
calls = {}
local function rec(name, ...) calls[#calls + 1] = {name, ...} end
local tracks = {}
local qpm = 120
reaper = {
  GetCursorPosition = function() return 2.0 end,
  Undo_BeginBlock = function() rec("Undo_BeginBlock") end,
  Undo_EndBlock = function(l) rec("Undo_EndBlock", l) end,
  PreventUIRefresh = function() end, UpdateArrange = function() end, UpdateTimeline = function() end,
  SetTempoTimeSigMarker = function(p, i, t, m, b, bpm, n, d, lin) qpm = bpm; rec("SetTempoTimeSigMarker", t, bpm, n, d) return true end,
  TimeMap2_timeToQN = function(p, t) return t * qpm / 60 end,
  TimeMap2_QNToTime = function(p, qn) return qn * 60 / qpm end,
  CountTracks = function() return #tracks end,
  InsertTrackAtIndex = function(i) table.insert(tracks, i + 1, {notes = 0, cc = 0}) end,
  GetTrack = function(p, i) return tracks[i + 1] end,
  GetSetMediaTrackInfo_String = function(tr, k, v, set) tr.name = v return true, v end,
  CreateNewMIDIItemInProj = function(tr, s, e) tr.item = {s = s, e = e, take = tr} return tr.item end,
  GetActiveTake = function(item) return item.take end,
  GetSetMediaItemTakeInfo_String = function(tk, k, v) tk.take_name = v return true, v end,
  MIDI_GetPPQPosFromProjQN = function(tk, qn) return math.floor((qn - tk.item.s * qpm / 60) * 960 + 0.5) end,
  MIDI_InsertCC = function(tk, sel, mute, ppq, type, ch, a, b) tk.cc = tk.cc + 1 tk.ch = ch end,
  MIDI_InsertNote = function(tk, sel, mute, s, e, ch, p, v) assert(e > s, "note end <= start") assert(p >= 0 and p < 128) tk.notes = tk.notes + 1 end,
  MIDI_Sort = function(tk) tk.sorted = true end,
  GetUserInputs = function(title, n, labels, defaults) return true, "techno,A,minor,8,3," end,
  ShowMessageBox = function(msg) error("ShowMessageBox: " .. msg) end,
  ShowConsoleMsg = function(msg) io.write(msg) end,
}
_G.stub_tracks = tracks
"""


def run_harness(url: str) -> str:
    """Run the ReaScript against the service at `url`; raises AssertionError on any mismatch."""
    from lupa.lua54 import LuaRuntime  # REAPER embeds Lua 5.4

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUB)

    def exec_process(cmd, timeout_ms):  # REAPER's ExecProcess contract: "exitcode\noutput"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout_ms / 1000)
        return f"{r.returncode}\n{r.stdout}{r.stderr}"
    lua.globals().reaper.ExecProcess = exec_process
    lua.globals().MIDIGEN_NO_MAIN = True
    src = (HERE / "midigen_generate.lua").read_text()
    m = lua.execute(src)
    m.CONFIG.url = url
    assert m.build_body(lua.table_from({"genre": 'say "hi"', "prompt": "", "key": "", "mode": "", "bars": "8",
                                        "seed": "x"})) == '{"genre":"say \\"hi\\"","bars":8}'
    m.main()
    tracks = lua.globals().stub_tracks
    names = [tracks[i].name for i in range(1, len(tracks) + 1)]
    notes = [tracks[i].notes for i in range(1, len(tracks) + 1)]
    assert names and all(n > 0 for n in notes), (names, notes)
    assert all(tracks[i].sorted for i in range(1, len(tracks) + 1))
    calls = lua.globals().calls
    first = [calls[i][1] for i in range(1, len(calls) + 1)]
    assert first[0] == "Undo_BeginBlock" and first[-1] == "Undo_EndBlock" and "SetTempoTimeSigMarker" in first
    # cross-check against the service's own note counts
    import json, urllib.request
    req = urllib.request.Request(url + "/generate", data=json.dumps({"genre": "techno", "key": "A", "mode": "minor",
                                 "bars": 8, "seed": 3}).encode(), headers={"Content-Type": "application/json"})
    summary = json.load(urllib.request.urlopen(req))["summary"]
    assert notes == [t["notes"] for t in summary["tracks"]], (notes, summary["tracks"])
    drums = [tracks[i] for i in range(1, len(tracks) + 1) if tracks[i].ch == 9]
    assert len(drums) == 1
    # error path: bad params -> message box
    lua.execute('reaper.GetUserInputs = function() return true, "techno,H,minor,8,1," end')
    try:
        m.main()
        raise AssertionError("expected an error message box")
    except Exception as e:  # lupa.LuaError
        assert "422" in str(e) and "key" in str(e), e
    return f"OK: {len(names)} tracks {names} notes {notes}; error path shows 422"



@contextlib.contextmanager
def live_service():
    """Serve midigen_integrations.service on a free localhost port for the duration of the block."""
    import uvicorn
    from midigen_integrations.service import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("midigen service did not start")
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


def test_reascript_against_live_service():
    import pytest

    pytest.importorskip("lupa.lua54", reason="lupa (Lua 5.4) not installed: pip install lupa")
    pytest.importorskip("fastapi", reason="service extra not installed: pip install -e 'integrations/python[service]'")
    pytest.importorskip("uvicorn", reason="service extra not installed: pip install -e 'integrations/python[service]'")
    if not shutil.which("curl"):
        pytest.skip("curl not found on PATH")
    with live_service() as url:
        assert run_harness(url).startswith("OK:")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(run_harness(argv[0]))
    else:
        with live_service() as url:
            print(run_harness(url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
