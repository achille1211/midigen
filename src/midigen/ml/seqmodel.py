"""Small symbolic sequence models: Witten-Bell interpolated n-grams.

* ``ChordModel``: next roman numeral given (genre, mode, section type, previous 1-2 chords).
* ``DrumModel``: next 16th-step drum token given (genre, step position in bar, previous 1-2 steps).

Both are pure Python (no numpy/torch), train in seconds, serialise to JSON and expose sampling hooks
(``sample_progression``, ``sample_drum_bar``). The backoff chain goes from the most specific history to the
empty history, then a uniform floor over the vocabulary, so every test token gets non-zero probability and the
reported perplexity is a proper held-out number.

No neural model is trained here: torch is not a midigen dependency, and with the data available (the generator's
own curated progressions plus ~1.1k knowledge-pack progressions) an n-gram is the honest baseline.
"""
from __future__ import annotations

import gzip
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

BOS, EOS = "<s>", "</s>"


class WBNgram:
    """Witten-Bell interpolation over an explicit chain of history feature tuples."""

    def __init__(self, chain: list[tuple[str, ...]]):
        self.chain = [tuple(c) for c in chain]
        self.counts: list[dict] = [defaultdict(Counter) for _ in self.chain]
        self.vocab: set = set()

    @staticmethod
    def _key(ctx: dict, fields) -> str:
        return "\x1f".join(str(ctx.get(f, "")) for f in fields)

    def add(self, ctx: dict, tok: str, w: float = 1.0) -> None:
        self.vocab.add(tok)
        for lvl, fields in enumerate(self.chain):
            self.counts[lvl][self._key(ctx, fields)][tok] += w

    def prob(self, ctx: dict, tok: str) -> float:
        p = 1.0 / (len(self.vocab) + 1)  # uniform floor (+1 for unseen)
        for lvl in range(len(self.chain) - 1, -1, -1):
            c = self.counts[lvl].get(self._key(ctx, self.chain[lvl]))
            if not c:
                continue
            n, t = sum(c.values()), len(c)
            p = (c.get(tok, 0.0) + t * p) / (n + t)
        return p

    def dist(self, ctx: dict) -> dict:
        return {w: self.prob(ctx, w) for w in self.vocab}

    def sample(self, ctx: dict, rng: random.Random, temperature: float = 1.0, exclude=()) -> str:
        d = {w: p for w, p in self.dist(ctx).items() if w not in exclude}
        if temperature != 1.0:
            d = {w: p ** (1.0 / max(1e-3, temperature)) for w, p in d.items()}
        tot = sum(d.values())
        r, acc = rng.random() * tot, 0.0
        for w, p in sorted(d.items()):
            acc += p
            if acc >= r:
                return w
        return max(d, key=d.get)

    def to_json(self) -> dict:
        return {"chain": [list(c) for c in self.chain], "vocab": sorted(self.vocab),
                "counts": [{k: dict(v) for k, v in lvl.items()} for lvl in self.counts]}

    @classmethod
    def from_json(cls, d: dict) -> "WBNgram":
        m = cls([tuple(c) for c in d["chain"]])
        m.vocab = set(d["vocab"])
        m.counts = [defaultdict(Counter, {k: Counter(v) for k, v in lvl.items()}) for lvl in d["counts"]]
        return m


def _ctx(base: dict, hist: list[str]) -> dict:
    return {**base, "p1": hist[-1] if hist else BOS, "p2": hist[-2] if len(hist) > 1 else BOS}


# ------------------------------------------------------------------ chords

CHORD_CHAIN = [("g", "m", "s", "p2", "p1"), ("g", "m", "p2", "p1"), ("g", "m", "p1"), ("m", "p2", "p1"), ("m", "p1"),
               ("g", "m"), ("m",), ()]
UNIGRAM_CHAIN = [()]


class ChordModel:
    kind = "chords"

    def __init__(self, chain=None):
        self.ng = WBNgram(chain or CHORD_CHAIN)

    def fit(self, rows, weight_key=None):
        for r in rows:
            base = {"g": r["genre"], "m": r["mode"], "s": r["section"]}
            hist: list[str] = []
            w = float(r.get(weight_key) or 1.0) if weight_key else 1.0
            for tok in list(r["chords"]) + [EOS]:
                self.ng.add(_ctx(base, hist), tok, w)
                hist.append(tok)
        return self

    def logprob_seq(self, r) -> tuple[float, int, int]:
        """(sum log p, n tokens, n top-1 correct) for one row (EOS included)."""
        base = {"g": r["genre"], "m": r["mode"], "s": r["section"]}
        hist, lp, n, hit = [], 0.0, 0, 0
        for tok in list(r["chords"]) + [EOS]:
            ctx = _ctx(base, hist)
            lp += math.log(self.ng.prob(ctx, tok))
            d = self.ng.dist(ctx)
            hit += int(max(d, key=d.get) == tok)
            n += 1
            hist.append(tok)
        return lp, n, hit

    def sample_progression(self, genre: str, mode: str, section: str, length: int, rng: random.Random,
                           temperature: float = 1.0, valid=None, max_tries: int = 20) -> list[str] | None:
        """Sample exactly ``length`` chords (EOS suppressed until the end). ``valid(sym)`` filters symbols."""
        base = {"g": genre, "m": mode, "s": section}
        for _ in range(max_tries):
            hist: list[str] = []
            ok = True
            while len(hist) < length:
                tok = self.ng.sample(_ctx(base, hist), rng, temperature, exclude=(EOS,))
                if valid and not valid(tok):
                    ok = False
                    break
                hist.append(tok)
            if ok:
                return hist
        return None

    def to_json(self):
        return {"kind": self.kind, "ngram": self.ng.to_json()}

    @classmethod
    def from_json(cls, d):
        m = cls()
        m.ng = WBNgram.from_json(d["ngram"])
        return m


# ------------------------------------------------------------------ drums

DRUM_CHAIN = [("g", "pos", "p2", "p1"), ("g", "pos", "p1"), ("g", "pos"), ("pos", "p1"), ("pos",), ()]


class DrumModel:
    kind = "drums"

    def __init__(self, chain=None):
        self.ng = WBNgram(chain or DRUM_CHAIN)

    max_bars = 16  # distinct bars per song used for fitting/eval (keeps long songs from dominating)

    def _bars(self, r):
        seen, out = set(), []
        for bar in r["bars"]:
            k = tuple(bar)
            if k not in seen:
                seen.add(k)
                out.append(bar)
        return out[: self.max_bars]

    def _iter(self, r):
        for bar in self._bars(r):
            hist: list[str] = []
            for k, tok in enumerate(bar):
                yield {"g": r["genre"], "pos": k}, hist, tok
                hist = hist + [tok]

    def fit(self, rows):
        for r in rows:
            for base, hist, tok in self._iter(r):
                self.ng.add(_ctx(base, hist), tok)
        return self

    def logprob_seq(self, r):
        lp, n, hit = 0.0, 0, 0
        for base, hist, tok in self._iter(r):
            ctx = _ctx(base, hist)
            lp += math.log(self.ng.prob(ctx, tok))
            d = self.ng.dist(ctx)
            hit += int(max(d, key=d.get) == tok)
            n += 1
        return lp, n, hit

    def sample_drum_bar(self, genre: str, steps: int, rng: random.Random, temperature: float = 1.0) -> list[str]:
        hist: list[str] = []
        for k in range(steps):
            hist.append(self.ng.sample(_ctx({"g": genre, "pos": k}, hist), rng, temperature))
        return hist

    def to_json(self):
        return {"kind": self.kind, "ngram": self.ng.to_json()}

    @classmethod
    def from_json(cls, d):
        m = cls()
        m.ng = WBNgram.from_json(d["ngram"])
        return m


# ------------------------------------------------------------------ io / eval

def save(model, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as fh:
        json.dump(model.to_json(), fh, separators=(",", ":"))
    return path


def load(path):
    with gzip.open(path, "rt") as fh:
        d = json.load(fh)
    return (ChordModel if d["kind"] == "chords" else DrumModel).from_json(d)


def evaluate(model, rows) -> dict:
    lp = n = hit = 0
    for r in rows:
        a, b, c = model.logprob_seq(r)
        lp, n, hit = lp + a, n + b, hit + c
    if not n:
        return {"tokens": 0}
    return {"tokens": n, "nll": round(-lp / n, 4), "perplexity": round(math.exp(-lp / n), 3),
            "top1_acc": round(hit / n, 4)}


# ------------------------------------------------------------------ training entry points

def _ksplit(src: str) -> str:
    import zlib
    return "test" if zlib.crc32(("kn|" + src).encode()) % 10 == 0 else "train"


def train_progression(dataset=None, out=None, knowledge: bool = True, knowledge_weight: float = 5.0, log=print) -> dict:
    """Train the chord n-gram on train-split sections (+ knowledge-pack progressions); report held-out metrics."""
    import time
    from .dataset import iter_jsonl
    from .paths import artifact_dir, data_dir
    t0 = time.time()
    dataset = Path(dataset) if dataset else data_dir() / "synthetic"
    secs = {sp: [r for r in iter_jsonl(dataset / "sections.jsonl", sp)] for sp in ("train", "val", "test")}
    if not secs["train"]:
        raise FileNotFoundError(f"no train sections in {dataset}; run `midigen dataset build` first")
    kn = {"train": [], "test": []}
    if knowledge:
        from .knowledge import progressions
        for r in progressions():
            kn[_ksplit(r["provenance"]["source"])].append(r)
    for r in kn["train"]:
        r["w"] = knowledge_weight
    train_rows = secs["train"] + kn["train"]
    model = ChordModel().fit(train_rows, weight_key="w")
    uni = ChordModel(UNIGRAM_CHAIN).fit(train_rows, weight_key="w")
    bi = ChordModel([("m", "p1"), ("m",), ()]).fit(train_rows, weight_key="w")
    res = {}
    for name, rows in (("synthetic_val", secs["val"]), ("synthetic_test", secs["test"]), ("knowledge_test", kn["test"])):
        if not rows:
            continue
        m = evaluate(model, rows)
        res[name] = {"model": m, "baseline_unigram": evaluate(uni, rows), "baseline_mode_bigram": evaluate(bi, rows),
                     "baseline_uniform_perplexity": len(model.ng.vocab) + 1}
    # sampling validity check on held-out contexts
    from ..theory import is_valid_chord
    rng = random.Random(0)
    ok = tot = 0
    for r in secs["test"][:300]:
        key = "C"
        ch = model.sample_progression(r["genre"], r["mode"], r["section"], max(1, len(r["chords"])), rng)
        tot += 1
        ok += int(bool(ch) and all(is_valid_chord(c, key, r["mode"]) for c in ch))
    out = Path(out) if out else artifact_dir()
    path = save(model, out / "chords.json.gz")
    meta = {"kind": "chords", "model": "Witten-Bell interpolated n-gram", "chain": [list(c) for c in CHORD_CHAIN],
            "vocab": len(model.ng.vocab), "train_sequences": {"synthetic": len(secs["train"]), "knowledge": len(kn["train"])},
            "knowledge_weight": knowledge_weight if knowledge else None,
            "train_tokens": sum(len(r["chords"]) + 1 for r in train_rows), "eval": res,
            "sample_validity": {"n": tot, "valid": ok}, "artifact": str(path), "bytes": path.stat().st_size,
            "wall_seconds": round(time.time() - t0, 2),
            "knowledge_provenance": "midigen-songwriting harmony.json/genres.json/song_analyses.json (songwriting coach "
                                    "agent, own-words analysis)" if knowledge else None}
    (out / "chords.json").write_text(json.dumps(meta, indent=2))
    return meta


def train_drums(dataset=None, out=None, log=print) -> dict:
    import time
    from .dataset import iter_jsonl
    from .paths import artifact_dir, data_dir
    t0 = time.time()
    dataset = Path(dataset) if dataset else data_dir() / "synthetic"
    rows = {sp: [r for r in iter_jsonl(dataset / "drums.jsonl", sp) if r.get("steps_per_bar") == 16]
            for sp in ("train", "val", "test")}
    if not rows["train"]:
        raise FileNotFoundError(f"no 4/4 drum rows in {dataset}")
    model = DrumModel().fit(rows["train"])
    uni = DrumModel([()]).fit(rows["train"])
    pos = DrumModel([("pos",), ()]).fit(rows["train"])
    res = {}
    for sp in ("val", "test"):
        if rows[sp]:
            res[sp] = {"model": evaluate(model, rows[sp]), "baseline_unigram": evaluate(uni, rows[sp]),
                       "baseline_position": evaluate(pos, rows[sp]), "baseline_uniform_perplexity": len(model.ng.vocab) + 1}
    out = Path(out) if out else artifact_dir()
    path = save(model, out / "drums.json.gz")
    meta = {"kind": "drums", "model": "Witten-Bell interpolated n-gram", "chain": [list(c) for c in DRUM_CHAIN],
            "vocab": len(model.ng.vocab), "train_songs": len(rows["train"]), "max_distinct_bars_per_song": DrumModel.max_bars,
            "eval": res, "artifact": str(path), "bytes": path.stat().st_size, "wall_seconds": round(time.time() - t0, 2)}
    (out / "drums.json").write_text(json.dumps(meta, indent=2))
    return meta


def sample_to_midi(genre: str, out_path, mode: str = "major", key: str = "C", bars: int = 8, seed: int = 0,
                   tempo: float = 110, d=None) -> dict:
    """Sampling hook: a chord progression + drum pattern from the trained models, written as a MIDI file."""
    from ..theory import is_valid_chord, parse_chord
    from .paths import artifact_dir
    from .tokens import grid_to_notes
    import mido
    d = Path(d) if d else artifact_dir()
    rng = random.Random(seed)
    out = {"genre": genre, "mode": mode, "key": key}
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo)))
    events = []
    if (d / "chords.json.gz").exists():
        cm = load(d / "chords.json.gz")
        ch = cm.sample_progression(genre, mode, "verse", bars, rng, valid=lambda s: is_valid_chord(s, key, mode))
        out["chords"] = ch
        for i, sym in enumerate(ch or []):
            c = parse_chord(sym, key, mode)
            for iv in c.intervals[:4]:
                p = 48 + (c.root + iv) % 12 + (12 if iv >= 12 else 0)
                events += [(i * 4 * 480, "on", 0, p, 70), ((i + 1) * 4 * 480 - 10, "off", 0, p, 0)]
    if (d / "drums.json.gz").exists():
        dm = load(d / "drums.json.gz")
        grid = [dm.sample_drum_bar(genre, 16, rng) for _ in range(bars)]
        out["drums"] = grid
        for st, dur, p, v in grid_to_notes(grid):
            events += [(int(st * 480), "on", 9, p, v), (int((st + dur) * 480), "off", 9, p, 0)]
    events.sort(key=lambda e: (e[0], e[1] == "on"))
    now = 0
    for t, kind, chn, p, v in events:
        tr.append(mido.Message("note_on" if kind == "on" else "note_off", channel=chn, note=p, velocity=v, time=t - now))
        now = t
    mid.save(str(out_path))
    out["path"] = str(out_path)
    return out
