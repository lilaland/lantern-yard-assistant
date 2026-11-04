"""Interactive onboarding: the question plan, what each answer records, unknowns, idempotency, the CLI."""
import os
import subprocess
import sys

import pytest

from assistant import detectors, onboarding, policies
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.records import load_all

ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="module")
def records(sim, tmp_path_factory):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low",
                 state_dir=tmp_path_factory.mktemp("s"), dry=True)
    return load_all(ApiClient(cfg))


@pytest.fixture(scope="module")
def findings(records):
    return detectors.run_all(records)


@pytest.fixture
def ledger(tmp_path):
    with Ledger(tmp_path / "state.sqlite") as l:
        yield l


def answer_all(ledger, records, findings, answers=None, again=False):
    plan = onboarding.question_plan(records, findings, ledger, again)
    out = {}
    for q in plan:
        out[q.id] = onboarding.apply_answer(ledger, q, (answers or {}).get(q.id, q.default), records.revision)
    return plan, out


def active_values(ledger, *kinds):
    return {(b.kind, b.subject): b.value for k in kinds for b in ledger.active(k)}


def test_plan_is_six_questions_each_with_a_default_and_an_unknown(records, findings, ledger):
    plan = onboarding.question_plan(records, findings, ledger)
    assert [q.id for q in plan] == ["inbound", "route", "sku", "missing_recipe", "price", "ordering"]
    for q in plan:
        keys = [o.key for o in q.options]
        assert q.default in keys and "unknown" in keys and q.title and q.why
        assert q.evidence, q.id
        assert all(" SITE-01 " in e for e in q.evidence), q.id  # every evidence line is a citation
    assert "40 lb of Chicken" in plan[0].why and "{" not in plan[0].why


def test_default_answers_record_the_documented_defaults_under_a_human_source(records, findings, ledger):
    plan, out = answer_all(ledger, records, findings)
    recorded = active_values(ledger, "policy", "preference")
    with Ledger(ledger.path.with_name("defaults.sqlite")) as other:
        policies.write_defaults(other, records)
        documented = active_values(other, "policy", "preference")
    assert recorded == documented
    assert all(b.source == "human:onboarding" for k in ("policy", "preference") for b in ledger.active(k))
    facts = {b.subject: b for b in ledger.active("assertion")}
    assert facts["invoice.INV-099.physically_received"].value == {"received": False}
    assert facts["invoice.INV-099.physically_received"].source == "human:onboarding"
    assert facts["vendor.V1.delivery_days"].source == "human:onboarding" and facts["vendor.V1.delivery_days"].recheck == "never"
    assert "invoice.INV-099.delivery_date" not in facts  # the interactive path records only what a person answered
    assert all(o.action == "added" for outs in out.values() for o in outs[1])
    assert set(onboarding.answers(ledger)) == {q.id for q in plan}


def test_unknown_keeps_facts_at_evidence_level(records, findings, ledger):
    answer_all(ledger, records, findings, {"inbound": "unknown", "route": "unknown"})
    facts = {b.subject: b for b in ledger.active("assertion")}
    assert ledger.value("policy", "inbound.on_hand_rule") == {"rule": "physical_confirmation"}
    assert facts["invoice.INV-099.physically_received"].source == "note:N15"
    assert facts["vendor.V1.delivery_days"].source == "note:N09" and facts["vendor.V1.delivery_days"].recheck != "never"
    assert onboarding.answers(ledger)["inbound"] == "unknown"


def test_answers_change_what_the_detectors_do(records, findings, ledger):
    answer_all(ledger, records, findings, {"inbound": "office", "missing_recipe": "ignore", "price": 11})
    params = policies.detector_params(ledger)
    assert params.on_hand_rule == "office_status" and params.missing_recipe_rule == "ignore" and params.price_outlier_ratio == 11.0
    ids = {f.id: f for f in detectors.run_all(records, params)}
    assert ids["phantom_receipt:INV-099"].severity == 3
    assert not any(i.startswith("item_without_recipe") for i in ids)
    assert "unit_price_outlier:INV-010-L01" not in ids  # 10x is below an 11x threshold
    assert ids["count_variance:Chicken thigh boneless skinless"].numbers["gap"] == pytest.approx(-45.0)


def test_arrived_answer_counts_the_cases(records, findings, ledger):
    answer_all(ledger, records, findings, {"inbound": "arrived"})
    params = policies.detector_params(ledger)
    assert params.confirmed_received == {"INV-099"}
    ids = {f.id: f for f in detectors.run_all(records, params)}
    assert ids["phantom_receipt:INV-099"].severity == 3 and "confirmed" in ids["phantom_receipt:INV-099"].next_step


def test_rerun_asks_nothing_until_again_or_a_new_class(records, findings, ledger, client, sim):
    answer_all(ledger, records, findings)
    assert onboarding.question_plan(records, findings, ledger) == []
    assert [q.id for q in onboarding.question_plan(records, findings, ledger, again=True)] == \
           ["inbound", "route", "sku", "missing_recipe", "price", "ordering"]
    plan, out = answer_all(ledger, records, findings, {"ordering": {"buffer_days": 2}}, again=True)
    assert all(o.action in ("unchanged", "superseded") for outs in out.values() for o in outs[1])
    assert ledger.value("preference", "order.buffer_days") == {"days": 2}
    assert ledger.counts()["preference"]["superseded"] == 1  # only the buffer changed
    with sim.under_scenario("delivery-correction"):
        rec2 = load_all(client)
        plan2 = onboarding.question_plan(rec2, detectors.run_all(rec2), Ledger(ledger.path.with_name("fresh.sqlite")))
    inbound = next(q for q in plan2 if q.id == "inbound")
    assert "scheduled" in inbound.why and "2026-10-08" in inbound.why  # still asked: the invoice is still not on hand


def test_ordering_values_are_validated(records, findings, ledger):
    with pytest.raises(ValueError):
        onboarding.ordering_beliefs({"buffer_days": 9})
    with pytest.raises(ValueError):
        onboarding.ordering_beliefs({"cover_through": "Funday"})
    with pytest.raises(ValueError):
        onboarding.ordering_beliefs({"max_unflagged_usd": 0})
    q = next(q for q in onboarding.question_plan(records, findings, ledger) if q.id == "ordering")
    with pytest.raises(ValueError, match="needs values"):
        onboarding.apply_answer(ledger, q, "custom")
    opt, outs = onboarding.apply_answer(ledger, q, {"buffer_days": 2, "uplift": 0.5, "cover_through": "Saturday", "max_unflagged_usd": 800})
    assert {o.belief.subject: o.belief.value for o in outs} == {
        "order.buffer_days": {"days": 2}, "order.cover_through": {"weekday": "Saturday"},
        "order.brunch_uplift": {"uplift": 0.5}, "order.max_unflagged_usd": {"usd": 800.0}}
    assert all("set during onboarding" in o.belief.statement for o in outs)
    with pytest.raises(ValueError, match="unknown answer"):
        onboarding.apply_answer(ledger, q, "maybe")


def test_parse_answers_forms():
    assert onboarding.parse_answers('{"inbound": "office", "ordering": {"buffer_days": 2}}') == {"inbound": "office", "ordering": {"buffer_days": 2}}
    assert onboarding.parse_answers("inbound=office, price=5") == {"inbound": "office", "price": "5"}


def _cli(sim, state_dir, *args):
    env = dict(os.environ, MARBLE_API_BASE=sim.base, MARBLE_AGENT_TOKEN="demo-agent-site-01")
    return subprocess.run([sys.executable, "-m", "assistant", "--dry", "--state-dir", str(state_dir), *args],
                          capture_output=True, text=True, env=env, cwd=ROOT)


def test_cli_onboarding_then_brief_applies_the_answers(sim, tmp_path):
    state = tmp_path / ".marble"
    r = _cli(sim, state, "onboard", "--answers", "inbound=office,missing_recipe=ignore,price=5")
    assert r.returncode == 0, r.stderr
    assert "6 question(s)" in r.stdout and r.stdout.count("recorded ") >= 10 and "Onboarding complete" in r.stdout
    assert "rule=office_status" in r.stdout and "ratio=5" in r.stdout
    brief = _cli(sim, state, "brief").stdout
    assert "[LOW] INV-099 is marked received" in brief and "item_without_recipe" not in brief
    assert "Retained (human choices" in brief and "4 policies, 6 preferences" in brief
    again = _cli(sim, state, "onboard")
    assert again.returncode == 0 and "Nothing new to ask" in again.stdout
    bad = _cli(sim, state, "onboard", "--again", "--answers", "inbound=maybe")
    assert bad.returncode == 2 and "rejected" in bad.stderr
