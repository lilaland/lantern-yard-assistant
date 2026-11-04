"""The correction path end to end: a brief at revision 1, the delivery correction, a brief at revision 2."""
import os
import subprocess
import sys

import pytest

from assistant import analysis, detectors, ordering, policies, render
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.records import load_all

ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture
def ledger(tmp_path, client):
    with Ledger(tmp_path / "state.sqlite") as l:
        policies.write_defaults(l, load_all(client))
        yield l


def brief_run(client, ledger):
    records = load_all(client)
    params = policies.detector_params(ledger)
    findings = detectors.run_all(records, params)
    report = policies.recheck(ledger, records, findings)
    return records, findings, report


def test_changes_block_across_the_delivery_correction(client, sim, ledger):
    records1, findings1, r1 = brief_run(client, ledger)
    assert render.changes_block(r1, records1) is None  # first run: nothing to compare with
    with sim.under_scenario("delivery-correction"):
        records2, findings2, r2 = brief_run(client, ledger)
        block = render.changes_block(r2, records2)
        records3, findings3, r3 = brief_run(client, ledger)
        again = render.changes_block(r3, records3)
    assert r2.revision_changed and r2.headline_changed and r2.anything_changed
    assert r2.previous_headline["id"] == "phantom_receipt:INV-099" and r2.headline["id"] == "unit_price_outlier:INV-010-L01"
    assert [n.ref for n in r2.new_notes] == ["N16"]
    assert block.startswith("What changed since the last brief at ") and "dataset revision 1 -> 2" in block
    assert "Headline: was [HIGH] INV-099 is marked received" in block and "now [HIGH] INV-010 bills a line at 10x" in block
    assert "Cleared: INV-099 is marked received but its delivery date is in the future (phantom_receipt:INV-099); INV-099 is now 'scheduled' with delivery date 2026-10-08" in block
    assert 'New note N16 (2026-10-05 06:10, Maya): "Alder confirmed INV-099 has NOT arrived.' in block and "[notes.csv SITE-01 N16 rev1]" in block
    assert "Record corrected: invoice.INV-099.delivery_date date=2026-10-06 -> date=2026-10-08 (invoice:INV-099@rev2)" in block
    assert "Unchanged: 16 finding(s); 4 policies and 6 preferences retained" in block
    assert "Needs you" not in block
    # the full brief carries the block above the headline
    burn, window = analysis.daily_burn(records2, 7)
    text = render.brief_text(records2, findings2, burn, window, render.retained_block(r2, ledger, "x"), block)
    assert text.index("What changed since") < text.index("[HIGH] INV-010")
    # a second brief at the same revision reports no change
    assert again.startswith("No changes since the last brief at ") and "(dataset revision 2)" in again
    assert not r3.anything_changed and r3.observations_unchanged == 16


def test_conflict_with_a_human_statement_is_surfaced_in_the_block(client, sim, ledger):
    brief_run(client, ledger)
    ledger.record("assertion", "invoice.INV-099.delivery_date", "Alder said Tuesday", {"date": "2026-10-06"}, "human:ask")
    with sim.under_scenario("delivery-correction"):
        records2, _, r2 = brief_run(client, ledger)
        block = render.changes_block(r2, records2)
    assert "Needs you: invoice.INV-099.delivery_date: human:ask said date=2026-10-06" in block
    assert "invoice:INV-099@rev2 now says date=2026-10-08" in block and "Neither was overwritten" in block
    assert len(ledger.conflicts()) == 1


def test_order_rationale_warns_about_inbound_inside_the_window(client, sim, ledger):
    with sim.under_scenario("delivery-correction"):
        records2 = load_all(client)
        p = ordering.plan_order(records2, ledger, "V1")
    assert [l.api_line for l in p.lines][0] == {"sku": "AW-CHK20", "quantity": 8, "unit": "case"}  # still not counted
    w = next(w for w in p.warnings if w.startswith("INV-099"))
    assert "scheduled to land Thursday 2026-10-08, inside this window, but is not counted" in w and "40 lb" in w
    assert any("status scheduled" in n for n in p.not_counted)


def _cli(sim, state_dir, *args):
    env = dict(os.environ, MARBLE_API_BASE=sim.base, MARBLE_AGENT_TOKEN="demo-agent-site-01")
    return subprocess.run([sys.executable, "-m", "assistant", "--dry", "--state-dir", str(state_dir), *args],
                          capture_output=True, text=True, env=env, cwd=ROOT)


def test_cli_brief_before_and_after_the_correction(sim, tmp_path):
    state = tmp_path / ".marble"
    assert _cli(sim, state, "onboard", "--defaults").returncode == 0
    first = _cli(sim, state, "brief").stdout
    assert "What changed" not in first and "[HIGH] INV-099 is marked received" in first.splitlines()[3]
    with sim.under_scenario("delivery-correction"):
        second = _cli(sim, state, "brief").stdout
        third = _cli(sim, state, "brief").stdout
    lines = second.splitlines()
    assert lines[3].startswith("What changed since the last brief") and "revision 1 -> 2" in lines[3]
    assert "New note N16" in second and second.index("What changed") < second.index("[HIGH] INV-010")
    assert "No changes since the last brief" in third.splitlines()[3]


def test_first_brief_after_onboarding_does_not_say_no_changes(client, ledger):
    records = load_all(client)
    params = policies.detector_params(ledger)
    findings = detectors.run_all(records, params)
    policies.recheck(ledger, records, findings, kind="onboarding")
    r = policies.recheck(ledger, records, findings)
    assert r.previous_run_kind == "onboarding" and render.changes_block(r, records) is None
    r2 = policies.recheck(ledger, records, findings)
    assert r2.previous_run_kind == "brief" and render.changes_block(r2, records).startswith("No changes since the last brief")
