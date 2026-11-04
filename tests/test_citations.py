"""Citation parsing round-trips every citation the renderer can produce, and the evidence lookup resolves each."""
import pytest

from assistant import detectors, policies, render, tools
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.records import load_all


@pytest.fixture(scope="module")
def records(sim, tmp_path_factory):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low",
                 state_dir=tmp_path_factory.mktemp("s"), dry=True)
    return load_all(ApiClient(cfg))


def test_every_rendered_citation_is_found_and_resolves(records, tmp_path):
    with Ledger(tmp_path / "s.sqlite") as ledger:
        policies.write_defaults(ledger, records)
        params = policies.detector_params(ledger)
        findings = detectors.run_all(records, params)
        ctx = tools.ToolContext(records, ledger, "ask", params, findings)
        seen = 0
        for f in findings:
            block = render.finding_block(f)
            links = render.citation_links(block)
            assert len(links) == len(f.evidence), f.id  # one link per evidence line, nothing invented
            for link, cite in zip(links, f.evidence):
                assert link["resource"] == cite.source[:-4] and link["ref"] == cite.ref and link["revision"] == cite.revision
                assert block[link["span"][0]:link["span"][1]] == link["text"]
                ev = tools.evidence(ctx, link["resource"], link["ref"])
                assert ev["record"] is not None and ev["via_tool"].startswith("list_")
                if link["resource"] in ("notes", "supplier_documents"):
                    assert ev["evidence"].startswith("<evidence ")
                seen += 1
        assert seen > 40


def test_no_false_positives_and_unknown_refs_fail_cleanly(records, tmp_path):
    prose = ("The revision rev1 of the sheet and SITE-01's INV-099 came in; see notes.csv for N15 and "
             "notes.csv SITE-01 N15 rev1: the real one. Also pos_daily.csv SITE-01 92 rows 2026-09-14..2026-10-04 rev1: agg.")
    links = render.citation_links(prose)
    assert [l["ref"] for l in links] == ["N15", "92 rows 2026-09-14..2026-10-04"] and links[1]["aggregate"]
    with Ledger(tmp_path / "s.sqlite") as ledger:
        ctx = tools.ToolContext(records, ledger, "ask")
        with pytest.raises(tools.ToolError, match="no notes record"):
            tools.evidence(ctx, "notes", "N99")
        with pytest.raises(tools.ToolError, match="unknown resource"):
            tools.evidence(ctx, "sites", "SITE-02")
        agg = tools.evidence(ctx, "pos_daily", "92 rows 2026-09-14..2026-10-04")
        assert agg["record"]["rows"] == 92 and "aggregate" in agg["record"]
