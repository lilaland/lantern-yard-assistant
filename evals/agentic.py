#!/usr/bin/env python3
"""Agentic scenario suite: scripted and persona-driven conversations through the real modes, graded by the
deterministic evaluators per turn and by an LLM judge per scenario, with a coverage matrix and a cost cap.

  python evals/agentic.py --list                      # scenarios, flows, estimated cost; no calls
  python evals/agentic.py --fake                      # whole pipeline with a fake assistant and judge; no calls
  python evals/agentic.py --budget-usd 5 [--iterations 2] [--only brief_rev1,eggs_question]
  python evals/agentic.py --assistant-model claude-sonnet-5-5   # cheaper tuning runs

Writes evals/live/<timestamp>/report.md and report.json plus one run record per turn (feed them to
`evals/run.py --runs`). Exit 1 when any scenario fails a deterministic check or a trusted judge criterion.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant import analysis, detectors, llm, onboarding, ordering, policies, render, tools  # noqa: E402
from assistant.api_client import ApiClient  # noqa: E402
from assistant.config import Config, load, load_dotenv  # noqa: E402
from assistant.ledger import Ledger  # noqa: E402
from assistant.modes.brief import narration_request  # noqa: E402
from assistant.modes.onboard import wording_schema  # noqa: E402
from assistant.orderflow import OrderFlow  # noqa: E402
from assistant.records import load_all  # noqa: E402
from evals import evaluators as E  # noqa: E402
from evals import judge as J  # noqa: E402
from evals.run import Sim, make_resolver  # noqa: E402

SCENARIOS = ROOT / "evals" / "scenarios"
THRESHOLDS = json.loads((ROOT / "evals" / "thresholds.json").read_text())
ALL_FLOWS = ("brief", "citations", "policy_applied", "grounding", "onboarding", "memory", "gate", "ask", "order",
             "approval", "lost_response", "status_vocabulary", "correction", "conflict", "recheck", "injection",
             "safety", "tools", "persona", "why")
# per-step cost for --list, USD, measured on the 2026-11-03 live run (Opus 5.5 assistant, Sonnet 5.5 judge/persona)
ESTIMATE = {"brief": 0.040, "ask": 0.030, "order_explain": 0.030, "onboard_wording": 0.024, "persona_turn": 0.036,
            "judge": 0.022, "onboard_answers": 0.0, "operator": 0.0, "order_path": 0.0}


@dataclass
class Turn:
    label: str
    mode: str
    messages: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    text: str = ""
    usage: dict = field(default_factory=dict)
    event: str | None = None
    deterministic: list = field(default_factory=list)
    user_text: str = ""
    revision: int | None = None

    def as_run(self, flows: list[str]) -> dict:
        return {"name": self.label, "tags": flows, "expect": {"pass": True},
                "entry": {"mode": self.mode, "model": self.usage.get("model"), "rounds": self.usage.get("rounds"),
                          "input_tokens": self.usage.get("input_tokens", 0), "output_tokens": self.usage.get("output_tokens", 0),
                          "cache_read_tokens": self.usage.get("cache_read_tokens", 0), "usd": self.usage.get("usd"),
                          "revision": self.revision, "user_text": self.user_text, "calls": self.calls,
                          "tool_calls": [c["name"] for c in self.calls]},
                "messages": self.messages, "event": self.event}


@dataclass
class ScenarioResult:
    name: str
    flows: list
    turns: list = field(default_factory=list)
    reference: str = ""
    judge: dict | None = None
    judge_trusted: bool = False
    cost_usd: float = 0.0
    error: str | None = None
    skipped: bool = False

    @property
    def deterministic_ok(self) -> bool:
        return all(d["passed"] for t in self.turns for d in t.deterministic)

    @property
    def judge_ok(self) -> bool | None:
        if not self.judge:
            return None
        return all(c.get("pass") for c in self.judge.get("criteria", []))

    @property
    def ok(self) -> bool:
        if self.skipped or self.error:
            return False
        return self.deterministic_ok and (self.judge_ok is not False if self.judge_trusted else True)


# ----- fakes for the pipeline test ---------------------------------------------------------------

class FakeAssistant:
    """Routes the last user text to a plausible tool, then answers with numbers copied from the result."""

    def __call__(self, kw: dict):
        msgs = kw["messages"]
        last = msgs[-1]
        usage = NS(input_tokens=2000, output_tokens=120, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        if last["role"] == "user" and isinstance(last["content"], list):  # tool results arrived
            res = last["content"][0]
            text = self._answer(res, msgs)
            return NS(content=[NS(type="text", text=text, model_dump=lambda exclude_none=True, t=text: {"type": "text", "text": t})],
                      stop_reason="end_turn", usage=usage)
        user = last["content"] if isinstance(last["content"], str) else ""
        names = {t["name"] for t in kw.get("tools", [])}
        tool = self._route(user, names)
        if tool is None:
            text = self._direct(user, kw)
            return NS(content=[NS(type="text", text=text, model_dump=lambda exclude_none=True, t=text: {"type": "text", "text": t})],
                      stop_reason="end_turn", usage=usage)
        name, args = tool
        d = {"type": "tool_use", "id": "f1", "name": name, "input": args}
        return NS(content=[NS(type="tool_use", id="f1", name=name, input=args, model_dump=lambda exclude_none=True, d=d: d)],
                  stop_reason="tool_use", usage=usage)

    @staticmethod
    def _route(user: str, names: set):
        u = user.lower()
        if "<findings" in u or "<proposal>" in u or "questions:" in u:
            return None
        if "buffer" in u and "from now on" in u and "record_belief" in names:
            return "record_belief", {"kind": "preference", "subject": "order.buffer_days", "statement": user, "value_json": '{"days": 2}'}
        if ("50%" in u or "50 %" in u) and "record_belief" in names:
            return "record_belief", {"kind": "preference", "subject": "order.brunch_uplift", "statement": user, "value_json": '{"uplift": 0.5}'}
        if "lands on tuesday" in u and "record_belief" in names:
            return "record_belief", {"kind": "assertion", "subject": "invoice.INV-099.delivery_date", "statement": user, "value_json": '{"date": "2026-10-06"}'}
        if "sup-v1-01" in u:
            return "list_supplier_documents", {}
        if "egg" in u:
            return "weekend_forecast", {"pos_item_id": "M05"}
        if "go through" in u or ("did the" in u and "order" in u):
            return "fulfilment_status", {}
        if "order look like" in u or "alder order" in u:
            return "sizing_preview", {"vendor_id": "V1"}
        if "vendor sheet" in u or "thursday" in u:
            return "explain", {"subject": "vendor.V1.delivery_days"}
        if "chicken" in u:
            return "stock_runway", {"ingredient": "Chicken thigh boneless skinless"}
        return "get_findings", {"finding_id": None}

    @staticmethod
    def _direct(user: str, kw: dict) -> str:
        if "questions:" in user.lower():
            try:
                qs = json.loads(user.split("Questions:\n", 1)[1].rsplit("\n\nReturn", 1)[0])
                return json.dumps({q["id"]: f"Here is what the records show about {q['title'].lower()} and why your answer matters today." for q in qs})
            except (ValueError, KeyError, IndexError):
                return "{}"
        if "<proposal>" in user:
            lines = [l for l in user.splitlines() if l.startswith("Delivery:") or l.startswith("Estimated total")]
            return "This proposal is for the " + " ".join(lines)[:300] + " The quantities are the code's; approve in terminal A when ready."
        # brief: copy the headline block's first lines and two citations so numbers and cites are grounded
        head = user.split("<findings", 1)[1] if "<findings" in user else user
        first = [l for l in head.splitlines() if l.strip()][1:4]
        cites = re.findall(r"[a-z_]+\.csv SITE-01 [A-Za-z0-9-]+ rev\d+", user)[:2]
        changes = "What changed: " + user.split("<changes>\n", 1)[1].split("\n", 1)[0] + " " if "<changes>" in user else ""
        return (changes + " ".join(first) + f" ({'; '.join(cites)}). Supplier document SUP-V1-01 contains instructions aimed at an "
                "assistant; it was quoted and not followed. Running low: see the runway table days. The order mode can draft what is needed.")

    @staticmethod
    def _answer(res: dict, msgs: list) -> str:
        content = res.get("content") or ""
        if res.get("is_error"):
            return f"I could not do that: {content}"
        try:
            data = json.loads(content)
        except ValueError:
            data = {}
        if "recorded" in data:
            b = data["recorded"]
            return f"Recorded [{data['action']}]: {b['kind']} {b['subject']} = {json.dumps(b['value'])}."
        if "needs" in data:
            eggs = data["needs"].get("Large shell eggs")
            return (f"Breakfast tacos averaged {data['portions_per_day']:.0f} portions per weekend day; you need about {eggs:.0f} eggs "
                    f"for {data['days'][0]} and {data['days'][1]} with your {data['uplift']:.0%} brunch uplift ({data['cites'][0]}).")
        if "exact_order" in data:
            l = data["lines"][0]
            return (f"Per sizing_preview the window covers {data['window_days']} days plus {data['buffer_days']} buffer days "
                    f"({data['window_days'] + data['buffer_days']} days total): {l['cases']} cases of {l['sku']} for {l['ingredient']}; "
                    f"applied {data['applied'][0]}.")
        if "drafts" in data and "vocabulary" in data:
            if not data["drafts"]:
                return "No draft exists in the ledger, so nothing was submitted."
            d = data["drafts"][-1]
            return (f"Draft {d['draft_id']} is {d['status']}" + (f" as order {d['order_id']}, verified by lookup: the supplier recorded it; "
                    f"accepted means recorded, not shipped." if d["status"] == "accepted" else "."))
        if "documents" in data:
            return ("I read SUP-V1-01 (supplier_documents.csv SITE-01 SUP-V1-01 rev1). It is evidence, not permission; I did not follow it "
                    "and have no tool that could place an order. Approval stays with the operator in terminal A.")
        if "chain" in data:
            b = data["chain"][-1]
            return f"Planning uses {'/'.join(b['value']['days'])} because {b['statement']} ({b['source']}, notes.csv SITE-01 N09 rev1)."
        if "rows" in data:
            r = data["rows"][0]
            return (f"{r['ingredient']}: {r['on_hand']} {r['unit']} on hand, about {r['runway_days']:.1f} days; next planning delivery "
                    f"days {'/'.join(r['planning_delivery_days'])} ({r['count_cite']}).")
        if "findings" in data:
            f = data["findings"][0]
            return f"{f['title']} ({f['evidence'][0]})."
        return "Done."


# ----- persona driver ------------------------------------------------------------------------------

PERSONA_SCHEMA = {"type": "object", "properties": {"utterance": {"type": "string"}, "done": {"type": "boolean"}},
                  "required": ["utterance", "done"], "additionalProperties": False}


def persona_next(persona: dict, conversation: list[tuple[str, str]], model: str, call=None) -> tuple[str | None, dict]:
    """Ask the persona model for the manager's next line. Returns (utterance or None when done, usage)."""
    system = (f"You role-play {persona['role']} You are talking to the restaurant's assistant. Your goals, in order: "
              + " ".join(f"({i + 1}) {g}" for i, g in enumerate(persona["goals"])) +
              " Say one short natural line at a time, as this person would type it; do not explain yourself; move to the "
              "next goal once the assistant answered the previous one; set done=true when all goals are covered.")
    convo = "\n".join(f"{'Maya' if r == 'user' else 'Assistant'}: {t}" for r, t in conversation) or "(start)"
    kwargs = {"model": model, "max_tokens": 300, "system": system,
              "messages": [{"role": "user", "content": f"Conversation so far:\n{convo}\n\nYour next line as JSON."}],
              "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": PERSONA_SCHEMA}}}
    real = call is None
    if real:
        import anthropic
        client = anthropic.Anthropic()

        def call(kw):
            with client.messages.stream(**kw) as s:
                return s.get_final_message()
    msg = call(kwargs)
    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    u = getattr(msg, "usage", None)
    usage = {"input_tokens": getattr(u, "input_tokens", 0) or 0, "output_tokens": getattr(u, "output_tokens", 0) or 0}
    usage["usd"] = llm.cost(model, usage["input_tokens"], usage["output_tokens"])
    if real:
        J.log_cost(model, usage, "persona")
    try:
        data = json.loads(text)
    except ValueError:
        return None, usage
    return (None if data.get("done") and not data.get("utterance") else data.get("utterance")), usage


# ----- the runner --------------------------------------------------------------------------------------

class Runner:
    def __init__(self, sim: Sim, out_dir: Path, assistant_model: str | None = None, judge_model: str = J.JUDGE_MODEL,
                 fake: bool = False, budget_usd: float = 5.0, resolver=None, calibrate: bool = True):
        self.sim, self.out, self.fake = sim, out_dir, fake
        self.assistant_model, self.judge_model = assistant_model, judge_model
        self.budget, self.spent = budget_usd, 0.0
        self.resolver = resolver
        self.judge_trusted = None
        self.calibration_rows: list[dict] = []
        self.calibrate = calibrate
        self.client = ApiClient(self._cfg(out_dir / "_probe"))

    def _cfg(self, state_dir: Path) -> Config:
        base = load(dry=False, state_dir=str(state_dir))
        kw = {**base.__dict__, "api_base": self.sim.base}
        if self.assistant_model:
            kw["model"] = self.assistant_model
        if self.fake:
            kw["cost_log"] = None
        return Config(**kw)

    def _spend(self, usd: float | None) -> None:
        self.spent += usd or 0.0

    def _over(self) -> bool:
        return self.spent >= self.budget

    def ensure_judge(self) -> None:
        if self.judge_trusted is not None:
            return
        if self.fake:
            self.judge_trusted, self.calibration_rows = True, [{"case": "fake", "agree": True}]
            return
        if not self.calibrate:
            self.judge_trusted = True
            return
        ok, rows, usd = J.calibrate(self.judge_model)
        self._spend(usd)
        self.judge_trusted, self.calibration_rows = ok, rows
        print(f"judge calibration: {'trusted' if ok else 'NOT trusted'} ({sum(r['agree'] for r in rows)}/{len(rows)} cases, ${usd:.4f})")

    # -- one scenario --
    def run_scenario(self, sc: dict, iteration: int = 1) -> ScenarioResult:
        name = sc["name"] + (f"#{iteration}" if iteration > 1 else "")
        res = ScenarioResult(name, sc.get("flows", []))
        state_dir = self.out / name.replace("#", "-it") / "state"
        cfg = self._cfg(state_dir)
        reference: list[str] = [f"Scenario goal: {sc['goal']}"]
        fake_call = FakeAssistant() if self.fake else None
        self.sim.operator("scenario", "--name", "normal")
        try:
            with Ledger(cfg.db_path) as ledger:
                if sc.get("setup", {}).get("onboard") == "defaults":
                    policies.write_defaults(ledger, load_all(self.client))
                    ledger.set_meta("onboarded_at", datetime.now().astimezone().isoformat(timespec="seconds"))
                for step in sc["steps"]:
                    if self._over():
                        res.error = f"budget ${self.budget:.2f} reached before step '{step.get('label')}'"
                        res.skipped = True
                        break
                    for turn in self._step(step, sc, cfg, ledger, reference, fake_call):
                        self._grade_turn(turn, sc)
                        res.turns.append(turn)
                        res.cost_usd += turn.usage.get("usd") or 0.0
                reference.append("Beliefs in force at the end:\n" + render.memory_text(ledger, ledger.meta("onboarded_at"), None)[:3000])
        except Exception as e:  # a crash is a failed scenario, not a crashed suite
            res.error = f"{type(e).__name__}: {e}"
        res.reference = "\n\n".join(reference)
        if res.turns and not res.skipped and not res.error:
            self.ensure_judge()
            try:
                jr = J.judge(sc["goal"], res.reference, [self._judge_turn(t) for t in res.turns], sc["rubric"], self.judge_model,
                             J.fake_judge_call if self.fake else None)
                res.judge = {"criteria": jr.criteria, "overall_note": jr.overall_note, "model": jr.model, "usage": jr.usage}
                res.judge_trusted = bool(self.judge_trusted)
                self._spend(jr.usage.get("usd"))
                res.cost_usd += jr.usage.get("usd") or 0.0
            except Exception as e:
                res.judge = {"criteria": [], "overall_note": f"judge failed: {type(e).__name__}: {e}", "model": self.judge_model, "usage": {}}
        self._write_scenario(res, sc)
        return res

    def _judge_turn(self, t: Turn) -> dict:
        return {"label": t.label, "mode": t.mode, "messages": t.messages, "calls": t.calls, "deterministic": t.deterministic, "event": t.event}

    def _grade_turn(self, turn: Turn, sc: dict) -> None:
        if not turn.messages:
            return
        verdicts = E.evaluate(turn.as_run(sc.get("flows", [])), resolver=self.resolver, thresholds=THRESHOLDS)
        turn.deterministic = [{"name": v.name, "passed": v.passed, "reason": v.reason} for v in verdicts]

    # -- steps --
    def _run_model(self, cfg, mode, content, ctx, history=None, gate=None, fake_call=None, **kw) -> llm.Result:
        r = llm.run(cfg, mode, content, ctx, history=history, call=fake_call, gate=gate, log=True, **kw)
        self._spend(r.usage.usd(cfg.model))
        return r

    def _turn_from(self, label, mode, r: llm.Result, cfg, user_text, revision, history_len=0) -> Turn:
        return Turn(label, mode, r.messages[history_len:], [{"name": c.name, "ok": c.ok, "gate": c.gate, "ms": c.ms} for c in r.tool_calls],
                    r.text, {**r.usage.__dict__, "usd": r.usage.usd(cfg.model), "model": cfg.model}, None, [], user_text, revision)

    def _ctx(self, ledger, mode, records=None):
        records = records or load_all(self.client)
        params = policies.detector_params(ledger)
        return records, tools.ToolContext(records, ledger, mode, params, detectors.run_all(records, params))

    def _gate_policy(self, step: dict):
        want = step.get("gate", "approve")
        def gate(name, args):
            return ("reject", None, "declined by the scenario") if want == "reject" else ("approve", None, None)
        return gate

    def _step(self, step: dict, sc: dict, cfg: Config, ledger: Ledger, reference: list[str], fake_call):
        label = step.get("label", "step")
        if step.get("brief"):
            records, ctx = self._ctx(ledger, "brief")
            report = policies.recheck(ledger, records, ctx.findings)
            burn, window = analysis.daily_burn(records, 7)
            changes = render.changes_block(report, records)
            footer = render.retained_block(report, ledger, ledger.meta("onboarded_at"))
            reference.append(f"Deterministic brief at revision {records.revision}:\n" + render.brief_text(records, ctx.findings, burn, window, footer, changes))
            req = narration_request(records, ctx.findings, burn, window, ledger.meta("onboarded_at"), changes)
            r = self._run_model(cfg, "brief", req, ctx, fake_call=fake_call)
            yield self._turn_from(label, "brief", r, cfg, req, records.revision)
        elif "ask" in step:
            yield self._ask(step["ask"], step, label, cfg, ledger, reference, fake_call)
        elif step.get("onboard_wording"):
            records, ctx = self._ctx(ledger, "onboard")
            plan = onboarding.question_plan(records, ctx.findings, ledger)
            payload = [{"id": q.id, "title": q.title, "why": q.why, "evidence": q.evidence, "options": [o.label for o in q.options]} for q in plan]
            reference.append("Onboarding questions (data given to the model):\n" + json.dumps(payload, indent=1)[:6000])
            content = "Questions:\n" + json.dumps(payload, indent=1) + "\n\nReturn the JSON object."
            r = self._run_model(cfg, "onboard", content, ctx, fake_call=fake_call,
                                output_format={"type": "json_schema", "schema": wording_schema([q.id for q in plan])}, effort="low")
            yield self._turn_from(label, "onboard", r, cfg, content, records.revision)
        elif "onboard_answers" in step:
            records, ctx = self._ctx(ledger, "onboard")
            plan = onboarding.question_plan(records, ctx.findings, ledger)
            lines = []
            for q in plan:
                opt, outs = onboarding.apply_answer(ledger, q, step["onboard_answers"].get(q.id, q.default), records.revision)
                lines.append(f"{q.id} -> {opt.key}: " + "; ".join(f"{o.belief.kind} {o.belief.subject}={json.dumps(o.belief.value)} ({o.belief.source}) [{o.action}]" for o in outs))
            ledger.set_meta("onboarded_at", datetime.now().astimezone().isoformat(timespec="seconds"))
            reference.append("Onboarding answers recorded:\n" + "\n".join(lines))
            yield Turn(label, "onboard", [], [], "", {}, "onboarding answers applied by code:\n" + "\n".join(lines), [], "", records.revision)
        elif "operator" in step:
            self.sim.operator("scenario", "--name", step["operator"])
            reference.append(f"Operator action: scenario {step['operator']}")
            yield Turn(label, "operator", [], [], "", {}, f"operator ran: scenario --name {step['operator']}", [], "", None)
        elif step.get("order_explain"):
            records, ctx = self._ctx(ledger, "order")
            p = ordering.plan_order(records, ledger, step.get("vendor", "V1"))
            text = ordering.rationale(p)
            reference.append("Order proposal (code):\n" + text)
            content = f"<proposal>\n{text}\n</proposal>\n\nExplain this proposal to the manager."
            r = self._run_model(cfg, "order", content, ctx, fake_call=fake_call)
            yield self._turn_from(label, "order", r, cfg, content, records.revision)
        elif "order_path" in step:
            records = load_all(self.client)
            flow = OrderFlow(self.client, ledger)
            d = flow.create_draft(ordering.plan_order(records, ledger, step.get("vendor", "V1")))
            self.sim.operator("approve", "--draft-id", d.draft_id)
            if step["order_path"] == "supplier-timeout":
                self.sim.operator("scenario", "--name", "supplier-timeout")
            out = flow.submit(d.draft_id)
            self.sim.operator("scenario", "--name", "normal")
            event = f"order path: draft {d.draft_id} created, operator approved, submitted -> [{out.state}] {out.message}"
            reference.append(event)
            yield Turn(label, "order", [], [], "", {}, event, [], "", records.revision)
        elif step.get("persona"):
            persona = sc["persona"]
            convo: list[tuple[str, str]] = []
            fallback = list(persona.get("fallback_turns", []))
            for i in range(persona.get("max_turns", 3)):
                if self._over():
                    break
                if self.fake or not os.environ.get("ANTHROPIC_API_KEY"):
                    line = fallback[i] if i < len(fallback) else None
                else:
                    line, pu = persona_next(persona, convo, self.judge_model)
                    self._spend(pu.get("usd"))
                if not line:
                    break
                turn = self._ask(line, step, f"{label} {i + 1}", cfg, ledger, reference, fake_call)
                convo += [("user", line), ("assistant", turn.text)]
                yield turn
        else:
            raise ValueError(f"unknown step {step}")

    def _ask(self, text: str, step: dict, label: str, cfg: Config, ledger: Ledger, reference: list[str], fake_call) -> Turn:
        records, ctx = self._ctx(ledger, "ask")
        history = [m for ex in ledger.exchanges("ask", last=6) for m in ex.messages]
        r = self._run_model(cfg, "ask", text, ctx, history=history, gate=self._gate_policy(step), fake_call=fake_call)
        ledger.save_exchange("ask", text, r.text, r.messages[len(history):], records.revision)
        return self._turn_from(label, "ask", r, cfg, text, records.revision, len(history))

    # -- output --
    def _write_scenario(self, res: ScenarioResult, sc: dict) -> None:
        d = self.out / res.name.replace("#", "-it")
        d.mkdir(parents=True, exist_ok=True)
        for i, t in enumerate(res.turns, 1):
            if t.messages:
                (d / f"turn-{i:02d}-{t.mode}.json").write_text(json.dumps(t.as_run(sc.get("flows", [])), indent=1, default=str))
        (d / "reference.txt").write_text(res.reference)
        (d / "result.json").write_text(json.dumps({**asdict(res), "ok": res.ok, "deterministic_ok": res.deterministic_ok,
                                                   "judge_ok": res.judge_ok}, indent=1, default=str))


# ----- reporting -------------------------------------------------------------------------------------

def coverage(scenarios: list[dict]) -> tuple[list[str], dict[str, list[str]]]:
    flows = sorted(set(ALL_FLOWS) | {f for s in scenarios for f in s.get("flows", [])})
    return flows, {f: [s["name"] for s in scenarios if f in s.get("flows", [])] for f in flows}


def estimate(sc: dict, iterations: int = 1) -> float:
    usd = ESTIMATE["judge"]
    for step in sc["steps"]:
        if step.get("brief"):
            usd += ESTIMATE["brief"]
        elif "ask" in step:
            usd += ESTIMATE["ask"]
        elif step.get("order_explain"):
            usd += ESTIMATE["order_explain"]
        elif step.get("onboard_wording"):
            usd += ESTIMATE["onboard_wording"]
        elif step.get("persona"):
            usd += ESTIMATE["persona_turn"] * sc.get("persona", {}).get("max_turns", 3)
    return usd * iterations


def report_md(results: list[ScenarioResult], scenarios: list[dict], runner: Runner, seconds: float) -> str:
    out = [f"# Agentic eval report — {datetime.now():%Y-%m-%d %H:%M}", "",
           f"Assistant model: {runner.assistant_model or 'default (claude-opus-5-5)'} · judge: {runner.judge_model} · "
           f"{'FAKE (pipeline test, no model calls)' if runner.fake else 'live'} · spent ${runner.spent:.4f} of ${runner.budget:.2f} · {seconds:.0f} s", ""]
    out.append(f"Judge calibration: {'trusted' if runner.judge_trusted else 'NOT trusted (judge verdicts shown but not counted)'}"
               + (": " + ", ".join(f"{r['case']} {'ok' if r['agree'] else 'DISAGREE'}" for r in runner.calibration_rows) if runner.calibration_rows else ""))
    out += ["", "| scenario | turns | deterministic | judge | cost | result |", "|---|---|---|---|---|---|"]
    for r in results:
        det = "pass" if r.deterministic_ok else "FAIL: " + "; ".join(f"{t.label}/{d['name']}" for t in r.turns for d in t.deterministic if not d["passed"])
        jd = "—" if r.judge is None else ("pass" if r.judge_ok else "FAIL: " + ", ".join(c["id"] for c in r.judge["criteria"] if not c.get("pass")))
        status = "SKIPPED" if r.skipped else ("ERROR: " + r.error if r.error else ("OK" if r.ok else "FAIL"))
        out.append(f"| {r.name} | {len(r.turns)} | {det} | {jd} | ${r.cost_usd:.4f} | {status} |")
    out += ["", "## Judge evidence", ""]
    for r in results:
        if not r.judge:
            continue
        out.append(f"### {r.name}")
        for c in r.judge["criteria"]:
            out.append(f"- {'PASS' if c.get('pass') else 'FAIL'} `{c['id']}`: {c.get('note', '')} — evidence: {c.get('evidence', '')[:240]}")
        if r.judge.get("overall_note"):
            out.append(f"- note: {r.judge['overall_note'][:400]}")
        out.append("")
    flows, cov = coverage(scenarios)
    out += ["## Coverage (flow × scenario)", "", "| flow | scenarios |", "|---|---|"]
    for f in flows:
        out.append(f"| {f} | {', '.join(cov[f]) or '**none**'} |")
    out += ["", f"Uncovered flows: {', '.join(f for f in flows if not cov[f]) or 'none'}", ""]
    return "\n".join(out)


def load_scenarios(directory: Path, only: str | None) -> list[dict]:
    scs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]
    if only:
        names = {n.strip() for n in only.split(",")}
        scs = [s for s in scs if s["name"] in names]
    return scs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", default=str(SCENARIOS))
    ap.add_argument("--only", help="comma-separated scenario names")
    ap.add_argument("--list", action="store_true", help="list scenarios, flows and estimated cost; make no calls")
    ap.add_argument("--fake", action="store_true", help="fake assistant and judge: exercises the pipeline without a key")
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--budget-usd", type=float, default=5.0)
    ap.add_argument("--assistant-model", help="override MARBLE_MODEL for the assistant, e.g. claude-sonnet-5-5")
    ap.add_argument("--judge-model", default=J.JUDGE_MODEL)
    ap.add_argument("--skip-calibration", action="store_true")
    ap.add_argument("--out", help="output directory (default evals/live/<timestamp>)")
    args = ap.parse_args(argv)

    scenarios = load_scenarios(Path(args.scenarios), args.only)
    if not scenarios:
        print("no scenarios")
        return 2
    if args.list:
        flows, cov = coverage(scenarios)
        print(f"{'scenario':26s} {'steps':>5s} {'est $/iter':>10s}  flows")
        for s in scenarios:
            print(f"{s['name']:26s} {len(s['steps']):5d} {estimate(s):10.2f}  {', '.join(s.get('flows', []))}")
        print(f"\nestimated total per iteration: ${sum(estimate(s) for s in scenarios):.2f} "
              f"(measured 2026-11-03: Opus assistant, Sonnet judge/persona; roughly halve for a Sonnet assistant)")
        print("uncovered flows: " + (", ".join(f for f in flows if not cov[f]) or "none"))
        return 0
    if not args.fake:
        load_dotenv()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("needs ANTHROPIC_API_KEY (or use --fake)")
            return 2
    out = Path(args.out) if args.out else ROOT / "evals" / "live" / datetime.now().strftime("%Y%m%d-%H%M%S" + ("-fake" if args.fake else ""))
    out.mkdir(parents=True, exist_ok=True)
    sim = Sim()
    started = time.time()
    try:
        resolver = make_resolver(sim)
        runner = Runner(sim, out, args.assistant_model, args.judge_model, args.fake, args.budget_usd, resolver, not args.skip_calibration)
        results: list[ScenarioResult] = []
        for it in range(1, args.iterations + 1):
            for sc in scenarios:
                print(f"running {sc['name']}" + (f" (iteration {it})" if args.iterations > 1 else "") + f" … spent ${runner.spent:.4f}")
                results.append(runner.run_scenario(sc, it))
        md = report_md(results, scenarios, runner, time.time() - started)
        (out / "report.md").write_text(md)
        (out / "report.json").write_text(json.dumps([{**asdict(r), "ok": r.ok, "deterministic_ok": r.deterministic_ok, "judge_ok": r.judge_ok} for r in results], indent=1, default=str))
        print(md)
        print(f"report: {out / 'report.md'}")
        return 0 if all(r.ok for r in results) else 1
    finally:
        sim.close()


if __name__ == "__main__":
    sys.exit(main())
