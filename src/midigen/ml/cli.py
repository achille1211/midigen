"""Command line for the ML side: ``midigen dataset ...``, ``midigen train reward|progression|drums|export-finetune``,
``midigen eval``, ``midigen ml ...``; also ``python -m midigen.ml ...``.

``midigen train`` without one of the ML targets keeps its existing meaning (``midigen.learn`` self-training)."""
from __future__ import annotations

import argparse
import json
import sys

ML_TRAIN_TARGETS = ("reward", "progression", "drums", "export-finetune", "all")


def _pj(obj):
    print(json.dumps(obj, indent=1, default=str))


def cmd_dataset(a):
    if a.action == "build":
        from .dataset import build_synthetic
        seeds = list(range(a.seed_start, a.seed_start + a.seeds))
        m = build_synthetic(a.out, a.genres.split(",") if a.genres else None, seeds, a.variants, a.remi, a.workers)
        from .card import write_card
        card = write_card(a.out)
        _pj({k: m[k] for k in ("wall_seconds", "counts", "songs_by_split", "groups_by_split", "critic_score", "n_errors",
                               "bytes")})
        print("dataset card:", card)
    elif a.action == "import":
        from .licensed import import_folder
        if not a.folder:
            sys.exit("error: dataset import needs a folder")
        r = import_folder(a.folder, a.license, a.source, a.author, genre=a.genre)
        print(f"accepted {len(r['accepted'])}, rejected {len(r['rejected'])} -> {r['manifest']}")
        for x in r["rejected"][:50]:
            print("  rejected", x["file"], "-", x["reason"])
    elif a.action == "card":
        from .card import write_card
        print(write_card(a.out))
    elif a.action == "feedback":
        from .feedback import load_feedback
        recs, st = load_feedback()
        _pj(st)


def cmd_train(a):
    if a.target in ("reward", "all"):
        _need_numpy()
    targets = ["reward", "progression", "drums", "export-finetune"] if a.target == "all" else [a.target]
    for t in targets:
        if t == "reward":
            from .reward import train
            m = train(a.data, a.out, kind=a.model, min_ratings=a.min_ratings, target=a.target_label)
            _pj({k: m[k] for k in ("mode", "model", "target", "rows", "fit_seconds", "metrics", "artifact")})
        elif t == "progression":
            from .seqmodel import train_progression
            m = train_progression(a.data, a.out, knowledge=not a.no_knowledge, knowledge_weight=a.knowledge_weight)
            _pj({k: v for k, v in m.items() if k != "chain"})
        elif t == "drums":
            from .seqmodel import train_drums
            _pj({k: v for k, v in train_drums(a.data, a.out).items() if k != "chain"})
        elif t == "export-finetune":
            from .finetune import export
            _pj(export(a.ft_out, n_per_genre=a.per_genre, fmt=a.format))


def cmd_eval(a):
    _need_numpy()
    from .evaluate import evaluate
    r = evaluate(seeds=a.seeds, genres=a.genres.split(",") if a.genres else None, n=a.n, d=a.out, json_out=a.json)
    _pj(r["summary"])


def cmd_sample(a):
    from .seqmodel import sample_to_midi
    _pj(sample_to_midi(a.genre, a.output, mode=a.mode, bars=a.bars, seed=a.seed, d=a.out))


def register(sub) -> None:
    p = sub.add_parser("dataset", help="ML datasets: build (synthetic) | import (licensed MIDI) | card | feedback")
    p.add_argument("action", choices=["build", "import", "card", "feedback"])
    p.add_argument("folder", nargs="?", help="(import) folder of .mid files you have the rights to use")
    p.add_argument("--out", help="output folder (default <data>/synthetic)")
    p.add_argument("--genres", help="comma-separated (default: all)")
    p.add_argument("--seeds", type=int, default=30, help="seeds per genre")
    p.add_argument("--seed-start", type=int, default=1)
    p.add_argument("--variants", type=int, default=3, help="edited variants per (genre, seed), incl. the plain one")
    p.add_argument("--remi", action="store_true", help="also write full REMI note tokens (large)")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--license", help="(import) SPDX-like id for the whole folder, e.g. CC0-1.0")
    p.add_argument("--source", help="(import) where the files came from (URL)")
    p.add_argument("--author", help="(import) author / attribution")
    p.add_argument("--genre", help="(import) genre label")
    p.set_defaults(fn=cmd_dataset)

    p = sub.add_parser("ml-train", help=argparse.SUPPRESS)
    _train_args(p)

    p = sub.add_parser("eval", help="held-out evaluation: plain theory engine vs ML-assisted build_spec")
    p.add_argument("--seeds", type=int, default=5, help="held-out seeds per genre (seeds 10001..)")
    p.add_argument("--genres")
    p.add_argument("--n", type=int, default=6, help="candidates for best-of-N")
    p.add_argument("--out", help="artifact folder (default artifacts/ml)")
    p.add_argument("--json", help="write full results JSON here")
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("ml-sample", help="sample a progression + drum pattern from the trained models to .mid")
    p.add_argument("genre")
    p.add_argument("-o", "--output", default="ml_sample.mid")
    p.add_argument("--mode", default="major")
    p.add_argument("--bars", type=int, default=8)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", help="artifact folder")
    p.set_defaults(fn=cmd_sample)


def _train_args(p):
    p.add_argument("target", choices=ML_TRAIN_TARGETS)
    p.add_argument("--data", help="synthetic dataset folder (default <data>/synthetic)")
    p.add_argument("--out", help="artifact folder (default artifacts/ml or $MIDIGEN_ML_DIR)")
    p.add_argument("--model", default="auto", choices=["auto", "gbm", "ridge"], help="(reward) model family")
    p.add_argument("--target-label", default="critic", choices=["critic", "analyze"],
                   help="(reward) surrogate label without enough user ratings: midigen critic score or the independent "
                        "analyzer's analyze_quality")
    p.add_argument("--min-ratings", type=int, default=30, help="(reward) user ratings needed for user mode")
    p.add_argument("--no-knowledge", action="store_true", help="(progression) don't add knowledge-pack progressions")
    p.add_argument("--knowledge-weight", type=float, default=5.0, help="(progression) count weight of pack progressions")
    p.add_argument("--ft-out", help="(export-finetune) output folder (default <data>/finetune)")
    p.add_argument("--per-genre", type=int, default=12, help="(export-finetune) template prompts per genre")
    p.add_argument("--format", default="compact", choices=["compact", "runtime"])
    p.set_defaults(fn=cmd_train)


def route(argv: list[str]) -> list[str]:
    """Map ``train <ml-target> ...`` to the hidden ``ml-train`` parser; leave everything else alone."""
    if len(argv) > 1 and argv[0] == "train" and argv[1] in ML_TRAIN_TARGETS:
        return ["ml-train"] + argv[1:]
    return argv


def _need_numpy() -> None:
    try:
        import numpy  # noqa: F401
    except ImportError:
        raise SystemExit("midigen ML training/eval needs numpy (and, for the default model, scikit-learn): "
                         "pip install numpy scikit-learn  (see docs/ML.md)")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="python -m midigen.ml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    register(sub)
    p = sub.add_parser("train", help="train reward | progression | drums | export-finetune | all")
    _train_args(p)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
