"""Order mode: propose, draft, wait for a person's approval, submit, verify. Honest about every failure."""
from __future__ import annotations

import json
import sys
import time

from .. import detectors, llm, ordering, policies, tools
from ..api_client import ApiClient, ServerUnavailable
from ..config import Config
from ..ledger import Ledger
from ..orderflow import APPROVE_COMMAND, OrderFlow, Outcome
from ..records import load_all

EXIT_AWAITING = 3


def say(outcome: Outcome) -> None:
    print(f"[{outcome.state.replace('_', ' ')}] {outcome.message}")


def run(cfg: Config, vendor: str = "V1", propose_only: bool = False, yes: bool = False, resume: bool = False,
        poll_seconds: float = 2.0, timeout: float = 600.0) -> int:
    client = ApiClient(cfg)
    try:
        records = load_all(client)
    except ServerUnavailable:
        print(f"Cannot reach the operational API at {cfg.api_base}.", file=sys.stderr)
        return 1
    with Ledger(cfg.db_path) as ledger:
        flow = OrderFlow(client, ledger)

        # 1. Settle anything left from an earlier run before talking about a new order.
        settled = flow.reconcile(submit_approved=resume)
        if settled:
            print("Earlier drafts:")
            for o in settled:
                say(o)
            print()
        if resume:
            if not settled:
                done = [d for d in ledger.drafts() if d.status == "accepted"]
                print("No open drafts." + (f" Last accepted: {done[-1].draft_id} as order {done[-1].order_id} "
                                           f"({done[-1].updated_at[:16]})." if done else ""))
            return 0 if all(o.state in ("accepted", "cancelled") for o in settled) else EXIT_AWAITING
        blocked = flow.blocked_vendors()
        if vendor in blocked:
            print(f"Draft {blocked[vendor].draft_id} for {vendor} is {blocked[vendor].status}. No new draft until it is "
                  f"settled (`order --resume` rechecks it).")
            return EXIT_AWAITING
        waiting = [d for d in flow.open_drafts() if d.vendor_id == vendor]
        if waiting and not yes:
            d = waiting[-1]
            print(f"Draft {d.draft_id} for {vendor} is still {d.status.replace('_', ' ')}. Approve it and run "
                  f"`order --resume`, or `order --yes` to propose a fresh one (the old draft stays unsubmitted).")
            return EXIT_AWAITING

        # 2. Propose. Numbers come from ordering.plan_order; the model may explain, never change them.
        proposal = ordering.plan_order(records, ledger, vendor)
        print(ordering.rationale(proposal))
        if not proposal.lines:
            print("\nNothing to order from this vendor for the coverage window.")
            return 0
        if not cfg.dry:
            params = policies.detector_params(ledger)
            ctx = tools.ToolContext(records, ledger, "order", params, detectors.run_all(records, params))
            try:
                res = llm.run(cfg, "order", f"<proposal>\n{ordering.rationale(proposal)}\n</proposal>\n\n"
                                            f"Explain this proposal to the manager.", ctx)
                print("\n" + (res.text or ""))
                llm.report(cfg, res)
            except llm.ModelUnavailable as e:
                print(f"[model unavailable: {e}; the proposal above stands on its own]", file=sys.stderr)
        if propose_only:
            print("\n(proposal only; no draft was created)")
            return 0

        # 3. The operator's go-ahead creates the draft. The draft is immutable; a change means a new draft.
        if not yes:
            answer = input(f"\nCreate this draft with {proposal.vendor_name}? It still needs your approval before "
                           f"anything is submitted. [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("No draft created.")
                return 0
        draft = flow.create_draft(proposal)
        print(f"\n[drafted] {draft.draft_id} — status {draft.status.replace('_', ' ')}. Exact order held by the supplier:")
        print(json.dumps(draft.order, indent=1))
        print(f"\nTo approve, run in another terminal:\n  {APPROVE_COMMAND.format(draft_id=draft.draft_id)}\n"
              f"Ctrl-C here cancels (the draft stays unsubmitted).")

        # 4. Wait for a person. Polling only reads the draft; nothing is submitted until the server says approved.
        deadline = time.time() + timeout
        status = draft.status
        try:
            while status != "approved":
                if time.time() >= deadline:
                    print(f"\n[awaiting approval] {draft.draft_id} not approved after {timeout:g}s. Approve it, then run "
                          f"`order --resume` to submit and verify.")
                    return EXIT_AWAITING
                time.sleep(poll_seconds)
                status = flow.approval_status(draft.draft_id)
                print(".", end="", flush=True)
        except KeyboardInterrupt:
            flow.cancel(draft.draft_id)
            print(f"\n[cancelled] {draft.draft_id} was never submitted. It remains on the supplier side as an "
                  f"unsubmitted draft.")
            return 0
        print(f"\n[approved] {draft.draft_id} approved by the operator. Submitting.")

        # 5. Submit and verify. Every branch reports exactly what the supplier said.
        outcome = flow.submit(draft.draft_id)
        say(outcome)
        return 0 if outcome.state == "accepted" else EXIT_AWAITING if outcome.state in ("awaiting_approval", "unknown") else 1
