-- midigen_generate.lua — REAPER ReaScript (Lua)
-- Asks the midigen REST service for a song and inserts it at the edit cursor as one new REAPER track per
-- instrument (drums on MIDI channel 10), with track names, program changes, volume/pan CCs and, optionally,
-- a tempo/time-signature marker matching the song.
--
-- Requirements: the service running (`midigen-service`, default http://127.0.0.1:8765) and `curl` on PATH
-- (built into Windows 10+, macOS and most Linux distros).
-- Install: Actions > Show action list > New action > Load ReaScript... > pick this file.
--
-- Status: written against the documented ReaScript API, exercised outside REAPER with a stubbed `reaper`
-- table (integrations/reaper/test_reascript.py) against the live service. Not yet run inside REAPER itself.

local M = {}

M.CONFIG = {
  url = "http://127.0.0.1:8765",
  set_project_tempo = true,   -- add a tempo/time-signature marker at the cursor matching the song
  timeout_ms = 60000,
}

local DEFAULTS = { genre = "house", key = "", mode = "", bars = "16", seed = "1", prompt = "" }

-- ----------------------------------------------------------------------------- helpers
local function json_str(s)
  s = tostring(s):gsub('[%c"\\]', function(c)
    local map = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
    return map[c] or string.format("\\u%04x", c:byte())
  end)
  return '"' .. s .. '"'
end

function M.build_body(p)
  local parts = {}
  local function add(k, v) parts[#parts + 1] = json_str(k) .. ":" .. v end
  if p.genre ~= "" then add("genre", json_str(p.genre)) end
  if p.prompt ~= "" then add("prompt", json_str(p.prompt)) end
  if p.key ~= "" then add("key", json_str(p.key)) end
  if p.mode ~= "" then add("mode", json_str(p.mode)) end
  local bars, seed = tonumber(p.bars), tonumber(p.seed)
  if bars then add("bars", string.format("%d", bars)) end
  if seed then add("seed", string.format("%d", seed)) end
  return "{" .. table.concat(parts, ",") .. "}"
end

local function shell_quote(s)
  if package.config:sub(1, 1) == "\\" then return '"' .. s:gsub('"', '""') .. '"' end  -- Windows
  return "'" .. s:gsub("'", "'\\''") .. "'"
end

local function run(cmd)
  if reaper and reaper.ExecProcess then
    local out = reaper.ExecProcess(cmd, M.CONFIG.timeout_ms)       -- "exitcode\noutput"
    if out then
      local code, rest = out:match("^(%-?%d+)\n(.*)$")
      return tonumber(code) or -1, rest or out
    end
  end
  local h = io.popen(cmd)
  local out = h:read("*a")
  local ok, _, code = h:close()
  return (ok and 0) or code or 1, out
end

-- POST body to url; returns (http_status, response_text)
function M.http_post(url, body)
  local tmp = os.tmpname()
  local f = assert(io.open(tmp, "wb")); f:write(body); f:close()
  local cmd = "curl -sS -X POST -H " .. shell_quote("Content-Type: application/json") .. " --data-binary @"
    .. shell_quote(tmp) .. " -w " .. shell_quote("\n%{http_code}") .. " " .. shell_quote(url)
  local code, out = run(cmd)
  os.remove(tmp)
  if code ~= 0 then return nil, "curl failed (" .. tostring(code) .. "): " .. tostring(out) end
  local text, status = out:match("^(.*)\n(%d%d%d)%s*$")
  return tonumber(status), text or out
end

-- Parse the service's `lines` format (see /export docs).
function M.parse_lines(text)
  local song = { tracks = {} }
  local cur
  for line in text:gmatch("[^\r\n]+") do
    local f = {}
    for field in (line .. "\t"):gmatch("([^\t]*)\t") do f[#f + 1] = field end
    if f[1] == "H" then
      song.tempo, song.num, song.den, song.total = tonumber(f[2]), tonumber(f[3]), tonumber(f[4]), tonumber(f[5])
    elseif f[1] == "T" then
      cur = { channel = tonumber(f[2]), program = tonumber(f[3]), drums = f[4] == "1", volume = tonumber(f[5]),
              pan = tonumber(f[6]), name = f[7] or "midigen", notes = {} }
      song.tracks[#song.tracks + 1] = cur
    elseif f[1] == "N" and cur then
      cur.notes[#cur.notes + 1] = { tonumber(f[2]), tonumber(f[3]), tonumber(f[4]), tonumber(f[5]) }
    end
  end
  assert(song.tempo and song.total, "response is not in midigen lines format")
  return song
end

-- Insert a parsed song at the edit cursor. Returns the number of tracks and notes inserted.
function M.insert_song(song, label)
  local proj = 0
  local pos = reaper.GetCursorPosition()
  reaper.Undo_BeginBlock()
  reaper.PreventUIRefresh(1)
  if M.CONFIG.set_project_tempo then
    reaper.SetTempoTimeSigMarker(proj, -1, pos, -1, -1, song.tempo, song.num, song.den, false)
    reaper.UpdateTimeline()
  end
  local qn0 = reaper.TimeMap2_timeToQN(proj, pos)
  local t_end = reaper.TimeMap2_QNToTime(proj, qn0 + song.total)
  local base = reaper.CountTracks(proj)
  local n_notes = 0
  for i, tr in ipairs(song.tracks) do
    reaper.InsertTrackAtIndex(base + i - 1, true)
    local track = reaper.GetTrack(proj, base + i - 1)
    reaper.GetSetMediaTrackInfo_String(track, "P_NAME", tr.name, true)
    local item = reaper.CreateNewMIDIItemInProj(track, pos, t_end, false)
    local take = reaper.GetActiveTake(item)
    reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", (label or "midigen") .. " - " .. tr.name, true)
    local ppq0 = reaper.MIDI_GetPPQPosFromProjQN(take, qn0)
    if not tr.drums then reaper.MIDI_InsertCC(take, false, false, ppq0, 0xC0, tr.channel, tr.program, 0) end
    reaper.MIDI_InsertCC(take, false, false, ppq0, 0xB0, tr.channel, 7, tr.volume)
    reaper.MIDI_InsertCC(take, false, false, ppq0, 0xB0, tr.channel, 10, tr.pan)
    for _, n in ipairs(tr.notes) do
      local s = reaper.MIDI_GetPPQPosFromProjQN(take, qn0 + n[1])
      local e = reaper.MIDI_GetPPQPosFromProjQN(take, qn0 + n[1] + n[2])
      reaper.MIDI_InsertNote(take, false, false, s, e, tr.channel, n[3], n[4], true)
      n_notes = n_notes + 1
    end
    reaper.MIDI_Sort(take)
  end
  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock("midigen: insert " .. (label or "song"), -1)
  return #song.tracks, n_notes
end

function M.ask_params()
  local d = DEFAULTS
  local ok, csv = reaper.GetUserInputs("midigen", 6,
    "Genre (or empty),Key (e.g. A),Mode (e.g. minor),Bars,Seed,Prompt (if no genre),extrawidth=220",
    table.concat({ d.genre, d.key, d.mode, d.bars, d.seed, d.prompt }, ","))
  if not ok then return nil end
  local v = {}
  for field in (csv .. ","):gmatch("([^,]*),") do v[#v + 1] = field:match("^%s*(.-)%s*$") end
  return { genre = v[1] or "", key = v[2] or "", mode = v[3] or "", bars = v[4] or "", seed = v[5] or "",
           prompt = v[6] or "" }
end

function M.main()
  local p = M.ask_params()
  if not p then return end
  local status, text = M.http_post(M.CONFIG.url .. "/generate?format=lines", M.build_body(p))
  if status ~= 200 then
    reaper.ShowMessageBox("midigen service error " .. tostring(status) .. ":\n" .. tostring(text), "midigen", 0)
    return
  end
  local song = M.parse_lines(text)
  local label = (p.genre ~= "" and p.genre or "prompt") .. " seed " .. p.seed
  local nt, nn = M.insert_song(song, label)
  reaper.ShowConsoleMsg(string.format("midigen: inserted %d tracks, %d notes at %.1f BPM\n", nt, nn, song.tempo))
end

if not MIDIGEN_NO_MAIN then M.main() end
return M
