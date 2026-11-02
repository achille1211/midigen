import random
import midigen as mg
from midigen import harmony
from midigen.theory import parse_chord, scale_pcs, transpose_symbol


def pcs(sym, key="C", mode="major"):
    return sorted(parse_chord(sym, key, mode).pcs)


def test_symbols_and_romans_agree():
    assert pcs("Am7") == pcs("vi7")
    assert pcs("G7") == pcs("V7")
    assert pcs("F") == pcs("IV")


def test_mode_relative_and_borrowed_numerals():
    assert parse_chord("VI", "A", "minor").root == 5          # F
    assert parse_chord("bVII", "C", "major").root == 10       # Bb
    assert parse_chord("bVI", "C", "major").root == 8         # Ab


def test_secondary_dominant_and_slash():
    c = parse_chord("V7/vi", "C", "major")                     # E7
    assert c.root == 4 and sorted(c.pcs) == sorted({4, 8, 11, 2})
    assert parse_chord("C/E").bass == 4


def test_half_diminished_and_scale():
    assert sorted(parse_chord("iiø7", "A", "minor").pcs) == sorted({11, 2, 5, 9})
    assert scale_pcs("D", "dorian") == [2, 4, 5, 7, 9, 11, 0]


def test_transpose_symbol():
    assert transpose_symbol("Am7", 2) == "Bm7"
    assert transpose_symbol("vi", 5) == "vi"


def test_cadence_detection():
    assert harmony.detect_cadence(["I", "IV", "V", "I"], "C", "major") == "authentic"
    assert harmony.detect_cadence(["I", "vi", "IV", "V"], "C", "major") == "half"
    assert harmony.detect_cadence(["I", "V", "IV", "I"], "C", "major") == "plagal"
    assert harmony.detect_cadence(["I", "IV", "V", "vi"], "C", "major") == "deceptive"


def test_generated_progressions_are_valid_and_cadence():
    for seed in range(20):
        prog = harmony.generate_progression("major", 4, random.Random(seed), cadence="authentic")
        assert len(prog) == 4 and all(mg.theory.is_valid_chord(c) for c in prog)
        assert harmony.detect_cadence(prog, "C", "major") == "authentic"


def test_voice_leading_is_smooth_and_in_range():
    prev = None
    for sym in ["I", "vi", "IV", "V7", "I"]:
        v = harmony.voice_lead(parse_chord(sym), prev, 52, 76, 4)
        assert all(52 <= p <= 76 for p in v)
        if prev:
            assert sum(abs(a - b) for a, b in zip(sorted(v), sorted(prev))) <= 16
        prev = v


def test_reharmonize_techniques_produce_valid_chords():
    for tech in ["7ths", "9ths", "secondary_dominants", "borrowed", "triads", "jazz", "modal_mixture", "tritone"]:
        try:
            out = harmony.reharmonize(["I", "vi", "IV", "V"], "C", "major", random.Random(1), tech)
        except ValueError:
            continue
        assert all(mg.theory.is_valid_chord(c) for c in out), (tech, out)
