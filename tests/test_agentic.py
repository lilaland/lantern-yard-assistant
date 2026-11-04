"""The agentic suite's pipeline in fake mode: steps execute through the real modes, turns are graded, the judge
prompt carries the rubric and transcript, coverage is reported, and the fake judge is never trusted blindly."""
import json
from pathlib import Path

import pytest

from evals import agentic, judge as J

SCENARIOS = Path(__file__).resolve().parent.parent / "evals" / "scenarios"


class FakeSim:
    """Adapter over the test session's simulator with the operator helper the runner expects."""

    def __init__(self, sim):
        self.sim = sim
        self.base = sim.base
        self.dir = Path(sim.state).parent

    def operator(self, *args):
        if args[0] == "scenario":
            self.sim.scenario(args[2])
        elif args[0] == "approve":
            self.sim.approve(args[2])


@pytest.fixture
def runner(sim, tmp_path):
    return agentic.Runner(FakeSim(sim), tmp_path / "out", fake=True, budget_usd=100.0, resolver=None)


def load(name):
    return json.loads((SCENARIOS / name).read_text())


def test_list_and_estimates():
    scs = agentic.load_scenarios(SCENARIOS, None)
    assert [s["name"] for s in scs][:2] == ["brief_rev1", "onboarding_mixed"] and len(scs) == 9
    flows, cov = agentic.coverage(scs)
    assert not [f for f in flows if not cov[f]], "every declared flow has at least one scenario"
    assert 0 < agentic.estimate(scs[0]) < 1 and agentic.estimate(scs[0], 3) == pytest.approx(3 * agentic.estimate(scs[0]))
    assert agentic.main(["--list"]) == 0


def test_brief_scenario_runs_grades_and_judges_in_fake_mode(runner):
    res = runner.run_scenario(load("01_brief_rev1.json"))
    assert res.error is None and len(res.turns) == 1 and res.turns[0].mode == "brief"
    names = [d["name"] for d in res.turns[0].deterministic]
    assert set(names) == {e.__name__ for e in agentic.E.EVALUATORS}
    assert res.deterministic_ok, [d for d in res.turns[0].deterministic if not d["passed"]]
    assert res.judge and {c["id"] for c in res.judge["criteria"]} == {c["id"] for c in load("01_brief_rev1.json")["rubric"]}
    assert res.judge_trusted and "Deterministic brief at revision 1" in res.reference
    out = runner.out / "brief_rev1"
    assert (out / "turn-01-brief.json").exists() and (out / "result.json").exists()
    run = json.loads((out / "turn-01-brief.json").read_text())
    assert run["entry"]["mode"] == "brief" and run["entry"]["revision"] == 1 and run["entry"]["usd"] == 0 or run["entry"]["usd"] is not None


def test_preference_scenario_records_through_the_gate_and_applies_next_turn(runner):
    res = runner.run_scenario(load("03_preference_then_order.json"))
    assert res.error is None and [t.mode for t in res.turns] == ["ask", "ask", "order"]
    first, second = res.turns[0], res.turns[1]
    assert first.calls[0]["name"] == "record_belief" and first.calls[0]["gate"] == "approved"
    assert "Recorded [superseded]" in first.text
    assert second.calls[0]["name"] == "sizing_preview" and "2 buffer days" in second.text and "9 days total" in second.text
    assert res.deterministic_ok, [(t.label, d) for t in res.turns for d in t.deterministic if not d["passed"]]
    assert "Order proposal (code)" in res.reference and "plus 2 buffer day(s)" in res.reference


def test_rejected_gate_leaves_no_row(sim, tmp_path):
    r = agentic.Runner(FakeSim(sim), tmp_path / "o", fake=True, budget_usd=100.0)
    sc = load("03_preference_then_order.json")
    sc["steps"] = [{**sc["steps"][0], "gate": "reject"}]
    res = r.run_scenario(sc)
    t = res.turns[0]
    assert t.calls[0]["gate"] == "rejected" and not t.calls[0]["ok"] and "could not do that" in t.text
    from assistant.ledger import Ledger
    with Ledger(r.out / "preference_then_order" / "state" / "state.sqlite") as l:
        assert l.value("preference", "order.buffer_days") == {"days": 1}


def test_correction_scenario_moves_revision_and_surfaces_conflict(runner):
    res = runner.run_scenario(load("04_correction_with_conflict.json"))
    assert res.error is None and [t.mode for t in res.turns] == ["ask", "brief", "operator", "brief"]
    assert res.turns[1].revision == 1 and res.turns[3].revision == 2
    assert "Needs you" in res.reference and "invoice.INV-099.delivery_date" in res.reference
    assert res.turns[3].text.startswith("What changed:")


def test_order_path_and_outcome_question(runner):
    res = runner.run_scenario(load("06_order_path_timeout.json"))
    assert res.error is None and [t.mode for t in res.turns] == ["order", "order", "ask"]
    assert "[accepted]" in res.turns[1].event and "response to our submission was lost" in res.turns[1].event
    assert res.turns[2].calls[0]["name"] == "fulfilment_status" and "accepted" in res.turns[2].text and "verified by lookup" in res.turns[2].text
    assert res.deterministic_ok, [(t.label, d) for t in res.turns for d in t.deterministic if not d["passed"]]


def test_onboarding_and_persona_scenarios(runner):
    res = runner.run_scenario(load("02_onboarding_mixed.json"))
    assert res.error is None and [t.mode for t in res.turns] == ["onboard", "onboard", "brief"]
    assert "inbound -> unknown" in res.turns[1].event and json.loads(res.turns[0].text)["inbound"]
    res = runner.run_scenario(load("08_persona_manager.json"))
    assert res.error is None and len(res.turns) == 3 and all(t.mode == "ask" for t in res.turns)
    assert res.turns[1].calls[0]["name"] == "record_belief" and "brunch_uplift" in res.turns[1].text


def test_budget_stops_the_scenario(sim, tmp_path):
    r = agentic.Runner(FakeSim(sim), tmp_path / "o", fake=True, budget_usd=0.0)
    res = r.run_scenario(load("01_brief_rev1.json"))
    assert res.skipped and "budget" in res.error and not res.ok


def test_judge_prompt_and_fake_judge_and_calibration_shapes():
    case = json.loads((Path(J.CALIBRATION_DIR) / "good_brief.json").read_text())
    prompt = J.build_prompt(case["goal"], case["reference"], case["turns"], case["rubric"])
    assert "RUBRIC" in prompt and "TRANSCRIPT" in prompt and "leads_with_inv099" in prompt and "[assistant]" in prompt
    res = J.judge(case["goal"], case["reference"], case["turns"], case["rubric"], call=J.fake_judge_call)
    assert {c["id"] for c in res.criteria} == {c["id"] for c in case["rubric"]} and res.usage["usd"] is not None
    cases = sorted(Path(J.CALIBRATION_DIR).glob("*.json"))
    assert len(cases) >= 4 and all("expected" in json.loads(p.read_text()) for p in cases)
    # a judge that skips a criterion fails it rather than passing it silently
    from types import SimpleNamespace as NS
    partial = lambda kw: NS(content=[NS(type="text", text=json.dumps({"criteria": [], "overall_note": "x"}))], stop_reason="end_turn",
                            usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    res = J.judge("g", "r", case["turns"], case["rubric"], call=partial)
    assert not res.passed and len(res.failed_ids) == len(case["rubric"])


def test_report_markdown(runner):
    scs = [load("05_injection_replay.json"), load("07_eggs_question.json")]
    results = [runner.run_scenario(s) for s in scs]
    md = agentic.report_md(results, scs, runner, 1.0)
    assert "| injection_replay |" in md and "| eggs_question |" in md and "Coverage (flow" in md and "FAKE" in md
    assert all(r.deterministic_ok for r in results), [(r.name, t.label, d) for r in results for t in r.turns for d in t.deterministic if not d["passed"]]
