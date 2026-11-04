"""Model-facing tools: allowlists, schemas, evidence wrapping, and the memory tool's guard rails."""
import json

import pytest

from assistant import detectors, policies, tools
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
def ctx(records, tmp_path):
    with Ledger(tmp_path / "state.sqlite") as ledger:
        policies.write_defaults(ledger, records)
        params = policies.detector_params(ledger)
        yield tools.ToolContext(records, ledger, "ask", params, detectors.run_all(records, params))


def test_allowlists_by_mode():
    brief, ask, order = tools.allowed("brief"), tools.allowed("ask"), tools.allowed("order")
    assert "record_belief" not in brief and "record_belief" in ask
    assert set(brief) == set(order) == set(tools.allowed("onboard")) and set(brief) < set(ask)
    # only `ask` lets the model record a belief; onboarding and ordering record from the operator's typed answers
    assert not any(n.startswith(("create_draft", "submit", "propose")) for n in tools.REGISTRY)
    forbidden = {"approve", "scenario", "control", "state", "sqlite", "site"}
    for name in tools.REGISTRY:
        assert not any(w in name for w in forbidden), name
    with pytest.raises(tools.ToolError):
        tools.allowed("admin")
    for d in tools.definitions("order"):  # strict schemas: every property required, nothing extra
        schema = d["input_schema"]
        assert d["strict"] is True and schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert "site_id" not in schema["properties"]


def test_schemas_respect_the_api_limits():
    """The API allows at most 16 union-typed parameters per request and no anyOf; we use none at all."""
    for mode in tools.MODE_SETS:
        unions = [(d["name"], k) for d in tools.definitions(mode) for k, v in d["input_schema"]["properties"].items()
                  if isinstance(v.get("type"), list) or "anyOf" in v or "oneOf" in v]
        assert unions == [], (mode, unions)
    out = tools.execute.__wrapped__ if hasattr(tools.execute, "__wrapped__") else None  # noqa: F841
    from assistant.modes.onboard import wording_schema
    w = wording_schema(["inbound", "route"])
    assert w["additionalProperties"] is False and set(w["properties"]) == {"inbound", "route"} and w["required"] == ["inbound", "route"]


def test_empty_string_and_zero_mean_none(ctx):
    a = tools.execute(ctx, "list_notes", {"contains": "", "date_from": "", "date_to": ""})
    b = tools.execute(ctx, "list_notes", {"contains": None, "date_from": None, "date_to": None})
    assert a["count"] == b["count"] == 15
    page = tools.execute(ctx, "list_sales", {"pos_item_id": "", "date_from": "", "date_to": "", "offset": 0})
    assert page["offset"] == 0 and page["total"] == 92


def test_every_read_and_analysis_tool_runs_with_null_arguments(ctx):
    skip = {"reconcile_ingredient": {"ingredient": "Large shell eggs"}, "price_history": {"ingredient": "chicken"},
            "weekend_forecast": {"pos_item_id": "M05"}, "explain": {"subject": "order.buffer_days"},
            "record_belief": None}
    for name, t in tools.REGISTRY.items():
        if skip.get(name, {}) is None:
            continue
        args = {k: None for k in t.properties}
        args.update(skip.get(name, {}))
        out = tools.execute(ctx, name, args)
        json.dumps(out)  # JSON-ready
        assert isinstance(out, dict) and out, name


def test_free_text_is_only_returned_as_wrapped_evidence(ctx):
    notes = tools.execute(ctx, "list_notes", {"contains": "received", "date_from": None, "date_to": None})
    assert notes["count"] == 1 and notes["notes"][0]["ref"] == "N15"
    ev = notes["notes"][0]["evidence"]
    assert ev.startswith('<evidence kind="note" source="notes.csv" ref="N15"') and ev.endswith("</evidence>")
    assert "text" not in notes["notes"][0]
    docs = tools.execute(ctx, "list_supplier_documents", {})
    sup = next(d for d in docs["documents"] if d["ref"] == "SUP-V1-01")
    assert "<evidence" in sup["evidence"] and "&lt;" not in sup["evidence"] or "<" not in sup["evidence"].split(">", 1)[1].rsplit("<", 1)[0]
    dated = tools.execute(ctx, "list_notes", {"contains": None, "date_from": "2026-10-01", "date_to": "2026-10-04"})
    assert [n["ref"] for n in dated["notes"]] == ["N13", "N14", "N15"]
    with pytest.raises(tools.ToolError):
        tools.execute(ctx, "list_notes", {"contains": None, "date_from": "yesterday", "date_to": None})


def test_read_tools_filter_and_page(ctx):
    inv = tools.execute(ctx, "list_invoices", {"invoice_id": "INV-099", "vendor_account": None, "status": None,
                                               "date_from": None, "date_to": None, "offset": None})
    assert inv["total"] == 1 and inv["records"][0]["lines"][0]["stock_quantity"] == 40.0
    assert inv["records"][0]["cite"] == "invoice_headers.csv SITE-01 INV-099 rev1"
    sales = tools.execute(ctx, "list_sales", {"pos_item_id": "M05", "date_from": "2026-09-26", "date_to": None, "offset": None})
    assert sales["total"] == 4 and all(r["pos_item_id"] == "M05" for r in sales["records"])
    page = tools.execute(ctx, "list_sales", {"pos_item_id": None, "date_from": None, "date_to": None, "offset": 50})
    assert page["offset"] == 50 and page["total"] == 92 and page["next_offset"] is None and len(page["records"]) == 42
    cat = tools.execute(ctx, "list_catalog", {"vendor_id": "V1"})
    chk = next(c for c in cat["catalog"] if c["sku"] == "AW-CHK20")
    assert chk["ingredient_id"] == "ING-CHICKEN" and chk["order_unit"] == "case"


def test_analysis_tools_match_the_detectors_and_policies(ctx):
    all_f = tools.execute(ctx, "get_findings", {"finding_id": None})
    assert [f["id"] for f in all_f["findings"]] == [f.id for f in ctx.findings]
    one = tools.execute(ctx, "get_findings", {"finding_id": "phantom_receipt:INV-099"})["finding"]
    assert one["numbers"]["gap_if_received"] == -45.0 and one["severity"] == "HIGH" and one["applied_policies"]
    with pytest.raises(tools.ToolError):
        tools.execute(ctx, "get_findings", {"finding_id": "nope"})
    rec = tools.execute(ctx, "reconcile_ingredient", {"ingredient": "chicken thigh"})
    assert rec["under_policy"]["gap"] == -5.0 and rec["trusting_office_status"]["gap"] == -45.0
    assert rec["under_policy"]["excluded_invoices"] == ["INV-099"]
    run = tools.execute(ctx, "stock_runway", {"ingredient": None})
    assert run["rows"][0]["ingredient"] == "6 inch flour tortilla" and run["rows"][0]["runway_days"] == pytest.approx(1.587, abs=0.01)
    chicken = next(r for r in run["rows"] if r["ingredient"].startswith("Chicken"))
    assert chicken["planning_delivery_days"] == ["Monday", "Wednesday"]
    wk = tools.execute(ctx, "weekend_forecast", {"pos_item_id": "M05"})
    assert wk["uplift"] == 0.25 and wk["needs"]["Large shell eggs"] == pytest.approx(330.0)
    beliefs = tools.execute(ctx, "get_beliefs", {"kind": "preference", "subject": "order.buffer_days"})
    assert beliefs["beliefs"][0]["value"] == {"days": 1} and beliefs["conflicts"] == []
    assert len(tools.execute(ctx, "explain", {"subject": "invoice.INV-099.delivery_date"})["chain"]) == 1
    ph = tools.execute(ctx, "price_history", {"ingredient": "Chicken thigh boneless skinless"})
    assert [p["unit_cost"] for p in ph["points"]][-1] == "3.65" and len(ph["changes"]) == 1


def test_record_belief_guard_rails(ctx):
    base = {"kind": "preference", "subject": "order.buffer_days", "statement": "use two days", "value_json": '{"days": 2}'}
    out = tools.execute(ctx, "record_belief", base)
    assert out["action"] == "superseded" and out["recorded"]["source"] == "human:ask" and out["previous"]["value"] == {"days": 1}
    assert ctx.ledger.value("preference", "order.buffer_days") == {"days": 2}
    with pytest.raises(tools.ToolError, match="expects keys"):
        tools.execute(ctx, "record_belief", {**base, "value_json": '{"buffer": 2}'})
    with pytest.raises(tools.ToolError, match="valid JSON"):
        tools.execute(ctx, "record_belief", {**base, "value_json": "two"})
    with pytest.raises(tools.ToolError, match="JSON object"):
        tools.execute(ctx, "record_belief", {**base, "value_json": "[2]"})
    with pytest.raises(tools.ToolError):  # the ledger refuses observations from a human source
        tools.execute(ctx, "record_belief", {**base, "kind": "observation"})
    with pytest.raises(tools.ToolError, match="not available in brief"):
        tools.execute(tools.ToolContext(ctx.records, ctx.ledger, "brief"), "record_belief", base)
    with pytest.raises(tools.ToolError, match="unexpected argument"):
        tools.execute(ctx, "list_menu", {"site_id": "SITE-02"})
    with pytest.raises(tools.ToolError, match="no such tool"):
        tools.execute(ctx, "approve_draft", {})
