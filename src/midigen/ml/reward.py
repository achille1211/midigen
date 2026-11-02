"""Quality / reward model used for best-of-N candidate selection.

Target policy (honest by construction):

* **user mode** — when at least ``min_ratings`` user ratings are available (``midigen.learn`` experience store and/or
  feedback JSONL, see :mod:`midigen.ml.feedback`), the target is the user rating (0..100) and the features are
  spec/render features **plus** critic features (the critic is a legitimate input for predicting a human rating).
* **critic-surrogate mode** — otherwise the target is the critic score of the synthetic dataset, and critic features
  are *excluded* (they would leak the label). The model then learns to predict "what the critic will say" from the
  spec and the rendered notes. That is useful only for cheap pre-screening; it is not a model of human taste, and it
  mostly re-learns the generator's own rules.

Model: scikit-learn ``HistGradientBoostingRegressor`` if available, else closed-form ridge regression in numpy.
Artifacts: ``reward.pkl`` (pickle; only load artifacts you trained yourself) + ``reward.json`` (metadata/metrics).
"""
from __future__ import annotations

import json
import math
import pickle
import random
import time
from pathlib import Path

from .paths import artifact_dir, data_dir


class Ridge:
    """Tiny standardised ridge regression (numpy only)."""

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha

    def fit(self, X, y):
        import numpy as np
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        Z = (X - self.mu) / self.sd
        self.b0 = float(y.mean())
        A = Z.T @ Z + self.alpha * np.eye(Z.shape[1])
        self.w = np.linalg.solve(A, Z.T @ (y - self.b0))
        return self

    def predict(self, X):
        return ((X - self.mu) / self.sd) @ self.w + self.b0


def _make_model(kind: str = "auto", seed: int = 0):
    if kind in ("auto", "gbm"):
        try:
            from sklearn.ensemble import HistGradientBoostingRegressor
            return HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
                                                 min_samples_leaf=10, l2_regularization=1.0, random_state=seed,
                                                 early_stopping=False), "sklearn.HistGradientBoostingRegressor"
        except ImportError:
            if kind == "gbm":
                raise
    return Ridge(alpha=3.0), "numpy.ridge"


def _metrics(y, p) -> dict:
    import numpy as np
    y, p = np.asarray(y, float), np.asarray(p, float)
    if len(y) == 0:
        return {"n": 0}
    err = p - y
    ss = float(((y - y.mean()) ** 2).sum())
    out = {"n": int(len(y)), "mae": round(float(np.abs(err).mean()), 3), "rmse": round(float(np.sqrt((err ** 2).mean())), 3),
           "r2": round(1 - float((err ** 2).sum()) / ss, 4) if ss > 0 else None}
    out["spearman"] = _spearman(y, p)
    return out


def _avg_ranks(x):
    import numpy as np
    x = np.asarray(x, float)
    order = np.argsort(x, kind="stable")
    r = np.empty(len(x))
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return r


def _spearman(a, b):
    """Spearman rho with average ranks for ties (None if undefined)."""
    import numpy as np
    if len(a) < 3:
        return None
    ra, rb = _avg_ranks(a), _avg_ranks(b)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return round(float(np.corrcoef(ra, rb)[0, 1]), 4)


def _feature_row(spec: dict, perf=None, with_critic: bool = False) -> dict:
    from ..arranger import render
    from .features import critic_features, spec_features
    if perf is None:
        perf = render({k: v for k, v in spec.items() if k != "mix"} if "mix" in spec else spec)
    f = spec_features(spec, perf)
    if with_critic:
        from ..critic import critique
        f.update(critic_features(critique(perf)))
    return f


class RewardModel:
    def __init__(self, model, names, mode, meta=None):
        self.model, self.names, self.mode, self.meta = model, names, mode, meta or {}

    @property
    def with_critic(self) -> bool:
        return self.mode == "user"

    def predict_rows(self, rows):
        from .features import vectorize
        X, _ = vectorize(rows, self.names)
        return [float(v) for v in self.model.predict(X)]

    def score_spec(self, spec: dict, perf=None) -> float:
        return self.predict_rows([_feature_row(spec, perf, self.with_critic)])[0]

    def save(self, d: str | Path | None = None) -> Path:
        d = Path(d) if d else artifact_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "reward.pkl", "wb") as fh:
            pickle.dump({"model": self.model, "names": self.names, "mode": self.mode}, fh)
        (d / "reward.json").write_text(json.dumps(self.meta, indent=2))
        return d / "reward.pkl"


def load(d: str | Path | None = None) -> RewardModel | None:
    d = Path(d) if d else artifact_dir()
    p = d / "reward.pkl"
    if not p.exists():
        return None
    with open(p, "rb") as fh:
        obj = pickle.load(fh)  # noqa: S301 - own artifact
    meta = json.loads((d / "reward.json").read_text()) if (d / "reward.json").exists() else {}
    return RewardModel(obj["model"], obj["names"], obj["mode"], meta)


def _split_feedback(rec_id: str) -> str:
    import zlib
    h = zlib.crc32(("fb|" + rec_id).encode()) % 100
    return "train" if h < 80 else "val" if h < 90 else "test"


def train(dataset: str | Path | None = None, out: str | Path | None = None, kind: str = "auto",
          min_ratings: int = 30, seed: int = 0, target: str = "critic", log=print) -> dict:
    """Train the reward model; returns metadata with honest held-out metrics."""
    import numpy as np
    from .dataset import iter_jsonl
    from .features import vectorize
    from .feedback import load_feedback
    t0 = time.time()
    dataset = Path(dataset) if dataset else data_dir() / "synthetic"
    fb, fb_stats = load_feedback()
    mode = "user" if len(fb) >= min_ratings else "critic_surrogate" if target == "critic" else "analyze_surrogate"
    rows = {"train": [], "val": [], "test": []}
    if mode == "user":
        log(f"user mode: {len(fb)} rated songs")
        for r in fb:
            try:
                f = _feature_row(r["spec"], with_critic=True)
            except Exception:
                continue
            rows[_split_feedback(str(r["id"]))].append((f, r["rating"], r["spec"].get("genre"), str(r["id"]), None))
    else:
        songs_path = dataset / "songs.jsonl"
        if not songs_path.exists():
            raise FileNotFoundError(f"{songs_path} not found; run `midigen dataset build` first")
        log(f"{mode} mode ({len(fb)} user ratings < {min_ratings}); dataset {dataset}")
        for s in iter_jsonl(songs_path):
            if mode == "critic_surrogate":
                y = float(s["critic_score"])
            else:
                y = (s.get("analyze") or {}).get("analyze_quality")
                if y is None:
                    continue
            rows[s["split"]].append((s["features"], float(y), s["genre"], s["group"], s["variant"]))
    if len(rows["train"]) < 10:
        raise ValueError(f"not enough training rows ({len(rows['train'])})")
    names = sorted({k for f, *_ in rows["train"] for k in f})
    if mode != "user":
        names = [n for n in names if not n.startswith(("crit_", "critic_", "an_"))]
    Xtr, _ = vectorize([r[0] for r in rows["train"]], names)
    ytr = np.array([r[1] for r in rows["train"]])
    model, model_kind = _make_model(kind, seed)
    t1 = time.time()
    model.fit(Xtr, ytr)
    fit_s = time.time() - t1
    rm = RewardModel(model, names, mode)
    gmean = {}
    for f, y, g, *_ in rows["train"]:
        gmean.setdefault(g, []).append(y)
    gmean = {g: sum(v) / len(v) for g, v in gmean.items()}
    mu = float(ytr.mean())
    metrics = {}
    for sp in ("train", "val", "test"):
        R = rows[sp]
        if not R:
            metrics[sp] = {"n": 0}
            continue
        X, _ = vectorize([r[0] for r in R], names)
        y = [r[1] for r in R]
        p = model.predict(X)
        metrics[sp] = {"model": _metrics(y, p), "baseline_mean": _metrics(y, [mu] * len(y)),
                       "baseline_genre_mean": _metrics(y, [gmean.get(r[2], mu) for r in R])}
        if mode != "user" and sp == "test":
            metrics[sp]["best_of_variants"] = _best_of(R, p, seed)
    meta = {"mode": mode, "model": model_kind, "n_features": len(names), "target":
            "user rating 0..100" if mode == "user" else "critic score 0..100 (surrogate; critic features excluded)"
            if mode == "critic_surrogate" else "tools/analyze.py analyze_quality 0..100 (surrogate)",
            "rows": {k: len(v) for k, v in rows.items()}, "feedback": fb_stats, "dataset": str(dataset),
            "fit_seconds": round(fit_s, 2), "wall_seconds": round(time.time() - t0, 2), "metrics": metrics,
            "trained_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    if hasattr(model, "feature_importances_"):
        pass
    elif isinstance(model, Ridge):
        top = sorted(zip(names, model.w), key=lambda t: -abs(t[1]))[:15]
        meta["top_weights"] = [[n, round(float(w), 3)] for n, w in top]
    rm.meta = meta
    path = rm.save(out)
    meta["artifact"] = str(path)
    Path(path).with_suffix(".json").write_text(json.dumps(meta, indent=2))
    return meta


def _best_of(R, preds, seed) -> dict:
    """Within each held-out group (same genre+seed, different variants), pick by predicted reward."""
    groups: dict = {}
    for (f, y, g, grp, var), p in zip(R, preds):
        groups.setdefault(grp, []).append((var, y, float(p)))
    rng = random.Random(seed)
    chosen, v0, rnd, oracle, n = 0.0, 0.0, 0.0, 0.0, 0
    for grp, items in groups.items():
        if len(items) < 2:
            continue
        n += 1
        chosen += max(items, key=lambda t: t[2])[1]
        v0 += next((t[1] for t in items if t[0] == 0), items[0][1])
        rnd += sum(t[1] for t in items) / len(items)
        oracle += max(t[1] for t in items)
    if not n:
        return {"groups": 0}
    return {"groups": n, "candidates_per_group": round(sum(len(v) for v in groups.values()) / len(groups), 2),
            "label": "target of the chosen variant (critic score or analyze_quality)",
            "critic_of_reward_pick": round(chosen / n, 2), "critic_of_v0": round(v0 / n, 2),
            "critic_of_random_pick": round(rnd / n, 2), "critic_of_oracle_pick": round(oracle / n, 2)}
