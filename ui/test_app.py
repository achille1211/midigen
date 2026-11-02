#!/usr/bin/env python3
"""Tests for midigen-ui. Run: .venv/bin/python test_app.py  (or pytest test_app.py)

Starts the server on a free port in a thread, exercises every endpoint, verifies the .mid with mido,
checks the output matches what `midigen new --genre ...` writes for the same parameters, and covers
section lock/regenerate semantics, takes, limits (take/session/rate) and config loading.
"""
import io
import copy
import json
import os
import random
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import app

# Tests use built-in defaults (not the local config.json) with the hourly limit off; tests that need a
# limit set it temporarily with limits(...).
BASE_OVERRIDES = {"generations_per_hour_per_ip": 0}
app.set_config(None, **BASE_OVERRIDES)
app.load_midigen(app.DEFAULT_SRC)
import mido  # noqa: E402

SRV = app.make_server("127.0.0.1", 0, quiet=True)
BASE = f"http://127.0.0.1:{SRV.server_address[1]}"
threading.Thread(target=SRV.serve_forever, daemon=True).start()


class limits:
    """Context manager: temporarily override CONFIG values (and reset the rate limiter)."""
    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        app.set_config(None, **dict(BASE_OVERRIDES, **self.kw))
        app.RATE.reset()

    def __exit__(self, *exc):
        app.set_config(None, **BASE_OVERRIDES)
        app.RATE.reset()


def req(path, body=None, raw=None, method=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"} if data else {},
                               method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def j(path, body=None, raw=None, method=None):
    st, _, b = req(path, body, raw, method)
    return st, json.loads(b)


def midi_notes(mid):
    out = []
    for i, tr in enumerate(mid.tracks):
        t = 0
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                out.append((i, t, m.channel, m.note, m.velocity))
    return sorted(out)


def test_page_and_static():
    st, h, b = req("/")
    assert st == 200 and b"midigen" in b and "text/html" in h["Content-Type"]
    for f, ct in (("app.js", "javascript"), ("style.css", "text/css")):
        st, h, b = req("/static/" + f)
        assert st == 200 and ct in h["Content-Type"] and len(b) > 1000, f
    assert req("/static/../app.py")[0] == 404
    assert req("/nope")[0] == 404


def test_genres():
    st, d = j("/api/genres")
    assert st == 200 and len(d["genres"]) >= 10
    g = {x["name"]: x for x in d["genres"]}["house"]
    assert g["tempo"]["min"] <= g["defaults"]["tempo"] <= g["tempo"]["max"] and g["description"]
    assert "dorian" in d["modes"] and "harmonic_minor" in d["modes"]


def check_song(body):
    st, d = j("/api/generate", body)
    assert st == 200, d
    assert d["total_beats"] == d["bars"] * d["bar_beats"]
    assert d["tracks"] and all({"name", "role", "program", "channel", "notes"} <= set(t) for t in d["tracks"])
    assert any(t["is_drums"] and t["channel"] == 9 for t in d["tracks"]) or body["genre"] == "ambient"
    for t in d["tracks"]:
        for s, dur, p, v in t["notes"]:
            assert 0 <= s < d["total_beats"] + 1e-6 and dur > 0 and 0 <= p <= 127 and 1 <= v <= 127
    assert 0 <= d["critique"]["score"] <= 100 and "critic score" in d["critique"]["summary"]
    st, h, b = req(d["download_url"])
    assert st == 200 and h["Content-Type"] == "audio/midi" and b[:4] == b"MThd" and "attachment" in h["Content-Disposition"]
    mid = mido.MidiFile(file=io.BytesIO(b))
    assert mid.type == 1 and len(mid.tracks) == len(d["tracks"]) + 1
    tempo = next(m.tempo for m in mid.tracks[0] if m.type == "set_tempo")
    assert abs(mido.tempo2bpm(tempo) - d["tempo"]) < 0.01
    n_api = sum(len(t["notes"]) for t in d["tracks"])
    n_mid = len(midi_notes(mid))
    assert n_mid > 0 and abs(n_mid - n_api) <= max(2, n_api // 100), (n_mid, n_api)
    assert abs(mid.length - d["total_beats"] * 60 / d["tempo"]) < 1.0
    return d, b


def test_generate_and_download():
    for body in ({"genre": "house", "key": "A", "mode": "minor", "tempo": 124, "bars": 16, "seed": 7},
                 {"genre": "funk", "key": "E", "mode": "dorian", "tempo": 104, "bars": 8, "seed": 3},
                 {"genre": "jazz", "bars": 12},  # auto key/mode/tempo, random seed
                 {"genre": "trap", "key": "f#", "mode": "phrygian", "tempo": 150, "bars": 32, "seed": 99}):
        d, _ = check_song(body)
        print(f"  {body['genre']:<6} {d['key']} {d['mode']:<9} {d['tempo']} BPM {d['bars']} bars seed {d['seed']}: "
              f"{len(d['tracks'])} tracks, {sum(len(t['notes']) for t in d['tracks'])} notes, critic {d['critique']['score']}/100")


def test_matches_cli():
    """Same params through the API and the real CLI (`midigen new --genre ...`) give identical notes."""
    from midigen import cli
    body = {"genre": "funk", "key": "E", "mode": "dorian", "tempo": 104, "bars": 8, "seed": 3}
    _, b = check_song(body)
    with tempfile.TemporaryDirectory() as td:
        with redirect_stdout(io.StringIO()):
            cli.main(["new", "--genre", "funk", "--key", "E", "--mode", "dorian", "--tempo", "104", "--bars", "8",
                      "--seed", "3", "--offline", "--out", str(Path(td) / "song")])
        cli_mid = mido.MidiFile(str(Path(td) / "song.mid"))
    assert midi_notes(mido.MidiFile(file=io.BytesIO(b))) == midi_notes(cli_mid)


def test_clamping_and_errors():
    st, d = j("/api/generate", {"genre": "pop", "bars": 9999, "tempo": 999, "seed": 1})
    assert st == 200 and d["bars"] == app.CONFIG["max_bars"] == 32 and d["tempo"] == 300
    assert any("clamped" in n for n in d["notes"])
    st, d = j("/api/generate", {"genre": "pop", "bars": 0, "seed": 1})
    assert st == 200 and d["bars"] >= app.CONFIG["min_bars"]
    for body, field in (({"genre": "polka"}, "genre"), ({}, "genre"), ({"genre": "pop", "key": "H"}, "key"),
                        ({"genre": "pop", "mode": "bebop"}, "mode"), ({"genre": "pop", "tempo": "fast"}, "tempo"),
                        ({"genre": "pop", "seed": -5}, "seed"), ({"genre": "pop", "bars": True}, "bars")):
        st, d = j("/api/generate", body)
        assert st == 400 and d["error"]["field"] == field and d["error"]["message"], (body, d)
    st, d = j("/api/generate", raw=b"{not json")
    assert st == 400 and "invalid JSON" in d["error"]["message"]
    st, d = j("/api/generate", raw=b"[1,2]")
    assert st == 400
    assert j("/api/download/0123456789abcdef.mid")[0] == 404
    assert j("/api/download/../../etc/passwd")[0] == 404


# ----------------------------------------------------------------- takes, locks, limits, config

SONG = {"genre": "house", "key": "A", "mode": "minor", "tempo": 124, "bars": 16, "seed": 7}


def sec_notes(d, i):
    """API note data of section i, per track (exact tuples)."""
    s = d["sections"][i]
    return [[tuple(n) for n in t["notes"] if s["start"] <= n[0] < s["end"]] for t in d["tracks"]]


def take_url(d, suffix=""):
    return f"/api/sessions/{d['session_id']}/takes/{d['id']}{suffix}"


def mid_section_events(b, sections):
    """note_on events of a downloaded .mid grouped by section index (via onset beat)."""
    mid = mido.MidiFile(file=io.BytesIO(b))
    out = [[] for _ in sections]
    for ti, tr in enumerate(mid.tracks):
        t = 0
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                beat = t / mid.ticks_per_beat
                k = next(i for i, s in enumerate(sections) if beat < s["end"] - 1e-6 or i == len(sections) - 1)
                out[k].append((ti, t, m.channel, m.note, m.velocity))
    return [sorted(x) for x in out]


def test_config_defaults_and_file():
    st, d = j("/api/config")
    assert st == 200
    L = d["limits"]
    assert L["bars"] == [2, 32] and L["max_takes_per_song"] == 8 and L["downloads"] == ["mid"]
    assert L["max_active_sessions"] == app.DEFAULTS["max_active_sessions"] > 0
    assert app.DEFAULTS["generations_per_hour_per_ip"] == 30 and app.DEFAULTS["session_ttl_minutes"] > 0
    assert "generations_used" in d["usage"]
    # missing file -> defaults, no warnings
    cfg, warn = app.load_config("/nonexistent/config.json")
    assert cfg == app.DEFAULTS and warn == []
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.json"
        p.write_text(json.dumps({"_comment": "x", "max_bars": 24, "max_takes_per_song": 3, "bogus": 1,
                                 "generations_per_hour_per_ip": -4, "trust_proxy_headers": "yes"}))
        cfg, warn = app.load_config(p)
        assert cfg["max_bars"] == 24 and cfg["max_takes_per_song"] == 3
        assert cfg["generations_per_hour_per_ip"] == 30 and cfg["trust_proxy_headers"] is False  # bad -> default
        assert len(warn) == 3 and any("bogus" in w for w in warn)
        p.write_text("{broken")
        cfg, warn = app.load_config(p)
        assert cfg == app.DEFAULTS and warn
    # the shipped config.json is valid and matches the documented defaults
    cfg, warn = app.load_config(Path(__file__).with_name("config.json"))
    assert warn == [] and cfg == app.DEFAULTS
    # config limits are enforced and reported
    with limits(max_bars=12):
        st, d = j("/api/generate", {"genre": "pop", "bars": 20, "seed": 1})
        assert st == 200 and d["bars"] == 12 and any("limit" in n for n in d["notes"])
        assert j("/api/config")[1]["limits"]["bars"][1] == 12
        assert j("/api/genres")[1]["limits"]["bars"][1] == 12


def test_lock_semantics():
    st, a = j("/api/generate", SONG)
    assert st == 200 and a["number"] == 1 and a["label"] == "Take 1" and a["kind"] == "generate"
    n = len(a["sections"])
    assert n >= 3 and [s["index"] for s in a["sections"]] == list(range(n))
    locked = [0, 2]
    st, b = j(take_url(a, "/regenerate"), {"locked": locked})
    assert st == 200, b
    assert b["parent_id"] == a["id"] and b["kind"] == "regenerate" and b["number"] == 2
    assert b["locked"] == locked and b["regenerated"] == [i for i in range(n) if i not in locked]
    for i in range(n):
        if i in locked:
            assert sec_notes(a, i) == sec_notes(b, i), f"locked section {i} changed"
        else:
            assert sec_notes(a, i) != sec_notes(b, i), f"unlocked section {i} did not change"
    assert b["bars"] == a["bars"] and [s["name"] for s in b["sections"]] == [s["name"] for s in a["sections"]]
    # the downloaded .mid is built from the merged result: locked sections byte-identical in MIDI too
    ea = mid_section_events(req(a["download_url"])[2], a["sections"])
    eb = mid_section_events(req(b["download_url"])[2], b["sections"])
    for i in range(n):
        assert (ea[i] == eb[i]) == (i in locked), i
    # single-section regenerate of a derived take; locks are inherited by default (saved on the parent)
    st, c = j(take_url(b, "/regenerate"), {"sections": [1]})
    assert st == 200 and c["parent_id"] == b["id"] and c["locked"] == locked and c["regenerated"] == [1]
    for i in range(n):
        assert (sec_notes(b, i) == sec_notes(c, i)) == (i != 1), i
    # saved locks via /locks are the default for the next regenerate
    st, m = j(take_url(c, "/locks"), {"locked": [1]})
    assert st == 200 and m["locked"] == [1]
    st, d = j(take_url(c, "/regenerate"), {})
    assert st == 200 and d["locked"] == [1] and d["regenerated"] == [i for i in range(n) if i != 1]
    assert sec_notes(c, 1) == sec_notes(d, 1)
    # errors: regenerate a locked section / everything locked / bad indices
    st, e = j(take_url(c, "/regenerate"), {"sections": [1], "locked": [1]})
    assert st == 409 and e["error"]["code"] == "locked"
    st, e = j(take_url(c, "/regenerate"), {"locked": list(range(n))})
    assert st == 409 and e["error"]["code"] == "all_locked"
    st, e = j(take_url(c, "/regenerate"), {"sections": [99]})
    assert st == 400 and e["error"]["field"] == "sections"
    st, e = j(take_url(c, "/regenerate"), {"locked": "intro"})
    assert st == 400 and e["error"]["field"] == "locked"
    # the parent take itself is untouched by regenerating from it
    st, a2 = j(take_url(a))
    assert st == 200 and a2["tracks"] == a["tracks"]
    # compare endpoint: score diff + per-section identity
    st, cmp = j(f"/api/sessions/{a['session_id']}/compare?a={a['id']}&b={b['id']}")
    assert st == 200 and cmp["score"]["diff"] == b["critique"]["score"] - a["critique"]["score"]
    assert [s["identical"] for s in cmp["sections"]] == [i in locked for i in range(n)]
    assert j(f"/api/sessions/{a['session_id']}/compare?a={a['id']}")[0] == 400
    assert j(f"/api/sessions/{a['session_id']}/compare?a={a['id']}&b={'f' * 16}")[0] == 404


def test_regenerate_native_vs_splice():
    """Native path: the take's spec reproduces the merged notes. Repeated section names need the splice."""
    params, _ = app.validate_request(dict(SONG))
    spec, perf, _ = app.generate(params)
    sp2, merged, _, seeds, exact = app.regenerate_sections(spec, perf, {1}, random.Random(5), 2)
    assert exact and set(seeds) == {perf.sections[1].name}
    rerender = app.mg.render(sp2)
    flat = lambda p: [[(x.start, x.dur, x.pitch, x.vel) for x in t.notes] for t in p.tracks]
    assert flat(rerender) == flat(merged)
    assert spec["tracks"][0]["overrides"] == perf.spec["tracks"][0]["overrides"]   # input spec not mutated
    # same section name twice in the arrangement: regenerate one occurrence only
    spec_r = copy.deepcopy(spec)
    spec_r["arrangement"] = spec_r["arrangement"] + [spec_r["arrangement"][1]]
    perf_r = app.mg.render(spec_r)
    assert perf_r.sections[1].name == perf_r.sections[-1].name
    last = len(perf_r.sections) - 1
    _, merged_r, _, _, exact_r = app.regenerate_sections(spec_r, perf_r, {1}, random.Random(9), 2)
    assert not exact_r
    assert app.section_notes(merged_r, last) == app.section_notes(perf_r, last)
    assert app.section_notes(merged_r, 0) == app.section_notes(perf_r, 0)
    assert app.section_notes(merged_r, 1) != app.section_notes(perf_r, 1)


def test_takes_unique_ids_download_and_delete():
    st, a = j("/api/generate", dict(SONG, seed=11))
    sid = a["session_id"]
    assert len(sid) == 24 and a["session"]["take_count"] == 1
    st, b = j(f"/api/sessions/{sid}/takes", {})
    assert st == 200 and b["number"] == 2 and b["kind"] == "new_take" and b["parent_id"] is None
    assert b["seed"] != a["seed"] and (b["key"], b["mode"], b["tempo"], b["bars"]) == (a["key"], a["mode"], a["tempo"], a["bars"])
    st, c = j(take_url(a, "/regenerate"), {"locked": [0]})
    ids = [a["id"], b["id"], c["id"]]
    assert len(set(ids)) == 3 and all(len(i) == 16 for i in ids)
    st, s = j(f"/api/sessions/{sid}")
    assert st == 200 and [t["id"] for t in s["takes"]] == ids and [t["number"] for t in s["takes"]] == [1, 2, 3]
    # another session gets different ids and starts at Take 1 again
    st, other = j("/api/generate", dict(SONG, seed=11))
    assert other["session_id"] != sid and other["id"] not in ids and other["number"] == 1
    assert other["tracks"] == a["tracks"]  # same seed + params = same notes, still a separate take
    # every take has its own .mid download, parseable with mido, named after the take
    blobs = []
    for t in (a, b, c):
        assert t["download_url"] == take_url(t, ".mid")
        st, h, body = req(t["download_url"])
        assert st == 200 and h["Content-Type"] == "audio/midi" and f"take{t['number']}.mid" in h["Content-Disposition"]
        mid = mido.MidiFile(file=io.BytesIO(body))
        assert len(mid.tracks) == len(t["tracks"]) + 1
        n_api = sum(len(x["notes"]) for x in t["tracks"])
        assert abs(len(midi_notes(mid)) - n_api) <= max(2, n_api // 100)
        blobs.append(body)
    assert len(set(blobs)) == 3
    assert req(f"/api/download/{a['id']}.mid")[2] == blobs[0]          # legacy route
    assert req(f"/api/sessions/{other['session_id']}/takes/{a['id']}.mid")[0] == 404   # wrong session
    # delete a take: its download goes away, numbering is not reused, parent label survives
    st, s = j(take_url(a), method="DELETE")
    assert st == 200 and s["take_count"] == 2 and a["id"] not in [t["id"] for t in s["takes"]]
    assert req(a["download_url"])[0] == 404 and req(take_url(a))[0] == 404
    assert next(t for t in s["takes"] if t["id"] == c["id"])["parent_label"] == "deleted take"
    st, d = j(f"/api/sessions/{sid}/takes", {})
    assert d["number"] == 4
    st, s = j(take_url(b, "/delete"), {})
    assert st == 200 and s["take_count"] == 2
    st, s = j(take_url(d), method="DELETE")
    st, e = j(take_url(c), method="DELETE")
    assert st == 409 and e["error"]["code"] == "last_take"
    st, _ = j(f"/api/sessions/{sid}", method="DELETE")
    assert st == 200 and j(f"/api/sessions/{sid}")[1]["error"]["code"] == "session_not_found"
    assert req(c["download_url"])[0] == 404
    assert j("/api/sessions/" + "0" * 24)[0] == 404


def test_take_limit():
    with limits(max_takes_per_song=3):
        st, a = j("/api/generate", dict(SONG, seed=21))
        assert a["session"]["max_takes"] == 3
        assert j(f"/api/sessions/{a['session_id']}/takes", {})[0] == 200
        st, c = j(take_url(a, "/regenerate"), {})
        assert st == 200 and c["session"]["take_count"] == 3
        for path, body in ((f"/api/sessions/{a['session_id']}/takes", {}), (take_url(a, "/regenerate"), {})):
            st, e = j(path, body)
            assert st == 409 and e["error"]["code"] == "take_limit" and "3 takes" in e["error"]["message"]
        assert j(take_url(c), method="DELETE")[0] == 200          # deleting frees a slot
        assert j(f"/api/sessions/{a['session_id']}/takes", {})[0] == 200


def test_rate_limit():
    with limits(generations_per_hour_per_ip=3):
        st, a = j("/api/generate", dict(SONG, seed=31))
        assert st == 200 and a["usage"] == {**a["usage"], "generations_used": 1, "generations_limit": 3, "generations_left": 2}
        assert j(f"/api/sessions/{a['session_id']}/takes", {})[0] == 200    # new takes count
        assert j(take_url(a, "/regenerate"), {})[0] == 200                   # regenerations count
        for path, body in (("/api/generate", SONG), (f"/api/sessions/{a['session_id']}/takes", {}),
                           (take_url(a, "/regenerate"), {})):
            st, h, b = req(path, body)
            e = json.loads(b)["error"]
            assert st == 429 and e["code"] == "rate_limit" and int(h["Retry-After"]) > 0 and e["retry_after_s"] > 0
        # reading, downloading, locking and comparing are not rate limited
        assert req(a["download_url"])[0] == 200 and j(take_url(a))[0] == 200
        assert j(take_url(a, "/locks"), {"locked": [0]})[0] == 200
        assert j("/api/config")[1]["usage"]["generations_left"] == 0
        # invalid requests do not burn quota
        app.RATE.reset()
        assert j("/api/generate", {"genre": "polka"})[0] == 400
        assert j("/api/config")[1]["usage"]["generations_used"] == 0


def test_session_limit():
    with limits(max_active_sessions=len(app.STORE.sessions) + 1):
        assert j("/api/generate", dict(SONG, seed=41))[0] == 200
        st, e = j("/api/generate", dict(SONG, seed=42))
        assert st == 503 and e["error"]["code"] == "session_limit"


# ---- prompt box, presets, genre list, plain labels, interpretation, friendly errors ---------------------------

def test_full_genre_list_and_plain_labels():
    import midigen as mg
    st, d = j("/api/genres")
    names = [g["name"] for g in d["genres"]]
    assert names == list(mg.available_genres()) and len(names) >= 20      # includes genre packs, like `midigen genres`
    assert {"bigband", "emo", "metal", "screamo"} <= set(names)
    assert all(g["label"] and g["plain"] for g in d["genres"])
    assert d["mode_labels"]["major"] == "bright / happy" and d["mode_labels"]["minor"] == "darker / sadder"
    ids = {p["id"] for p in d["presets"]}
    assert {"podcast_intro", "boss_fight", "coffee_shop", "lullaby", "horror", "eight_bit", "fanfare"} <= ids
    assert d["examples"]
    st, d = j("/api/generate", {"genre": "metal", "bars": 4, "seed": 2})        # pack genres generate too
    assert st == 200 and d["genre"] == "metal"


def test_prompt_uses_library_entry_point():
    """With a length inside the limits, the prompt result is exactly Composer(use_env=False).generate(prompt, seed)."""
    import midigen as mg
    from midigen.llm import Composer
    prompt = "chill coffee shop music, 8 bars"
    st, d = j("/api/generate", {"prompt": prompt, "seed": 5})
    assert st == 200, d
    r = Composer(use_env=False, record=False).generate(prompt, seed=5)
    assert (d["genre"], d["key"], d["mode"], d["tempo"], d["bars"]) == (r.spec["genre"], r.spec["key"], r.spec["mode"],
                                                                        r.spec["tempo"], 8)
    i = d["interpreted"]
    assert i["source"] == "prompt" and not i["rebuilt"] and not i["fallback"] and i["recognised"]["genre"] == "lofi_hiphop"
    assert i["line"].startswith("Interpreted as: Lo-fi hip hop /") and "8 bars" in i["line"] and i["explanation"]
    with tempfile.TemporaryDirectory() as td:
        mg.export_midi(mg.render(r.spec), Path(td) / "x.mid")
        lib = mido.MidiFile(str(Path(td) / "x.mid"))
    _, _, b = req(d["download_url"])
    assert midi_notes(mido.MidiFile(file=io.BytesIO(b))) == midi_notes(lib)


def test_prompt_duration_and_limits():
    assert app.parse_duration("a 15 second intro") == 15 and app.parse_duration("2 minutes") == 120
    assert app.parse_duration("half a minute") == 30 and app.parse_duration("1:30") == 90
    assert app.parse_duration("retro 80s synths") is None and app.parse_duration("120 bpm, 8 bars") is None
    st, d = j("/api/generate", {"prompt": "chill coffee shop music, 15 seconds", "seed": 1})
    spb = d["bar_beats"] * 60 / d["tempo"]
    assert st == 200 and abs(d["bars"] * spb - 15) <= spb / 2 + 1e-6 and d["interpreted"]["duration_from"] == "prompt"
    st, d = j("/api/generate", {"prompt": "epic trailer music, 10 minutes", "seed": 1})
    assert st == 200 and d["bars"] == app.CONFIG["max_bars"] and any("allows" in n for n in d["notes"])
    st, d = j("/api/generate", {"prompt": "epic trailer music", "seed": 1})        # full song > limit -> default bars
    assert st == 200 and d["bars"] == app.CONFIG["default_bars"] and any("sketches" in n for n in d["notes"])


def test_presets_apply_explicit_settings():
    for p in app.presets.PRESETS:
        st, d = j("/api/generate", {"prompt": p["prompt"], "preset": p["id"], "seed": 4})
        assert st == 200, (p["id"], d)
        s = p["settings"]
        assert (d["genre"], d["mode"], d["tempo"]) == (s["genre"], s["mode"], s["tempo"]), p["id"]
        want = max(app.CONFIG["min_bars"], min(app.CONFIG["max_bars"], round(s["seconds"] * s["tempo"] / 60 / d["bar_beats"])))
        assert d["bars"] == want, (p["id"], d["bars"], want)
        roles = {t["role"] for t in d["tracks"]}
        if s.get("roles"):
            assert roles <= set(s["roles"]), (p["id"], roles)
        for r in s.get("exclude_roles", []):
            assert r not in roles, p["id"]
        for t in d["tracks"]:
            if t["role"] in s.get("programs", {}):
                assert t["program"] == s["programs"][t["role"]], (p["id"], t["role"])
        assert d["interpreted"]["source"] == "preset" and not d["interpreted"]["fallback"]
    st, d = j("/api/generate", {"preset": "podcast_intro", "seed": 2})            # preset alone (no text) works
    assert st == 200 and d["interpreted"]["seconds"] == pytest_approx(15, 2.5)


def pytest_approx(v, tol):
    class A:
        def __eq__(self, o):
            return abs(o - v) <= tol
    return A()


def test_fallback_is_reported_honestly():
    st, d = j("/api/generate", {"prompt": "asdf qwerty", "seed": 1})
    i = d["interpreted"]
    assert st == 200 and d["genre"] == "pop" and i["fallback"] and "generic pop" in i["fallback_message"]
    assert i["suggested_presets"] and i["recognised"] is None
    st, d = j("/api/generate", {"prompt": "epic trailer music", "seed": 1})
    assert not d["interpreted"]["fallback"] and d["interpreted"]["recognised"]["genre"] == "cinematic"
    st, d = j("/api/generate", {"genre": "house", "bars": 4, "seed": 1})          # manual mode also explains itself
    assert d["interpreted"]["source"] == "manual" and d["interpreted"]["line"].startswith("Interpreted as: House /")
    assert "(darker / sadder)" in d["interpreted"]["line"] or "(" in d["interpreted"]["line"]


def test_prompt_session_new_take_and_regenerate():
    st, d = j("/api/generate", {"preset": "coffee_shop", "seed": 9})
    st, t2 = j(f"/api/sessions/{d['session_id']}/takes", {})
    assert st == 200, t2
    assert t2["genre"] == "lofi_hiphop" and t2["bars"] == d["bars"] and t2["interpreted"]["source"] == "preset"
    assert t2["seed"] != d["seed"] and (t2["key"], t2["mode"]) == (d["key"], d["mode"])


def test_friendly_prompt_errors():
    for body, code in (({"prompt": ""}, "empty_prompt"), ({"prompt": "   "}, "empty_prompt"),
                       ({"prompt": "x" * 400}, "prompt_too_long"), ({"prompt": "jazz", "preset": "nope"}, "unknown_preset")):
        st, d = j("/api/generate", body)
        assert st == 400 and d["error"]["code"] == code, (body, d)
    st, d = j("/api/generate", {"prompt": ""})
    assert d["error"]["examples"] and "preset" in d["error"]["message"]
    st, d = j("/api/generate", {"genre": "polka"})
    assert st == 400 and d["error"]["code"] == "unknown_genre"
    orig = app.generate_any
    app.generate_any = lambda params: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        with redirect_stderr(io.StringIO()):
            st, d = j("/api/generate", {"prompt": "jazz"})
    finally:
        app.generate_any = orig
    assert st == 500 and d["error"]["code"] == "internal" and "Traceback" not in d["error"]["message"]
    assert "boom" not in d["error"]["message"]
    js = req("/static/app.js")[2].decode()
    for code in ("not_understood", "empty_prompt", "internal", "network"):
        assert code + ":" in js, code


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {e}")
    SRV.shutdown()
    print("all tests passed" if not fails else f"{fails} test(s) failed")
    sys.exit(1 if fails else 0)
