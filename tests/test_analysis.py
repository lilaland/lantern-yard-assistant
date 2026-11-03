from datetime import date
from decimal import Decimal

import pytest

from assistant import analysis as an
from assistant.records import load_all

CHICKEN = "Chicken thigh boneless skinless"
EGGS = "Large shell eggs"
TORTILLA = "6 inch flour tortilla"


@pytest.fixture(scope="module")
def records(sim, tmp_path_factory):
    from assistant.api_client import ApiClient
    from assistant.config import Config
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low",
                 state_dir=tmp_path_factory.mktemp("s"), dry=True)
    return load_all(ApiClient(cfg))


def test_theoretical_usage_totals(records):
    u = an.theoretical_usage(records.sales, records.recipes)
    assert u.totals[CHICKEN] == pytest.approx(519.1)
    assert u.totals[EGGS] == pytest.approx(528.0)
    assert u.totals[TORTILLA] == pytest.approx(3432.0)
    assert u.unknown_items == {"M99": 32}
    assert u.portions["M05"] == 264 and u.window == (date(2026, 9, 14), date(2026, 10, 4))
    assert u.sales_cite is not None and "92 rows" in u.sales_cite.ref


def test_weekly_chicken_usage(records):
    weeks = [(date(2026, 9, 14), date(2026, 9, 20)), (date(2026, 9, 21), date(2026, 9, 27)),
             (date(2026, 9, 28), date(2026, 10, 4))]
    got = [round(an.theoretical_usage(records.sales, records.recipes, a, b).totals[CHICKEN], 1) for a, b in weeks]
    assert got == [177.0, 171.1, 171.0]


def test_purchases_use_captured_pack_quantity(records):
    p = an.purchases(records)
    assert p.totals[CHICKEN] == pytest.approx(580.0)
    assert p.spend[CHICKEN] == Decimal("1955.00")
    assert p.unmapped == []
    assert an.purchases(records, exclude_invoices=("INV-099",)).totals[CHICKEN] == pytest.approx(540.0)


def test_reconciliation_pinned_numbers(records):
    rec = an.reconcile_all(records)
    c = rec[CHICKEN]
    assert (round(c.expected_close, 1), c.counted, round(c.gap, 1)) == (92.9, 47.9, -45.0)
    assert rec[EGGS].gap == pytest.approx(0.0)
    assert rec["Long grain rice, dry"].gap == pytest.approx(0.0, abs=0.01)
    assert rec[TORTILLA].gap == pytest.approx(-48.0)
    assert rec["Mixed bell peppers, trimmed"].gap == pytest.approx(-3.0, abs=0.05)
    assert rec["Shredded cheddar"].gap == pytest.approx(-2.4, abs=0.05)
    excl = an.reconcile_all(records, exclude_invoices=("INV-099",))[CHICKEN]
    assert (round(excl.expected_close, 1), round(excl.gap, 1)) == (52.9, -5.0)
    assert any(ct.ref == "COUNT-CLOSE-CHICKEN" for ct in c.cites)
    assert any(ct.ref == "INV-099-L01" for ct in c.cites) and not any(ct.ref == "INV-099-L01" for ct in excl.cites)


def test_invoice_checks_find_the_planted_anomalies(records):
    issues = {(i.kind, i.invoice_id): i for i in an.invoice_checks(records)}
    mismatch = issues[("header_total_mismatch", "INV-009")]
    assert mismatch.numbers["difference"] == Decimal("47.50")
    outlier = issues[("unit_price_outlier", "INV-010")]
    assert outlier.numbers["ratio"] == pytest.approx(10.0)
    assert outlier.numbers["amount_vs_median"] == Decimal("378.00")
    phantom = issues[("status_inconsistent_with_date", "INV-099")]
    assert phantom.numbers["delivery_date"] == date(2026, 10, 6)
    names = issues[("vendor_name_variance", "INV-001")]
    assert names.numbers["names"] == ["ALDER WHOLESALE LLC", "Alder Wholesale"]
    assert len(issues) == 4


def test_price_history_and_change(records):
    pts = an.price_history(records, CHICKEN)
    assert [float(p.unit_cost) for p in pts] == [3.2, 3.2, 3.2, 3.2, 3.65, 3.65, 3.65]
    changes = an.price_changes(pts)
    assert len(changes) == 1 and changes[0][1].invoice_id == "INV-013" and changes[0][1].pack_quantity == 20.0
    assert an.latest_unit_cost(records, "Whole avocado") == Decimal("0.80")


def test_daily_burn_and_runway(records):
    burn, window = an.daily_burn(records, 7)
    assert window == (date(2026, 9, 28), date(2026, 10, 4))
    assert burn[CHICKEN] == pytest.approx(171.0 / 7, abs=0.05)
    assert an.runway_days(47.9, burn[CHICKEN]) == pytest.approx(1.96, abs=0.02)
    assert an.runway_days(10, 0) is None
    assert EGGS not in an.daily_service_ingredients(records)
    assert CHICKEN in an.daily_service_ingredients(records)


def test_weekend_forecast(records):
    f = an.weekend_forecast(records, "M05")
    assert f.days == [date(2026, 10, 10), date(2026, 10, 11)]
    assert f.portions_per_day == pytest.approx(66.0)
    assert f.needs[EGGS] == pytest.approx(264.0)
    assert an.weekend_forecast(records, "M05", uplift=0.25).needs[EGGS] == pytest.approx(330.0)
    assert an.weekend_forecast(records, "M99").needs == {}


def test_deliveries_by_weekday(records):
    alder = an.deliveries_by_weekday(records, "AC-417", since=date(2026, 9, 14))
    assert set(alder) == {"Monday", "Thursday", "Wednesday"}
    assert [h.invoice_id for h in alder["Wednesday"]] == ["INV-016"]
    assert set(an.deliveries_by_weekday(records, "CP-609")) == {"Monday"}


def test_next_weekend():
    assert an.next_weekend(date(2026, 10, 5)) == [date(2026, 10, 10), date(2026, 10, 11)]
    assert an.next_weekend(date(2026, 10, 10)) == [date(2026, 10, 17), date(2026, 10, 18)]


def test_on_hand_rules(records):
    assert [h.invoice_id for h in an.not_on_hand_invoices(records)] == ["INV-099"]
    assert an.unreceived_invoices(records) == []
