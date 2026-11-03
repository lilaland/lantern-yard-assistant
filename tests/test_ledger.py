"""The belief ledger: who may write what, how beliefs change, and that it survives restarts."""
import os
import subprocess
import sys

import pytest

from assistant.ledger import Ledger, LedgerError, authority


@pytest.fixture
def ledger(tmp_path):
    with Ledger(tmp_path / "state.sqlite") as l:
        yield l


def test_source_authority_and_write_rules(ledger):
    assert authority("human:ask") == 3 and authority("invoice:INV-001@rev1") == 2 and authority("detector:x") == 1
    with pytest.raises(LedgerError):
        authority("vendor_email:whatever")
    with pytest.raises(LedgerError):  # policies come from people, not detectors or documents
        ledger.record("policy", "inbound.on_hand_rule", "s", {"rule": "office_status"}, "detector:phantom")
    with pytest.raises(LedgerError):
        ledger.record("preference", "order.buffer_days", "s", {"days": 2}, "supplier_document:SUP-V1-01")
    with pytest.raises(LedgerError):  # observations come from detectors only
        ledger.record("observation", "phantom_receipt:INV-099", "s", {"severity": 1}, "human:ask")
    with pytest.raises(LedgerError):  # assertions need a person or a record
        ledger.record("assertion", "invoice.INV-099.status", "s", {"status": "received"}, "detector:x")
    with pytest.raises(LedgerError):  # policies are never rechecked against data
        ledger.record("policy", "inbound.on_hand_rule", "s", {"rule": "office_status"}, "human:ask", recheck="each_run")
    pol = ledger.record("policy", "inbound.on_hand_rule", "s", {"rule": "office_status"}, "human:ask").belief
    obs = ledger.record("observation", "x:1", "s", {"severity": 2}, "detector:x").belief
    human_fact = ledger.record("assertion", "f.1", "s", {"v": 1}, "human:ask").belief
    evidence_fact = ledger.record("assertion", "f.2", "s", {"v": 1}, "invoice:INV-001@rev1").belief
    assert (pol.recheck, obs.recheck, human_fact.recheck, evidence_fact.recheck) == \
           ("never", "each_run", "never", "on_revision_change")


def test_same_value_same_source_is_unchanged(ledger):
    a = ledger.record("preference", "order.buffer_days", "one day", {"days": 1}, "human:onboarding")
    b = ledger.record("preference", "order.buffer_days", "one day", {"days": 1}, "human:onboarding", revision=2)
    assert a.action == "added" and b.action == "unchanged" and b.belief.id == a.belief.id
    assert len(ledger.why("order.buffer_days")) == 1
    assert b.belief.revision == 2 and b.belief.confirmed_at >= a.belief.recorded_at


def test_a_person_changing_their_mind_supersedes(ledger):
    first = ledger.record("preference", "order.buffer_days", "one day", {"days": 1}, "human:onboarding")
    second = ledger.record("preference", "order.buffer_days", "two days", {"days": 2}, "human:ask")
    assert second.action == "superseded" and second.previous.id == first.belief.id
    assert ledger.value("preference", "order.buffer_days") == {"days": 2}
    chain = ledger.why("order.buffer_days")
    assert [b.status for b in chain] == ["superseded", "active"]
    assert chain[1].supersedes == chain[0].id
    assert ledger.counts()["preference"] == {"active": 1, "superseded": 1, "conflicted": 0}


def test_evidence_against_a_human_statement_conflicts_and_a_person_resolves_it(ledger):
    said = ledger.record("assertion", "invoice.INV-099.delivery_date", "Alder said Tuesday", {"date": "2026-10-06"},
                         "human:ask")
    corrected = ledger.record("assertion", "invoice.INV-099.delivery_date", "corrected invoice", {"date": "2026-10-08"},
                              "invoice:INV-099@rev2", "on_revision_change", 2)
    assert corrected.action == "conflicted"
    assert ledger.get(said.belief.id).status == "conflicted"
    assert corrected.belief.status == "active" and corrected.belief.conflicts_with == said.belief.id
    (old, new), = ledger.conflicts()
    assert old.id == said.belief.id and new.id == corrected.belief.id
    assert ledger.value("assertion", "invoice.INV-099.delivery_date") == {"date": "2026-10-08"}
    # the manager confirms the correction: every earlier row closes, nothing is deleted
    resolved = ledger.record("assertion", "invoice.INV-099.delivery_date", "confirmed Thursday", {"date": "2026-10-08"},
                             "human:ask")
    assert resolved.action == "superseded" and ledger.conflicts() == []
    assert [b.status for b in ledger.why("invoice.INV-099.delivery_date")] == ["superseded", "superseded", "active"]


def test_newer_evidence_supersedes_older_evidence_without_conflict(ledger):
    ledger.record("assertion", "invoice.INV-099.status", "s", {"status": "received"}, "invoice:INV-099@rev1", None, 1)
    o = ledger.record("assertion", "invoice.INV-099.status", "s", {"status": "scheduled"}, "invoice:INV-099@rev2", None, 2)
    assert o.action == "superseded" and ledger.conflicts() == []
    # and a person restating what the record says takes over the belief with higher authority
    h = ledger.record("assertion", "invoice.INV-099.status", "office confirmed", {"status": "scheduled"}, "human:ask")
    assert h.action == "superseded" and ledger.one("assertion", "invoice.INV-099.status").recheck == "never"


def test_beliefs_and_meta_persist_across_reopen(tmp_path):
    path = tmp_path / "state.sqlite"
    with Ledger(path) as l:
        l.record("policy", "inbound.on_hand_rule", "s", {"rule": "physical_confirmation"}, "human:onboarding")
        l.set_meta("onboarded_at", "2026-11-03T16:00:00-05:00")
        l.save_draft("D-1", "V1", {"site_id": "SITE-01", "vendor_id": "V1", "lines": []}, "drafted")
    with Ledger(path) as l:
        assert l.value("policy", "inbound.on_hand_rule") == {"rule": "physical_confirmation"}
        assert l.meta("onboarded_at") == "2026-11-03T16:00:00-05:00"
        assert [d.draft_id for d in l.pending_drafts()] == ["D-1"]


def test_drafts_lifecycle(ledger):
    d = ledger.save_draft("D-9", "V1", {"lines": [{"sku": "AW-CHK20", "quantity": 6, "unit": "case"}]}, "drafted")
    assert d.status == "drafted" and d.order_id is None
    d = ledger.update_draft("D-9", "awaiting_approval")
    d = ledger.update_draft("D-9", "unknown", last_error="RemoteDisconnected")
    assert d.last_error == "RemoteDisconnected" and [x.draft_id for x in ledger.pending_drafts()] == ["D-9"]
    d = ledger.update_draft("D-9", "accepted", order_id="O-1")
    assert d.order_id == "O-1" and d.last_error is None and ledger.pending_drafts() == []
    with pytest.raises(LedgerError):
        ledger.update_draft("D-9", "shipped")


def _cli(sim, state_dir, *args):
    env = dict(os.environ, MARBLE_API_BASE=sim.base, MARBLE_AGENT_TOKEN="demo-agent-site-01")
    return subprocess.run([sys.executable, "-m", "assistant", "--dry", "--state-dir", str(state_dir), *args],
                          capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.dirname(__file__)))


def test_preferences_survive_a_process_restart(sim, tmp_path):
    """Three separate processes: onboard, then read memory, then re-apply defaults."""
    state = tmp_path / ".marble"
    first = _cli(sim, state, "onboard", "--defaults")
    assert first.returncode == 0, first.stderr
    assert "Onboarded with defaults" in first.stdout and "14 added" in first.stdout
    second = _cli(sim, state, "memory")
    assert second.returncode == 0, second.stderr
    assert "order.buffer_days" in second.stdout and "human:onboarding-defaults" in second.stdout
    assert "onboarded 20" in second.stdout
    third = _cli(sim, state, "onboard", "--defaults")
    assert "Defaults re-applied" in third.stdout and "14 unchanged" in third.stdout
    why = _cli(sim, state, "why", "order.brunch_uplift")
    assert "1 record(s)" in why.stdout and "uplift=0.25" in why.stdout
