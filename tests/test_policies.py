"""Policies: defaults are recorded with sources, they shape findings, and rechecks behave."""
import pytest

from assistant import detectors, policies, render
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.records import load_all


@pytest.fixture(scope="module")
def records(sim, tmp_path_factory):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low",
                 state_dir=tmp_path_factory.mktemp("s"), dry=True)
    return load_all(ApiClient(cfg))


@pytest.fixture
def ledger(tmp_path):
    with Ledger(tmp_path / "state.sqlite") as l:
        yield l


def by_id(findings):
    return {f.id: f for f in findings}


def test_defaults_are_recorded_with_their_sources(ledger, records):
    outcomes = policies.write_defaults(ledger, records)
    assert len(outcomes) == 14 and {o.action for o in outcomes} == {"added"}
    human = [o.belief for o in outcomes if o.belief.kind in ("policy", "preference")]
    assert len(human) == 10 and all(b.source == "human:onboarding-defaults" and b.recheck == "never" for b in human)
    facts = {o.belief.subject: o.belief for o in outcomes if o.belief.kind == "assertion"}
    assert facts["invoice.INV-099.delivery_date"].value == {"date": "2026-10-06"}
    assert facts["invoice.INV-099.delivery_date"].source == "invoice:INV-099@rev1"
    assert facts["invoice.INV-099.delivery_date"].recheck == "on_revision_change"
    assert facts["invoice.INV-099.physically_received"].value == {"received": False}
    assert facts["invoice.INV-099.physically_received"].source == "note:N15"
    assert facts["vendor.V1.delivery_days"].value == {"days": ["Monday", "Wednesday"], "cutoff": "noon the day before"}
    assert facts["vendor.V1.delivery_days"].source == "note:N09"


def test_params_come_from_the_ledger(ledger, records):
    assert policies.detector_params(ledger) == detectors.Params()  # empty ledger: detector defaults
    policies.write_defaults(ledger, records)
    p = policies.detector_params(ledger)
    assert p.on_hand_rule == "physical_confirmation" and p.brunch_uplift == 0.25 and p.price_outlier_ratio == 2.0
    assert p.delivery_days == {"V1": ("Monday", "Wednesday")}
    assert p.confirmed_not_received == {"INV-099"} and p.confirmed_received == frozenset()
    assert {"on_hand_rule", "brunch_uplift", "price_outlier_ratio", "delivery_days:V1", "received:INV-099"} <= set(p.applied)


def test_defaults_shape_the_findings_and_name_themselves(ledger, records):
    policies.write_defaults(ledger, records)
    fs = by_id(detectors.run_all(records, policies.detector_params(ledger)))
    top = fs["phantom_receipt:INV-099"]
    assert top.severity == 1
    assert any(a.startswith("inbound.on_hand_rule=physical_confirmation") for a in top.applied_policies)
    assert any(a.startswith("invoice.INV-099.physically_received=False (note:N15)") for a in top.applied_policies)
    assert top.next_step.startswith("The manager confirmed the cases are not here.")
    wk = fs["weekend_readiness:M05"]
    assert wk.numbers["uplift"] == 0.25 and wk.numbers["short"]["Large shell eggs"] == pytest.approx(246.0)
    assert any(a.startswith("order.brunch_uplift=0.25") for a in wk.applied_policies)
    assert "25% uplift" in wk.uncertainty[0]
    stale = fs["reference_table_stale:V1"]
    assert stale.numbers["planning_days"] == ["Monday", "Wednesday"] and "Planning already uses" in stale.next_step
    chicken = fs["runway_risk:Chicken thigh boneless skinless"]
    assert "Monday, Wednesday per the recorded route change" in chicken.summary
    assert any("note:N09" in a for a in chicken.applied_policies)
    assert "Applied:" in render.finding_block(top)


def test_a_preference_changes_the_brief(ledger, records):
    policies.write_defaults(ledger, records)
    assert "unit_price_outlier:INV-010-L01" in by_id(detectors.run_all(records, policies.detector_params(ledger)))
    o = ledger.record("preference", "invoices.price_anomaly_ratio", "only flag 20x", {"ratio": 20}, "human:ask")
    assert o.action == "superseded"
    assert "unit_price_outlier:INV-010-L01" not in by_id(detectors.run_all(records, policies.detector_params(ledger)))
    ledger.record("preference", "invoices.price_anomaly_ratio", "back to 2x", {"ratio": 2}, "human:ask")
    assert "unit_price_outlier:INV-010-L01" in by_id(detectors.run_all(records, policies.detector_params(ledger)))


def test_a_policy_changes_how_inv099_is_treated(ledger, records):
    # Trust the office status: the 40 lb count as on hand, so the chicken gap is the big one.
    ledger.record("policy", "inbound.on_hand_rule", "trust the office", {"rule": "office_status"}, "human:ask")
    fs = by_id(detectors.run_all(records, policies.detector_params(ledger)))
    assert fs["phantom_receipt:INV-099"].severity == 3 and "Policy trusts the office" in fs["phantom_receipt:INV-099"].next_step
    assert fs["count_variance:Chicken thigh boneless skinless"].numbers["gap"] == pytest.approx(-45.0)
    assert fs["count_variance:Chicken thigh boneless skinless"].severity == 1
    assert policies.treat_inbound("INV-099", "received", None, records, ledger) is True
    # Default policy, but the manager later says the cases did arrive: same effect, different reason.
    with Ledger(ledger.path.with_name("other.sqlite")) as l2:
        policies.write_defaults(l2, records)
        assert policies.treat_inbound("INV-099", "received", None, records, l2) is False
        l2.record("assertion", "invoice.INV-099.physically_received", "Maya found the cases", {"received": True}, "human:ask")
        fs2 = by_id(detectors.run_all(records, policies.detector_params(l2)))
        assert fs2["phantom_receipt:INV-099"].severity == 3 and "confirmed" in fs2["phantom_receipt:INV-099"].next_step
        assert fs2["count_variance:Chicken thigh boneless skinless"].numbers["gap"] == pytest.approx(-45.0)
        assert policies.treat_inbound("INV-099", "received", None, records, l2) is True


def test_order_prefs_defaults(ledger, records):
    assert policies.order_prefs(ledger) == policies.OrderPrefs()
    policies.write_defaults(ledger, records)
    p = policies.order_prefs(ledger)
    assert (p.buffer_days, p.cover_through, p.uplift, p.max_unflagged_usd) == (1, "Sunday", 0.25, 1000.0)
    assert p.sku_overrides == {"ING-CHICKEN": {"vendor_id": "V1", "sku": "AW-CHK20"}}
    assert p.count_units == {"ING-CHICKEN": "lb"}


def test_recheck_first_run_repeat_run_and_revision_change(ledger, records, client, sim):
    policies.write_defaults(ledger, records)
    params = policies.detector_params(ledger)
    r1 = policies.recheck(ledger, records, detectors.run_all(records, params))
    assert r1.first_run and len(r1.observations_new) == 17 and r1.retained == {"policy": 4, "preference": 6}
    r1b = policies.recheck(ledger, records, detectors.run_all(records, params))
    assert not r1b.first_run and not r1b.revision_changed
    assert (len(r1b.observations_new), r1b.observations_unchanged, len(r1b.observations_changed),
            len(r1b.observations_cleared)) == (0, 17, 0, 0)
    assert ledger.counts()["observation"] == {"active": 17, "superseded": 0, "conflicted": 0}
    with sim.under_scenario("delivery-correction"):
        rec2 = load_all(client)
        fs2 = detectors.run_all(rec2, policies.detector_params(ledger))
        r2 = policies.recheck(ledger, rec2, fs2)
    assert r2.revision_changed and (r2.previous_revision, r2.revision) == (1, 2)
    (cleared, reason), = r2.observations_cleared
    assert cleared.subject == "phantom_receipt:INV-099" and "scheduled" in reason and "2026-10-08" in reason
    assert r2.observations_unchanged == 16 and r2.observations_new == [] and r2.observations_changed == []
    sup = {old.subject: (old.value, new.value) for old, new in r2.assertions_superseded}
    assert sup == {"invoice.INV-099.status": ({"status": "received"}, {"status": "scheduled"}),
                   "invoice.INV-099.delivery_date": ({"date": "2026-10-06"}, {"date": "2026-10-08"})}
    assert r2.assertions_conflicted == [] and ledger.conflicts() == []
    assert ledger.one("assertion", "invoice.INV-099.delivery_date").source == "invoice:INV-099@rev2"
    assert ledger.value("preference", "order.brunch_uplift") == {"uplift": 0.25}  # retained, untouched
    chain = ledger.why("phantom_receipt:INV-099")
    assert [b.status for b in chain] == ["superseded", "active"] and chain[1].value["cleared"] is True
    footer = render.retained_block(r2, ledger, "2026-11-03T16:00")
    assert "revision 1 -> 2" in footer and "cleared: phantom_receipt:INV-099" in footer and "superseded: invoice.INV-099" in footer
    assert "Needs you" not in footer


def test_a_human_statement_contradicted_by_a_correction_is_flagged_not_overwritten(ledger, records, client, sim):
    policies.write_defaults(ledger, records)
    said = ledger.record("assertion", "invoice.INV-099.delivery_date", "Alder told Maya it lands Tuesday",
                         {"date": "2026-10-06"}, "human:ask")
    assert said.action == "superseded"  # the person's word replaces the rev-1 invoice reading
    with sim.under_scenario("delivery-correction"):
        rec2 = load_all(client)
        r2 = policies.recheck(ledger, rec2, detectors.run_all(rec2, policies.detector_params(ledger)))
        # a second brief at the same revision must not re-flag or churn
        r2b = policies.recheck(ledger, rec2, detectors.run_all(rec2, policies.detector_params(ledger)))
    (old, new), = r2.assertions_conflicted
    assert old.source == "human:ask" and old.value == {"date": "2026-10-06"}
    assert new.source == "invoice:INV-099@rev2" and new.value == {"date": "2026-10-08"}
    assert ledger.get(old.id).status == "conflicted" and ledger.conflicts()[0][0].id == old.id
    assert r2b.assertions_conflicted == [] and len(ledger.conflicts()) == 1
    footer = render.retained_block(r2, ledger, "2026-11-03T16:00")
    assert "Needs you" in footer and "human:ask said date=2026-10-06" in footer and "invoice:INV-099@rev2 says date=2026-10-08" in footer
    assert "nothing was overwritten" in footer
