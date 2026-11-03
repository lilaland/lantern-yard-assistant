"""Onboarding: record the manager's policies and preferences. ``--defaults`` accepts the documented defaults."""
from __future__ import annotations

import sys
from datetime import datetime

from .. import policies, render
from ..api_client import ApiClient, ServerUnavailable
from ..config import Config
from ..ledger import Ledger
from ..records import load_all


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
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
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
    if records is None:
        return 1
    return 0


def run_interactive(cfg: Config) -> int:
    print("Interactive onboarding arrives in BUILD_PLAN.md block 7. Use `onboard --defaults` for now.", file=sys.stderr)
    return 2
