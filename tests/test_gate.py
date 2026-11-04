"""The gate hook: a gated tool call is not executed until a person approves; reject and edit behave."""
import copy
from types import SimpleNamespace as NS

import pytest

from assistant import detectors, llm, policies, tools
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.modes import ask
from assistant.records import load_all


@pytest.fixture
def setup(sim, tmp_path):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="claude-opus-5-5", effort="low",
                 state_dir=tmp_path / ".marble", dry=False)
    records = load_all(ApiClient(cfg))
    with Ledger(cfg.db_path) as ledger:
        policies.write_defaults(ledger, records)
        params = policies.detector_params(ledger)
        yield cfg, tools.ToolContext(records, ledger, "ask", params, detectors.run_all(records, params))


def msg(content, stop="end_turn"):
    return NS(content=content, stop_reason=stop,
              usage=NS(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0))


def text(t):
    return NS(type="text", text=t, model_dump=lambda exclude_none=True: {"type": "text", "text": t})


def use(i, name, inp):
    d = {"type": "tool_use", "id": i, "name": name, "input": inp}
    return NS(type="tool_use", id=i, name=name, input=inp, model_dump=lambda exclude_none=True: d)


RECORD = {"kind": "preference", "subject": "order.buffer_days", "statement": "two days", "value_json": '{"days": 2}'}


def script(*responses):
    it = iter(responses)
    seen = []
    def call(kw):
        seen.append(copy.deepcopy(kw))
        return next(it)
    call.seen = seen
    return call


def test_reject_returns_an_error_result_and_writes_nothing(setup):
    cfg, ctx = setup
    asked = []
    def gate(name, args):
        asked.append((name, args))
        return "reject", None, "not now"
    r = llm.run(cfg, "ask", "use two days", ctx, call=script(msg([use("t1", "record_belief", RECORD)], "tool_use"), msg([text("ok")])),
                gate=gate, log=False)
    assert asked == [("record_belief", RECORD)]
    res = r.messages[2]["content"][0]
    assert res["is_error"] and "the manager declined: not now" in res["content"]
    assert r.tool_calls[0].gate == "rejected" and not r.tool_calls[0].ok
    assert ctx.ledger.value("preference", "order.buffer_days") == {"days": 1}


def test_approve_executes_once_and_edit_records_the_edited_value(setup):
    cfg, ctx = setup
    r = llm.run(cfg, "ask", "x", ctx, call=script(msg([use("t1", "record_belief", RECORD)], "tool_use"), msg([text("ok")])),
                gate=lambda n, a: ("approve", None, None), log=False)
    assert r.tool_calls[0].gate == "approved" and r.tool_calls[0].ok
    assert ctx.ledger.value("preference", "order.buffer_days") == {"days": 2}
    edited = {**RECORD, "value_json": '{"days": 3}'}
    r = llm.run(cfg, "ask", "x", ctx, call=script(msg([use("t2", "record_belief", RECORD)], "tool_use"), msg([text("ok")])),
                gate=lambda n, a: ("edit", edited, None), log=False)
    assert r.tool_calls[0].gate == "edited" and r.tool_calls[0].args == edited
    assert ctx.ledger.value("preference", "order.buffer_days") == {"days": 3}


def test_ungated_tools_never_consult_the_gate_and_events_stream(setup):
    cfg, ctx = setup
    asked = []
    events = []
    r = llm.run(cfg, "ask", "x", ctx, call=script(msg([use("t1", "list_vendors", {})], "tool_use"), msg([text("done")])),
                gate=lambda n, a: asked.append(n) or ("reject", None, "never"), on_event=events.append, log=False)
    assert asked == [] and r.tool_calls[0].ok and r.tool_calls[0].gate is None
    kinds = [e["type"] for e in events]
    assert kinds == ["round", "tool_call", "tool_result", "round", "text", "usage"]
    assert events[2]["name"] == "list_vendors" and events[2]["ok"] and "usd" in events[2]["usage"]
    assert events[-2]["text"] == "done" and events[-1]["rounds"] == 2


def test_cli_ask_gate_prompts_and_honours_no(setup, capsys):
    cfg, ctx = setup
    answers = iter(["n"])
    call = script(msg([use("t1", "record_belief", RECORD)], "tool_use"), msg([text("Understood, not recorded.")]))
    ask.run(cfg, once="use two days of buffer", call=call, input_fn=lambda prompt: next(answers), auto_approve=False)
    out = capsys.readouterr().out
    assert "wants to record a preference" in out and "Recorded [" not in out
    with Ledger(cfg.db_path) as l:
        assert l.value("preference", "order.buffer_days") == {"days": 1}
    answers = iter(["edit", "nope", '{"days": 4}'])
    call = script(msg([use("t2", "record_belief", RECORD)], "tool_use"), msg([text("Recorded four days.")]))
    ask.run(cfg, once="use two days of buffer", call=call, input_fn=lambda prompt: next(answers), auto_approve=False)
    out = capsys.readouterr().out
    assert "not valid JSON" in out and 'Recorded [superseded]: preference order.buffer_days = {"days": 4}' in out
