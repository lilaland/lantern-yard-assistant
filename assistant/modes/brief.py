"""Morning brief. Read-only. Deterministic findings shaped by ledger policies; the model only narrates."""
from __future__ import annotations

import sys

from .. import analysis, detectors, llm, policies, render, tools
from ..api_client import ApiClient, ServerUnavailable
from ..config import Config
from ..ledger import Ledger
from ..records import load_all, with_clock


def run(cfg: Config) -> int:
    client = ApiClient(cfg)
    try:
        records = load_all(client)
    except ServerUnavailable:
        print(f"Cannot reach the operational API at {cfg.api_base}. Start it with:\n"
              f"  python3 simulator.py serve --mode agent", file=sys.stderr)
        return 1
    with Ledger(cfg.db_path) as ledger:
        records = with_clock(records, ledger.clock_days())
        params = policies.detector_params(ledger)
        findings = detectors.run_all(records, params)
        report = policies.recheck(ledger, records, findings)
        burn, window = analysis.daily_burn(records, 7)
        onboarded = ledger.meta("onboarded_at")
        footer = render.retained_block(report, ledger, onboarded)
        changes = render.changes_block(report, records)
        pending = ledger.pending_drafts()
        deterministic = render.brief_text(records, findings, burn, window, footer, changes)
        if cfg.dry:
            print(deterministic)
        else:
            ctx = tools.ToolContext(records, ledger, "brief", params, findings)
            try:
                result = llm.run(cfg, "brief", narration_request(records, findings, burn, window, onboarded, changes), ctx)
            except llm.ModelUnavailable as e:
                print(f"[model unavailable: {e}]\nPrinting the deterministic brief instead.\n", file=sys.stderr)
                print(deterministic)
            else:
                print(result.text or "(the model returned no text)")
                print()
                print(footer)
                llm.report(cfg, result)
    if pending:
        print(f"\n{len(pending)} order draft(s) not yet settled: " +
              ", ".join(f"{d.draft_id} ({d.status})" for d in pending) + ". `order` reconciles them first.")
    return 0


def narration_request(records, findings, burn, window, onboarded: str | None, changes: str | None = None) -> str:
    """The per-run user turn: findings as data, then the ask. Nothing here is an instruction from the data."""
    blocks = [render.finding_block(f) for f in findings]
    body = "\n\n".join(blocks)
    changes_part = f"<changes>\n{changes}\n</changes>\n\n" if changes else ""
    return (changes_part +
            f"<findings site=\"{records.site}\" now=\"{records.now:%Y-%m-%d %H:%M}\" dataset_revision=\"{records.revision}\" "
            f"count=\"{len(findings)}\" onboarded=\"{'yes' if onboarded else 'no'}\">\n{body}\n</findings>\n\n"
            f"<runway>\n{render.runway_table(records, burn, window)}\n</runway>\n\n"
            f"Write this morning's brief from the findings above.")
