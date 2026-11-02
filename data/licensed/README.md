# Licensed MIDI (empty by design)

`midigen dataset import <folder> --license CC0-1.0 --source <url> [--author ..]` (or a `LICENSES.json` /
`licenses.csv` sidecar per file) appends to `manifest.jsonl` (created on first import: file, sha256, license, source, author, split) and
`remi.jsonl`. Allowed: cc0-1.0, pddl-1.0, public-domain, cc-by-3.0/4.0, cc-by-sa-3.0/4.0, mit, odc-by-1.0. Any
missing or unknown license, a missing source, or an attribution license with no author means the file is rejected.
Nothing is downloaded, and this repository ships no third-party MIDI.
