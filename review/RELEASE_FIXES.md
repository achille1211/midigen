# Release fixes and delivery products

Base: `release/1`, commit `b87f13be` (also the fetched `main` head when work began).

## Changes

- Web UI: plain-language edit endpoint and form; each edit creates a new take and preserves its parent.
  Locked or merged section takes receive a clear error instead of silently regenerating their notes.
- Prompt warnings use the library's interpretation, including typo corrections, mood presets and solo
  instrument inference, instead of a second, incomplete keyword recognizer.
- Mode edits remap diatonic harmony as well as the melody scale. Repeated “sadder” requests in minor
  slow the song instead of borrowing brighter major chords.
- Density edits update pack rhythms and fixed-style bass/chord onsets, including section overrides.
- Generated solo wind, brass and vocal lines receive range, monophony, leap and breathing constraints.
  Imported MIDI clips are preserved. These are conservative rehearsal constraints, not instrument-specific
  arranging expertise; a live big-band session still needs human review.
- Matplotlib is installed with the core package. Empty performances can also produce a preview.
- Windows CLI output tolerates characters outside the active console code page.
- `midigen deliver` exports Creator Cue Packs, Producer MIDI Kits and Rehearsal Packs with MIDI,
  editable JSON, preview, quality report, checksummed manifest, optional WAVs and a ZIP.

## Verification

On Windows / Python 3.11 with development and audio extras:

`python -m pytest tests integrations ui/test_app.py -q --tb=short`

The full run recorded **242 passed, 18 skipped, 12 failed**. The untouched release was independently
extracted and run with UTF-8 output: **231 passed, 18 skipped, 12 failed**. The failing test names match.
The baseline comparison ran core and UI tests; integration modules were included in the changed-tree run.
The final focused run passed **37 tests**, excluding one known external-pack-dependent UI assertion.
It includes compound-meter cutdown timing, template bass rest tokens, all three product bundles,
WAV rendering, conservative solo constraints, core edit operations and the web edit endpoint.

The remaining baseline failures are five expression tests, one linked-track test, two melodic restatement
tests, three harmonic-rhythm/seed-variety tests and one UI genre-count test. This checkout contains 16
built-in genres and does not contain the sibling `midigen-genres` / `midigen-knowledge` repositories used
by the original release's larger test environment. No missing packs were fabricated and no original
tests were weakened to produce a green report.

Manual smoke checks generated a MIDI, JSON and PNG through the CLI, exported a creator ZIP and
verified web generation followed by “make it sadder”: Take 1 remained in C major and Take 2 appeared
in C minor. Automated pack checks open the ZIPs and MIDI files, compare durations, validate SHA-256
checksums, enforce non-overwrite behavior, and read a rendered stereo WAV with SoundFile.

No previous critic-agent chat or unfinished critic review was present in the repository. The existing
`review/COMPARISON.md` remains available; this report does not claim to recover the cut-off review.
