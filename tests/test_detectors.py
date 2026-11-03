import pytest

from assistant import detectors
from assistant.records import load_all


@pytest.fixture(scope="module")
def findings(sim, tmp_path_factory):
    from assistant.api_client import ApiClient
    from assistant.config import Config
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low",
                 state_dir=tmp_path_factory.mktemp("s"), dry=True)
    return detectors.run_all(load_all(ApiClient(cfg)))


def by_id(findings):
    return {f.id: f for f in findings}


def test_headline_is_the_phantom_receipt(findings):
    top = findings[0]
    assert top.id == "phantom_receipt:INV-099" and top.severity == 1
    assert top.numbers["gap_if_received"] == -45.0 and top.numbers["gap_if_not_received"] == -5.0
    assert top.numbers["runway_days"] == pytest.approx(2.0, abs=0.05)
    refs = {c.ref for c in top.evidence}
    assert {"INV-099", "INV-099-L01", "N15"} <= refs
    assert any("N06" in u for u in top.uncertainty) and any("M99" in u for u in top.uncertainty)
    assert "Alder Wholesale" in top.next_step and "INV-099" in top.next_step


def test_every_planted_anomaly_has_a_finding(findings):
    ids = set(by_id(findings))
    expected = {
        "phantom_receipt:INV-099", "unit_price_outlier:INV-010-L01", "header_total_mismatch:INV-009",
        "vendor_name_variance:AC-417", "reference_table_stale:V1", "sku_substitution:ING-CHICKEN",
        "item_without_recipe:M99", "weekend_readiness:M05", "untrusted_instructions:SUP-V1-01",
        "runway_risk:Chicken thigh boneless skinless", "runway_risk:6 inch flour tortilla",
        "count_variance:6 inch flour tortilla",
    }
    assert expected <= ids, expected - ids


def test_ranking_is_deterministic_and_sensible(findings):
    ids = [f.id for f in findings]
    assert ids.index("phantom_receipt:INV-099") < ids.index("unit_price_outlier:INV-010-L01")
    assert ids.index("unit_price_outlier:INV-010-L01") < ids.index("reference_table_stale:V1")
    assert all(a.severity <= b.severity for a, b in zip(findings, findings[1:]))
    assert ids == [f.id for f in sorted(findings, key=detectors.rank_key)]


def test_every_finding_is_cited_scoped_and_stamped(findings):
    for f in findings:
        assert f.evidence, f.id
        assert all(c.site == "SITE-01" for c in f.evidence), f.id
        assert f.next_step and f.summary and f.title
        assert f.computed_at_revision == 1


def test_invoice_findings_carry_money(findings):
    fs = by_id(findings)
    assert fs["unit_price_outlier:INV-010-L01"].usd_at_stake == pytest.approx(378.0)
    assert fs["header_total_mismatch:INV-009"].usd_at_stake == pytest.approx(47.5)
    assert "Juniper Produce" in fs["unit_price_outlier:INV-010-L01"].next_step


def test_reference_table_and_sku_findings(findings):
    fs = by_id(findings)
    stale = fs["reference_table_stale:V1"]
    assert {"V1", "INV-016", "N09"} <= {c.ref for c in stale.evidence}
    assert stale.numbers["observed_days_since_review"] == ["Monday", "Thursday", "Wednesday"]
    sub = fs["sku_substitution:ING-CHICKEN"]
    assert sub.numbers["old_pack"] == 40.0 and sub.numbers["new_pack"] == 20.0
    assert float(sub.numbers["old_unit_cost"]) == 3.2 and float(sub.numbers["new_unit_cost"]) == 3.65
    assert "N11" in {c.ref for c in sub.evidence}


def test_runway_is_medium_when_a_listed_delivery_falls_inside_it(findings):
    fs = by_id(findings)
    tort = fs["runway_risk:6 inch flour tortilla"]
    assert tort.severity == 2 and tort.numbers["days_until_listed_delivery"] == 0
    assert any("order sheet" in u for u in tort.uncertainty)
    assert "runway_risk:Large shell eggs" not in fs  # weekend-only ingredient handled by weekend_readiness


def test_weekend_readiness_flags_eggs(findings):
    f = by_id(findings)["weekend_readiness:M05"]
    assert f.numbers["short"]["Large shell eggs"] == pytest.approx(180.0)
    assert f.urgency_days == 5.0
    assert "N15" in {c.ref for c in f.evidence}


def test_untrusted_instructions_are_flagged_not_followed(findings):
    f = by_id(findings)["untrusted_instructions:SUP-V1-01"]
    assert "bypass" in f.numbers["matched"] and "Alder Wholesale" in f.next_step
    assert "not acted on" in f.summary


def test_after_delivery_correction_the_phantom_clears(client, sim):
    with sim.under_scenario("delivery-correction"):
        fs = detectors.run_all(load_all(client))
    ids = {f.id for f in fs}
    assert "phantom_receipt:INV-099" not in ids
    assert all(f.computed_at_revision == 2 for f in fs)
    chicken = next(f for f in fs if f.id == "count_variance:Chicken thigh boneless skinless")
    assert chicken.numbers["gap"] == pytest.approx(-5.0, abs=0.05)
    assert "N16" in {c.ref for f in fs for c in f.evidence}
