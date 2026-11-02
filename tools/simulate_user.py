"""Simulated user with a hidden taste: does the learning loop converge to it?

The user secretly likes octave bass and busier parts in pop. Each round midigen generates a song (learning on),
the simulated user rates it 1-5 from that hidden taste plus noise, and the rating is fed back with learn.rate().
Run: python tools/simulate_user.py [rounds] [out.json]   (uses a throwaway MIDIGEN_DATA_DIR unless one is set)
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile

HIDDEN = {"bass_style": "octave", "density": "busier"}


def hidden_rating(choices: dict, rng: random.Random) -> float:
    r = 1.5 + sum(1.5 for d, a in HIDDEN.items() if choices.get(d) == a) + rng.uniform(-0.5, 0.5)
    return max(1.0, min(5.0, r))


def simulate(rounds: int = 40, genre: str = "pop", seed: int = 0, bars: int = 16) -> dict:
    from midigen import learn
    from midigen.genres import build_spec
    rng = random.Random(seed)
    rows = []
    for i in range(rounds):
        spec = build_spec(genre, seed=1000 + i, bars=bars)
        ch = learn.choices_for(spec)
        sid = learn.record(spec, source="simulated", choices=ch)
        r = hidden_rating(ch, rng)
        learn.rate(sid, r)
        rows.append({"round": i, "rating": round(r, 2), **{d: ch.get(d) for d in HIDDEN}})
    pol = learn.load_policy(genre)
    means = {d: {a: round(learn.arm_mean(pol, d, a), 3) for a in pol["arms"].get(d, {})} for d in HIDDEN}
    q = max(1, rounds // 4)
    def hit(rs):
        return sum(all(r[d] == a for d, a in HIDDEN.items()) for r in rs) / len(rs)
    def avg(rs):
        return sum(r["rating"] for r in rs) / len(rs)
    return {"rounds": rounds, "hidden": HIDDEN, "arm_means": means,
            "top_arm": {d: max(m, key=m.get) for d, m in means.items()},
            "first_quarter": {"hit_rate": hit(rows[:q]), "mean_rating": round(avg(rows[:q]), 2)},
            "last_quarter": {"hit_rate": hit(rows[-q:]), "mean_rating": round(avg(rows[-q:]), 2)},
            "rows": rows}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    if not os.environ.get("MIDIGEN_DATA_DIR"):
        os.environ["MIDIGEN_DATA_DIR"] = tempfile.mkdtemp(prefix="midigen-sim-")
    res = simulate(n)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w") as f:
            json.dump(res, f, indent=1)
