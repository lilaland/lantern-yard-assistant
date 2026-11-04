"""Onboarding: resolve the data's open questions with the manager and record the answers as beliefs.

``--defaults`` records the documented defaults without asking. The interactive path asks at most six
questions derived from the current findings; the model may word the lead-in, the code owns the
options, the defaults, and every ledger row.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

from .. import detectors, llm, onboarding, policies, render, tools
from ..api_client import ApiClient, ServerUnavailable
from ..config import Config
from ..ledger import Ledger
from ..records import load_all, with_clock

def wording_schema(ids: list[str]) -> dict:
    """Structured-output schema for the question lead-ins: one string per question id, nothing else.
    (The API rejects additionalProperties given as a schema, so the ids are listed explicitly.)"""
    return {"type": "object", "properties": {i: {"type": "string"} for i in ids}, "required": list(ids),
            "additionalProperties": False}


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_defaults(cfg: Config) -> int:
    records = None
    try:
        records = load_all(ApiClient(cfg))
    except ServerUnavailable:
        print(f"API at {cfg.api_base} not reachable: recording the human defaults only; evidence assertions "
              f"will be recorded on the first brief.", file=sys.stderr)
    with Ledger(cfg.db_path) as ledger:
        already = ledger.meta("onboarded_at")
        outcomes = policies.write_defaults(ledger, records)
        stamp = _stamp()
        ledger.set_meta("onboarded_at", already or stamp)
        ledger.set_meta("onboarded_with", "defaults")
        if records is not None:
            ledger.set_meta("onboarded_at_revision", str(records.revision))
    by_action: dict[str, int] = {}
    for o in outcomes:
        by_action[o.action] = by_action.get(o.action, 0) + 1
    head = ("Defaults re-applied" if already else "Onboarded with defaults") + \
           f" at {stamp[:16]}: " + ", ".join(f"{n} {a}" for a, n in sorted(by_action.items()))
    print(head)
    print("These are the beliefs now in force. Change any of them with `ask` (e.g. \"use two days of buffer\").")
    for o in outcomes:
        print(render._belief_line(o.belief) + ("" if o.action in ("added", "unchanged") else f"  [{o.action}]"))
    return 1 if records is None else 0


# ----- interactive ---------------------------------------------------------------------------

def wording(cfg: Config, plan: list[onboarding.Question], ctx: tools.ToolContext) -> dict[str, str]:
    """Ask the model for a lead-in per question. Any failure means plain deterministic wording."""
    if cfg.dry:
        return {}
    payload = [{"id": q.id, "title": q.title, "why": q.why, "evidence": q.evidence,
                "options": [o.label for o in q.options]} for q in plan]
    try:
        res = llm.run(cfg, "onboard", "Questions:\n" + json.dumps(payload, indent=1) + "\n\nReturn the JSON object.",
                      ctx, output_format={"type": "json_schema", "schema": wording_schema([q.id for q in plan])}, effort="low")
        llm.report(cfg, res)
        data = json.loads(res.text)
        return {k: v for k, v in data.items() if isinstance(v, str) and k in {q.id for q in plan}}
    except llm.ModelUnavailable as e:
        print(f"[model unavailable: {e}; asking in plain wording]", file=sys.stderr)
    except (ValueError, TypeError) as e:
        print(f"[model wording unusable ({type(e).__name__}); asking in plain wording]", file=sys.stderr)
    return {}


def show_question(n: int, total: int, q: onboarding.Question, lead: str | None) -> None:
    print(f"\n[{n}/{total}] {q.title}")
    print(lead or q.why)
    if lead:
        print(f"  (facts: {q.why})")
    for e in q.evidence:
        print(f"  - {e}")
    print()
    for i, o in enumerate(q.options, 1):
        mark = " (default)" if o.key == q.default else ""
        print(f"  {i}. {o.label}{mark}")
    if q.custom:
        print(f"  {q.custom['prompt']}")
    if q.id == "ordering":
        for key, subject, vkey, _, desc in onboarding.ORDERING_FIELDS:
            print(f"     {key}: {desc}")


def read_answer(q: onboarding.Question, provided: Any) -> Any:
    """Turn a typed line or a provided --answers value into an option key, number or dict."""
    if provided is not None:
        if q.id == "ordering" and isinstance(provided, dict):
            return provided
        if q.custom and not isinstance(provided, str):
            return provided
        if q.custom and isinstance(provided, str) and provided.replace(".", "", 1).isdigit():
            return q.custom["cast"](provided)
        return provided
    while True:
        raw = input(f"Answer [1-{len(q.options)}, Enter = default]: ").strip()
        if not raw:
            return q.default
        if raw.isdigit() and 1 <= int(raw) <= len(q.options):
            key = q.options[int(raw) - 1].key
            if key == "custom" and q.id == "ordering":
                return ask_ordering_values()
            return key
        if q.custom:
            try:
                return q.custom["cast"](raw)
            except ValueError:
                pass
        if q.option(raw):
            return raw
        print("  Please pick a number from the list" + (", or type a value." if q.custom else "."))


def ask_ordering_values() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, subject, vkey, cast, desc in onboarding.ORDERING_FIELDS:
        default = next(v for _, s, _, v in policies.DEFAULTS if s == subject)[vkey]
        while True:
            raw = input(f"  {desc} [{default}]: ").strip()
            try:
                values[key] = cast(raw) if raw else default
                onboarding.ordering_beliefs(values)  # validates ranges as we go
                break
            except ValueError as e:
                print(f"  {e}")
    return values


def run_interactive(cfg: Config, answers_text: str | None = None, again: bool = False) -> int:
    try:
        records = load_all(ApiClient(cfg))
    except ServerUnavailable:
        print(f"Cannot reach the operational API at {cfg.api_base}; onboarding needs the current records.", file=sys.stderr)
        return 1
    provided = onboarding.parse_answers(answers_text) if answers_text else None
    with Ledger(cfg.db_path) as ledger:
        records = with_clock(records, ledger.clock_days())
        params = policies.detector_params(ledger)
        findings = detectors.run_all(records, params)
        plan = onboarding.question_plan(records, findings, ledger, again)
        already = ledger.meta("onboarded_at")
        if already:
            counts = ledger.counts()
            print(f"Onboarded {already[:16]}: {counts['policy']['active']} policies, {counts['preference']['active']} "
                  f"preferences, {counts['assertion']['active']} assertions in force (`memory` lists them).")
        if not plan:
            print("Nothing new to ask: every question for the current findings has an answer. "
                  "Use `onboard --again` to revisit them, or `ask` to change one belief.")
            return 0
        print(f"{len(plan)} question(s) from the current findings at dataset revision {records.revision}. "
              f"Enter accepts the default; 'unknown' keeps the assistant checking.")
        ctx = tools.ToolContext(records, ledger, "onboard", params, findings)
        leads = wording(cfg, plan, ctx)
        for n, q in enumerate(plan, 1):
            show_question(n, len(plan), q, leads.get(q.id))
            answer = provided.get(q.id, q.default) if provided is not None else None
            while True:
                try:
                    chosen = read_answer(q, answer)
                    opt, outcomes = onboarding.apply_answer(ledger, q, chosen, records.revision)
                    break
                except ValueError as e:
                    if provided is not None:
                        print(f"  --answers value for {q.id} rejected: {e}", file=sys.stderr)
                        return 2
                    print(f"  {e}")
                    answer = None
            print(f"  -> {opt.label}")
            for o in outcomes:
                print("     recorded " + render._belief_line(o.belief).strip() + ("" if o.action == "added" else f"  [{o.action}]"))
            if opt.consequence:
                print(f"     {opt.consequence}")
        ledger.set_meta("onboarded_at", already or _stamp())
        ledger.set_meta("onboarded_with", "interactive")
        ledger.set_meta("onboarded_at_revision", str(records.revision))
        counts = ledger.counts()
    print(f"\nOnboarding complete: {counts['policy']['active']} policies, {counts['preference']['active']} preferences, "
          f"{counts['assertion']['active']} assertions in force. `brief` applies them; `why <subject>` explains one.")
    return 0
