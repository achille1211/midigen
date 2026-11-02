# Review comparison: before vs after

Same 28 songs (`midigen new --genre G --seed N --offline`), measured by `tools/analyze.py`.
Before is `/workspace/midigen-critic/samples`, generated before the pack, review and QA fixes. After is `review/after/`.

| metric | before | after | note |
|---|---|---|---|
| in-key notes % | 98.1 | 98.1 | higher |
| strong-beat m2/b9 clash % | 3.6 | 4.9 | lower |
| kick with a bass note % | 73.9 | 60.9 | genre-dependent |
| lead strong-beat chord tones % | 99.7 | 79.6 | review target 60-80 for jazz/bossa/funk/neo-soul (was 100 everywhere) |
| lead phrase-end chord tones % | 56.1 | 90.6 | higher |
| lead repeated bars % | 58.8 | 19.7 | lower (review F3: one-bar loops) |
| lead distinct bar rhythms | 7.0 | 35.9 | higher |
| lead max leap (semitones) | 11.0 | 11.2 | <=12 |
| drum velocity SD | 17.6 | 23.8 | higher = less mechanical |
| timing deviation ms (mean) | 4.2 | 7.1 | humanize / laid-back feel |
| technical issues per song | 0.3 | 0 | lower |
| loudest section busier & louder than quietest % | 85.7 | 96.4 | higher |
| midigen critic score, mean (min) | 97.8 (84) | 96.0 (89) | built-in critic; the minimum rose and the mean fell slightly with the larger pack arrangements |

Notes:
- The after songs use the pack profiles. Most genres now have 5–9 tracks instead of 4–6, which gives more chances for strong-beat clashes between parts.
  Most of the added clashes are between two harmony parts voicing 7th/9th chord tones a semitone apart (e.g. Rhodes against pad), or a walking bass passing under held piano chords (jazz).
- Kick/bass coincidence follows the pack bass rhythms. Bossa rose from 25% to 75% (the fix the review asked for), while ambient and cinematic fell because their basses sustain.
- The review's hand-scored average was 7.0/10. It was not re-scored by hand.
