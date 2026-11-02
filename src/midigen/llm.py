"""Conversational layer: an LLM writes *spec-level* decisions (form, harmony, genre, instrumentation,
edit ops); the deterministic theory engine writes every note.

Reliability loop for each request:
 1. Build a prompt with the current spec, the genre profile, theory rules and the op schema.
 2. Ask for JSON: either {"action":"ops","ops":[...]} or {"action":"spec","spec":{...}}.
 3. Parse + validate (JSON schema, semantic checks, chord parsing). On failure, send the exact errors
    back and ask for a corrected reply (up to `max_retries`).
 4. Render + run the critic. If it reports warnings, send them back and ask for fixing ops; keep the
    revision only if the critic score does not get worse.
 5. With no provider (or if the provider keeps failing) the offline rule-based parser is used.

Provider config (OpenAI-compatible /chat/completions): MIDIGEN_API_KEY, MIDIGEN_BASE_URL, MIDIGEN_MODEL."""
from __future__ import annotations

import copy
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import nl
from .critic import critique, serious
from .genres import available_genres, build_spec, detect_genre, list_genres, profile_summary
from .ops import OpError, apply_ops
from .schema import DRUM_PATTERNS, OP_SCHEMAS, STYLES
from .spec import INSTRUMENTS, SpecError, check, dumps


class ProviderError(RuntimeError):
    pass


class OpenAICompatProvider:
    """Minimal OpenAI-compatible chat client (works with OpenAI, OpenRouter, Together, Groq, Ollama, LM Studio, vLLM...)."""

    def __init__(self, api_key: str | None, base_url: str, model: str, timeout: float = 120, temperature: float = 0.4):
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self.timeout, self.temperature = timeout, temperature
        self._json_mode = True

    @classmethod
    def from_env(cls) -> "OpenAICompatProvider | None":
        key = os.environ.get("MIDIGEN_API_KEY")
        base = os.environ.get("MIDIGEN_BASE_URL")
        model = os.environ.get("MIDIGEN_MODEL")
        if not model or not (key or base):
            return None
        return cls(key, base or "https://api.openai.com/v1", model)

    def chat(self, messages: list[dict]) -> str:
        body = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 400 and self._json_mode and "response_format" in detail:
                self._json_mode = False
                return self.chat(messages)
            raise ProviderError(f"HTTP {e.code} from provider: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ProviderError(f"provider unreachable: {e}") from e
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"unexpected provider response: {str(data)[:300]}") from e


THEORY_RULES = """You are the arranger/composer brain of midigen. You decide SPEC-LEVEL musical structure; a deterministic
music-theory engine renders the notes (voice-led chords, chord-tone-targeted motif-developed melodies, bass locked to the kick,
genre drum grooves, fills, energy-based layering). Never write individual notes.

Harmony rules:
- Prefer roman numerals so harmony survives key changes. Plain numerals use the current mode's degrees ("VI" in A minor = F);
  numerals with b/# are relative to the parallel major ("bVI", "bVII", "bIII"). Lower case = minor, ° = diminished, ø = half-dim.
  Qualities/extensions: 7, maj7, m7 (write ii7), 9, maj9, add9, sus2, sus4, 6. Secondary dominants: "V7/vi", "V/V". Slash bass: "I/III" or "C/E".
- Functional flow: tonic (I, vi, iii) -> predominant (ii, IV) -> dominant (V, vii°) -> tonic. Borrowed chords (iv, bVI, bVII in major)
  add colour/sadness; secondary dominants add pull. End choruses/outros with authentic (V-I) or plagal (IV-I) cadences, verses/builds
  with half cadences (-> V); a deceptive cadence (V-vi) extends a phrase. Set section "cadence" to the intended ending.
- Match harmonic rhythm to genre (beats_per_chord); keep chord count/bars consistent (chords cycle within a section).
Arrangement rules:
- Energy (0-1) per section drives layering (tracks drop out below their genre threshold), drum density, fills and crashes.
  Build contrast: intro low, verse mid, chorus/drop high, breakdown low, outro falling.
- Keep registers separate: bass < chords/pad < lead; arps above chords. Use track "sections" lists to layer instruments.
- Drums stay on channel 9 (set automatically). Max 16 tracks.
Reply ONLY with a JSON object, no prose outside it."""


def _ops_doc() -> list[dict]:
    out = []
    for name, sch in OP_SCHEMAS.items():
        props = {k: v.get("enum", v.get("type", "object")) for k, v in sch["properties"].items() if k != "op"}
        out.append({"op": name, "params": props, "required": [r for r in sch["required"] if r != "op"], "about": sch["description"]})
    return out


def _vocab() -> dict:
    return {"genres": available_genres(), "drum_patterns": DRUM_PATTERNS, "styles": STYLES, "instruments": sorted(INSTRUMENTS),
            "roles": ["drums", "bass", "chords", "pad", "arp", "lead", "other"],
            "generator_params": ["type", "style", "pattern", "density 0-1", "octave", "velocity", "seed", "fill (bool)",
                                 "swing", "rate (1/4,1/8,1/16,1/8t,1/16t)", "gate", "range [lo,hi] midi", "humanize{timing,velocity}"]}


@dataclass
class Result:
    spec: dict
    ops: list = field(default_factory=list)
    explanation: str = ""
    report: dict | None = None
    source: str = "offline"     # "llm" | "offline" | "offline-fallback"
    attempts: int = 0
    log: list = field(default_factory=list)


def _extract_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("reply contained no JSON object")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def refine(spec: dict, rounds: int = 4) -> tuple[dict, dict]:
    """Deterministic critic loop (no LLM): reseed tracks the critic complains about; keep the best-scoring version."""
    best, best_rep = spec, critique(spec)
    for r in range(rounds):
        bad_tracks = sorted({f["track"] for f in serious(best_rep) if f.get("track")})
        if not bad_tracks and not serious(best_rep):
            break
        ops = [{"op": "reseed", "track": t} for t in bad_tracks] or [{"op": "reseed"}]
        try:
            cand = apply_ops(best, ops)
        except OpError:
            break
        rep = critique(cand)
        if rep["score"] > best_rep["score"]:
            best, best_rep = cand, rep
    return best, best_rep


class Composer:
    """High-level conversational API: generate(prompt) and edit(spec, instruction)."""

    def __init__(self, provider=None, use_env: bool = True, max_retries: int = 3, critic_rounds: int = 1,
                 offline_fallback: bool = True, auto_refine: bool = True):
        self.provider = provider if provider is not None else (OpenAICompatProvider.from_env() if use_env else None)
        self.max_retries, self.critic_rounds = max_retries, critic_rounds
        self.offline_fallback, self.auto_refine = offline_fallback, auto_refine

    # ------------------------------------------------------------ public API
    def generate(self, prompt: str, seed: int = 1) -> Result:
        baseline = nl.prompt_to_spec(prompt, seed=seed)
        if not self.provider:
            return self._finish(Result(baseline, explanation="built offline from genre profile", source="offline"))
        genre = baseline.get("genre")
        task = (f"User request: {prompt!r}\nA theory-built draft for genre {genre!r} is below. Improve it to fit the request: "
                "return {\"action\":\"ops\",\"ops\":[...],\"explanation\":\"...\"} to edit the draft (preferred), or "
                "{\"action\":\"spec\",\"spec\":{...full spec...},\"explanation\":\"...\"} to replace it. If the draft already fits, "
                "return {\"action\":\"ops\",\"ops\":[],\"explanation\":...}.")
        try:
            return self._finish(self._llm_loop(baseline, task, genre))
        except ProviderError as e:
            if not self.offline_fallback:
                raise
            r = Result(baseline, explanation=f"LLM unavailable ({e}); built offline", source="offline-fallback")
            return self._finish(r)

    def edit(self, spec: dict, instruction: str) -> Result:
        spec = check(spec)
        offline_err = None
        if not self.provider:
            ops = nl.edit_to_ops(spec, instruction)  # raises NotUnderstood
            return self._finish(Result(apply_ops(spec, ops), ops=ops, explanation="offline parser", source="offline"),
                                refine_ok=False)
        task = (f"User instruction: {instruction!r}\nReturn {{\"action\":\"ops\",\"ops\":[...],\"explanation\":\"...\"}} "
                "with the smallest set of ops that does it (preferred), or a full replacement spec with action \"spec\".")
        genre = spec.get("genre") or detect_genre(instruction)
        try:
            return self._finish(self._llm_loop(spec, task, genre), refine_ok=False)
        except (ProviderError, OpError) as e:
            offline_err = e
        if self.offline_fallback:
            ops = nl.edit_to_ops(spec, instruction)
            return self._finish(Result(apply_ops(spec, ops), ops=ops, source="offline-fallback",
                                       explanation=f"LLM failed ({offline_err}); used offline parser"), refine_ok=False)
        raise offline_err

    # ------------------------------------------------------------ internals
    def _finish(self, r: Result, refine_ok: bool = True) -> Result:
        if self.auto_refine and refine_ok:
            r.spec, r.report = refine(r.spec)
        else:
            r.report = critique(r.spec)
        return r

    def _messages(self, spec: dict, task: str, genre: str | None) -> list[dict]:
        ctx = {"vocabulary": _vocab(), "ops": _ops_doc()}
        if genre:
            try:
                ctx["genre_profile"] = profile_summary(genre)
            except KeyError:
                pass
        return [
            {"role": "system", "content": THEORY_RULES},
            {"role": "user", "content": "Reference (vocabulary, op catalogue, genre profile):\n" + json.dumps(ctx, separators=(",", ":"))
             + "\n\nCurrent spec:\n" + dumps(spec) + "\n\n" + task},
        ]

    def _parse(self, spec: dict, reply: str) -> tuple[dict, list, str]:
        obj = _extract_json(reply)
        if isinstance(obj, list):
            obj = {"action": "ops", "ops": obj}
        action = obj.get("action") or ("spec" if "spec" in obj else "ops")
        expl = str(obj.get("explanation", ""))
        if action == "spec":
            new = obj.get("spec")
            if not isinstance(new, dict):
                raise OpError(["action 'spec' requires a 'spec' object"])
            try:
                return check(new), [], expl
            except SpecError as e:
                raise OpError(e.errors)
        ops = obj.get("ops", [])
        return apply_ops(spec, ops), ops, expl

    def _llm_loop(self, spec: dict, task: str, genre: str | None) -> Result:
        msgs = self._messages(spec, task, genre)
        log, attempts = [], 0
        result = None
        for attempt in range(self.max_retries + 1):
            attempts += 1
            reply = self.provider.chat(msgs)
            try:
                new, ops, expl = self._parse(spec, reply)
                result = Result(new, ops=ops, explanation=expl, source="llm")
                break
            except (OpError, ValueError, json.JSONDecodeError) as e:
                errs = e.errors if isinstance(e, OpError) else [str(e)]
                log.append({"attempt": attempts, "errors": errs})
                msgs += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": "Your reply was rejected by the validator:\n- " + "\n- ".join(errs[:20])
                          + "\nFix these problems and reply with corrected JSON only."}]
        if result is None:
            raise OpError([f"LLM did not produce a valid reply after {attempts} attempts"] + (log[-1]["errors"] if log else []))
        # critic feedback loop
        report = critique(result.spec)
        for _ in range(self.critic_rounds):
            issues = serious(report)
            if not issues:
                break
            msgs += [{"role": "assistant", "content": json.dumps({"action": "ops", "ops": result.ops, "explanation": result.explanation})},
                     {"role": "user", "content": "The rule-based critic rendered your result and found:\n"
                      + json.dumps(issues, indent=1) + f"\nScore {report['score']}/100. Reply with {{\"action\":\"ops\",\"ops\":[...]}} "
                      "that fix these issues on top of your result (spec-level changes only: ranges, density, styles, chords, "
                      "sections). Reply with an empty ops list if they are intentional."}]
            attempts += 1
            try:
                new, ops, expl = self._parse(result.spec, self.provider.chat(msgs))
            except (OpError, ValueError, json.JSONDecodeError) as e:
                log.append({"critic_revision_rejected": str(e)})
                break
            rep2 = critique(new)
            log.append({"critic_revision": ops, "score_before": report["score"], "score_after": rep2["score"]})
            if rep2["score"] >= report["score"]:
                result = Result(new, ops=result.ops + ops, explanation=(result.explanation + " " + expl).strip(), source="llm")
                report = rep2
        result.attempts, result.log, result.report = attempts, log, report
        return result
