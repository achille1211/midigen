# Delivery pack examples

These three ZIPs were exported from `release-cue.json`:

- `creator-cue-pack.zip`: full cue and 15/30-second MIDI cutdowns.
- `producer-midi-kit.zip`: full cue and five isolated instrument MIDI files.
- `rehearsal-pack.zip`: full cue, five isolated instrument MIDI files and an 80% tempo practice take.

Each ZIP also contains the source JSON, a PNG preview, quality report and checksummed manifest.
They demonstrate delivery formats, not a finished commercial recording. Audio is optional and is
not included in these small examples. Use your DAW instruments for playback, or install the audio
extras and pass `--audio` when exporting your own pack.

To reproduce into a fresh folder:

```sh
midigen deliver demo/release-cue.json --product creator --out my-creator-pack
midigen deliver demo/release-cue.json --product producer --out my-producer-kit
midigen deliver demo/release-cue.json --product rehearsal --out my-rehearsal-pack
```

Choose a different output name for each run; exports refuse to overwrite an existing folder or ZIP.
