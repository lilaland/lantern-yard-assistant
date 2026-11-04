"""Local web UI backend: a window onto the same modules the CLI runs. Standard library only.

Every endpoint reads the simulator through the agent token and the ledger through ``Ledger``;
there is no endpoint that proxies the fixture's operator controls or the approve helper, and nothing
here reads the fixture's state directory. Model turns stream as server-sent events; a
``record_belief`` call pauses on a gate until the browser answers, exactly like the CLI's y/N.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import traceback
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .. import analysis, detectors, llm, onboarding, ordering, policies, render, tools
from ..modes import onboard as onboard_mode
from ..api_client import ApiClient, ApiError, ServerUnavailable
from ..config import Config, ROOT
from ..ledger import Ledger
from ..orderflow import OrderFlow, approve_command
from ..records import Records, load_all

STATIC = Path(__file__).parent / "static"
DIAGRAMS = ROOT / "docs" / "diagrams"
GATE_TIMEOUT = 600.0
RECORDS_TTL = 3.0


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Gates:
    """Pending human decisions for gated tool calls, keyed by id; the model turn blocks on one."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pending: dict[str, dict] = {}

    def open(self, card: dict) -> str:
        gid = uuid.uuid4().hex[:10]
        with self.lock:
            self.pending[gid] = {"card": card, "event": threading.Event(), "decision": None}
        return gid

    def wait(self, gid: str, timeout: float = GATE_TIMEOUT) -> tuple:
        g = self.pending[gid]
        g["event"].wait(timeout)
        with self.lock:
            self.pending.pop(gid, None)
        d = g["decision"] or {"decision": "reject", "reason": "no answer within the time limit"}
        return d.get("decision", "reject"), d.get("args"), d.get("reason")

    def answer(self, gid: str, decision: dict) -> bool:
        with self.lock:
            g = self.pending.get(gid)
            if not g:
                return False
            g["decision"] = decision
            g["event"].set()
            return True

    def cards(self) -> list[dict]:
        with self.lock:
            return [{"id": k, **v["card"]} for k, v in self.pending.items()]


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = ApiClient(cfg)
        self.gates = Gates()
        self._records: Records | None = None
        self._records_at = 0.0
        self._lock = threading.Lock()

    # ----- shared state -----------------------------------------------------

    def records(self, fresh: bool = False) -> Records:
        with self._lock:
            if fresh or self._records is None or time.time() - self._records_at > RECORDS_TTL:
                try:
                    self._records = load_all(self.client)
                except ServerUnavailable as e:
                    raise HttpError(503, f"operational API unreachable at {self.cfg.api_base}: {e}")
                self._records_at = time.time()
            return self._records

    def ledger(self) -> Ledger:
        return Ledger(self.cfg.db_path)

    def context(self, mode: str, ledger: Ledger, records: Records | None = None) -> tools.ToolContext:
        records = records or self.records()
        params = policies.detector_params(ledger)
        return tools.ToolContext(records, ledger, mode, params, detectors.run_all(records, params))

    def key_present(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    # ----- read endpoints -----------------------------------------------------

    def state(self) -> dict:
        records = self.records()
        with self.ledger() as led:
            counts = led.counts()
            meta = {k: led.meta(k) for k in ("onboarded_at", "onboarded_with", "last_revision", "last_run_at",
                                             "last_run_wall", "last_headline")}
            pending = [d.__dict__ for d in led.pending_drafts()]
            answers = onboarding.answers(led)
        return {"site": records.site, "revision": records.revision, "now": records.now.isoformat(), "counts": counts,
                "meta": meta, "pending_drafts": pending, "onboarding_answers": answers, "model": self.cfg.model,
                "effort": self.cfg.effort, "dry": self.cfg.dry, "key_present": self.key_present(),
                "state_dir": str(self.cfg.state_dir), "api_base": self.cfg.api_base, "gates": self.gates.cards(),
                "cost": {"this_state_dir_usd": llm.usage_total(self.cfg.usage_log)[0],
                         "all_usd": (llm.usage_total(self.cfg.cost_log)[0] if self.cfg.cost_log else None),
                         "all_runs": (llm.usage_total(self.cfg.cost_log)[1] if self.cfg.cost_log else None)}}

    def findings(self) -> dict:
        records = self.records()
        with self.ledger() as led:
            ctx = self.context("brief", led, records)
            runway = tools.execute(ctx, "stock_runway", {"ingredient": None})
            rec = {}
            for ing in sorted({c.ingredient_name for c in records.counts}):
                rec[ing] = tools.execute(ctx, "reconcile_ingredient", {"ingredient": ing})
            findings = tools.get_findings(ctx, None)["findings"]
            for f in findings:
                f["block"] = render.finding_block(next(x for x in ctx.findings if x.id == f["id"]))
                f["citations"] = render.citation_links(f["block"])
            return {"revision": records.revision, "now": records.now.isoformat(), "findings": findings,
                    "runway": runway, "reconciliation": rec, "params": tools.plain(ctx.params.__dict__)}

    def findings_diff(self) -> dict:
        with self.ledger() as led:
            return {"reports": json.loads(led.meta("last_recheck") or "[]")}

    def dry_brief(self, kind: str = "brief") -> dict:
        """The deterministic brief, running the recheck exactly like the CLI does (``kind`` = brief | onboarding)."""
        records = self.records(fresh=True)
        with self.ledger() as led:
            params = policies.detector_params(led)
            findings = detectors.run_all(records, params)
            report = policies.recheck(led, records, findings, kind)
            burn, window = analysis.daily_burn(records, 7)
            footer = render.retained_block(report, led, led.meta("onboarded_at"))
            changes = render.changes_block(report, records)
            text = render.brief_text(records, findings, burn, window, footer, changes)
            return {"text": text, "footer": footer, "changes": changes, "revision": records.revision,
                    "citations": render.citation_links(text), "report": policies.report_dict(report)}

    def beliefs(self, q: dict) -> dict:
        with self.ledger() as led:
            rows = led.all(include_closed=True)
            kind, status, prefix, search = q.get("kind"), q.get("status"), q.get("source"), (q.get("q") or "").lower()
            out = []
            for b in rows:
                if kind and b.kind != kind or status and b.status != status:
                    continue
                if prefix and not b.source.startswith(prefix):
                    continue
                if search and search not in b.subject.lower() and search not in b.statement.lower():
                    continue
                out.append(b.__dict__)
            return {"beliefs": out, "conflicts": [{"old": o.__dict__, "new": n.__dict__ if n else None} for o, n in led.conflicts()],
                    "counts": led.counts(), "drafts": [d.__dict__ for d in led.drafts()],
                    "answers": onboarding.answers(led), "exchanges": len(led.exchanges("ask"))}

    def exchanges(self) -> dict:
        with self.ledger() as led:
            return {"exchanges": [{"seq": e.seq, "created_at": e.created_at, "question": e.question, "answer": e.answer,
                                   "revision": e.revision, "citations": render.citation_links(e.answer or ""),
                                   "tool_calls": [b.get("name") for m in e.messages if m["role"] == "assistant"
                                                  for b in (m["content"] if isinstance(m["content"], list) else [])
                                                  if isinstance(b, dict) and b.get("type") == "tool_use"]}
                                  for e in led.exchanges("ask")]}

    def why(self, subject: str) -> dict:
        with self.ledger() as led:
            return {"subject": subject, "chain": [b.__dict__ for b in led.why(subject)],
                    "text": render.why_text(subject, led.why(subject))}

    def record_belief(self, body: dict) -> dict:
        """Direct write from the UI (behind its own confirmation), source human:web."""
        for k in ("kind", "subject", "statement", "value"):
            if k not in body:
                raise HttpError(400, f"missing {k}")
        with self.ledger() as led:
            ctx = tools.ToolContext(self.records(), led, "web")
            try:
                return tools.plain(tools.REGISTRY["record_belief"].fn(ctx, kind=body["kind"], subject=body["subject"],
                                                                      statement=body["statement"], value_json=json.dumps(body["value"])))
            except tools.ToolError as e:
                raise HttpError(400, str(e))

    def evidence(self, q: dict) -> dict:
        resource, ref = q.get("resource"), q.get("ref")
        if not resource or not ref:
            raise HttpError(400, "resource and ref are required")
        with self.ledger() as led:
            ctx = tools.ToolContext(self.records(), led, "ask")
            try:
                return tools.evidence(ctx, resource, ref)
            except tools.ToolError as e:
                raise HttpError(404, str(e))

    def tool_matrix(self, q: dict) -> dict:
        mode = q.get("mode") or "ask"
        return {"modes": {m: tools.allowed(m) for m in tools.MODE_SETS}, "sets": {n: t.set for n, t in tools.REGISTRY.items()},
                "mode_sets": {m: list(s) for m, s in tools.MODE_SETS.items()},
                "definitions": tools.definitions(mode) if mode in tools.MODE_SETS else [], "mode": mode,
                "gated": sorted(llm.GATED_TOOLS)}

    def tool_execute(self, body: dict) -> dict:
        mode, name, args = body.get("mode", "ask"), body.get("name"), body.get("args") or {}
        if mode not in tools.MODE_SETS:
            raise HttpError(400, f"unknown mode {mode!r}")
        with self.ledger() as led:
            ctx = self.context(mode, led)
            t0 = time.time()
            try:
                out = tools.execute(ctx, name, args)
            except tools.ToolError as e:
                return {"ok": False, "error": str(e), "ms": int((time.time() - t0) * 1000), "mode": mode, "name": name}
            return {"ok": True, "result": out, "ms": int((time.time() - t0) * 1000), "mode": mode, "name": name,
                    "chars": len(json.dumps(out))}

    def proposal(self, q: dict) -> dict:
        records = self.records()
        vendor = q.get("vendor") or "V1"
        with self.ledger() as led:
            base_prefs = policies.order_prefs(led)
            base_params = policies.detector_params(led)
            overrides: dict[str, Any] = {}
            try:
                if q.get("buffer_days") not in (None, ""):
                    overrides["buffer_days"] = int(q["buffer_days"])
                if q.get("cover_through"):
                    overrides["cover_through"] = q["cover_through"]
                if q.get("uplift") not in (None, ""):
                    overrides["uplift"] = float(q["uplift"])
                if q.get("max_unflagged_usd") not in (None, ""):
                    overrides["max_unflagged_usd"] = float(q["max_unflagged_usd"])
            except ValueError as e:
                raise HttpError(400, f"bad override: {e}")
            prefs = base_prefs
            if overrides:
                applied = dict(base_prefs.applied)
                for k in overrides:
                    applied[k] = f"what-if override ({k}={overrides[k]}), not recorded"
                prefs = policies.OrderPrefs(**{**base_prefs.__dict__, **overrides, "applied": applied})
            params = base_params
            pk: dict[str, Any] = {}
            if q.get("on_hand_rule") in detectors.ON_HAND_RULES:
                pk["on_hand_rule"] = q["on_hand_rule"]
            if q.get("inv099_received") in ("true", "false"):
                inv = q.get("invoice") or "INV-099"
                rec = set(base_params.confirmed_received) - {inv}
                nrec = set(base_params.confirmed_not_received) - {inv}
                (rec if q["inv099_received"] == "true" else nrec).add(inv)
                pk["confirmed_received"], pk["confirmed_not_received"] = frozenset(rec), frozenset(nrec)
            if pk:
                applied = dict(base_params.applied)
                for k, v in pk.items():
                    applied[k if k == "on_hand_rule" else f"received:{q.get('invoice') or 'INV-099'}"] = f"what-if override, not recorded"
                params = detectors.Params(**{**base_params.__dict__, **pk, "applied": applied})
            try:
                p = ordering.plan_order(records, led, vendor, prefs, params)
            except ValueError as e:
                raise HttpError(400, str(e))
            return {"rationale": ordering.rationale(p), "order": p.order, "lines": tools.plain(p.lines),
                    "skipped": p.skipped, "not_counted": p.not_counted, "warnings": p.warnings, "applied": p.applied,
                    "est_total_usd": str(p.est_total_usd) if p.est_total_usd is not None else None, "over_max": p.over_max,
                    "calendar": {"today": str(p.today), "delivery_date": str(p.delivery_date) if p.delivery_date else None,
                                 "following_delivery": str(p.following_delivery) if p.following_delivery else None,
                                 "order_by": p.order_by.isoformat() if p.order_by else None, "window_end": str(p.window_end),
                                 "window_days": p.window_days, "buffer_days": p.buffer_days, "planning_days": p.planning_days},
                    "overrides": {**overrides, **{k: (sorted(v) if isinstance(v, frozenset) else v) for k, v in pk.items()}},
                    "what_if": bool(overrides or pk), "vendor": vendor, "revision": records.revision,
                    "prefs": tools.plain(prefs.__dict__)}

    # ----- client experience -----------------------------------------------------------

    CAPABILITIES = [
        {"id": "brief", "title": "Morning brief", "blurb": "One issue that matters today, with the evidence, what is uncertain and the next step.",
         "samples": ["Give me this morning's brief"]},
        {"id": "ask", "title": "Ask anything about the numbers", "blurb": "Every answer comes from your records with a citation you can open. Tell me a rule and I remember it.",
         "samples": ["How many days of chicken do we have?", "Why do you think Alder delivers on Wednesday?", "Use two days of buffer from now on"]},
        {"id": "order", "title": "Your next order", "blurb": "I size it from counts, recipes and your rules, show every line and why, and wait for your approval before anything is sent.",
         "samples": ["Help me with the next Alder order"]},
    ]

    def client_intro(self) -> dict:
        records = self.records()
        with self.ledger() as led:
            onboarded = led.meta("onboarded_at")
            counts = led.counts()
        return {"name": "Marbly", "site": records.site, "revision": records.revision, "now": records.now.isoformat(),
                "onboarded_at": onboarded, "model": self.cfg.model, "key_present": self.key_present(), "dry": self.cfg.dry,
                "beliefs": {k: counts[k]["active"] for k in ("policy", "preference", "assertion")},
                "greeting": ("Hi, I'm Marbly. I help run Lantern Yard Kitchen from the numbers you already keep: sales, counts, "
                             "invoices, recipes and the shift notebook. I've read everything and found a few places where the "
                             "records disagree. Before I plan anything, let's straighten those out together; your answers "
                             "become rules I keep in Memory and apply every time."),
                "welcome_back": "Welcome back. I've rechecked everything against your rules; here is where things stand.",
                "capabilities": self.CAPABILITIES, "tour": self.tour()}

    def _question_dict(self, q, leads: dict) -> dict:
        d = {"id": q.id, "title": q.title, "why": q.why, "lead": leads.get(q.id), "evidence": q.evidence,
             "evidence_links": [render.citation_links(e) for e in q.evidence], "default": q.default,
             "options": [{"key": o.key, "label": o.label, "consequence": o.consequence} for o in q.options],
             "custom": {"prompt": q.custom["prompt"]} if q.custom else None, "fields": None}
        if q.id == "ordering":
            d["fields"] = [{"key": k, "subject": subj, "value_key": vk, "prompt": desc,
                            "default": next(v for _, s2, _, v in policies.DEFAULTS if s2 == subj)[vk]}
                           for k, subj, vk, _, desc in onboarding.ORDERING_FIELDS]
        return d

    def onboarding_plan(self, q: dict) -> dict:
        records = self.records()
        with self.ledger() as led:
            ctx = self.context("onboard", led, records)
            again = q.get("again") in ("1", "true")
            plan = onboarding.question_plan(records, ctx.findings, led, again)
            leads = {}
            if q.get("wording") in ("1", "true") and plan and not self.cfg.dry and self.key_present():
                leads = onboard_mode.wording(self.cfg, plan, ctx)
            return {"revision": records.revision, "onboarded_at": led.meta("onboarded_at"), "answered": onboarding.answers(led),
                    "questions": [self._question_dict(x, leads) for x in plan], "wording": bool(leads)}

    def onboarding_answer(self, body: dict) -> dict:
        qid, answer = body.get("id"), body.get("answer")
        if not qid or answer is None:
            raise HttpError(400, "id and answer are required")
        records = self.records()
        with self.ledger() as led:
            ctx = self.context("onboard", led, records)
            plan = onboarding.question_plan(records, ctx.findings, led, again=True)
            qobj = next((x for x in plan if x.id == qid), None)
            if qobj is None:
                raise HttpError(404, f"no question {qid} for the current findings")
            try:
                opt, outs = onboarding.apply_answer(led, qobj, answer, records.revision)
            except ValueError as e:
                raise HttpError(400, str(e))
            remaining = [x.id for x in onboarding.question_plan(records, ctx.findings, led)]
            return {"option": {"key": opt.key, "label": opt.label, "consequence": opt.consequence},
                    "recorded": [{"action": o.action, **o.belief.__dict__} for o in outs], "remaining": remaining}

    def onboarding_complete(self) -> dict:
        records = self.records()
        with self.ledger() as led:
            already = led.meta("onboarded_at")
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            led.set_meta("onboarded_at", already or stamp)
            led.set_meta("onboarded_with", "client")
            led.set_meta("onboarded_at_revision", str(records.revision))
            before = {f.id: f for f in detectors.run_all(records)}                              # detector defaults
            after = {f.id: f for f in detectors.run_all(records, policies.detector_params(led))}  # with the answers
            sev = detectors.SEVERITY_LABEL
            effect = {"before": len(before), "after": len(after),
                      "removed": [{"id": i, "title": f.title} for i, f in before.items() if i not in after],
                      "added": [{"id": i, "title": f.title} for i, f in after.items() if i not in before],
                      "severity_changed": [{"id": i, "title": after[i].title, "from": sev[before[i].severity], "to": sev[after[i].severity]}
                                           for i in before if i in after and before[i].severity != after[i].severity],
                      "applied": sorted({a for f in after.values() for a in f.applied_policies}),
                      "critical_after": sum(1 for f in after.values() if f.severity == 1),
                      "headline": after and next(iter(after.values())).title}
            return {"onboarded_at": led.meta("onboarded_at"), "counts": led.counts(), "effect": effect}

    def onboarding_restart(self) -> dict:
        """Forget which questions were answered so they are asked again; beliefs stay (a new answer supersedes)."""
        with self.ledger() as led:
            led.set_meta(onboarding.ANSWERS_KEY, "{}")
            led.db.execute("DELETE FROM meta WHERE key IN ('onboarded_at', 'onboarded_with', 'client.tour')")
            led.db.commit()
            return {"ok": True}

    TOUR_STEPS = ("facts", "alerts", "memory", "order")

    def tour(self) -> dict:
        """The client's four setup steps. 'facts' and 'order' are derived from the ledger; the two click-throughs are flags."""
        with self.ledger() as led:
            flags = json.loads(led.meta("client.tour") or "{}")
            done = {"facts": bool(led.meta("onboarded_at")), "alerts": bool(flags.get("alerts")), "memory": bool(flags.get("memory")),
                    "order": any(d.status == "accepted" for d in led.drafts())}
            current = next((s for s in self.TOUR_STEPS if not done[s]), None)
            return {"steps": list(self.TOUR_STEPS), "done": done, "current": current, "complete": current is None}

    def tour_mark(self, body: dict) -> dict:
        step = body.get("step")
        if step not in ("alerts", "memory"):
            raise HttpError(400, "only the alerts and memory steps are marked by a click; facts and order come from the ledger")
        with self.ledger() as led:
            flags = json.loads(led.meta("client.tour") or "{}")
            flags[step] = True
            led.set_meta("client.tour", json.dumps(flags))
        return self.tour()

    ACTION_CLASSES = {"runway_risk", "status_inconsistent_with_evidence", "weekend_readiness", "sku_substitution", "count_variance"}

    def _alert_action(self, f, records) -> dict | None:
        vendor_id = None
        for c in f.evidence:
            if c.source == "invoice_headers.csv":
                h = records.header(c.ref)
                v = records.vendor_by_account(h.vendor_account) if h else None
                vendor_id = v.vendor_id if v else None
                break
        if vendor_id is None and f.numbers.get("vendor"):
            v = next((x for x in records.vendors if x.vendor_name == f.numbers["vendor"]), None)
            vendor_id = v.vendor_id if v else None
        if vendor_id is None and f.cls in ("weekend_readiness", "sku_substitution"):
            vendor_id = "V1"
        if f.cls in self.ACTION_CLASSES and vendor_id:
            name = next((x.vendor_name for x in records.vendors if x.vendor_id == vendor_id), vendor_id)
            return {"kind": "order", "vendor": vendor_id, "label": f"Plan the next {name.split(',')[0].split()[0]} order"}
        return {"kind": "ask", "label": "Ask Marbly what to do", "prompt": f"What should I do about this: {f.title}?"}

    def alerts(self) -> dict:
        records = self.records()
        with self.ledger() as led:
            ctx = self.context("ask", led, records)
            seen = set(json.loads(led.meta("alerts.seen") or "[]"))
            out = []
            for f in ctx.findings:
                block = render.finding_block(f)
                out.append({"id": f.id, "cls": f.cls, "severity": detectors.SEVERITY_LABEL[f.severity], "title": f.title,
                            "summary": f.summary, "next_step": f.next_step, "uncertainty": f.uncertainty,
                            "evidence": [str(c) for c in f.evidence], "block": block, "citations": render.citation_links(block),
                            "applied": f.applied_policies, "numbers": tools.plain(f.numbers), "seen": f.id in seen,
                            "action": self._alert_action(f, records)})
            counts = {k: sum(1 for a in out if a["severity"] == k) for k in ("HIGH", "MED", "LOW")}
            return {"revision": records.revision, "alerts": out, "counts": counts,
                    "unseen_critical": sum(1 for a in out if a["severity"] == "HIGH" and not a["seen"]),
                    "unseen": sum(1 for a in out if not a["seen"])}

    def alerts_seen(self, body: dict) -> dict:
        ids = body.get("ids") or []
        with self.ledger() as led:
            seen = set(json.loads(led.meta("alerts.seen") or "[]")) | set(ids)
            led.set_meta("alerts.seen", json.dumps(sorted(seen)))
        return {"seen": sorted(seen)}

    # ----- order path -------------------------------------------------------------

    APPROVE_HOW_TO = ("Run this in the operator's terminal: the one that started `python3 simulator.py serve`, from the same "
                      "folder. If the simulator was started with --state, the command includes it when MARBLE_SIM_STATE is "
                      "set; otherwise add the same --state yourself. The operator credential lives only there; this screen "
                      "cannot approve.")

    def create_draft(self, body: dict) -> dict:
        vendor = body.get("vendor") or "V1"
        records = self.records(fresh=True)
        with self.ledger() as led:
            flow = OrderFlow(self.client, led)
            blocked = flow.blocked_vendors()
            if vendor in blocked:
                raise HttpError(409, f"draft {blocked[vendor].draft_id} for {vendor} is {blocked[vendor].status}; settle it first")
            p = ordering.plan_order(records, led, vendor)  # from the ledger's beliefs only, never from what-if overrides
            if body.get("lines"):
                try:
                    lines = ordering.validate_lines(records, vendor, body["lines"])
                except ValueError as e:
                    raise HttpError(400, str(e))
                diff = ordering.lines_diff(p.api_lines, [{"sku": l["sku"], "quantity": l["quantity"]} for l in lines])
                try:
                    d = flow.create_draft_from_lines(vendor, lines, note=f"manager edited the proposal: {'; '.join(diff)}")
                except (RuntimeError, ApiError) as e:
                    raise HttpError(409, str(e))
                return {"draft": d.__dict__, "approve_command": approve_command(d.draft_id, self.cfg.api_base),
                        "how_to": self.APPROVE_HOW_TO, "rationale": ordering.rationale(p), "edited": diff,
                        "lines": lines}
            if not p.lines:
                raise HttpError(400, "nothing to order for this vendor")
            try:
                d = flow.create_draft(p)
            except (RuntimeError, ApiError) as e:
                raise HttpError(409, str(e))
            return {"draft": d.__dict__, "approve_command": approve_command(d.draft_id, self.cfg.api_base),
                    "how_to": self.APPROVE_HOW_TO, "rationale": ordering.rationale(p)}

    REVISE_SCHEMA = {"type": "object", "properties": {
        "lines": {"type": "array", "items": {"type": "object", "properties": {"sku": {"type": "string"}, "quantity": {"type": "integer"}},
                                              "required": ["sku", "quantity"], "additionalProperties": False}},
        "note": {"type": "string"}}, "required": ["lines", "note"], "additionalProperties": False}

    def revise(self, body: dict) -> dict:
        """The manager asks for a change in words; the model proposes revised lines; code validates them."""
        vendor = body.get("vendor") or "V1"
        request = (body.get("request") or "").strip()
        if not request:
            raise HttpError(400, "request is required")
        if self.cfg.dry or not self.key_present():
            raise HttpError(400, "no model available; edit the quantities in the table instead")
        records = self.records()
        with self.ledger() as led:
            p = ordering.plan_order(records, led, vendor)
            current = body.get("lines") or p.api_lines
            catalog = [{"sku": c.sku, "description": c.description, "pack": f"{c.pack_quantity:g} {c.pack_unit}", "unit": c.order_unit,
                        "ingredient": records.ingredient_for_sku(c.sku)} for c in records.catalog if c.vendor_id == vendor]
            content = (f"<proposal>\n{ordering.rationale(p)}\n</proposal>\n\nCurrent lines (what will be drafted): {json.dumps(current)}\n"
                       f"Catalog for this vendor: {json.dumps(catalog)}\n\nThe manager asks: \"{request}\"\n\n"
                       f"Return the full revised line list (every line that should be ordered, quantities as whole cases 1-100, only "
                       f"SKUs from this catalog), keeping every line the manager did not mention, and a one-sentence note saying what "
                       f"changed and anything the manager should know (for example if a request cannot be met from this catalog).")
            ctx = self.context("order", led, records)
            try:
                res = llm.run(self.cfg, "order", content, ctx, output_format={"type": "json_schema", "schema": self.REVISE_SCHEMA}, effort="low")
                data = json.loads(res.text)
            except llm.ModelUnavailable as e:
                raise HttpError(400, str(e))
            except ValueError:
                raise HttpError(502, "the model did not return a valid revision")
            try:
                lines = ordering.validate_lines(records, vendor, data.get("lines") or [])
            except ValueError as e:
                raise HttpError(400, f"the revision was not valid: {e}; attempted lines {data.get('lines')}")
            return {"lines": lines, "note": data.get("note", ""), "diff": ordering.lines_diff(current, [{"sku": l["sku"], "quantity": l["quantity"]} for l in lines]),
                    "usage": {**res.usage.__dict__, "usd": res.usage.usd(self.cfg.model)}}

    def planner(self) -> dict:
        records = self.records()
        with self.ledger() as led:
            return {"revision": records.revision, "now": records.now.isoformat(), "vendors": tools.plain(ordering.planner(records, led))}

    def drafts(self) -> dict:
        with self.ledger() as led:
            return {"drafts": [d.__dict__ for d in led.drafts()]}

    def draft_status(self, draft_id: str) -> dict:
        with self.ledger() as led:
            flow = OrderFlow(self.client, led)
            d = led.draft(draft_id)
            if d is None:
                raise HttpError(404, f"no draft {draft_id} in the ledger")
            status = d.status
            if d.status in ("drafted", "awaiting_approval", "approved"):
                try:
                    status = flow.approval_status(draft_id)
                except ApiError as e:
                    raise HttpError(e.status, e.code)
            return {"draft": led.draft(draft_id).__dict__, "server_status": status,
                    "approve_command": approve_command(draft_id, self.cfg.api_base), "how_to": self.APPROVE_HOW_TO}

    def submit(self, draft_id: str) -> dict:
        with self.ledger() as led:
            flow = OrderFlow(self.client, led)
            d = led.draft(draft_id)
            if d is None:
                raise HttpError(404, f"no draft {draft_id}")
            if d.status in ("drafted", "awaiting_approval"):
                if flow.approval_status(draft_id) != "approved":
                    raise HttpError(409, f"{draft_id} is not approved; the operator approves in terminal A")
            out = flow.submit(draft_id)
            return {"state": out.state, "message": out.message, "order_id": out.order_id, "verified": out.verified,
                    "draft": out.draft.__dict__ if out.draft else None}

    def resume(self) -> dict:
        with self.ledger() as led:
            outs = OrderFlow(self.client, led).reconcile(submit_approved=True)
            return {"outcomes": [{"state": o.state, "message": o.message, "order_id": o.order_id} for o in outs]}

    # ----- memory admin ---------------------------------------------------------------

    def usage(self) -> dict:
        total, runs, unknown = llm.usage_total(self.cfg.usage_log)
        rows = []
        if self.cfg.usage_log.exists():
            for line in self.cfg.usage_log.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        files = sorted(p.name for p in (self.cfg.state_dir / "runs").glob("*.json")) if (self.cfg.state_dir / "runs").exists() else []
        g_total, g_runs, g_unknown = llm.usage_total(self.cfg.cost_log) if self.cfg.cost_log else (0.0, 0, False)
        g_rows = []
        by_dir: dict[str, dict] = {}
        if self.cfg.cost_log and self.cfg.cost_log.exists():
            for line in self.cfg.cost_log.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                g_rows.append(e)
                d = by_dir.setdefault(e.get("state_dir", "?"), {"runs": 0, "usd": 0.0, "input_tokens": 0, "output_tokens": 0})
                d["runs"] += 1
                d["usd"] += e.get("usd") or 0.0
                d["input_tokens"] += e.get("input_tokens") or 0
                d["output_tokens"] += e.get("output_tokens") or 0
        return {"total_usd": total, "runs": runs, "unknown_cost": unknown, "rows": rows, "files": files,
                "prices": llm.PRICES, "global": {"log": str(self.cfg.cost_log) if self.cfg.cost_log else None,
                                                 "total_usd": g_total, "runs": g_runs, "unknown_cost": g_unknown,
                                                 "rows": g_rows[-200:], "by_state_dir": by_dir}}

    def run_file(self, name: str) -> dict:
        if "/" in name or ".." in name or not name.endswith(".json"):
            raise HttpError(400, "bad run name")
        p = self.cfg.state_dir / "runs" / name
        if not p.exists():
            raise HttpError(404, "no such run")
        return json.loads(p.read_text(encoding="utf-8"))

    def reset(self, body: dict) -> dict:
        expected = self.cfg.state_dir.name
        if body.get("confirm") != expected:
            raise HttpError(400, f"type the state directory name ({expected}) to confirm")
        if self.cfg.state_dir.exists():
            shutil.rmtree(self.cfg.state_dir)
        self._records = None
        return {"deleted": str(self.cfg.state_dir), "note": "the simulator's own state and orders are untouched"}

    def onboard_defaults(self) -> dict:
        records = self.records()
        with self.ledger() as led:
            already = led.meta("onboarded_at")
            outs = policies.write_defaults(led, records)
            led.set_meta("onboarded_at", already or time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            led.set_meta("onboarded_with", "defaults")
            return {"recorded": [{"action": o.action, **o.belief.__dict__} for o in outs]}

    def injection(self) -> dict:
        records = self.records()
        with self.ledger() as led:
            ctx = self.context("ask", led, records)
            doc = next((d for d in records.supplier_documents if d.ref == "SUP-V1-01"), None)
            f = next((x for x in ctx.findings if x.id == "untrusted_instructions:SUP-V1-01"), None)
            return {"raw": doc.text if doc else None, "wrapped": doc.wrapped() if doc else None,
                    "cite": str(doc.cite) if doc else None,
                    "finding": render.finding_block(f) if f else None,
                    "tools_in_ask": tools.allowed("ask"), "tools_in_order": tools.allowed("order")}

    def diagrams(self) -> dict:
        out = []
        if DIAGRAMS.exists():
            for p in sorted(DIAGRAMS.glob("*.md")):
                text = p.read_text(encoding="utf-8")
                title = next((l[2:] for l in text.splitlines() if l.startswith("# ")), p.stem)
                code = text.split("```mermaid", 1)[1].split("```", 1)[0] if "```mermaid" in text else ""
                prose = text.split("```")[-1].strip() if text.count("```") >= 2 else ""
                out.append({"file": p.name, "title": title, "mermaid": code.strip(), "prose": prose})
        total, runs, _ = llm.usage_total(self.cfg.usage_log)
        return {"diagrams": out, "cost_usd": total, "runs": runs}

    # ----- model turns (SSE) ----------------------------------------------------------

    def chat(self, body: dict, emit: Callable[[dict], None]) -> None:
        text = (body.get("text") or "").strip()
        if not text:
            raise HttpError(400, "text is required")
        mode = body.get("mode") or "ask"
        if mode not in tools.MODE_SETS:
            raise HttpError(400, f"unknown mode {mode!r}")
        records = self.records()
        with self.ledger() as led:
            ctx = self.context(mode, led, records)
            history = [m for ex in led.exchanges("ask", last=6) for m in ex.messages] if mode == "ask" else []
            emit({"type": "start", "mode": mode, "allowed": tools.allowed(mode), "history_exchanges": len(history) // 2})
            if self.cfg.dry or not self.key_present():
                emit({"type": "error", "message": "no model: the server runs with --dry or without ANTHROPIC_API_KEY; "
                                                  "use the Tools page or the CLI's `set` command"})
                return

            def gate(name: str, args: dict) -> tuple:
                card = {"tool": name, "args": args, "mode": "ask"}
                try:
                    value = json.loads(args.get("value_json", "{}"))
                except ValueError:
                    value = args.get("value_json")
                current = led.one(args.get("kind", ""), args.get("subject", ""))
                card.update({"kind": args.get("kind"), "subject": args.get("subject"), "statement": args.get("statement"),
                             "value": value, "supersedes": current.__dict__ if current else None})
                gid = self.gates.open(card)
                emit({"type": "gate", "id": gid, **card})
                decision, new_args, reason = self.gates.wait(gid)
                if decision == "edit" and isinstance(new_args, dict):
                    merged = dict(args)
                    if "value" in new_args:
                        merged["value_json"] = json.dumps(new_args["value"])
                    for k in ("statement", "subject", "kind"):
                        if new_args.get(k):
                            merged[k] = new_args[k]
                    emit({"type": "gate_result", "id": gid, "decision": "edited"})
                    return "edit", merged, None
                emit({"type": "gate_result", "id": gid, "decision": decision, "reason": reason})
                return decision, None, reason

            try:
                result = llm.run(self.cfg, mode, text, ctx, history=history, gate=gate, on_event=emit)
            except llm.ModelUnavailable as e:
                emit({"type": "error", "message": str(e)})
                return
            led.save_exchange(mode, text, result.text, result.messages[len(history):], records.revision)
            recorded = [c.result for c in result.tool_calls if c.name == "record_belief" and c.ok and isinstance(c.result, dict)]
            emit({"type": "done", "text": result.text, "citations": render.citation_links(result.text),
                  "recorded": recorded, "note": result.note, "usage": {**result.usage.__dict__, "usd": result.usage.usd(self.cfg.model)},
                  "tool_calls": [{"name": c.name, "ok": c.ok, "gate": c.gate, "ms": c.ms, "summary": c.summary} for c in result.tool_calls]})

    def brief(self, body: dict, emit: Callable[[dict], None]) -> None:
        from ..modes.brief import narration_request
        records = self.records(fresh=True)
        with self.ledger() as led:
            params = policies.detector_params(led)
            findings = detectors.run_all(records, params)
            report = policies.recheck(led, records, findings)
            burn, window = analysis.daily_burn(records, 7)
            onboarded = led.meta("onboarded_at")
            footer = render.retained_block(report, led, onboarded)
            changes = render.changes_block(report, records)
            deterministic = render.brief_text(records, findings, burn, window, footer, changes)
            emit({"type": "start", "mode": "brief", "allowed": tools.allowed("brief"), "deterministic": deterministic,
                  "footer": footer, "changes": changes, "revision": records.revision})
            if self.cfg.dry or not self.key_present():
                emit({"type": "done", "text": None, "footer": footer, "note": "no model; deterministic brief shown"})
                return
            ctx = tools.ToolContext(records, led, "brief", params, findings)
            try:
                result = llm.run(self.cfg, "brief", narration_request(records, findings, burn, window, onboarded, changes),
                                 ctx, on_event=emit)
            except llm.ModelUnavailable as e:
                emit({"type": "error", "message": str(e)})
                return
            emit({"type": "done", "text": result.text, "footer": footer, "citations": render.citation_links(result.text or ""),
                  "note": result.note, "usage": {**result.usage.__dict__, "usd": result.usage.usd(self.cfg.model)},
                  "tool_calls": [{"name": c.name, "ok": c.ok, "ms": c.ms, "summary": c.summary} for c in result.tool_calls]})

    def explain_order(self, body: dict, emit: Callable[[dict], None]) -> None:
        records = self.records()
        with self.ledger() as led:
            p = ordering.plan_order(records, led, body.get("vendor") or "V1")
            text = ordering.rationale(p)
            emit({"type": "start", "mode": "order", "allowed": tools.allowed("order"), "rationale": text})
            if self.cfg.dry or not self.key_present():
                emit({"type": "done", "text": None, "note": "no model; the rationale stands on its own"})
                return
            ctx = self.context("order", led, records)
            try:
                result = llm.run(self.cfg, "order", f"<proposal>\n{text}\n</proposal>\n\nExplain this proposal to the manager.",
                                 ctx, on_event=emit)
            except llm.ModelUnavailable as e:
                emit({"type": "error", "message": str(e)})
                return
            emit({"type": "done", "text": result.text, "citations": render.citation_links(result.text or ""),
                  "usage": {**result.usage.__dict__, "usd": result.usage.usd(self.cfg.model)}})


# ----- HTTP plumbing -------------------------------------------------------------------------

def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "lantern-assistant-web/0.1"

        def log_message(self, fmt, *args):  # quiet
            pass

        # -- helpers --
        def _json(self, status: int, payload: Any) -> None:
            raw = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                raise HttpError(400, "body must be JSON")
            return data if isinstance(data, dict) else {}

        def _sse(self, producer: Callable[[Callable[[dict], None]], None]) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            lock = threading.Lock()

            def emit(event: dict) -> None:
                with lock:
                    try:
                        self.wfile.write(f"data: {json.dumps(event, default=str)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        raise
            try:
                producer(emit)
            except HttpError as e:
                emit({"type": "error", "message": e.message})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # report, never hang the stream
                emit({"type": "error", "message": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]})
            finally:
                try:
                    self.wfile.write(b"data: {\"type\": \"end\"}\n\n")
                    self.wfile.flush()
                except Exception:
                    pass
                self.close_connection = True

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            if name.startswith("static/"):
                name = name[len("static/"):]
            target = (STATIC / name).resolve()
            if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
                raise HttpError(404, "not found")
            ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}.get(target.suffix, "application/octet-stream")
            raw = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        # -- routing --
        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def _route(self, method: str) -> None:
            url = urllib.parse.urlsplit(self.path)
            path, q = url.path, {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            if "site_id" in q:
                return self._json(400, {"error": "this UI serves SITE-01 only; site_id is not a parameter"})
            try:
                if method == "GET":
                    if not path.startswith("/api/"):
                        return self._static(path)
                    return self._json(200, self._get(path, q))
                return self._post(path, q)
            except HttpError as e:
                self._json(e.status, {"error": e.message})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                self._json(500, {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]})

        def _get(self, path: str, q: dict) -> Any:
            a = app
            if path == "/api/health":
                return {"status": "ok"}
            if path == "/api/state":
                return a.state()
            if path == "/api/findings":
                return a.findings()
            if path == "/api/findings/diff":
                return a.findings_diff()
            if path == "/api/brief/dry":
                return a.dry_brief("onboarding" if q.get("kind") == "onboarding" else "brief")
            if path == "/api/beliefs":
                return a.beliefs(q)
            if path == "/api/exchanges":
                return a.exchanges()
            if path == "/api/client/intro":
                return a.client_intro()
            if path == "/api/onboarding/plan":
                return a.onboarding_plan(q)
            if path == "/api/alerts":
                return a.alerts()
            if path == "/api/client/tour":
                return a.tour()
            if path == "/api/beliefs/why":
                if not q.get("subject"):
                    raise HttpError(400, "subject is required")
                return a.why(q["subject"])
            if path == "/api/evidence":
                return a.evidence(q)
            if path == "/api/tools":
                return a.tool_matrix(q)
            if path == "/api/order/proposal":
                return a.proposal(q)
            if path == "/api/order/drafts":
                return a.drafts()
            if path == "/api/order/planner":
                return a.planner()
            if path.startswith("/api/order/"):
                return a.draft_status(path[len("/api/order/"):])
            if path == "/api/usage":
                return a.usage()
            if path.startswith("/api/runs/"):
                return a.run_file(path[len("/api/runs/"):])
            if path == "/api/injection":
                return a.injection()
            if path == "/api/diagrams":
                return a.diagrams()
            if path == "/api/gates":
                return {"gates": a.gates.cards()}
            raise HttpError(404, f"no such endpoint {path}")

        def _post(self, path: str, q: dict) -> None:
            a = app
            body = self._body()
            if path == "/api/chat":
                return self._sse(lambda emit: a.chat(body, emit))
            if path == "/api/brief":
                return self._sse(lambda emit: a.brief(body, emit))
            if path == "/api/order/explain":
                return self._sse(lambda emit: a.explain_order(body, emit))
            if path == "/api/beliefs":
                return self._json(200, a.record_belief(body))
            if path == "/api/tools/execute":
                return self._json(200, a.tool_execute(body))
            if path == "/api/order/draft":
                return self._json(201, a.create_draft(body))
            if path == "/api/order/resume":
                return self._json(200, a.resume())
            if path == "/api/order/revise":
                return self._json(200, a.revise(body))
            if path.startswith("/api/order/") and path.endswith("/submit"):
                return self._json(200, a.submit(path[len("/api/order/"):-len("/submit")]))
            if path.startswith("/api/gate/"):
                ok = a.gates.answer(path[len("/api/gate/"):], body)
                return self._json(200 if ok else 404, {"ok": ok})
            if path == "/api/reset":
                return self._json(200, a.reset(body))
            if path == "/api/onboarding/answer":
                return self._json(200, a.onboarding_answer(body))
            if path == "/api/onboarding/complete":
                return self._json(200, a.onboarding_complete())
            if path == "/api/onboarding/restart":
                return self._json(200, a.onboarding_restart())
            if path == "/api/alerts/seen":
                return self._json(200, a.alerts_seen(body))
            if path == "/api/client/tour":
                return self._json(200, a.tour_mark(body))
            if path == "/api/onboard/defaults":
                return self._json(200, a.onboard_defaults())
            raise HttpError(404, f"no such endpoint {path}")

    return Handler


def serve(cfg: Config, host: str = "127.0.0.1", port: int = 8780, open_browser: bool = False) -> int:
    app = App(cfg)
    try:
        app.records()
    except HttpError as e:
        print(e.message, file=__import__("sys").stderr)
        return 1
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(f"Lantern Yard assistant UI at {url}  (state {cfg.state_dir}; model {'off (--dry)' if cfg.dry else cfg.model}"
          f"{'' if app.key_present() or cfg.dry else ', no ANTHROPIC_API_KEY: model turns disabled'}). Ctrl-C to stop.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def start_in_thread(cfg: Config, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, App, str]:
    """For tests: a server on a free port in a daemon thread."""
    app = App(cfg)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, app, f"http://{host}:{httpd.server_address[1]}"
