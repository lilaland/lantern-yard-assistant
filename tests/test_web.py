"""The web backend: every endpoint against a real server thread, the gate over SSE, and the boundaries."""
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from assistant import policies
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.records import load_all
from assistant.web import server as web


@pytest.fixture
def site(sim, tmp_path):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="claude-opus-5-5", effort="low",
                 state_dir=tmp_path / ".marble", dry=True)
    with Ledger(cfg.db_path) as l:
        policies.write_defaults(l, load_all(ApiClient(cfg)))
    httpd, app, base = web.start_in_thread(cfg)
    yield cfg, app, base
    httpd.shutdown()


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return r.status, json.load(r)


def post(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def sse(base, path, body=None, on_event=None):
    req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    events = []
    with urllib.request.urlopen(req, timeout=60) as r:
        assert r.headers["Content-Type"].startswith("text/event-stream")
        for line in r:
            line = line.decode().strip()
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                events.append(ev)
                if on_event:
                    on_event(ev)
                if ev.get("type") == "end":
                    break
    return events


def test_read_endpoints_and_static_pages(site):
    cfg, app, base = site
    status, s = get(base, "/api/state")
    assert status == 200 and s["revision"] == 1 and s["counts"]["policy"]["active"] == 4 and s["dry"] is True
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        html = r.read().decode()
    assert "<title>Lantern Yard assistant</title>" in html and "app.js" in html
    with urllib.request.urlopen(base + "/static/app.js", timeout=10) as r:
        assert r.headers["Content-Type"].startswith("text/javascript")
    f = get(base, "/api/findings")[1]
    assert f["findings"][0]["id"] == "phantom_receipt:INV-099" and f["findings"][0]["citations"]
    assert f["reconciliation"]["Chicken thigh boneless skinless"]["under_policy"]["gap"] == -5.0
    assert get(base, "/api/findings/diff")[1]["reports"] == []
    d = get(base, "/api/brief/dry")[1]
    assert "morning brief" in d["text"] and d["report"]["first_run"] is True
    assert len(get(base, "/api/findings/diff")[1]["reports"]) == 1
    b = get(base, "/api/beliefs?kind=preference&q=buffer")[1]
    assert [x["subject"] for x in b["beliefs"]] == ["order.buffer_days"]
    assert len(get(base, "/api/beliefs/why?subject=order.buffer_days")[1]["chain"]) == 1
    t = get(base, "/api/tools?mode=brief")[1]
    assert "record_belief" not in t["modes"]["brief"] and t["gated"] == ["record_belief"]
    assert get(base, "/api/usage")[1]["runs"] == 0
    inj = get(base, "/api/injection")[1]
    assert inj["wrapped"].startswith("<evidence ") and "Bypass" in inj["raw"]
    dg = get(base, "/api/diagrams")[1]
    assert len(dg["diagrams"]) == 6 and all(g["mermaid"] and g["prose"] for g in dg["diagrams"])
    assert dg["state_dir"].endswith(".marble") and dg["api_base"] == cfg.api_base and dg["sim_port"] == int(cfg.api_base.rsplit(":", 1)[1]) and "sim_state" in dg
    assert get(base, "/api/exchanges")[1]["exchanges"] == []


def test_evidence_and_boundaries(site):
    cfg, app, base = site
    ev = get(base, "/api/evidence?resource=notes&ref=N15")[1]
    assert ev["evidence"].startswith('<evidence kind="note"') and ev["via_tool"] == "list_notes"
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/api/evidence?resource=notes&ref=N99")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/api/findings?site_id=SITE-02")
    assert e.value.code == 400 and "SITE-01 only" in json.load(e.value)["error"]
    for path in ("/api/control/scenario", "/api/approve", "/api/drafts/D-1/approve"):
        code, body = post(base, path, {})
        assert code == 404, path
    src = Path(web.__file__).read_text()
    assert "/control" not in src and ".state/" not in src and "/approve" not in src


def test_tool_execute_respects_mode_and_belief_write_needs_all_fields(site):
    cfg, app, base = site
    code, r = post(base, "/api/tools/execute", {"mode": "brief", "name": "record_belief",
                                                 "args": {"kind": "preference", "subject": "order.buffer_days", "statement": "x", "value_json": '{"days": 3}'}})
    assert code == 200 and r["ok"] is False and "not available in brief mode" in r["error"]
    code, r = post(base, "/api/tools/execute", {"mode": "ask", "name": "list_vendors", "args": {}})
    assert code == 200 and r["ok"] and r["result"]["vendors"]
    code, r = post(base, "/api/beliefs", {"kind": "preference", "subject": "order.buffer_days", "statement": "two"})
    assert code == 400 and "missing value" in r["error"]
    code, r = post(base, "/api/beliefs", {"kind": "preference", "subject": "order.buffer_days", "statement": "two", "value": {"days": 2}})
    assert code == 200 and r["action"] == "superseded" and r["recorded"]["source"] == "human:web"
    code, r = post(base, "/api/beliefs", {"kind": "observation", "subject": "x", "statement": "s", "value": {}})
    assert code == 400


def test_order_proposal_overrides_do_not_touch_the_ledger(site):
    cfg, app, base = site
    base_p = get(base, "/api/order/proposal")[1]
    assert base_p["what_if"] is False and base_p["order"]["lines"][0]["quantity"] == 8
    p = get(base, "/api/order/proposal?buffer_days=2&inv099_received=true")[1]
    assert p["what_if"] is True and p["calendar"]["buffer_days"] == 2
    assert p["lines"][0]["inbound_counted"] == 40 and any("what-if override" in a for a in p["applied"])
    with Ledger(cfg.db_path) as l:
        assert l.value("preference", "order.buffer_days") == {"days": 1}  # untouched
    code, r = get(base, "/api/order/proposal?buffer_days=x") if False else post(base, "/api/order/proposal?buffer_days=x")
    assert code in (400, 404)


def test_order_path_through_the_api(site, sim):
    cfg, app, base = site
    code, r = post(base, "/api/order/draft", {"vendor": "V1"})
    assert code == 201 and r["draft"]["status"] == "awaiting_approval" and "approve --draft-id" in r["approve_command"]
    did = r["draft"]["draft_id"]
    assert get(base, f"/api/order/{did}")[1]["server_status"] == "awaiting_approval"
    code, r = post(base, f"/api/order/{did}/submit")
    assert code == 409 and "not approved" in r["error"]
    sim.approve(did)
    assert get(base, f"/api/order/{did}")[1]["server_status"] == "approved"
    with sim.under_scenario("supplier-timeout"):
        code, r = post(base, f"/api/order/{did}/submit")
    assert code == 200 and r["state"] == "accepted" and r["verified"] and "response to our submission was lost" in r["message"]
    assert get(base, "/api/order/drafts")[1]["drafts"][0]["status"] == "accepted"
    code, r = post(base, "/api/order/resume")
    assert code == 200 and r["outcomes"] == []


def test_chat_stream_without_a_model_explains_and_brief_stream_returns_the_deterministic_text(site):
    cfg, app, base = site
    events = sse(base, "/api/chat", {"text": "hello"})
    assert events[0]["type"] == "start" and events[0]["allowed"] and events[1]["type"] == "error" and "no model" in events[1]["message"]
    events = sse(base, "/api/brief", {})
    assert events[0]["type"] == "start" and "morning brief" in events[0]["deterministic"]
    assert events[1]["type"] == "done" and events[1]["text"] is None and events[-1]["type"] == "end"


def test_gate_over_sse_blocks_until_the_browser_answers(sim, tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from assistant import llm
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="claude-opus-5-5", effort="low",
                 state_dir=tmp_path / ".marble", dry=False)
    with Ledger(cfg.db_path) as l:
        policies.write_defaults(l, load_all(ApiClient(cfg)))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-used")
    rec = {"kind": "preference", "subject": "order.buffer_days", "statement": "two", "value_json": '{"days": 2}'}
    u = NS(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    responses = iter([NS(content=[NS(type="tool_use", id="t1", name="record_belief", input=rec,
                                     model_dump=lambda exclude_none=True: {"type": "tool_use", "id": "t1", "name": "record_belief", "input": rec})],
                         stop_reason="tool_use", usage=u),
                      NS(content=[NS(type="text", text="Recorded.", model_dump=lambda exclude_none=True: {"type": "text", "text": "Recorded."})],
                         stop_reason="end_turn", usage=u)])
    monkeypatch.setattr(llm, "api_call", lambda kw: next(responses))
    httpd, app, base = web.start_in_thread(cfg)
    try:
        answered = {}

        def on_event(ev):
            if ev["type"] == "gate":
                answered["card"] = ev
                threading.Thread(target=lambda: post(base, f"/api/gate/{ev['id']}", {"decision": "edit", "args": {"value": {"days": 3}}}), daemon=True).start()
        events = sse(base, "/api/chat", {"text": "use two days of buffer"}, on_event)
        kinds = [e["type"] for e in events]
        assert "gate" in kinds and "gate_result" in kinds and kinds[-2] == "done"
        assert answered["card"]["subject"] == "order.buffer_days" and answered["card"]["supersedes"]["value"] == {"days": 1}
        done = next(e for e in events if e["type"] == "done")
        assert done["recorded"][0]["recorded"]["value"] == {"days": 3} and done["tool_calls"][0]["gate"] == "edited"
        with Ledger(cfg.db_path) as l:
            assert l.value("preference", "order.buffer_days") == {"days": 3}
            assert len(l.exchanges("ask")) == 1
        assert get(base, "/api/exchanges")[1]["exchanges"][0]["tool_calls"] == ["record_belief"]
    finally:
        httpd.shutdown()


def test_reset_deletes_only_the_state_dir(site, sim):
    cfg, app, base = site
    code, r = post(base, "/api/reset", {"confirm": "wrong"})
    assert code == 400
    code, r = post(base, "/api/reset", {"confirm": cfg.state_dir.name})
    assert code == 200 and not cfg.state_dir.exists()
    assert get(base, "/api/state")[1]["meta"]["onboarded_at"] is None
    assert Path(sim.state).exists()
    code, r = post(base, "/api/onboard/defaults")
    assert code == 200 and len(r["recorded"]) == 14


def test_client_intro_onboarding_and_alerts_endpoints(sim, tmp_path):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low", state_dir=tmp_path / ".marble", dry=True)
    httpd, app, base = web.start_in_thread(cfg)
    try:
        intro = get(base, "/api/client/intro")[1]
        assert intro["name"] == "Marbly" and intro["onboarded_at"] is None and len(intro["capabilities"]) == 3
        plan = get(base, "/api/onboarding/plan")[1]
        ids = [q["id"] for q in plan["questions"]]
        assert ids == ["inbound", "route", "sku", "missing_recipe", "price", "ordering"] and plan["wording"] is False
        assert plan["questions"][-1]["fields"][0]["key"] == "buffer_days" and plan["questions"][4]["custom"]["prompt"]
        code, r = post(base, "/api/onboarding/answer", {"id": "inbound", "answer": "unknown"})
        assert code == 200 and r["option"]["key"] == "unknown" and [b["kind"] for b in r["recorded"]] == ["policy", "assertion"]
        assert r["recorded"][1]["source"] == "note:N15" and "inbound" not in r["remaining"] and len(r["remaining"]) == 5
        code, r = post(base, "/api/onboarding/answer", {"id": "price", "answer": 7})
        assert code == 200 and r["option"]["key"] == "custom" and r["recorded"][0]["value"] == {"ratio": 7.0}
        code, r = post(base, "/api/onboarding/answer", {"id": "ordering", "answer": {"buffer_days": 2}})
        assert code == 200 and any(b["subject"] == "order.buffer_days" and b["value"] == {"days": 2} for b in r["recorded"])
        code, r = post(base, "/api/onboarding/answer", {"id": "route", "answer": "nonsense"})
        assert code == 400
        code, r = post(base, "/api/onboarding/answer", {"id": "nope", "answer": "x"})
        assert code == 404
        for qid in ("route", "sku", "missing_recipe"):
            assert post(base, "/api/onboarding/answer", {"id": qid, "answer": get(base, "/api/onboarding/plan")[1]["questions"][0]["default"]})[0] == 200
        assert get(base, "/api/onboarding/plan")[1]["questions"] == []
        code, r = post(base, "/api/onboarding/complete")
        assert code == 200 and r["onboarded_at"] and get(base, "/api/client/intro")[1]["onboarded_at"]
        a = get(base, "/api/alerts")[1]
        assert a["alerts"][0]["id"] == "phantom_receipt:INV-099" and a["alerts"][0]["severity"] == "HIGH"
        assert a["alerts"][0]["action"] == {"kind": "order", "vendor": "V1", "label": "Plan the next Alder order"}
        assert a["unseen_critical"] == a["counts"]["HIGH"] >= 1 and all(not x["seen"] for x in a["alerts"])
        code, r = post(base, "/api/alerts/seen", {"ids": [a["alerts"][0]["id"]]})
        assert code == 200 and get(base, "/api/alerts")[1]["unseen_critical"] == a["counts"]["HIGH"] - 1
        price = next(x for x in a["alerts"] if x["id"].startswith("unit_price_outlier"))
        assert price["action"]["kind"] == "ask" and "INV-010" in price["action"]["prompt"]
        code, r = post(base, "/api/onboarding/restart")
        assert code == 200 and len(get(base, "/api/onboarding/plan")[1]["questions"]) == 6 and get(base, "/api/client/intro")[1]["onboarded_at"] is None
        with Ledger(cfg.db_path) as l:
            assert l.value("preference", "order.buffer_days") == {"days": 2}  # restart forgets the answers, not the beliefs
    finally:
        httpd.shutdown()


def test_chat_mode_parameter_picks_prompt_and_allowlist(site):
    cfg, app, base = site
    events = sse(base, "/api/chat", {"text": "hello", "mode": "brief"})
    assert events[0]["mode"] == "brief" and "record_belief" not in events[0]["allowed"]
    req = urllib.request.Request(base + "/api/chat", data=json.dumps({"text": "x", "mode": "admin"}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode()
    assert "unknown mode" in body


def test_tour_steps_are_derived_or_clicked(sim, tmp_path):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low", state_dir=tmp_path / ".marble", dry=True)
    httpd, app, base = web.start_in_thread(cfg)
    try:
        t = get(base, "/api/client/tour")[1]
        assert t["steps"] == ["facts", "alerts", "memory", "order"] and t["current"] == "facts" and not t["complete"]
        assert get(base, "/api/client/intro")[1]["tour"]["current"] == "facts"
        code, r = post(base, "/api/client/tour", {"step": "facts"})
        assert code == 400  # facts come from the ledger, not a click
        for q in get(base, "/api/onboarding/plan")[1]["questions"]:
            assert post(base, "/api/onboarding/answer", {"id": q["id"], "answer": q["default"]})[0] == 200
        post(base, "/api/onboarding/complete")
        t = get(base, "/api/client/tour")[1]
        assert t["done"]["facts"] and t["current"] == "alerts"
        t = post(base, "/api/client/tour", {"step": "alerts"})[1]
        assert t["current"] == "memory"
        t = post(base, "/api/client/tour", {"step": "memory"})[1]
        assert t["current"] == "order" and not t["complete"]
        post(base, "/api/onboarding/restart")
        assert get(base, "/api/client/tour")[1]["current"] == "facts"  # restart clears the click-throughs too
    finally:
        httpd.shutdown()


def test_client_wizard_and_sample_prompt_flows_in_api_order(sim, tmp_path):
    """The exact call sequence the Marbly page makes for each chip and for the order wizard, in dry mode."""
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low", state_dir=tmp_path / ".marble", dry=True)
    httpd, app, base = web.start_in_thread(cfg)
    try:
        for q in get(base, "/api/onboarding/plan")[1]["questions"]:
            post(base, "/api/onboarding/answer", {"id": q["id"], "answer": q["default"]})
        post(base, "/api/onboarding/complete")
        assert get(base, "/api/brief/dry?kind=onboarding")[1]["report"]["first_run"]
        # chip: "Give me this morning's brief" -> /api/brief (SSE); dry mode returns the deterministic text and no "no changes" line
        ev = sse(base, "/api/brief", {})
        assert ev[0]["type"] == "start" and "What changed" not in ev[0]["deterministic"] and "No changes since" not in ev[0]["deterministic"]
        # chips that go to /api/chat (ask mode): without a model the page shows the explanation, nothing else happens
        for text in ("How many days of chicken do we have?", "Why do you think Alder delivers on Wednesday?", "use two days of buffer from now on"):
            ev = sse(base, "/api/chat", {"text": text, "mode": "ask"})
            assert ev[0]["mode"] == "ask" and ev[1]["type"] == "error" and "no model" in ev[1]["message"]
        with Ledger(cfg.db_path) as l:
            assert l.value("preference", "order.buffer_days") == {"days": 1}
        # chip: "Help me with the next Alder order" -> wizard: proposal, draft, poll, (operator approves), submit, outcome, confirm
        p = get(base, "/api/order/proposal?vendor=V1")[1]
        assert p["what_if"] is False and p["lines"] and p["calendar"]["delivery_date"] == "2026-10-07"
        code, d = post(base, "/api/order/draft", {"vendor": "V1"})
        assert code == 201 and d["draft"]["order"] == p["order"] and "approve --draft-id" in d["approve_command"]
        did = d["draft"]["draft_id"]
        assert get(base, f"/api/order/{did}")[1]["server_status"] == "awaiting_approval"
        assert get(base, "/api/client/tour")[1]["current"] == "alerts" and get(base, "/api/client/tour")[1]["done"]["order"] is False
        sim.approve(did)
        assert get(base, f"/api/order/{did}")[1]["server_status"] == "approved"
        with sim.under_scenario("supplier-timeout"):
            o = post(base, f"/api/order/{did}/submit")[1]
        assert o["state"] == "accepted" and o["verified"] and "response to our submission was lost" in o["message"]
        assert get(base, "/api/client/tour")[1]["done"]["order"] is True
        # "Ask Marbly to confirm" answers from fulfilment_status; its note carries the outcome message
        r = post(base, "/api/tools/execute", {"mode": "ask", "name": "fulfilment_status", "args": {}})[1]
        assert r["ok"] and r["result"]["drafts"][-1]["status"] == "accepted" and "Verified by lookup" in r["result"]["drafts"][-1]["note"]
        a = get(base, "/api/alerts")[1]
        assert a["counts"]["HIGH"] >= 1 and all(x["action"] for x in a["alerts"])
        # the wizard reopened after the accepted order: the list shows it with its status, and the proposal nets it out
        ds = get(base, "/api/order/drafts")[1]["drafts"]
        assert ds[0]["draft_id"] == did and ds[0]["status"] == "accepted" and ds[0]["vendor_name"] == "Alder Wholesale"
        assert ds[0]["lines"][0]["sku"] == "AW-CHK20" and ds[0]["lines"][0]["ingredient"].startswith("Chicken") and ds[0]["lines"][0]["pack"] == "20 lb"
        p2 = get(base, "/api/order/proposal?vendor=V1")[1]
        assert p2["vendor_name"] == "Alder Wholesale" and p2["covered"]["AW-CHK20"] == p["lines"][0]["cases"] and p2["accepted"][0]["order_id"] == o["order_id"]
        assert all(l["remaining"] == 0 and l["already_ordered"] == l["cases"] for l in p2["lines"]) and p2["net_lines"] == []
        code, r = post(base, "/api/order/draft", {"vendor": "V1"})
        assert code == 400 and "already cover" in r["error"]  # never the same order twice by accident
        # only part of the proposal ordered: the next draft is what is left, with a note saying why
        with Ledger(cfg.db_path) as l:
            l.update_draft(did, "accepted", order_id=o["order_id"])
            l.db.execute("UPDATE drafts SET order_json=? WHERE draft_id=?", (json.dumps({**ds[0]["order"], "lines": ds[0]["order"]["lines"][:1]}), did))
            l.db.commit()
        p3 = get(base, "/api/order/proposal?vendor=V1")[1]
        assert [n["sku"] for n in p3["net_lines"]] == [x["sku"] for x in p["lines"][1:]] and p3["net_lines"][0]["already_ordered"] == 0
        code, r = post(base, "/api/order/draft", {"vendor": "V1"})
        assert code == 201 and [x["sku"] for x in r["draft"]["order"]["lines"]] == [x["sku"] for x in p["lines"][1:]]
        assert r["draft"]["note"].startswith("net of accepted order(s)") and r["draft"]["lines"][0]["ingredient"]
        assert get(base, f"/api/order/{r['draft']['draft_id']}")[1]["draft"]["vendor_name"] == "Alder Wholesale"
    finally:
        httpd.shutdown()


def test_onboarding_effect_edited_draft_revise_and_planner(sim, tmp_path):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low", state_dir=tmp_path / ".marble", dry=True)
    httpd, app, base = web.start_in_thread(cfg)
    try:
        for q in get(base, "/api/onboarding/plan")[1]["questions"]:
            post(base, "/api/onboarding/answer", {"id": q["id"], "answer": "office" if q["id"] == "inbound" else q["default"]})
        code, r = post(base, "/api/onboarding/complete")
        fx = r["effect"]
        assert code == 200 and fx["before"] == 17 and fx["after"] >= 16
        assert any(c["id"] == "phantom_receipt:INV-099" and c["from"] == "HIGH" and c["to"] == "LOW" for c in fx["severity_changed"])
        assert any(a.startswith("inbound.on_hand_rule=office_status") for a in fx["applied"]) and fx["critical_after"] >= 1
        # the draft carries the manager's edits and a note; bad edits are refused with the reason
        code, r = post(base, "/api/order/draft", {"vendor": "V1", "lines": [{"sku": "AW-CHK20", "quantity": 0}]})
        assert code == 400 and "outside 1..100" in r["error"]
        code, r = post(base, "/api/order/draft", {"vendor": "V1", "lines": [{"sku": "AW-CHK20", "quantity": 5}, {"sku": "V1-EGG", "quantity": 2}]})
        assert code == 201 and r["edited"] and r["draft"]["note"].startswith("manager edited") and "How" not in r["how_to"][:3]
        assert r["draft"]["order"]["lines"][0] == {"sku": "AW-CHK20", "quantity": 5, "unit": "case"}
        assert f"--port {sim.port}" in r["approve_command"] and "operator's terminal" in r["how_to"]
        code, r = post(base, "/api/order/revise", {"vendor": "V1", "request": "drop the cheese"})
        assert code == 400 and "no model" in r["error"]
        pl = get(base, "/api/order/planner")[1]
        assert {v["vendor_id"] for v in pl["vendors"]} == {"V1", "V2", "V3"}
        v1 = next(v for v in pl["vendors"] if v["vendor_id"] == "V1")
        assert v1["open_draft"]["status"] == "awaiting_approval" and v1["lines"] and v1["order_by"]
    finally:
        httpd.shutdown()
