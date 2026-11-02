"""Before/after demo for the learning loop. Writes into demo/learning/ using a throwaway store.

    python demo/learning/make_learning_demo.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
os.environ["MIDIGEN_DATA_DIR"] = tempfile.mkdtemp(prefix="midigen-demo-")
sys.path.insert(0, str(ROOT / "tools"))

import midigen as mg  # noqa: E402
from midigen import learn  # noqa: E402
from simulate_user import simulate  # noqa: E402

# 1) simulated listener with a hidden taste (octave bass + busier parts)
before = mg.build_spec("pop", seed=1000, bars=16)
sim = simulate(rounds=40, genre="pop")
after = mg.build_spec("pop", seed=1000, bars=16)
mg.export_midi(before, HERE / "pop_before.mid")
mg.export_midi(after, HERE / "pop_after_simulated_user.mid")
(HERE / "simulated_user.json").write_text(json.dumps(sim, indent=1))

# 2) self-training against the critic (a proxy, not ground truth) on a fresh store
os.environ["MIDIGEN_DATA_DIR"] = tempfile.mkdtemp(prefix="midigen-demo-train-")
res = learn.train(["pop", "funk", "lofi_hiphop", "house"], rounds=6, candidates=8, bars=16,
                  eval_seeds=tuple(range(1, 13)), chart=HERE / "train_curve.png", log=lambda *_: None)
(HERE / "train_curve.txt").write_text(res["ascii_chart"] + "\n")
(HERE / "train.json").write_text(json.dumps({k: res[k] for k in ("genres", "baseline", "baseline_mean", "curve",
                                                                 "final_mean")}, indent=1))
print(json.dumps({"simulated_user": {"first_quarter": sim["first_quarter"], "last_quarter": sim["last_quarter"],
                                     "top_arm": sim["top_arm"]},
                  "train": {"baseline_mean": res["baseline_mean"], "final_mean": res["final_mean"],
                            "curve": [round(c["eval_critic"], 2) for c in res["curve"]]}}, indent=1))
