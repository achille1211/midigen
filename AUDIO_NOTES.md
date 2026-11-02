# midigen audio: rendering, mix defaults, listening check

**Important: no person has listened to any of this audio.** Everything below that says something is "good" or
"bad" comes from numbers measured on the rendered files. The listening check measures the audio. It does not
replace listening.

## What it adds
| Piece | Where | Needs |
|---|---|---|
| Per-genre mix defaults: CC7 volume, CC10 pan, CC91 reverb, CC93 chorus per role, with a `default` fallback table | `src/midigen/data/mix_defaults.json`, `audio/mix.py`; applied in `midi_io.performance_to_midi(..., mix=True)` | mido only |
| Playback humanize: seeded and deterministic, applied to a **copy** | `audio/humanize.py` | mido only |
| Renderer: spec, .json, .mid or MidiFile to WAV, plus MP3 | `audio/render.py`, `audio/synth.py` | numpy/scipy/soundfile; FluidSynth and a GM .sf2 optional; ffmpeg for MP3 |
| Listening check: loudness, peaks, clipping, DC, silence, spectral bands, stereo, per-stem masking | `audio/analysis.py`, `audio/check.py` | numpy/scipy/soundfile |
| Critic "mix" section | `critic.critique(spec, mix=report)` / `critic.attach_mix()` | none |

### CLI
```
python -m midigen.audio backends
python -m midigen.audio render song.json out.wav --mp3 [--stems DIR] [--humanize SEED] [--no-mix] [--normalize -14]
python -m midigen.audio render song.mid  out.wav --mp3 --backend numpy
python -m midigen.audio analyze out.wav [--stems DIR/stems.json] [--genre house] [--json]
python -m midigen.audio check song.json outdir/      # render + stems + analysis + critic mix section
```
`check` writes `<name>.wav/.mp3/.mid` (the `.mid` is the exact humanized MIDI that was rendered), `<name>.mix.json`
(the full report), `<name>.mix.txt` (a plain-text summary) and `<name>.critic.txt` (the critic report with its "mix" section).

### Python
```python
from midigen.audio import render_audio, render_stems, analyze_mix, critic_findings, listening_check
res = render_audio(spec, "out.wav", mp3=True, humanize=1)        # RenderResult(backend, wav, mp3, seconds, ...)
rep = listening_check(spec, "outdir", name="song")                # mix report dict (verified_by_ear: False)
crit = midigen.critique(spec, mix=rep)                             # crit["mix"] = {score, findings, metrics, ...}
```
Mix findings use the critic's shape `{check, severity, message, suggestion, track}`, and each check name starts
with `mix_`. Examples: `mix_clipping`, `mix_true_peak`, `mix_loudness`, `mix_silent`, `mix_silence`, `mix_dc_offset`,
`mix_stereo_balance`, `mix_spectral_balance`, `mix_masking`, `mix_lead_level`, `mix_stem_level`. The mix score is
kept apart from the theory score, so the theory score does not change when audio is analysed.
`serious(report, include_mix=True)` also returns mix warnings and errors.

## Mix defaults (export)
- `performance_to_midi(perf, expression=True, mix=True)` and `export_midi(spec, path, expression=True, mix=True)`:
  - Existing callers keep the same signatures and gain the mix CCs.
  - `mix=False` reproduces the old CC7/CC10-only export, which is the "before" in the comparison below.
- **Volume:** the table volume × (spec volume ÷ `spec.DEFAULT_VOLUME[role]`). This keeps "louder"/"quieter" edits.
  A second track of the same role is set 8% lower.
- **Pan:** a spec pan other than 64 wins over the table. Otherwise the table pan is used, and duplicate chord, pad,
  arp or lead tracks are mirrored to the other side. Drums and bass stay centred.
- **Range and channels:** every value is clamped to the integers 0–127, and CCs go on the track's own channel.
  Drums stay on channel 10 (index 9) with no program change. Tests cover all 16 genres.
- **Tuning:** edit `mix_defaults.json`, either the `default` per-role table or the `genres.<id>` overrides. Each genre
  also has a `humanize` block (`timing_ms`, `velocity`), set to about half of the midigen-genres groove values.
  The quantized generator output still carries its own humanize.

## Playback humanize
- `humanize_midi(mid, seed, timing_ms, velocity)` returns a new MidiFile and leaves the input unchanged (tested
  byte-for-byte).
- It uses a per-track RNG seeded by `seed`, so the same seed gives identical output and a different seed gives
  different output.
- Note-offs move with their note-on. No note moves before 0, and same-pitch overlaps are repaired.
- Kick, snare and rim (35–40) get half the timing and velocity spread, so the backbeat stays tight while hats and
  percussion breathe.
- `render_audio(..., humanize=SEED)` applies it only to the rendered copy.

## Backends
1. **FluidSynth plus a GM soundfont** (preferred, used by `auto` when both are found).
   - Search order for the binary: `MIDIGEN_FLUIDSYNTH`, then `PATH`.
   - Search order for the soundfont: `MIDIGEN_SOUNDFONT`, `MIDIGEN_SOUNDFONT_DIR`, then the usual system dirs
     (`/usr/share/sounds/sf2`, ...). GeneralUser is preferred.
   - In this workspace apt mirrors were broken, so FluidSynth 2.6.2 was installed from conda-forge with micromamba
     into `.tools/fs/` (not in the patch):
     `micromamba create -p .tools/fs -c conda-forge fluidsynth`, then
     `export MIDIGEN_FLUIDSYNTH=$PWD/.tools/fs/bin/fluidsynth`.
   - On Debian or Ubuntu, `apt install fluidsynth` is enough.
   - Render command: `fluidsynth -ni -q -g 0.5 -r 44100 -O float -T wav -F out.wav sf2 in.mid`.
2. **numpy fallback** (`audio/synth.py`). This is used automatically when FluidSynth or a soundfont is missing.
   - It has simple voices by GM family (additive, saw or square with a filter, FM-ish keys, noise drums) and honours
     CC7, CC10 and CC11. It gives a rough version of CC91 (Schroeder reverb) and CC93 (modulated-delay chorus).
   - It is **crude**. It exists so the pipeline, tests and analysis always run. Do not judge timbre from it.
   - Sustain pedal (CC64) and pitch bend from `expression.py` are ignored by the numpy synth. FluidSynth honours them.
3. **MP3** via `ffmpeg -c:a libmp3lame -b:a 192k`. With `--normalize LUFS`, the gain is capped so the true peak stays
   at or below −1 dBTP.

If no backend is available, `AudioBackendError` explains what to install.

### Soundfont: GeneralUser GS v2.0.3
- **Source:** https://github.com/mrbumpy409/GeneralUser-GS. The file `GeneralUser-GS.sf2` came from repo commit
  684543d5 (2026-02-23). Size 32,319,396 bytes, sha256 `9575028c7a1f589f5770fccc8cff2734566af40cd26ed836944e9a5152688cfe`.
- **Location:** `.tools/soundfonts/GeneralUser-GS.sf2`, not committed. Its license is saved next to it as
  `GeneralUser-GS.LICENSE.txt`.
- **License (GeneralUser GS License v2.0):** it may be used "without restriction for your own music creation,
  private or commercial". It may be used in software projects, and the bank and its packaging may be modified.
  - The samples inherit rights that allow full use in music production.
  - The author notes he cannot be 100% sure where every sample came from. As far as he knows, none came from
    commercial packages.
  - He asks that people not link directly to his download files. Link to his site or ship a local copy instead.
- **Limitation:** midigen's drum "programs" (e.g. 25 = TR-808) are not sent on channel 10. FluidSynth therefore always
  plays the GM standard kit (bank 128, program 0). Genre-specific kits are not reproduced.

## Listening check: what is measured
- **Loudness:**
  - ITU-R BS.1770-4 K-weighting with absolute (−70) and relative (−10 LU) gating, implemented in numpy. It reports
    integrated, max momentary and max short-term loudness.
  - When ffmpeg is present it is cross-checked with `ffmpeg ebur128`.
  - Agreement on the samples was within 0.05 LU: house −25.19 vs −25.2, true peak −8.21 vs −8.2.
  - A test confirms agreement within 0.3 LU on a ramped sine, and that a 997 Hz −20 dBFS stereo sine reads about −20 LUFS.
- **Peaks and clipping:** sample peak and true peak (4× oversampled), clipped samples (|x| ≥ 0.999) and clip events.
- **Other basic measures:** DC offset and silence ratio (50 ms blocks below −60 dBFS).
- **Spectral bands:** six bands (sub, bass, low-mid, mid, high-mid, air) from a Welch PSD, compared with
  **heuristic** reference ranges (default, electronic and acoustic families). The ranges are rules of thumb, not
  calibrated targets.
- **Stereo:** L/R balance in dB, L/R correlation, and side-to-mid ratio (width).
- **Masking (per stem):**
  - Each track is rendered solo, and the drum track is split into "Kick" (35/36) and the rest.
  - A 1/3-octave band spectrogram is computed per channel in two focus ranges: low 30–250 Hz (kick/bass/drums) and
    mid 200–2000 Hz (chords/pad/lead).
  - For each relevant pair it reports overlap (the time-frequency cells where both are within 6 dB of each other
    and audible), how much of each one is covered, and the level difference.
  - Separate L/R spectrograms mean panning reduces measured masking. A test checks this.
  - It is an **energy-overlap estimate, not a psychoacoustic masking model.**
- **Findings:** each has a severity and a concrete suggestion. A CC7 change is computed with the GM gain law
  (40·log10(cc/127)).

## Before/after: mix defaults off vs on
Settings: FluidSynth with GeneralUser GS, 24 bars, playback humanize seed = spec seed, nothing normalized. Numbers are
`mix=False -> mix=True`, from `samples/comparison.json`.

| genre | LUFS | true peak dBTP | L/R balance dB | correlation | side/mid dB | masking score | mix score |
|---|---|---|---|---|---|---|---|
| house | −25.42 → −25.78 | −8.07 → −8.26 | 0.32 → 0.48 | 0.968 → 0.964 | −17.8 → −17.2 | 0.027 → 0.027 | 100 → 100 |
| jazz | −24.36 → −24.75 | −10.39 → −10.26 | 0.23 → **1.53** | 0.987 → 0.938 | −21.6 → −14.0 | 0.237 → **0.259** | 91 → **83** |
| rock | −21.74 → −22.30 | −7.38 → −7.78 | −0.17 → 0.09 | 0.964 → 0.911 | −17.3 → −13.3 | 0.118 → 0.116 | 89 → 90 |
| lofi_hiphop | −25.07 → −25.67 | −7.79 → −9.25 | −0.08 → 0.73 | 0.990 → 0.944 | −22.9 → −15.1 | 0.156 → 0.168 | 83 → 84 |

What the numbers show:
- **Width:** the defaults clearly widen the stereo image in every genre with more than drums, bass and chords. Side/mid
  rises by 4–8 dB and correlation drops. That is the intended effect of panning chords, pads and arps. Mono
  compatibility stays fine (correlation ≥ 0.91).
- **Masking:** the defaults do **not** measurably reduce masking overall (house ±0, rock −0.002, jazz +0.022,
  lofi +0.012).
  - One pair improved a lot: rock distorted rhythm guitar vs organ, 0.41 → 0.26.
  - Jazz got worse on this metric. The mix leans 1.5 dB left and the stereo-balance warning fires. The jazz spec
    itself pans the kit (60) and the upright bass (58) slightly left, and explicit spec pans win over the table. The
    piano is at 42 and the trumpet at 86, while the sax sits at 70 with its higher level. This is worth tuning: either
    let the table override near-centre spec pans for drums and bass, or retune jazz pans.
- **Loudness:** it drops 0.4–0.6 LU because the table volumes are a little lower than the spec defaults. All renders
  sit at about −22 to −26 LUFS with ≥ 7 dB of true-peak headroom. Use `--normalize -14` for streaming-level MP3s.
  The analyzer reports the low loudness as info only.
- **Recurring findings on the samples:**
  - lofi: the Rhodes vs pad overlap is 0.88 in 200–2k, and the lead (vibraphone/flute) sits 5.5 dB under the Rhodes.
  - jazz: 33% of the render is silent below −60 dBFS (the sparse intro and outro with brushes).
  - jazz: kick vs upright bass overlap is about 0.5.

## What was verified, and how
**Verified by measurement or tests (no listening):**
- Files render with both backends, have the right length, sample rate and channel count, and are not silent. MP3s are
  written.
- Mix CCs are present, in the range 0–127, on the right channels, with drums on channel 10, for all 16 genres. Spec
  pan and volume edits survive.
- Humanize is deterministic, bounded, keeps the note count, leaves the original untouched, and moves anchors less.
- The analyzer reports the correct values on synthetic signals: sine level and LUFS, a clipped sine, silence, partial
  silence, DC, L/R imbalance, masked and separated tones, and panning reducing masking. Loudness matches ffmpeg ebur128.
- The critic "mix" section is attached, its format matches the critic's, and the theory score is unchanged.

**NOT verified (nobody has heard the audio):**
- Whether any render sounds good, musical or genre-appropriate, or whether the mix defaults sound better.
- Reverb and chorus amounts, timbre choices, and the "feel" of humanize.
- Whether the masking score matches perceived muddiness, and whether the suggested CC changes fix anything.
- The numpy synth's sound quality, which is known to be crude.
- The MP3 encode, beyond its size and the decoder reading it.
