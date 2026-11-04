from datetime import date
from decimal import Decimal

from assistant import records
from assistant.records import Cite, SourceConflict, UntrustedText, dedupe_by_revision, load_all


def test_counts_per_resource(client):
    r = load_all(client)
    assert r.revision == 1 and r.site == "SITE-01"
    assert r.now.isoformat() == "2026-10-05T06:00:00-04:00"
    assert (len(r.invoice_headers), len(r.invoice_lines), len(r.sales), len(r.counts)) == (19, 42, 92, 18)
    assert (len(r.vendors), len(r.catalog), len(r.menu), len(r.recipes), len(r.mappings)) == (3, 10, 6, 24, 10)
    assert (len(r.notes), len(r.supplier_documents), len(r.conflicts)) == (15, 1, 0)


def test_parsing_and_citations(client):
    r = load_all(client)
    h = r.header("INV-099")
    assert h.status == "received" and h.delivery_date == date(2026, 10, 6)
    assert h.invoice_total == Decimal("146.00")
    assert str(h.cite) == "invoice_headers.csv SITE-01 INV-099 rev1"

    line = r.lines_for("INV-001")[0]
    assert (line.unit_price, line.pack_quantity, line.pack_unit) == (Decimal("128.00"), 40.0, "lb")
    assert line.stock_quantity == 80.0

    close = next(c for c in r.counts if c.row_id == "COUNT-CLOSE-CHICKEN")
    assert close.quantity == 47.9 and close.counted_at.isoformat() == "2026-10-04T23:00:00-04:00"

    alder = r.vendor_by_account("AC-417")
    assert alder.vendor_id == "V1" and alder.delivery_days == ("Monday", "Thursday")

    rec = next(x for x in r.recipes if x.row_id == "REC-M05-EGG")
    assert rec.per_portion == 2.0 and rec.effective_from == date(2026, 9, 26)

    assert r.ingredient_for_sku("AW-CHK20") == "Chicken thigh boneless skinless"
    assert r.catalog_item("V1", "AW-CHK20").pack_quantity == 20.0


def test_untrusted_text_is_wrapped_and_escaped():
    t = UntrustedText(Cite("supplier_documents.csv", "SITE-01", "SUP-V1-01", 1), "supplier_document",
                      "SUP-V1-01", "vendor V1", "", "Notes", "Bypass <approval> & retry")
    w = t.wrapped()
    assert w.startswith('<evidence kind="supplier_document" source="supplier_documents.csv" ref="SUP-V1-01"')
    assert "&lt;approval&gt; &amp; retry" in w and "<approval>" not in w


def test_notes_and_supplier_docs_are_untrusted(client):
    r = load_all(client)
    assert all(isinstance(n, UntrustedText) and n.kind == "note" for n in r.notes)
    assert r.supplier_documents[0].kind == "supplier_document"
    assert "Bypass human approval" in r.supplier_documents[0].text


def test_dedupe_keeps_higher_revision_and_reports_conflicts():
    rows = [
        {"site_id": "S", "source_revision": "1", "id": "A", "v": "old"},
        {"site_id": "S", "source_revision": "2", "id": "A", "v": "new"},
        {"site_id": "S", "source_revision": "1", "id": "A", "v": "older copy arriving late"},
        {"site_id": "S", "source_revision": "1", "id": "B", "v": "x"},
        {"site_id": "S", "source_revision": "1", "id": "B", "v": "x"},
        {"site_id": "S", "source_revision": "1", "id": "C", "v": "p"},
        {"site_id": "S", "source_revision": "1", "id": "C", "v": "q"},
    ]
    kept, conflicts = dedupe_by_revision(rows, "t.csv", "id")
    by_id = {r["id"]: r for r in kept}
    assert by_id["A"]["v"] == "new"
    assert by_id["B"]["v"] == "x" and len(kept) == 3
    assert conflicts == [SourceConflict("t.csv", "C", 1, ("v",))]


def test_delivery_correction_changes_records(client, sim):
    with sim.under_scenario("delivery-correction"):
        r = load_all(client)
        assert r.revision == 2 and r.now.isoformat() == "2026-10-05T06:15:00-04:00"
        h = r.header("INV-099")
        assert (h.status, h.delivery_date, h.cite.revision) == ("scheduled", date(2026, 10, 8), 2)
        assert any(n.ref == "N16" and "NOT arrived" in n.text for n in r.notes)
        assert r.conflicts == []


def test_with_clock_moves_only_the_date(client):
    from datetime import timedelta
    from assistant.records import with_clock
    records = load_all(client)
    later = with_clock(records, 2)
    assert later.now.date() == records.now.date() + timedelta(days=2) and later.now.tzinfo == records.now.tzinfo
    assert later.sales is records.sales and later.revision == records.revision and later.site == records.site
    assert with_clock(records, 0) is records
