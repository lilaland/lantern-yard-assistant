import pytest

from assistant import api_client
from assistant.api_client import ApiClient, BadRequest, Conflict, ConnectionLost, Forbidden, ServerUnavailable, Unauthorized
from assistant.config import Config

CHICKEN_LINE = [{"sku": "AW-CHK20", "quantity": 2, "unit": "case"}]


def test_health_needs_no_token(client):
    assert client.health() == {"status": "ok", "mode": "agent"}


def test_page_shape_and_revision(client):
    page = client.get_page("pos_daily", limit=10)
    assert len(page.records) == 10
    assert page.total == 92
    assert page.next_offset == 10
    assert page.source == "pos_daily.csv"
    assert page.dataset_revision == 1
    assert all(r["site_id"] == "SITE-01" for r in page.records)


def test_get_all_paginates_and_keeps_order(client):
    coll = client.get_all("pos_daily", page_size=40)
    assert len(coll.records) == coll.total == 92
    assert [r["row_id"] for r in coll.records[:2]] == ["POS-01-M01", "POS-01-M02"]


def test_exact_and_date_filters(client):
    assert client.get_all("pos_daily", pos_item_id="M05").total == 4
    coll = client.get_all("pos_daily", date_from="2026-10-03", date_to="2026-10-04")
    assert {r["business_date"] for r in coll.records} == {"2026-10-03", "2026-10-04"}


def test_unknown_filter_is_bad_request(client):
    with pytest.raises(BadRequest) as exc:
        client.get_page("menu", colour="blue")
    assert exc.value.code == "unknown_filter"


def test_date_filter_on_undated_resource_is_refused_locally(client):
    with pytest.raises(ValueError):
        client.get_page("menu", date_from="2026-09-14")


def test_site_is_pinned_before_any_request(client):
    with pytest.raises(ValueError):
        client.get_page("notes", site_id="SITE-02")
    # And the server enforces it independently of our pin.
    with pytest.raises(Forbidden):
        client._request("GET", "/api/notes", {"site_id": "SITE-02"})


def test_wrong_token_is_unauthorized(sim, tmp_path):
    bad = ApiClient(Config(api_base=sim.base, agent_token="nope", model="m", effort="low", state_dir=tmp_path))
    with pytest.raises(Unauthorized):
        bad.get_page("menu")


def test_server_unavailable_is_distinct_from_connection_lost(tmp_path):
    dead = ApiClient(Config(api_base="http://127.0.0.1:9", agent_token="x", model="m", effort="low",
                            state_dir=tmp_path), timeout=1)
    with pytest.raises(ServerUnavailable):
        dead.health()


def test_client_has_no_approval_or_control_methods():
    names = {n for n in dir(ApiClient) if not n.startswith("_")}
    assert not any("approve" in n or "scenario" in n or "control" in n for n in names)


def test_draft_validation_errors(client):
    with pytest.raises(BadRequest) as e1:
        client.create_draft("V1", [{"sku": "AW-CHK20", "quantity": 2, "unit": "each"}])
    assert e1.value.code == "invalid_order_unit"
    with pytest.raises(BadRequest) as e2:
        client.create_draft("V1", [{"sku": "AW-CHK20", "quantity": 0, "unit": "case"}])
    assert e2.value.code == "quantity_must_be_integer_1_to_100"
    with pytest.raises(BadRequest) as e3:
        client.create_draft("V1", [{"sku": "V3-RICE", "quantity": 1, "unit": "case"}])
    assert e3.value.code == "unknown_or_duplicate_sku"


def test_draft_approve_submit_verify_is_idempotent(client, sim):
    draft = client.create_draft("V1", CHICKEN_LINE)
    assert draft["status"] == "awaiting_approval"
    assert draft["order"] == {"site_id": "SITE-01", "vendor_id": "V1", "lines": CHICKEN_LINE}
    did = draft["draft_id"]

    with pytest.raises(Conflict) as exc:
        client.submit_order(did)
    assert exc.value.code == "approval_required"
    assert client.find_orders(did) == []

    assert sim.approve(did)["status"] == "approved"
    assert client.get_draft(did)["status"] == "approved"

    accepted = client.submit_order(did)
    assert accepted["status"] == "accepted" and accepted["client_reference"] == did
    orders = client.find_orders(did)
    assert len(orders) == 1 and orders[0]["order_id"] == accepted["order_id"]
    assert client.submit_order(did)["order_id"] == accepted["order_id"]


def test_lost_response_is_recoverable_by_reference(client, sim):
    draft = client.create_draft("V1", [{"sku": "V1-EGG", "quantity": 1, "unit": "case"}])
    did = draft["draft_id"]
    sim.approve(did)
    with sim.under_scenario("supplier-timeout"):
        with pytest.raises(ConnectionLost) as exc:
            client.submit_order(did)
        assert exc.value.method == "POST" and exc.value.path == "/api/orders"
        # The supplier committed before dropping the response.
        orders = client.find_orders(did)
        assert len(orders) == 1 and orders[0]["client_reference"] == did
        # Resending the SAME draft returns the SAME order; no duplicate is created.
        again = client.submit_order(did)
        assert again["order_id"] == orders[0]["order_id"]
        assert len(client.find_orders(did)) == 1


def test_delivery_correction_bumps_revision(client, sim):
    before = client.get_page("invoice_headers", invoice_id="INV-099")
    assert before.dataset_revision == 1
    assert before.records[0]["status"] == "received" and before.records[0]["source_revision"] == "1"
    with sim.under_scenario("delivery-correction"):
        after = client.get_page("invoice_headers", invoice_id="INV-099")
        assert after.dataset_revision == 2
        row = after.records[0]
        assert (row["status"], row["delivery_date"], row["source_revision"]) == ("scheduled", "2026-10-08", "2")
        notes = client.get_all("notes")
        assert any(n["note_id"] == "N16" for n in notes.records)
    assert client.get_page("invoice_headers", invoice_id="INV-099").dataset_revision == 1
    assert not any(n["note_id"] == "N16" for n in client.get_all("notes").records)


def test_resources_constant_matches_server(client):
    for resource in api_client.RESOURCES:
        assert client.get_page(resource, limit=1).source == f"{resource}.csv"
