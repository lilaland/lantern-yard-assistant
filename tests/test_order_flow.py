"""Order sizing numbers, the state machine against the real simulator, and the CLI across restarts."""
import json
import os
import re
import subprocess
import sys

import pytest

from assistant import ordering, policies
from assistant.api_client import ApiClient, ConnectionLost
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.orderflow import APPROVE_COMMAND, OrderFlow
from assistant.records import load_all

ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="module")
def records(sim, tmp_path_factory):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low",
                 state_dir=tmp_path_factory.mktemp("s"), dry=True)
    return load_all(ApiClient(cfg))


@pytest.fixture
def ledger(tmp_path, records):
    with Ledger(tmp_path / "state.sqlite") as l:
        policies.write_defaults(l, records)
        yield l


@pytest.fixture
def flow(client, ledger):
    return OrderFlow(client, ledger)


def lines(p):
    return {l.sku: l for l in p.lines}


# ----- sizing ----------------------------------------------------------------------------

def test_proposal_on_the_fixture_with_default_preferences(records, ledger):
    p = ordering.plan_order(records, ledger, "V1")
    assert (p.delivery_date.isoformat(), p.following_delivery.isoformat()) == ("2026-10-07", "2026-10-12")
    assert p.order_by.strftime("%A %H:%M") == "Tuesday 12:00"  # Monday's drop is past its noon-the-day-before cutoff
    assert p.window_end.isoformat() == "2026-10-11" and p.window_days == 7 and p.buffer_days == 1
    by = lines(p)
    assert p.api_lines == [{"sku": "AW-CHK20", "quantity": 8, "unit": "case"}, {"sku": "V1-EGG", "quantity": 2, "unit": "case"},
                           {"sku": "V1-CHEESE", "quantity": 4, "unit": "case"}]
    chk = by["AW-CHK20"]
    assert chk.on_hand == 47.9 and chk.burn_per_day == pytest.approx(24.43, abs=0.01) and chk.days == 8
    assert chk.short == pytest.approx(195.5 - 47.9, abs=0.1) and chk.inbound_counted == 0 and chk.uplift_add == 0
    eggs = by["V1-EGG"]
    assert eggs.uplift_add == pytest.approx(66.0) and eggs.need == pytest.approx(376.9, abs=0.1) and eggs.cases == 2
    assert str(p.est_total_usd) == "814.40" and not p.over_max
    assert any("INV-099" in n and "note:N15" in n for n in p.not_counted)
    assert [w for w in p.warnings if w.startswith("Chicken")] and not [w for w in p.warnings if w.startswith("Large shell eggs")]
    assert any("order.brunch_uplift=0.25" in a for a in p.applied) and any("note:N09" in a for a in p.applied)
    text = ordering.rationale(p)
    assert '"quantity": 8' in text and "Under your $1,000" in text and "Not counted: INV-099" in text


def test_lines_are_valid_for_the_catalog(records, ledger):
    p = ordering.plan_order(records, ledger, "V1")
    skus = [l["sku"] for l in p.api_lines]
    assert len(skus) == len(set(skus)) and 1 <= len(skus) <= 20
    for l in p.api_lines:
        cat = records.catalog_item("V1", l["sku"])
        assert cat is not None and l["unit"] == cat.order_unit and type(l["quantity"]) is int and 1 <= l["quantity"] <= 100
    with pytest.raises(ValueError):
        ordering.plan_order(records, ledger, "V9")


def test_preferences_and_policies_change_the_order(records, ledger):
    ledger.record("preference", "order.sku.ING-CHICKEN", "old case", {"vendor_id": "V1", "sku": "V1-CHICKEN"}, "human:ask")
    p = ordering.plan_order(records, ledger, "V1")
    assert lines(p)["V1-CHICKEN"].cases == 4  # 147.6 lb short / 40 lb cases
    ledger.record("preference", "order.buffer_days", "two days", {"days": 2}, "human:ask")
    assert lines(ordering.plan_order(records, ledger, "V1"))["V1-CHICKEN"].days == 9
    ledger.record("preference", "order.brunch_uplift", "none", {"uplift": 0}, "human:ask")
    p = ordering.plan_order(records, ledger, "V1")
    assert lines(p)["V1-EGG"].uplift_add == 0 and lines(p)["V1-CHICKEN"].cases == 5  # 24.43*9 - 47.9 = 172 lb / 40
    # the manager confirms the cases arrived: INV-099 becomes supply and the chicken line shrinks
    ledger.record("assertion", "invoice.INV-099.physically_received", "found them", {"received": True}, "human:ask")
    p = ordering.plan_order(records, ledger, "V1")
    assert lines(p)["V1-CHICKEN"].inbound_counted == 40 and lines(p)["V1-CHICKEN"].cases == 4 and p.not_counted == []
    assert any("inbound counted as supply" in n for n in lines(p)["V1-CHICKEN"].notes)


def test_ingredients_with_enough_stock_are_skipped_with_a_reason(records, ledger):
    p = ordering.plan_order(records, ledger, "V3")
    assert {l.ingredient for l in p.lines} == {"Long grain rice, dry", "Black beans, drained", "6 inch flour tortilla"}
    (ing, why), = p.skipped
    assert ing == "Canola oil" and "on hand covers" in why


# ----- state machine against the real simulator ------------------------------------------------

def test_draft_wait_submit_verify(records, ledger, flow, client, sim):
    p = ordering.plan_order(records, ledger, "V1")
    d = flow.create_draft(p)
    assert d.status == "awaiting_approval" and d.order == p.order and d.draft_id.startswith("D-")
    assert ledger.pending_drafts()[0].draft_id == d.draft_id
    early = flow.submit(d.draft_id)  # the server refuses; the ledger goes back to awaiting approval
    assert early.state == "awaiting_approval" and APPROVE_COMMAND.format(draft_id=d.draft_id) in early.message
    assert ledger.draft(d.draft_id).status == "awaiting_approval" and client.find_orders(d.draft_id) == []
    sim.approve(d.draft_id)
    assert flow.approval_status(d.draft_id) == "approved" and ledger.draft(d.draft_id).status == "approved"
    done = flow.submit(d.draft_id)
    assert done.state == "accepted" and done.verified and done.order_id.startswith("O-")
    assert "Accepted means the supplier recorded it" in done.message
    assert ledger.draft(d.draft_id).status == "accepted" and ledger.draft(d.draft_id).order_id == done.order_id
    again = flow.submit(d.draft_id)
    assert again.state == "accepted" and again.order_id == done.order_id and len(client.find_orders(d.draft_id)) == 1


def test_lost_response_is_recovered_without_a_second_draft(records, ledger, flow, client, sim):
    d = flow.create_draft(ordering.plan_order(records, ledger, "V1"))
    sim.approve(d.draft_id)
    with sim.under_scenario("supplier-timeout"):
        out = flow.submit(d.draft_id)
    assert out.state == "accepted" and out.verified and "response to our submission was lost" in out.message
    assert ledger.draft(d.draft_id).status == "accepted" and ledger.draft(d.draft_id).order_id == out.order_id
    assert [o["order_id"] for o in client.find_orders(d.draft_id)] == [out.order_id]
    assert len(ledger.drafts()) == 1


def test_unknown_result_is_persisted_blocks_the_vendor_and_settles_on_restart(records, ledger, client, sim, monkeypatch):
    flow = OrderFlow(client, ledger)
    d = flow.create_draft(ordering.plan_order(records, ledger, "V1"))
    sim.approve(d.draft_id)
    real_find = client.find_orders
    monkeypatch.setattr(client, "find_orders", lambda ref=None: (_ for _ in ()).throw(ConnectionLost("GET", "/api/orders", OSError("down"))))
    with sim.under_scenario("supplier-timeout"):
        out = flow.submit(d.draft_id)
    assert out.state == "unknown" and "rechecked on the next start" in out.message
    assert ledger.draft(d.draft_id).status == "unknown" and "lookup failed" in ledger.draft(d.draft_id).last_error
    assert flow.blocked_vendors() == {"V1": ledger.draft(d.draft_id)}
    with pytest.raises(RuntimeError, match="resolve it before drafting again"):
        flow.create_draft(ordering.plan_order(records, ledger, "V1"))
    monkeypatch.setattr(client, "find_orders", real_find)
    with Ledger(ledger.path) as reopened:  # a new process picks it up
        settled = OrderFlow(client, reopened).reconcile()
    assert [o.state for o in settled] == ["accepted"] and settled[0].order_id.startswith("O-")
    assert "earlier response was lost" in settled[0].message and ledger.draft(d.draft_id).status == "accepted"
    assert len(client.find_orders(d.draft_id)) == 1  # the committed order, exactly once


def test_cancel_never_submits_even_after_approval(records, ledger, flow, client, sim):
    d = flow.create_draft(ordering.plan_order(records, ledger, "V1"))
    flow.cancel(d.draft_id)
    sim.approve(d.draft_id)
    assert flow.reconcile(submit_approved=True) == [] and client.find_orders(d.draft_id) == []
    assert flow.submit(d.draft_id).state == "cancelled" and client.find_orders(d.draft_id) == []


# ----- the CLI across processes --------------------------------------------------------------

def _cli(sim, state_dir, *args):
    env = dict(os.environ, MARBLE_API_BASE=sim.base, MARBLE_AGENT_TOKEN="demo-agent-site-01")
    return subprocess.run([sys.executable, "-m", "assistant", "--dry", "--state-dir", str(state_dir), *args],
                          capture_output=True, text=True, env=env, cwd=ROOT)


def test_cli_propose_draft_approve_resume(sim, tmp_path):
    state = tmp_path / ".marble"
    assert _cli(sim, state, "onboard", "--defaults").returncode == 0
    r = _cli(sim, state, "order", "--propose-only")
    assert r.returncode == 0 and "proposal only; no draft" in r.stdout and '"quantity": 8' in r.stdout
    r = _cli(sim, state, "order", "--yes", "--timeout", "0.5", "--poll-seconds", "0.1")
    assert r.returncode == 3, r.stdout + r.stderr
    draft_id = re.search(r"\[drafted\] (D-[A-Za-z0-9]+)", r.stdout).group(1)
    assert APPROVE_COMMAND.format(draft_id=draft_id) in r.stdout and "not approved after" in r.stdout
    r = _cli(sim, state, "order")  # a second plain run does not stack a new draft on the open one
    assert r.returncode == 3 and "still awaiting approval" in r.stdout
    sim.approve(draft_id)
    r = _cli(sim, state, "order", "--resume")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[accepted]" in r.stdout and "Verified by lookup" in r.stdout and "O-" in r.stdout
    mem = _cli(sim, state, "memory").stdout
    assert draft_id in mem and "accepted" in mem


def test_outcome_note_survives_acceptance_and_old_ledgers_migrate(records, ledger, flow, client, sim, tmp_path):
    d = flow.create_draft(ordering.plan_order(records, ledger, "V1"))
    sim.approve(d.draft_id)
    with sim.under_scenario("supplier-timeout"):
        out = flow.submit(d.draft_id)
    stored = ledger.draft(d.draft_id)
    assert stored.status == "accepted" and stored.last_error is None
    assert "response to our submission was lost" in stored.note and "Verified by lookup" in stored.note
    assert stored.note == out.message
    # a ledger file created before the note column existed gains it on open
    import sqlite3
    old = tmp_path / "old.sqlite"
    con = sqlite3.connect(old)
    con.executescript("CREATE TABLE drafts (draft_id TEXT PRIMARY KEY, vendor_id TEXT NOT NULL, order_json TEXT NOT NULL, "
                      "status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, order_id TEXT, last_error TEXT);"
                      "INSERT INTO drafts VALUES ('D-old','V1','{}','accepted','t','t','O-1',NULL);")
    con.commit(); con.close()
    with Ledger(old) as l:
        assert l.draft("D-old").note is None and l.update_draft("D-old", "accepted", note="migrated").note == "migrated"


def test_edited_lines_are_validated_and_drafted_with_a_note(records, ledger, flow, client, sim, monkeypatch):
    from assistant.orderflow import approve_command
    p = ordering.plan_order(records, ledger, "V1")
    with pytest.raises(ValueError, match="not a catalog SKU"):
        ordering.validate_lines(records, "V1", [{"sku": "V2-PEPPER", "quantity": 1}])
    with pytest.raises(ValueError, match="outside 1..100"):
        ordering.validate_lines(records, "V1", [{"sku": "AW-CHK20", "quantity": 0}])
    with pytest.raises(ValueError, match="appears twice"):
        ordering.validate_lines(records, "V1", [{"sku": "AW-CHK20", "quantity": 1}, {"sku": "AW-CHK20", "quantity": 2}])
    with pytest.raises(ValueError, match="at least one line"):
        ordering.validate_lines(records, "V1", [])
    lines = ordering.validate_lines(records, "V1", [{"sku": "AW-CHK20", "quantity": 6}, {"sku": "V1-EGG", "quantity": 2}])
    assert lines[0]["unit"] == "case" and lines[0]["ingredient"] == "Chicken thigh boneless skinless" and lines[0]["pack"] == "20 lb"
    diff = ordering.lines_diff(p.api_lines, [{"sku": l["sku"], "quantity": l["quantity"]} for l in lines])
    assert diff == ["AW-CHK20: 8 -> 6", "removed V1-CHEESE (was 4)"]
    d = flow.create_draft_from_lines("V1", lines, note="manager edited the proposal: " + "; ".join(diff))
    assert d.order["lines"] == [{"sku": "AW-CHK20", "quantity": 6, "unit": "case"}, {"sku": "V1-EGG", "quantity": 2, "unit": "case"}]
    assert "manager edited" in d.note and d.status == "awaiting_approval"
    assert client.get_draft(d.draft_id)["order"] == d.order
    # the operator's command carries the fixture's port and, when the operator told us, its state path
    cmd = approve_command(d.draft_id, client.base)
    assert cmd.startswith(f"python3 simulator.py approve --draft-id {d.draft_id} --port {sim.port}") and "--state" not in cmd
    monkeypatch.setenv("MARBLE_SIM_STATE", ".state/live.sqlite")
    assert approve_command(d.draft_id, "http://127.0.0.1:8765").endswith(f"--draft-id {d.draft_id} --state .state/live.sqlite")


def test_planner_orders_vendors_by_cutoff_and_sees_drafts(records, ledger, flow, client, sim):
    plan = ordering.planner(records, ledger)
    by = {v["vendor_id"]: v for v in plan}
    assert set(by) == {"V1", "V2", "V3"}
    assert by["V1"]["delivery_date"].isoformat() == "2026-10-07" and by["V1"]["urgency"] in ("today", "this week")
    assert any(r["ingredient"].startswith("Chicken") for r in by["V1"]["runs_out_before_delivery"])
    assert by["V1"]["lines"][0]["sku"] == "AW-CHK20" and by["V1"]["accepted"] == [] and by["V1"]["open_draft"] is None
    assert [v["vendor_id"] for v in plan][0] in ("V2", "V3", "V1")  # sorted by urgency then cutoff, never alphabetically by accident
    assert all(v["hours_to_cutoff"] is None or isinstance(v["hours_to_cutoff"], float) for v in plan)
    d = flow.create_draft(ordering.plan_order(records, ledger, "V1"))
    sim.approve(d.draft_id)
    flow.submit(d.draft_id)
    by2 = {v["vendor_id"]: v for v in ordering.planner(records, ledger)}
    assert by2["V1"]["accepted"][0]["draft_id"] == d.draft_id and by2["V1"]["accepted"][0]["order_id"].startswith("O-")
    assert by2["V1"]["urgency"] == "ordered" and by2["V1"]["lines"] == []  # the accepted order covers the proposal
    assert by2["V1"]["proposed_lines"][0]["sku"] == "AW-CHK20"
    covered = ordering.accepted_cover(ledger.drafts(), "V1")
    assert covered["AW-CHK20"] == by2["V1"]["proposed_lines"][0]["cases"] and ordering.accepted_cover(ledger.drafts(), "V2") == {}
    half = {k: v // 2 for k, v in covered.items()}  # part of it ordered: the rest is still proposed, with what was ordered on the line
    net = ordering.net_lines(ordering.plan_order(records, ledger, "V1"), half)
    assert net and all(n["cases"] == n["proposed"] - n["already_ordered"] and n["quantity"] == n["cases"] for n in net)
