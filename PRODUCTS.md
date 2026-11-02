# MIDIGEN product direction

MIDIGEN's initial offer is an editable composition and delivery toolkit for people who finish music
in a DAW. The current release adds three local export products under `midigen deliver`. These are
working software workflows; there is no checkout, hosted account system, subscription, or sales claim.

## Available products

**Creator Cue Pack** serves podcast and video editors. Export the full cue with short timed edits,
an editable source, a preview and an audit manifest. Timed edits are truncations rather than newly
composed endings. Validate the ending in an editor before delivery to a client.

**Producer MIDI Kit** serves beatmakers and composers who want to replace instruments in their
own DAW. It includes the multitrack arrangement and individual instrument MIDI at the original
tempo and timeline. Optional WAVs use the existing local rendering backend. The built-in numpy
synth is suitable for checking arrangements; a chosen soundfont or DAW instruments determine
the finished production sound.

**Rehearsal Pack** serves arrangers preparing a practice sketch. It includes isolated instrument
MIDI and a slower practice mix. The engine limits solo wind, brass and vocal registers, overlaps
and continuous phrases. It does not supply notation, transposed scores, lyric prosody, articulation
instruction or a guarantee that a big-band arrangement is ready for a live session.

All three export a source JSON, PNG piano roll, rule-based quality report, named asset manifest,
checksums and ZIP. A critic score is an automated musical measurement, not a listening review.
No commercial licensing or ownership guarantee is created by an export.

## Why these are the first products

SOUNDRAW documents full-track and separate stem downloads:
https://docs.channel.io/soundraw-faq/en/articles/Can-I-download-stem-files-6aee13c3

AIVA documents orchestrated MIDI and other downloadable composition formats:
https://aiva.crisp.help/en/article/what-formats-can-i-download-the-music-in-1ppr6no/

These primary product sources were checked on November 2, 2026. They establish that editable
composition and separate-part delivery are existing product categories. They do not establish
demand, willingness to pay or a competitive advantage for MIDIGEN. Our hypothesis is that local,
seeded generation with editable harmony and inspectable quality reports is useful to DAW users.

## Next candidates, not shipped

**Adaptive game music kit:** related exploration/combat layers with matched bar lengths, exact
loop boundaries, transition cues and engine metadata. A prototype must pass loop-seam and
transition tests before being called game ready.

**Musician score desk:** MusicXML export, concert/transposed parts, section-aware breathing and
instrument-specific articulation. A working musician should review each supported instrument
before we present it as session ready.

**Brand cue family:** a shared motif across ident, intro, outro and bed variants, with intentionally
composed endings. Start with one edited brief and audition related variations together.

The next business step is to ask a few editors, producers and arrangers to complete their normal
workflow with an exported pack. Record where they need manual repair before adding pricing or
promising production readiness. No outreach or customer contact is automated by this repository.
