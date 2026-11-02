"""Linked tracks: when one part changes, which other parts should follow?

A small role-dependency model (who listens to whom in a band) turns an edit into ranked, explainable follow-up ops.
Every suggestion is validated: it is applied to the edited song, the result must pass ``check`` and the critic, and
it is kept only if the critic score stays the same or improves. ``suggest_followups(spec, edit)`` takes the song
*before* the edit and the edit (ops list, one op, or a plain-English instruction).
"""
from __future__ import annotations

import copy

from .critic import critique
from .ops import OpError, apply_ops
from .spec import SpecError, check

# follower role -> [(leader role, aspect, why)]
DEPENDENCIES: dict[str, list[tuple[str, str, str]]] = {
    "bass": [("drums", "rhythm", "the bass locks to the kick drum"), ("chords", "harmony", "the bass outlines the chord roots")],
    "chords": [("drums", "energy", "comping density tracks the groove's energy")],
    "pad": [("chords", "harmony", "the pad sustains the chords"), ("lead", "space", "the pad sits under the melody")],
    "arp": [("chords", "harmony", "the arp spells the chords"), ("drums", "rhythm", "the arp rides the drum grid")],
    "lead": [("chords", "harmony", "the melody leans on chord tones"), ("drums", "space", "a busy beat needs a sparer melody")],
    "other": [("lead", "space", "counter-lines answer the melody rather than doubling it")],
    "drums": [("tempo", "rhythm", "drum density should suit the tempo")],
}

ENSEMBLE_PROMPT = ("Linked tracks: bass locks to the kick and outlines chord roots; pad and arp follow the chords; the melody "
                   "leans on chord tones and gets sparer when the beat gets busier; counter-lines answer the melody. When you "
                   "change one part, add the smallest follow-up ops that keep the others consistent (e.g. busier drums -> "
                   "slightly busier bass, slightly sparer lead).")


def dependents(role: str) -> list[tuple[str, str, str]]:
    """Roles that follow `role`: [(follower, aspect, why)]."""
    return [(f, a, w) for f, deps in DEPENDENCIES.items() for (lead, a, w) in deps if lead == role]


def _ops(edit, spec) -> list[dict]:
    if isinstance(edit, str):
        from .nl import edit_to_ops
        return edit_to_ops(spec, edit)
    return [edit] if isinstance(edit, dict) else list(edit)


def _role_of_track(spec: dict, name: str | None) -> str | None:
    for t in spec["tracks"]:
        if name and t["name"].lower() == str(name).lower():
            return t["role"]
    return None


def _tracks(spec: dict, role: str) -> list[dict]:
    return [t for t in spec["tracks"] if t["role"] == role and t["generator"]["type"] != "clip" and not t.get("mute")]


def _candidates(before: dict, after: dict, ops: list[dict]) -> list[dict]:
    """Raw follow-up ideas: {ops, reason, leader, follower}."""
    out = []

    def dens(role, delta, why, leader, section=None):
        for t in _tracks(after, role):
            o = {"op": "set_density", "track": t["name"], "delta": delta, **({"section": section} if section else {})}
            out.append({"ops": [o], "reason": f"{'busier' if delta > 0 else 'sparser'} {t['name']}: {why}",
                        "leader": leader, "follower": role})

    for op in ops:
        name = op.get("op")
        if name == "set_density":
            role = _role_of_track(after, op.get("track")) if op.get("track") else None
            d = op.get("delta", (op.get("density", .5) - .5))
            if role is None or d == 0:
                continue
            for follower, aspect, why in dependents(role):
                if aspect in ("rhythm", "energy"):
                    dens(follower, round(d * .5, 3), f"{why} ({role} {'up' if d > 0 else 'down'})", role, op.get("section"))
                elif aspect == "space":
                    dens(follower, round(-d * .4, 3), f"{why} ({role} {'busier' if d > 0 else 'sparser'})", role, op.get("section"))
        elif name in ("set_tempo", "change_tempo"):
            dt = (op.get("bpm", before["tempo"]) - before["tempo"]) if name == "set_tempo" else op.get("delta", 0)
            if abs(dt) >= 8:
                for role in ("lead", "arp"):
                    dens(role, -0.1 if dt > 0 else 0.1, f"at {after['tempo']:g} BPM fast parts "
                         f"{'crowd the mix' if dt > 0 else 'leave gaps'} (tempo {'+' if dt > 0 else ''}{dt:g})", "tempo")
                dens("drums", -0.05 if dt > 0 else 0.1, "drum density should suit the tempo", "tempo")
        elif name in ("add_instrument", "add_track"):
            role = op.get("role") or op.get("track", {}).get("role")
            new_name = op.get("name") or (after["tracks"][-1]["name"] if after["tracks"] else None)
            same = [t for t in _tracks(after, role) if t["name"] != new_name] if role else []
            for t in same:
                out.append({"ops": [{"op": "set_track", "track": t["name"], "volume": max(40, t.get("volume", 100) - 12)}],
                            "reason": f"turn {t['name']} down a little: the new {new_name} shares its {role} role and register",
                            "leader": role, "follower": role})
                out.append({"ops": [{"op": "set_density", "track": new_name, "delta": -0.15}],
                            "reason": f"keep {new_name} sparser than {t['name']} so they don't double each other",
                            "leader": role, "follower": role})
            if role in ("lead", "other") and _tracks(after, "pad"):
                dens("pad", -0.1, f"make room under the new {new_name}", role)
        elif name == "remove_track":
            role = _role_of_track(before, op.get("track"))
            if role == "drums":
                for t in _tracks(after, "bass") + _tracks(after, "chords"):
                    out.append({"ops": [{"op": "humanize", "track": t["name"]}],
                                "reason": f"humanize {t['name']}: without drums the timing grid is exposed", "leader": "drums",
                                "follower": t["role"]})
            for follower, aspect, why in dependents(role or ""):
                if aspect == "space":
                    dens(follower, 0.1, f"fill the space {role} left", role)
        elif name == "set_section" and "energy" in op:
            sec = next((s for s in before["sections"] if s["name"] == op["section"]), None)
            if sec is not None:
                d = op["energy"] - sec.get("energy", .5)
                if abs(d) >= .1:
                    for role in ("drums", "bass"):
                        dens(role, round(d * .5, 3), f"{op['section']} energy {'up' if d > 0 else 'down'}: rhythm section follows",
                             "energy", op["section"])
        elif name in ("reharmonize", "set_chords"):
            for t in _tracks(after, "bass"):
                out.append({"ops": [{"op": "set_generator", "track": t["name"], "params": {"inversions": 0.3}}],
                            "reason": f"let {t['name']} use inversions so it moves smoothly through the new chords",
                            "leader": "chords", "follower": "bass"})
        elif name == "set_swing":
            for t in _tracks(after, "lead") + _tracks(after, "bass"):
                out.append({"ops": [{"op": "humanize", "track": t["name"]}],
                            "reason": f"humanize {t['name']} so it sits in the new swing feel", "leader": "drums",
                            "follower": t["role"]})
    return out


def suggest_followups(spec: dict, edit, limit: int = 5) -> list[dict]:
    """Ranked, explainable, validated follow-up ops for `edit` applied to `spec` (the song before the edit).

    Each item: {rank, ops, reason, leader, follower, score_before, score_after, delta}. Only suggestions that apply
    cleanly, pass validation and do not lower the critic score are returned (best delta first, then model order).
    """
    before = check(copy.deepcopy(spec))
    ops = _ops(edit, before)
    after = apply_ops(before, ops)
    base = critique(after)["score"]
    seen, good = set(), []
    for i, c in enumerate(_candidates(before, after, ops)):
        key = repr(c["ops"])
        if key in seen:
            continue
        seen.add(key)
        try:
            trial = check(apply_ops(after, c["ops"]))
        except (OpError, SpecError, KeyError, ValueError):
            continue
        sc = critique(trial)["score"]
        if sc >= base:
            good.append({**c, "score_before": base, "score_after": sc, "delta": round(sc - base, 2), "_i": i})
    good.sort(key=lambda x: (-x["delta"], x["_i"]))
    for r, g in enumerate(good[:limit], 1):
        g.pop("_i")
        g["rank"] = r
    return good[:limit]


def follow(spec: dict, edit, n: int = 2) -> tuple[dict, list[dict], list[dict]]:
    """Apply `edit` plus its top `n` follow-ups -> (new_spec, edit_ops, accepted_suggestions)."""
    ops = _ops(edit, spec)
    sugg = suggest_followups(spec, ops, limit=n)
    new = apply_ops(spec, ops + [o for s in sugg for o in s["ops"]])
    return new, ops, sugg


def format_suggestions(sugg: list[dict]) -> str:
    if not sugg:
        return "no follow-ups needed: the other parts already fit this change"
    return "\n".join(f"  {s['rank']}. {s['reason']}  [critic {s['score_before']:g}→{s['score_after']:g}]  ops: "
                     + ", ".join(f"{o['op']}({', '.join(f'{k}={v}' for k, v in o.items() if k != 'op')})" for o in s["ops"])
                     for s in sugg)
