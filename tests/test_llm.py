"""The tool loop, driven by a fake API: result handling, allowlists, limits, logging, cost."""
import copy
import json
from types import SimpleNamespace as NS

import pytest

from assistant import detectors, llm, policies, tools
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
def setup(records, sim, tmp_path):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="claude-opus-5-5", effort="medium",
                 state_dir=tmp_path / ".marble", dry=False)
    with Ledger(cfg.db_path) as ledger:
        policies.write_defaults(ledger, records)
        params = policies.detector_params(ledger)
        yield cfg, tools.ToolContext(records, ledger, "brief", params, detectors.run_all(records, params))


def msg(content, stop="end_turn", **usage):
    u = {"input_tokens": 1000, "output_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    u.update(usage)
    return NS(content=content, stop_reason=stop, usage=NS(**u))


def text(t):
    return NS(type="text", text=t, model_dump=lambda exclude_none=True: {"type": "text", "text": t})


def use(i, name, inp):
    d = {"type": "tool_use", "id": i, "name": name, "input": inp}
    return NS(type="tool_use", id=i, name=name, input=inp, model_dump=lambda exclude_none=True: d)


class Script:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def test_tool_loop_returns_all_results_in_one_user_message(setup):
    cfg, ctx = setup
    script = Script(
        msg([text("checking"), use("t1", "list_vendors", {}), use("t2", "list_notes", {"contains": "received", "date_from": "bad", "date_to": None})], "tool_use"),
        msg([text("Brief: chicken is tight.")], cache_read_input_tokens=800),
    )
    r = llm.run(cfg, "brief", "write the brief", ctx, call=script)
    assert r.text == "Brief: chicken is tight." and r.stop_reason == "end_turn" and r.note is None
    roles = [m["role"] for m in r.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    results = r.messages[2]["content"]
    assert [x["tool_use_id"] for x in results] == ["t1", "t2"]
    assert "vendors" in json.loads(results[0]["content"]) and results[1]["is_error"] is True and "date_from" in results[1]["content"]
    assert [(c.name, c.ok) for c in r.tool_calls] == [("list_vendors", True), ("list_notes", False)]
    req = script.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["output_config"] == {"effort": "medium"} and "thinking" not in req
    assert "temperature" not in req and "tool_choice" not in req and req["max_tokens"] == 16000
    assert {t["name"] for t in req["tools"]} == set(tools.allowed("brief")) and all(t["strict"] for t in req["tools"])
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"} and "SITE-01" in req["system"][0]["text"]
    assert "morning brief" in req["system"][0]["text"]
    # the second request carries the assistant turn and the tool results verbatim
    assert script.requests[1]["messages"][1]["content"][1]["type"] == "tool_use"
    assert r.usage.rounds == 2 and r.usage.input_tokens == 2000 and r.usage.cache_read_tokens == 800
    assert r.usage.usd("claude-opus-5-5") == pytest.approx((2000 * 4 + 200 * 20 + 800 * 0.4) / 1e6)
    # usage log and run transcript were written under the state dir, never anywhere else
    lines = cfg.usage_log.read_text().splitlines()
    entry = json.loads(lines[-1])
    assert len(lines) == 1 and entry["mode"] == "brief" and entry["tool_calls"] == ["list_vendors", "list_notes"]
    assert entry["usd"] == pytest.approx(r.usage.usd(cfg.model))
    runs = list((cfg.state_dir / "runs").glob("*-brief.json"))
    assert len(runs) == 1 and json.loads(runs[0].read_text())["messages"][0]["content"] == "write the brief"


def test_tools_outside_the_mode_are_refused_even_if_the_model_asks(setup):
    cfg, ctx = setup
    script = Script(
        msg([use("t1", "record_belief", {"kind": "preference", "subject": "order.buffer_days", "statement": "x", "value_json": '{"days": 9}'}),
             use("t2", "approve_draft", {"draft_id": "D-1"})], "tool_use"),
        msg([text("ok")]),
    )
    r = llm.run(cfg, "brief", "hi", ctx, call=script, log=False)
    results = r.messages[2]["content"]
    assert all(x["is_error"] for x in results)
    assert "not available in brief mode" in results[0]["content"] and "not available in brief mode" in results[1]["content"]
    assert ctx.ledger.value("preference", "order.buffer_days") == {"days": 1}  # nothing was written


def test_round_limit_and_truncation_notes(setup):
    cfg, ctx = setup
    forever = Script(msg([use("t1", "list_menu", {})], "tool_use"))
    r = llm.run(cfg, "brief", "hi", ctx, call=forever, max_rounds=3, log=False)
    assert r.usage.rounds == 3 and "stopped after 3" in r.note and r.text == ""
    cut = Script(msg([text("half a brief"), use("t9", "list_menu", {})], "max_tokens"))
    r2 = llm.run(cfg, "brief", "hi", ctx, call=cut, log=False)
    assert r2.usage.rounds == 1 and "output limit" in r2.note and r2.text == "half a brief"
    assert r2.tool_calls == []  # a tool_use under max_tokens is never executed


def test_tool_crash_becomes_an_error_result_not_a_traceback(setup, monkeypatch):
    cfg, ctx = setup
    monkeypatch.setitem(tools.REGISTRY, "list_menu", tools.Tool("list_menu", "READ", "boom", {}, lambda c: 1 / 0))
    script = Script(msg([use("t1", "list_menu", {})], "tool_use"), msg([text("done")]))
    r = llm.run(cfg, "brief", "hi", ctx, call=script, log=False)
    assert r.text == "done" and "ZeroDivisionError" in r.messages[2]["content"][0]["content"]


def test_missing_key_is_reported_before_any_request(setup, monkeypatch):
    cfg, ctx = setup
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.ModelUnavailable, match="ANTHROPIC_API_KEY"):
        llm.run(cfg, "brief", "hi", ctx, log=False)


def test_cost_table():
    assert llm.cost("claude-opus-5-5", 1_000_000, 0) == 4.0 and llm.cost("claude-sonnet-5-5", 0, 1_000_000) == 10.0
    assert llm.cost("claude-opus-5-5", 0, 0, cache_write=1_000_000) == 5.0
    assert llm.cost("claude-opus-5-5", 0, 0, cache_read=1_000_000) == pytest.approx(0.4)
    assert llm.cost("some-other-model", 10, 10) is None
    u = llm.Usage(1200, 300, 0, 900, 2)
    assert "cost unknown" in u.line("some-other-model") and "$" in u.line("claude-sonnet-5-5")
