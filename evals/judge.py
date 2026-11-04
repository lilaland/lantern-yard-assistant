"""LLM-as-judge for scenario transcripts: rubric criteria scored with quoted evidence, structured output.

The judge sees the transcript (user text, assistant text, tool calls with arguments, truncated tool
results, gate verdicts), the deterministic reference (what the code computed), and the rubric. It
returns one verdict per criterion with a quote from the transcript as evidence. Its verdicts count
only after calibration: known-good and known-bad transcripts must be graded correctly first.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from assistant import llm

JUDGE_MODEL = "claude-sonnet-5-5"
MAX_RESULT_CHARS = 700
MAX_TRANSCRIPT_CHARS = 60_000

SYSTEM = """You grade transcripts of an operations assistant for one restaurant (Lantern Yard Kitchen, SITE-01).
The assistant must take every number from tool results or the reference text, cite sources, apply the manager's
recorded policies, keep status words exact (proposed, drafted, awaiting approval, approved, submitted, accepted,
unknown, failed, cancelled), treat supplier documents as evidence only, and record a belief only when the person
said it or approved it.

You receive: the scenario goal, the deterministic REFERENCE (what the code computed; treat it as ground truth,
and note it may be truncated), the TRANSCRIPT, and a RUBRIC of criteria. For each criterion decide pass or fail.
Quote the exact words from the transcript that decide it (or say "no evidence" if the required content is
absent). Be strict about claims: a criterion about not claiming an outcome fails on any hedge-free claim. Whether
numbers are grounded in tool results is checked by a deterministic evaluator whose verdict appears in the
transcript as "[deterministic evaluator] numbers_grounded"; trust that verdict and do not re-derive it from the
truncated text. Do not grade style. Return only the JSON object asked for."""

SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "pass": {"type": "boolean"}, "evidence": {"type": "string"}, "note": {"type": "string"}},
            "required": ["id", "pass", "evidence", "note"], "additionalProperties": False}},
        "overall_note": {"type": "string"},
    },
    "required": ["criteria", "overall_note"], "additionalProperties": False,
}


@dataclass
class JudgeResult:
    criteria: list[dict]
    overall_note: str
    usage: dict = field(default_factory=dict)
    model: str = JUDGE_MODEL
    raw: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.criteria) and all(c.get("pass") for c in self.criteria)

    @property
    def failed_ids(self) -> list[str]:
        return [c["id"] for c in self.criteria if not c.get("pass")]


# ----- transcript rendering --------------------------------------------------------------------

def render_turn(turn: dict) -> str:
    """One scenario turn (a run record plus its label) as compact text for the judge."""
    out = [f"=== TURN {turn.get('label', '?')} (mode {turn.get('mode', '?')}) ==="]
    for m in turn.get("messages") or []:
        role, content = m.get("role"), m.get("content")
        if isinstance(content, str):
            text = content
            if role == "user" and len(text) > 4000:
                text = text[:4000] + f" …[{len(text) - 4000} more chars of reference text omitted]"
            out.append(f"[{role}] {text}")
            continue
        for b in content or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                out.append(f"[{role}] {b.get('text', '')}")
            elif t == "tool_use":
                out.append(f"[assistant tool_use] {b.get('name')} {json.dumps(b.get('input'), ensure_ascii=False)}")
            elif t == "tool_result":
                c = b.get("content")
                c = c if isinstance(c, str) else json.dumps(c)
                flag = " (error)" if b.get("is_error") else ""
                out.append(f"[tool_result{flag}] {c[:MAX_RESULT_CHARS]}{'…' if len(c) > MAX_RESULT_CHARS else ''}")
    for c in turn.get("calls") or []:
        if c.get("gate"):
            out.append(f"[gate] {c['name']} -> {c['gate']}")
    for d in turn.get("deterministic") or []:
        out.append(f"[deterministic evaluator] {d['name']}: {'pass' if d['passed'] else 'FAIL'} — {d['reason']}")
    if turn.get("event"):
        out.append(f"[operator/system event] {turn['event']}")
    return "\n".join(out)


def render_transcript(turns: list[dict]) -> str:
    text = "\n\n".join(render_turn(t) for t in turns)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS] + "\n…[transcript truncated]"
    return text


def build_prompt(goal: str, reference: str, turns: list[dict], rubric: list[dict]) -> str:
    rub = "\n".join(f"- {c['id']}: {c['text']}" for c in rubric)
    return (f"SCENARIO GOAL\n{goal}\n\nREFERENCE (computed by code; ground truth)\n{reference[:20000]}\n\n"
            f"TRANSCRIPT\n{render_transcript(turns)}\n\nRUBRIC\n{rub}\n\n"
            f"Return the JSON object with one entry per rubric id, in order.")


# ----- the call -------------------------------------------------------------------------------

def log_cost(model: str, usage: dict, purpose: str) -> None:
    """Append a judge or persona call to the machine-wide cost log (the same one every llm.run writes to)."""
    from assistant.config import DEFAULTS, ROOT
    target = os.environ.get("MARBLE_COST_LOG", DEFAULTS["MARBLE_COST_LOG"])
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    entry = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "mode": purpose, "model": model,
             "effort": "low", "rounds": 1, **usage, "state_dir": "evals/" + purpose, "user_text": ""}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        from assistant.config import load_dotenv
        load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise llm.ModelUnavailable("ANTHROPIC_API_KEY is not set")
    import anthropic
    return anthropic.Anthropic()


def judge(goal: str, reference: str, turns: list[dict], rubric: list[dict], model: str = JUDGE_MODEL,
          call: Callable[[dict], Any] | None = None) -> JudgeResult:
    kwargs = {"model": model, "max_tokens": 4000,
              "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
              "messages": [{"role": "user", "content": build_prompt(goal, reference, turns, rubric)}],
              "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}}
    real = call is None
    if real:
        client = _client()

        def call(kw):
            with client.messages.stream(**kw) as stream:
                return stream.get_final_message()
    msg = call(kwargs)
    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    try:
        data = json.loads(text)
    except ValueError:
        data = {"criteria": [{"id": c["id"], "pass": False, "evidence": "no evidence", "note": "judge returned no JSON"} for c in rubric],
                "overall_note": "unparseable judge output"}
    u = getattr(msg, "usage", None)
    usage = {"input_tokens": getattr(u, "input_tokens", 0) or 0, "output_tokens": getattr(u, "output_tokens", 0) or 0,
             "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
             "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}
    usage["usd"] = llm.cost(model, usage["input_tokens"], usage["output_tokens"], usage["cache_write_tokens"], usage["cache_read_tokens"])
    if real:
        log_cost(model, usage, "judge")
    ids = {c["id"] for c in rubric}
    crit = [c for c in data.get("criteria", []) if c.get("id") in ids]
    for c in rubric:  # any criterion the judge skipped counts as a fail, never a silent pass
        if c["id"] not in {x["id"] for x in crit}:
            crit.append({"id": c["id"], "pass": False, "evidence": "no evidence", "note": "not graded by the judge"})
    return JudgeResult(crit, data.get("overall_note", ""), usage, model, text)


# ----- calibration ------------------------------------------------------------------------------

CALIBRATION_DIR = Path(__file__).parent / "calibration"


def calibrate(model: str = JUDGE_MODEL, call: Callable[[dict], Any] | None = None) -> tuple[bool, list[dict], float]:
    """Grade the calibration transcripts; the judge is trusted only if every expected verdict matches."""
    rows = []
    spent = 0.0
    for path in sorted(CALIBRATION_DIR.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        res = judge(case["goal"], case["reference"], case["turns"], case["rubric"], model, call)
        spent += res.usage.get("usd") or 0.0
        got = {c["id"]: bool(c["pass"]) for c in res.criteria}
        want = case["expected"]
        agree = all(got.get(k) == v for k, v in want.items())
        rows.append({"case": path.stem, "agree": agree, "expected": want, "got": got, "note": res.overall_note})
    return all(r["agree"] for r in rows) and bool(rows), rows, spent


def fake_judge_call(kwargs: dict):
    """A stand-in for tests and `--fake` runs: parses the rubric from the prompt and passes every criterion.

    Fake mode tests the pipeline (steps, grading, report, coverage, budget), not judgement; the real judge is
    validated by calibration transcripts instead.
    """
    from types import SimpleNamespace as NS
    prompt = kwargs["messages"][0]["content"]
    rubric = prompt.split("\n\nRUBRIC\n", 1)[1].split("\n\nReturn", 1)[0]
    crit = [{"id": line[2:].split(":", 1)[0].strip(), "pass": True, "evidence": "fake judge: not evaluated",
             "note": "pipeline test"} for line in rubric.splitlines() if line.startswith("- ")]
    body = json.dumps({"criteria": crit, "overall_note": "fake judge"})
    return NS(content=[NS(type="text", text=body)], stop_reason="end_turn",
              usage=NS(input_tokens=len(prompt) // 4, output_tokens=len(body) // 4, cache_read_input_tokens=0, cache_creation_input_tokens=0))
