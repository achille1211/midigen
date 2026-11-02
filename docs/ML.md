# midigen.ml: dataset, reward model, opt-in ML hook

## Optional dependencies

The ML module is not a packaging extra (packaging is owned elsewhere). Install by hand:

* `numpy>=1.24`: required for `train reward` and `eval`. Without it they exit with a clear message.
* `scikit-learn>=1.3`: optional. It provides the default gradient-boosting reward model. Without it a numpy ridge model is used.
* torch is not used.

```
pip install "numpy>=1.24" "scikit-learn>=1.3"
```

All imports are lazy. Plain midigen, `dataset build` and the n-gram models run without them.
`build_spec(ml=...)` falls back to the plain spec, with a RuntimeWarning, when artifacts or dependencies are missing.
Artifacts (`artifacts/ml/`) and generated data (`data/synthetic/`) are outputs. Keep them out of version control.
Everything runs on CPU in seconds to minutes. Nothing is downloaded.

## Commands
```
midigen dataset build --seeds 60 --variants 3      # -> data/synthetic/{songs,sections,tracks,drums}.jsonl, manifest.json, DATASET_CARD.md
midigen dataset card | feedback | import <dir> --license CC0-1.0 --source URL
midigen train reward [--target-label critic|analyze] [--min-ratings 30]   # -> artifacts/ml/reward.pkl + reward.json
midigen train progression | drums                  # chord / drum Witten-Bell n-grams (sampling hooks; secondary)
midigen eval --seeds 2 --n 4                       # held-out seeds 10001.. ; -> artifacts/ml/eval.json
midigen new "..." --ml [reward,progression,n=8]    # opt-in; also build_spec(..., ml=True|"opts"|{...}) or MIDIGEN_ML=1
```
`midigen train` with no ML target (`--rounds ...`) is still the `midigen.learn` self-training command.
`python -m midigen.ml ...` works too. Env vars: `MIDIGEN_DATASET_DIR` (default `<repo>/data`), `MIDIGEN_ML_DIR`
(default `<repo>/artifacts/ml`), `MIDIGEN_FEEDBACK` (extra rating JSONL), `MIDIGEN_DATASET_ANALYZE=0` (skip analyzer labels).

## Data and labels
* Songs come from midigen itself: every genre × seeds × edit-op variants. Learning and recording are switched off
  during the build, so the experience store is never written. Labels are midigen's critic (score and findings,
  per song, section and track) and the independent critic's `tools/analyze.py` metrics plus `analyze_quality`
  (0-100 composite of in-key %, chord-tone %, melody strong-beat chord tones, bass root on change and clashes).
  The analyzer maps only 16 genres, so the other genres get no analyzer labels.
* Splits: crc32("genre|seed") % 100 gives 80/10/10 train/val/test by group, and all variants of a song share its split.
* User ratings come from the existing experience store (`midigen.learn.store()`, `songs.user_reward`) and from
  `$MIDIGEN_FEEDBACK`. With at least `--min-ratings` ratings, the reward model trains on them (critic features allowed);
  otherwise it trains on a surrogate label and critic features are excluded.
* The reward features accept an optional spec `mix` block (`mix_*`, `has_mix`). It is ignored when absent.
* `data/licensed/` is empty. The importer rejects any file whose license is missing or unknown.

## Real numbers (this CPU, 2026-11-01)
Dataset: 23 genres × 60 seeds × 3 variants = **4140 songs** (23103 sections, 25184 tracks), 0 errors, 83.9 s.
Split 3342/399/399 songs (1114/133/133 groups). Critic mean 95.53 (min 36). 2880 songs have analyzer
labels (analyze_quality mean 82.09, range 48.7-100).

Reward model, held-out **test** split (HistGradientBoosting):

| target | model MAE | genre-mean MAE | mean MAE | model R² | genre-mean R² | model Spearman | genre-mean Spearman |
|---|---|---|---|---|---|---|---|
| critic score (71 feats, 3342 train) | **2.557** | 3.177 | 4.451 | **0.436** | 0.332 | **0.734** | 0.580 |
| analyze_quality (64 feats, 2352 train) | **3.028** | 6.805 | 9.687 | **0.860** | 0.448 | **0.919** | 0.684 |

On val, the critic target gives R² 0.098 vs a genre-mean R² of 0.185: the model is *worse* than the genre-mean
baseline there. Test and val disagree, so treat the critic-target numbers as noisy.

Best-of-3 among each test group's variants (label of the pick): critic target 96.41, against 96.18 for v0, 96.12 for a random
pick and 96.74 for the oracle; analyze target 82.15, against 80.96 for v0, 81.17 random and 82.54 oracle.

End-to-end `midigen eval` (23 genres × held-out seeds 10001-10002 = 46 songs, N=4 candidates, reward model and
chord n-gram):

| | critic | in-key | analyzer in-key % | strong clash % | bass root on change % | MIDI valid | s/song |
|---|---|---|---|---|---|---|---|
| plain build_spec | 93.39 | 0.972 | 98.08 | 0.578 | 60.97 | 100% | 0.004 |
| random of the same 4 | 92.80 | | | | | | |
| **ML (reward pick)** | 92.83 | 0.971 | 98.11 | 0.459 | 55.52 | 100% | 5.18 |
| critic-ranked best of 4 (upper reference) | 97.11 | 0.979 | 98.46 | 0.400 | 57.12 | 100% | 0.62 |

ML minus plain critic: **-0.57** (11 wins, 20 ties, 15 losses). 21 of 46 ML picks were n-gram-resampled
progressions. On the 28 hand-scored songs in `midigen-critic/REVIEW.md`, Spearman correlation with the human
scores is -0.32 for the reward model and -0.32 for the critic (n=28, older midigen version; it only shows that neither tracks those scores).

## Limits (read before trusting any of this)
* The data is midigen's own output, labelled by midigen's (or the analyzer's) rules. The models mostly learn to
  **imitate the generator and its critics**. A good R² means predicting a deterministic rule from features that
  partly overlap it (for example `pitch_in_key_ratio` and in-key %). It says nothing about human taste.
* The opt-in ML path does **not** beat the plain engine on held-out seeds (-0.57 critic, n=46). Best-of-N selection with
  the reward model is roughly as good as a random pick. Ranking by the critic directly does better on the critic's own metric,
  which is circular. The hook stays opt-in for this reason, and it falls back to the plain spec when no artifacts exist.
* Candidates other than k=0 use derived seeds, so `--ml` may return a song from a different seed than requested.
  With the reward model it costs about 5 s per song.
* The chord and drum n-grams in `artifacts/ml/` were trained on an earlier build of the dataset (before the rebase).
  They imitate the generator (synthetic test perplexity 1.30, but 86.9 on held-out knowledge-pack progressions, worse than a unigram at 41.7).
* The real signal is user ratings. Rate songs (`midigen rate` / `keep` / `reject`), then rerun `midigen train reward`.
