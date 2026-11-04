"""The evaluator suite is itself tested: fixtures meet their stated expectations, negatives are caught."""
import json
from pathlib import Path

import pytest

from assistant import policies, tools
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.records import load_all
from evals import evaluators as E

FIXTURES = sorted((Path(__file__).resolve().parent.parent / "evals" / "fixtures").glob("*.json"))
THRESHOLDS = json.loads((Path(__file__).resolve().parent.parent / "evals" / "thresholds.json").read_text())


@pytest.fixture(scope="module")
def resolver(sim, tmp_path_factory):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low",
                 state_dir=tmp_path_factory.mktemp("s"), dry=True)
    client = ApiClient(cfg)
    recs = {1: load_all(client)}
    with sim.under_scenario("delivery-correction"):
        recs[2] = load_all(client)
    ledger = Ledger(cfg.db_path)
    ctxs = {r: tools.ToolContext(x, ledger, "ask") for r, x in recs.items()}

    def resolve(resource, ref, revision):
        try:
            ev = tools.evidence(ctxs.get(revision, ctxs[1]), resource, ref)
        except tools.ToolError as e:
            return False, None, str(e)
        rec = ev["record"]
        cite = rec.get("cite") if isinstance(rec, dict) else None
        rev = int(cite.split(" rev")[1].split(":")[0]) if isinstance(cite, str) and " rev" in cite else None
        return True, rev, "ok"
    return resolve


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_fixture_meets_its_expectation(path, resolver):
    run = json.loads(path.read_text())
    verdicts = E.evaluate(run, resolver=resolver, thresholds=THRESHOLDS)
    ok, why = E.expectation_met(run, verdicts)
    assert ok, why + " :: " + "; ".join(f"{v.name}: {v.reason}" for v in verdicts if not v.passed)


def test_negative_fixtures_exist_for_the_core_checks():
    expected_fail = set()
    for p in FIXTURES:
        run = json.loads(p.read_text())
        expected_fail |= set((run.get("expect") or {}).get("fail") or [])
    assert {"numbers_grounded", "citations_valid", "allowlist_respected", "status_vocabulary", "memory_only_from_person"} <= expected_fail


def test_evaluators_catch_fresh_violations(resolver):
    base = json.loads((FIXTURES[0].parent / "ask_preference_pass.json").read_text())
    # the same recorded belief, but the person never said it and no gate approved it
    run = json.loads(json.dumps(base))
    run["entry"]["user_text"] = "what is the chicken runway?"
    run["messages"][0]["content"] = "what is the chicken runway?"
    run["entry"]["calls"][0]["gate"] = None
    v = E.memory_only_from_person(run)
    assert not v.passed and "not in the person's words" in v.reason
    # a brief that is too long and restates the footer
    brief = json.loads((FIXTURES[0].parent / "brief_rev1_pass.json").read_text())
    brief["messages"][-1]["content"][0]["text"] += "\n\nRetained (human choices, never rechecked against data): 4 policies. " + "word " * 300
    v = E.brief_shape(brief)
    assert not v.passed and "words" in v.reason and "footer" in v.reason
    # cost over threshold
    brief["entry"]["usd"] = 5.0
    assert not E.cost_bound(brief, thresholds=THRESHOLDS).passed
