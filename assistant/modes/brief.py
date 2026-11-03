"""Morning brief. Read-only. Deterministic findings shaped by ledger policies; narration arrives in block 4."""
from __future__ import annotations

import sys

from .. import analysis, detectors, policies, render
from ..api_client import ApiClient, ServerUnavailable
from ..config import Config
from ..ledger import Ledger
from ..records import load_all


def run(cfg: Config) -> int:
    client = ApiClient(cfg)
    try:
        records = load_all(client)
    except ServerUnavailable:
        print(f"Cannot reach the operational API at {cfg.api_base}. Start it with:\n"
              f"  python3 simulator.py serve --mode agent", file=sys.stderr)
        return 1
    with Ledger(cfg.db_path) as ledger:
        params = policies.detector_params(ledger)
        findings = detectors.run_all(records, params)
        report = policies.recheck(ledger, records, findings)
        burn, window = analysis.daily_burn(records, 7)
        footer = render.retained_block(report, ledger, ledger.meta("onboarded_at"))
        pending = ledger.pending_drafts()
    print(render.brief_text(records, findings, burn, window, footer))
    if pending:
        print(f"\n{len(pending)} order draft(s) not yet settled: " +
              ", ".join(f"{d.draft_id} ({d.status})" for d in pending) + ". `order` reconciles them first.")
    if not cfg.dry:
        print("\n(model narration arrives in block 4; this is the deterministic brief)", file=sys.stderr)
    return 0
