"""JSON schemas for the song spec and edit ops. These are what the LLM is held to."""
from __future__ import annotations

GENERATOR_TYPES = ["drums", "bass", "chords", "pad", "arp", "lead", "clip"]
import json
from importlib import resources

DRUM_PATTERNS = sorted(k for k in json.loads(resources.files("midigen").joinpath("data/drums.json").read_text())
                       if not k.startswith("_"))
STYLES = {
    "bass": ["root", "root_fifth", "octave", "walking", "syncopated", "pulse", "offbeat", "rolling",
             "kick_lock", "sustained", "reggae", "arp", "template"],
    "chords": ["block", "stabs", "strum", "pad", "arpeggio", "offbeat", "comp", "bossa", "pulse", "power"],
    "pad": ["pad", "block", "arpeggio", "pulse"],
    "arp": ["up", "down", "updown", "random", "pinned"],
    "lead": ["melodic", "sparse", "riff", "busy"],
}
CADENCES = ["authentic", "half", "plagal", "deceptive", "none"]

_PARAMS = {
    "type": {"type": "string", "enum": GENERATOR_TYPES},
    "style": {"type": "string"},
    "pattern": {"type": "string", "description": "drum pattern name: a builtin (" + ", ".join(DRUM_PATTERNS) +
                ") or a genre-pack pattern such as 'bossa_nova.main' (checked against the registry)"},
    "variation": {"type": "string", "description": "drum pattern alternated every other 4-bar phrase"},
    "fill_pattern": {"type": "string", "description": "drum pattern used for fills"},
    "fill_every": {"type": "integer", "minimum": 0, "maximum": 64, "description": "also fill every N bars (0 = only at section ends)"},
    "transition": {"type": "string", "description": "drum pattern for the first bar when energy rises into a section"},
    "laid_back": {"type": "number", "minimum": -40, "maximum": 60, "description": "ms the backbeat (snare/clap/rim) sits behind the grid"},
    "rhythm": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 127}, "maxItems": 128,
               "description": "16th-step rhythm (velocity per step, 0 = rest) for template bass and chord/pad comping"},
    "tokens": {"type": "array", "items": {"type": ["string", "null"]}, "maxItems": 128,
               "description": "template bass: note token per step (root, third, fifth, seventh, octave, chord_tone, scale_step, approach)"},
    "cells": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}, "maxItems": 32,
              "description": "lead rhythm cells in signed 16ths (negative = rest)"},
    "restate": {"type": "boolean", "description": "lead: later instances of a section family (chorus2, final_chorus, drop2, verse2, head_a2) restate the first instance's melody with a fresh last bar (default true)"},
    "stepwise": {"type": "number", "minimum": 0, "maximum": 1, "description": "lead probability of stepwise motion"},
    "chord_tone_strong": {"type": "number", "minimum": 0, "maximum": 1,
                          "description": "lead probability of a chord tone on strong beats (default 0.9)"},
    "max_leap": {"type": "integer", "minimum": 1, "maximum": 24},
    "density": {"type": "number", "minimum": 0, "maximum": 1},
    "octave": {"type": "integer", "minimum": 0, "maximum": 8},
    "velocity": {"type": "integer", "minimum": 1, "maximum": 127},
    "seed": {"type": "integer"},
    "fill": {"type": "boolean"},
    "swing": {"type": "number", "minimum": 0, "maximum": 1, "description": "0 straight, 0.25 ~54%, 0.5 ~58% (MPC), 1 full triplet"},
    "rate": {"type": "string", "enum": ["1/4", "1/8", "1/16", "1/8t", "1/16t"]},
    "gate": {"type": "number", "minimum": 0.05, "maximum": 1.0},
    "range": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 127},
              "minItems": 2, "maxItems": 2},
    "sustain": {"type": "boolean", "description": "MIDI export: CC64 legato pedaling per chord (default on for chords/pad on piano/EP programs 0-7, off for stabs/offbeat/pulse/power)"},
    "glide": {"type": "object", "properties": {"probability": {"type": "number", "minimum": 0, "maximum": 1},
                                               "time_ms": {"type": "number", "minimum": 0, "maximum": 1000}},
              "additionalProperties": False,
              "description": "bass: MIDI export pitch-bend glide (808/portamento) into the next legato note with this probability"},
    "bend": {"type": "number", "minimum": 0, "maximum": 1, "description": "lead: MIDI export share of long notes that scoop up a semitone into pitch"},
    "humanize": {"type": "object", "properties": {
        "timing": {"type": "number", "minimum": 0, "maximum": 0.1},
        "velocity": {"type": "integer", "minimum": 0, "maximum": 40}},
        "additionalProperties": False},
    "clips": {"type": "object", "additionalProperties": {
        "type": "array", "items": {"type": "array", "minItems": 4, "maxItems": 4,
                                   "items": {"type": "number"}}}},
}

GENERATOR_SCHEMA = {
    "type": "object",
    "properties": _PARAMS,
    "required": ["type"],
    "additionalProperties": False,
}
PARAM_OVERRIDE_SCHEMA = {
    "type": "object",
    "properties": {k: v for k, v in _PARAMS.items() if k != "type"},
    "additionalProperties": False,
}

TRACK_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 64},
        "role": {"type": "string", "enum": ["drums", "bass", "chords", "pad", "arp", "lead", "other"]},
        "program": {"type": "integer", "minimum": 0, "maximum": 127},
        "channel": {"type": "integer", "minimum": 0, "maximum": 15},
        "volume": {"type": "integer", "minimum": 0, "maximum": 127},
        "pan": {"type": "integer", "minimum": 0, "maximum": 127},
        "mute": {"type": "boolean"},
        "sections": {"type": ["array", "null"], "items": {"type": "string"}},
        "generator": GENERATOR_SCHEMA,
        "overrides": {"type": "object", "additionalProperties": PARAM_OVERRIDE_SCHEMA},
    },
    "required": ["name", "role", "generator"],
    "additionalProperties": False,
}

SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 32},
        "bars": {"type": "integer", "minimum": 1, "maximum": 64},
        "chords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 64},
        "beats_per_chord": {"type": "number", "minimum": 0.5, "maximum": 32},
        "chord_beats": {"type": "array", "items": {"type": "number", "minimum": 0.25, "maximum": 64}, "maxItems": 64,
                        "description": "optional per-chord durations in beats (cycled with chords); overrides beats_per_chord"},
        "energy": {"type": "number", "minimum": 0, "maximum": 1},
        "cadence": {"type": "string", "enum": CADENCES,
                    "description": "intended phrase ending; melody lands on stable (authentic/plagal) or open (half) tones"},
    },
    "required": ["name", "bars", "chords"],
    "additionalProperties": False,
}

SPEC_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "midigen song spec",
    "type": "object",
    "properties": {
        "version": {"enum": [1, 2], "description": "2 = current; 1 is migrated (swing x3)"},
        "title": {"type": "string"},
        "tempo": {"type": "number", "minimum": 20, "maximum": 300},
        "time_signature": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 32},
                           "minItems": 2, "maxItems": 2},
        "key": {"type": "string", "pattern": "^[A-Ga-g][#b]?$"},
        "mode": {"type": "string", "enum": ["major", "minor", "dorian", "phrygian", "lydian",
                                             "mixolydian", "locrian", "harmonic_minor", "ionian", "aeolian"]},
        "seed": {"type": "integer"},
        "genre": {"type": "string"},
        "swing": {"type": "number", "minimum": 0, "maximum": 1, "description": "0 straight, 0.25 ~54%, 0.5 ~58% (MPC), 1 full triplet"},
        "swing_unit": {"type": "string", "enum": ["1/8", "1/16"]},
        "sections": {"type": "array", "items": SECTION_SCHEMA, "minItems": 1},
        "arrangement": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 64},
        "tracks": {"type": "array", "items": TRACK_SCHEMA, "maxItems": 16},
    },
    "required": ["version", "tempo", "time_signature", "key", "mode", "sections", "arrangement", "tracks"],
    "additionalProperties": False,
}


def _op(name, props, required, desc):
    return {
        "type": "object",
        "description": desc,
        "properties": {"op": {"const": name}, **props},
        "required": ["op", *required],
        "additionalProperties": False,
    }


_S = {"type": "string"}
_SEC = {"type": "string", "description": "section name"}
_TRK = {"type": "string", "description": "track name or role"}

OP_SCHEMAS = {
    "set_tempo": _op("set_tempo", {"bpm": {"type": "number", "minimum": 20, "maximum": 300}}, ["bpm"],
                     "Set tempo in BPM."),
    "change_tempo": _op("change_tempo", {"delta": {"type": "number", "minimum": -200, "maximum": 200}}, ["delta"],
                        "Add delta BPM to the tempo."),
    "set_key": _op("set_key", {"key": SPEC_SCHEMA["properties"]["key"], "mode": SPEC_SCHEMA["properties"]["mode"]},
                   ["key"], "Set key (and optionally mode). Roman-numeral chords follow automatically."),
    "transpose": _op("transpose", {"semitones": {"type": "integer", "minimum": -24, "maximum": 24}}, ["semitones"],
                     "Transpose the whole song (key, absolute chord symbols, clips)."),
    "set_time_signature": _op("set_time_signature", {"numerator": {"type": "integer", "minimum": 1, "maximum": 32},
                                                     "denominator": {"type": "integer", "enum": [2, 4, 8, 16]},
                                                     "keep_tempo": {"type": "boolean"}},
                              ["numerator", "denominator"],
                              "Set the time signature. Tempo counts the denominator note, so it is rescaled to keep the "
                              "quarter-note pulse unless keep_tempo is true."),
    "set_chords": _op("set_chords", {"section": _SEC, "chords": SECTION_SCHEMA["properties"]["chords"],
                                     "beats_per_chord": SECTION_SCHEMA["properties"]["beats_per_chord"]},
                      ["section", "chords"], "Replace a section's chord progression (symbols or roman numerals)."),
    "add_section": _op("add_section", {"section": SECTION_SCHEMA, "position": {"type": "integer"},
                                       "add_to_arrangement": {"type": "boolean"}},
                       ["section"], "Define a new section; optionally insert it into the arrangement at position."),
    "remove_section": _op("remove_section", {"section": _SEC}, ["section"],
                          "Delete a section definition and remove it from the arrangement."),
    "set_section": _op("set_section", {"section": _SEC, "bars": SECTION_SCHEMA["properties"]["bars"],
                                       "energy": SECTION_SCHEMA["properties"]["energy"],
                                       "new_name": {"type": "string", "minLength": 1}},
                       ["section"], "Change a section's length, energy or name."),
    "extend_section": _op("extend_section", {"section": _SEC, "bars": {"type": "integer", "minimum": -32, "maximum": 32}},
                          ["section", "bars"], "Make a section longer (positive) or shorter (negative) by N bars."),
    "set_arrangement": _op("set_arrangement", {"order": SPEC_SCHEMA["properties"]["arrangement"]}, ["order"],
                           "Set the full ordered list of section names to play."),
    "repeat_section": _op("repeat_section", {"section": _SEC, "position": {"type": "integer"}}, ["section"],
                          "Insert another occurrence of a section into the arrangement (default: after its last occurrence)."),
    "add_track": _op("add_track", {"track": TRACK_SCHEMA}, ["track"], "Add a track (channel auto-assigned if omitted)."),
    "add_instrument": _op("add_instrument", {"role": TRACK_SCHEMA["properties"]["role"], "name": _S,
                                             "instrument": {"type": "string", "description": "e.g. strings, piano, synth, organ"},
                                             "sections": TRACK_SCHEMA["properties"]["sections"]},
                          ["role"], "Add a track from a sensible preset for the role."),
    "remove_track": _op("remove_track", {"track": _TRK}, ["track"], "Delete a track."),
    "replace_track": _op("replace_track", {"track": _TRK, "with": TRACK_SCHEMA}, ["track", "with"],
                         "Replace a track wholesale."),
    "set_track": _op("set_track", {"track": _TRK, "name": _S, "program": TRACK_SCHEMA["properties"]["program"],
                                   "volume": TRACK_SCHEMA["properties"]["volume"], "pan": TRACK_SCHEMA["properties"]["pan"],
                                   "mute": {"type": "boolean"}, "sections": TRACK_SCHEMA["properties"]["sections"]},
                     ["track"], "Change track properties (instrument program, volume, pan, mute, which sections it plays in)."),
    "set_generator": _op("set_generator", {"track": _TRK, "section": _SEC, "params": PARAM_OVERRIDE_SCHEMA}, ["track", "params"],
                         "Merge generator params into a track (or only for one section when section is given)."),
    "set_drum_pattern": _op("set_drum_pattern", {"track": _TRK, "pattern": {"type": "string", "enum": DRUM_PATTERNS},
                                                 "section": _SEC}, ["pattern"], "Change the drum groove."),
    "set_density": _op("set_density", {"track": _TRK, "section": _SEC, "density": {"type": "number", "minimum": 0, "maximum": 1},
                                       "delta": {"type": "number", "minimum": -1, "maximum": 1}}, [],
                       "Set or nudge note density (busier/sparser). Omit track for all tracks."),
    "humanize": _op("humanize", {"track": _TRK, "timing": {"type": "number", "minimum": 0, "maximum": 0.1},
                                 "velocity": {"type": "integer", "minimum": 0, "maximum": 40}}, [],
                    "Add timing (beats) and velocity randomisation. Omit track for all tracks."),
    "reseed": _op("reseed", {"track": _TRK, "seed": {"type": "integer"}}, [],
                  "Regenerate ideas with a new seed (whole song or one track)."),
    "reharmonize": _op("reharmonize", {"section": _SEC, "technique": {"type": "string", "enum": [
        "secondary_dominants", "borrowed", "7ths", "9ths", "extensions", "triads", "functional"]},
        "seed": {"type": "integer"}}, ["technique"],
        "Theory-driven re-harmonisation of one section (or all): add secondary dominants, modal-mixture "
        "borrowed chords, 7th/9th extensions, strip to triads, or compose a new functional progression."),
    "set_cadence": _op("set_cadence", {"section": _SEC, "cadence": {"type": "string", "enum": CADENCES}},
                       ["section", "cadence"], "Rewrite the end of a section's progression to the given cadence."),
    "apply_genre": _op("apply_genre", {"genre": _S, "keep_harmony": {"type": "boolean"}}, ["genre"],
                       "Re-style the song in another genre (tempo, groove, instruments, layering, voicing)."),
    "set_swing": _op("set_swing", {"amount": {"type": "number", "minimum": 0, "maximum": 1},
                                   "unit": {"type": "string", "enum": ["1/8", "1/16"]}}, ["amount"],
                     "Set the global swing amount on the genre-file scale: 0 = straight 50%, 0.25 ~54% (house), "
                     "0.5 ~58% (MPC/lo-fi), 0.7 ~62% (jazz), 1 = full triplet 66.7%."),
    "set_title": _op("set_title", {"title": _S}, ["title"], "Rename the song."),
}

OPS_SCHEMA = {
    "type": "array",
    "items": {"oneOf": list(OP_SCHEMAS.values())},
}
