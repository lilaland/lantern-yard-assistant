import copy
"""The ask REPL: deterministic commands, the model recording a stated preference, persistence, trimming, CLI."""
import os
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from assistant import policies
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.modes import ask
from assistant.records import load_all

ROOT = os.path.dirname(os.path.dirname(__file__))
PROMPTS = os.path.join(ROOT, "assistant", "prompts")


@pytest.fixture
def cfgs(sim, tmp_path):
    base = dict(api_base=sim.base, agent_token="demo-agent-site-01", model="claude-opus-5-5", effort="low",
                state_dir=tmp_path / ".marble")
    dry, live = Config(dry=True, **base), Config(dry=False, **base)
    records = load_all(ApiClient(dry))
    with Ledger(dry.db_path) as l:
        policies.write_defaults(l, records)
    return dry, live


def msg(content, stop="end_turn"):
    return NS(content=content, stop_reason=stop,
              usage=NS(input_tokens=500, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0))


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


def feed(lines):
    it = iter(lines)
    return lambda prompt: next(it)


def test_prompt_lists_every_default_subject():
    prompt = open(os.path.join(PROMPTS, "ask.md"), encoding="utf-8").read()
    for kind, subject, _, _ in policies.DEFAULTS:
        needle = subject if "ING-" not in subject else subject.split("ING-")[0]
        assert needle in prompt, subject
    for extra in ("physically_received", "delivery_days", "delivery_date", "record_belief", "A question is not a statement"):
        assert extra in prompt


def test_dry_commands_work_without_a_model(cfgs, capsys):
    dry, _ = cfgs
    lines = ["findings", "finding phantom_receipt:INV-099", 'set preference order.buffer_days {"days": 2}',
             "set preference order.buffer_days nonsense", "set observation x:1 {}", "why order.buffer_days",
             "what about eggs for Saturday?", "memory", "clear", "quit"]
    assert ask.run(dry, input_fn=feed(lines)) == 0
    out = capsys.readouterr().out
    assert "phantom_receipt:INV-099" in out and "[HIGH] INV-099 is marked received" in out
    assert 'Recorded [superseded]: preference order.buffer_days = {"days": 2} (human:ask); was {"days": 1}' in out
    assert "not recorded: value_json is not valid JSON" in out and "not recorded: observations" in out
    assert "2 record(s), oldest first" in out and "(no model in --dry mode)" in out and "Forgot 0 earlier" in out
    with Ledger(dry.db_path) as l:
        assert l.value("preference", "order.buffer_days") == {"days": 2}
        assert l.one("preference", "order.buffer_days").source == "human:ask"


def test_model_records_a_stated_preference_and_the_conversation_persists(cfgs, capsys):
    _, live = cfgs
    first = Script(
        msg([use("t1", "record_belief", {"kind": "preference", "subject": "order.buffer_days", "statement": "use two days of buffer",
                                         "value_json": '{"days": 2}'})], "tool_use"),
        msg([text("Recorded: two days of buffer instead of one. Order proposals now cover two extra days.")]),
    )
    assert ask.run(live, once="use two days of buffer from now on", call=first) == 0
    out = capsys.readouterr().out
    assert "Recorded: two days of buffer" in out and 'Recorded [superseded]: preference order.buffer_days = {"days": 2} (human:ask)' in out
    with Ledger(live.db_path) as l:
        assert l.value("preference", "order.buffer_days") == {"days": 2}
        ex = l.exchanges("ask")
        assert len(ex) == 1 and ex[0].question.startswith("use two days") and ex[0].revision == 1
        assert [m["role"] for m in ex[0].messages] == ["user", "assistant", "user", "assistant"]
    assert {t["name"] for t in first.requests[0]["tools"]} >= {"record_belief", "get_findings", "explain"}
    assert "Mode: ask" in first.requests[0]["system"][0]["text"]
    # a new process continues the same conversation
    second = Script(msg([text("Eggs: 84 on hand per the count.")]))
    ask.run(live, once="and how are we on eggs?", call=second)
    sent = second.requests[0]["messages"]
    assert sent[0]["content"] == "use two days of buffer from now on" and sent[-1]["content"] == "and how are we on eggs?"
    assert sent[1]["content"][0]["type"] == "tool_use" and sent[2]["content"][0]["type"] == "tool_result"
    with Ledger(live.db_path) as l:
        assert len(l.exchanges("ask")) == 2
    # --fresh ignores the history without deleting it
    third = Script(msg([text("ok")]))
    ask.run(live, once="hello", resume=False, call=third)
    assert len(third.requests[0]["messages"]) == 1
    with Ledger(live.db_path) as l:
        assert len(l.exchanges("ask")) == 3


def test_history_is_trimmed_to_the_last_exchanges(cfgs):
    _, live = cfgs
    with Ledger(live.db_path) as l:
        for i in range(8):
            l.save_exchange("ask", f"q{i}", f"a{i}", [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]}], 1)
    script = Script(msg([text("fine")]))
    ask.run(live, once="latest", call=script, keep=3)
    sent = script.requests[0]["messages"]
    assert sent[0]["content"] == "q5" and len(sent) == 7  # 3 exchanges x 2 messages + the new question


def test_model_cannot_record_outside_the_tool(cfgs, capsys):
    _, live = cfgs
    script = Script(msg([use("t1", "record_belief", {"kind": "observation", "subject": "x", "statement": "s", "value_json": "{}"})], "tool_use"),
                    msg([text("done")]))
    ask.run(live, once="note that chicken is low", call=script)
    out = capsys.readouterr().out
    assert "Recorded [" not in out
    with Ledger(live.db_path) as l:
        assert l.active("observation") == []


def _cli(sim, state_dir, *args):
    env = dict(os.environ, MARBLE_API_BASE=sim.base, MARBLE_AGENT_TOKEN="demo-agent-site-01")
    return subprocess.run([sys.executable, "-m", "assistant", "--dry", "--state-dir", str(state_dir), *args],
                          capture_output=True, text=True, env=env, cwd=ROOT)


def test_cli_set_changes_the_order_proposal_in_the_next_process(sim, tmp_path):
    state = tmp_path / ".marble"
    assert _cli(sim, state, "onboard", "--defaults").returncode == 0
    r = _cli(sim, state, "ask", "--once", 'set preference order.buffer_days {"days": 2}')
    assert r.returncode == 0 and "Recorded [superseded]" in r.stdout, r.stdout + r.stderr
    prop = _cli(sim, state, "order", "--propose-only").stdout
    assert "plus 2 buffer day(s)" in prop and "order.buffer_days=2 (human:ask)" in prop
    why = _cli(sim, state, "why", "order.buffer_days").stdout
    assert "[superseded]" in why and "human:onboarding-defaults" in why and "human:ask" in why
