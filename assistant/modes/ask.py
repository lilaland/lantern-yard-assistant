"""Ask: follow-up questions and stated preferences, with the conversation persisted across restarts.

Deterministic commands work without a model (`why`, `memory`, `findings`, `finding`, `set`). Anything
else goes to the model with the READ, ANALYSIS and MEMORY tools; the only way a belief enters the
ledger from here is the record_belief tool or the `set` command, both under the source ``human:ask``.
"""
from __future__ import annotations

import json
import sys
from typing import Callable

from .. import detectors, llm, policies, render, tools
from ..api_client import ApiClient, ServerUnavailable
from ..config import Config
from ..ledger import Ledger
from ..records import load_all

HELP = ("Commands: why <subject> | memory | findings | finding <id> | set <kind> <subject> <json> | clear | quit\n"
        "Anything else is a question for the model (needs ANTHROPIC_API_KEY; with --dry only the commands work).")
KEEP_EXCHANGES = 6


def run(cfg: Config, once: str | None = None, resume: bool = True, input_fn: Callable[[str], str] = input,
        call=None, keep: int = KEEP_EXCHANGES) -> int:
    try:
        records = load_all(ApiClient(cfg))
    except ServerUnavailable:
        print(f"Cannot reach the operational API at {cfg.api_base}.", file=sys.stderr)
        return 1
    with Ledger(cfg.db_path) as ledger:
        params = policies.detector_params(ledger)
        findings = detectors.run_all(records, params)
        ctx = tools.ToolContext(records, ledger, "ask", params, findings)
        session = Session(cfg, ctx, call, keep, resume)
        if once is not None:
            session.handle(once)
            return 0
        print(f"Lantern Yard Kitchen assistant — ask. Dataset revision {records.revision}, {len(findings)} findings, "
              f"{'onboarded ' + ledger.meta('onboarded_at')[:16] if ledger.meta('onboarded_at') else 'not onboarded'}.")
        if session.history:
            print(f"Continuing the earlier conversation ({session.loaded} exchange(s) loaded; `clear` forgets them).")
        print(HELP)
        while True:
            try:
                line = input_fn("ask> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not session.handle(line):
                break
    return 0


class Session:
    def __init__(self, cfg: Config, ctx: tools.ToolContext, call, keep: int, resume: bool):
        self.cfg, self.ctx, self.call, self.keep = cfg, ctx, call, keep
        self.ledger = ctx.ledger
        past = self.ledger.exchanges("ask", last=keep) if resume else []
        self.loaded = len(past)
        self.history: list = [m for ex in past for m in ex.messages]

    # ----- dispatch ------------------------------------------------------------

    def handle(self, line: str) -> bool:
        line = line.strip()
        if not line:
            return True
        head, _, rest = line.partition(" ")
        cmd = head.lower()
        if cmd in ("quit", "exit", "q"):
            return False
        if cmd == "help":
            print(HELP)
        elif cmd == "why" and rest:
            print(render.why_text(rest.strip(), self.ledger.why(rest.strip())))
        elif cmd == "memory":
            print(render.memory_text(self.ledger, self.ledger.meta("onboarded_at"), None))
        elif cmd == "findings":
            print(render.findings_index(self.ctx.findings))
        elif cmd == "finding" and rest:
            f = next((f for f in self.ctx.findings if f.id == rest.strip()), None)
            print(render.finding_block(f) if f else f"no finding {rest.strip()!r}; `findings` lists them")
        elif cmd == "set":
            self.set_command(rest)
        elif cmd == "clear":
            n = self.ledger.clear_exchanges("ask")
            self.history = []
            print(f"Forgot {n} earlier exchange(s). Beliefs are untouched; `memory` still lists them.")
        else:
            self.ask_model(line)
        return True

    def set_command(self, rest: str) -> None:
        """set <kind> <subject> <json> — the keyboard path into record_belief, same guard rails as the model's."""
        parts = rest.strip().split(None, 2)
        if len(parts) < 3:
            print("  usage: set <policy|preference|assertion> <subject> <json object>")
            return
        kind, subject, value_json = parts
        args = {"kind": kind, "subject": subject, "statement": f"Set by the manager in ask: {subject} = {value_json}",
                "value_json": value_json}
        try:
            out = tools.execute(self.ctx, "record_belief", args)
        except tools.ToolError as e:
            print(f"  not recorded: {e}")
            return
        self.echo_recorded(out)

    # ----- model -----------------------------------------------------------------

    def ask_model(self, line: str) -> None:
        if self.cfg.dry:
            print("(no model in --dry mode) " + HELP)
            return
        try:
            result = llm.run(self.cfg, "ask", line, self.ctx, history=self.history, call=self.call)
        except llm.ModelUnavailable as e:
            print(f"[model unavailable: {e}]\n{HELP}")
            return
        print(result.text or "(no answer)")
        for c in result.tool_calls:
            if c.name == "record_belief" and c.ok and isinstance(c.result, dict):
                self.echo_recorded(c.result)
        new_messages = result.messages[len(self.history):]
        self.ledger.save_exchange("ask", line, result.text, new_messages, self.ctx.records.revision)
        self.history = [m for ex in self.ledger.exchanges("ask", last=self.keep) for m in ex.messages]
        self.ctx.params = policies.detector_params(self.ledger)  # a recorded belief applies to the next question
        llm.report(self.cfg, result)

    @staticmethod
    def echo_recorded(out: dict) -> None:
        b = out["recorded"]
        prev = out.get("previous")
        line = f"  Recorded [{out['action']}]: {b['kind']} {b['subject']} = {json.dumps(b['value'])} ({b['source']})"
        if prev:
            line += f"; was {json.dumps(prev['value'])} ({prev['source']})"
        print(line)
