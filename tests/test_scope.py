"""Restaurant scope and credential boundary: SITE-01 only, agent token only, API only."""
import inspect
import json
import re
import urllib.error
import urllib.request
from dataclasses import fields
from pathlib import Path

import pytest

from assistant import api_client, config, detectors, policies, tools
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.records import load_all

PKG = Path(config.ROOT) / "assistant"
SOURCES = sorted(PKG.rglob("*.py"))


@pytest.fixture(scope="module")
def records(sim, tmp_path_factory):
    cfg = Config(api_base=sim.base, agent_token="demo-agent-site-01", model="m", effort="low",
                 state_dir=tmp_path_factory.mktemp("s"), dry=True)
    return load_all(ApiClient(cfg))


def test_client_refuses_any_other_site_before_the_request_leaves(client):
    with pytest.raises(ValueError):
        client.get_page("vendors", site_id="SITE-02")
    with pytest.raises(ValueError):
        client.get_all("notes", site_id="SITE-02")
    assert client.get_page("vendors").records[0]["site_id"] == "SITE-01"
    assert "site_id" not in inspect.signature(client.create_draft).parameters
    assert "site_id" not in inspect.signature(client.find_orders).parameters


def test_the_fixture_also_refuses_the_agent_token_for_site_02(sim):
    req = urllib.request.Request(f"{sim.base}/api/vendors?site_id=SITE-02",
                                 headers={"Authorization": "Bearer demo-agent-site-01"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403 and json.load(e.value)["error"] == "site_forbidden"


def test_no_tool_takes_or_returns_another_site(records, tmp_path):
    for d in tools.definitions("ask"):
        assert "site" not in json.dumps(d["input_schema"]).lower(), d["name"]
    with Ledger(tmp_path / "s.sqlite") as ledger:
        policies.write_defaults(ledger, records)
        ctx = tools.ToolContext(records, ledger, "ask", policies.detector_params(ledger), detectors.run_all(records))
        with pytest.raises(tools.ToolError, match="unexpected argument"):
            tools.execute(ctx, "list_vendors", {"site_id": "SITE-02"})
        blob = json.dumps(tools.execute(ctx, "list_invoices", {"invoice_id": None, "vendor_account": None, "status": None,
                                                                 "date_from": None, "date_to": None, "offset": None}))
        assert "SITE-02" not in blob and blob.count("SITE-01") >= 19


def test_every_record_and_citation_is_site_01(records):
    assert records.site == "SITE-01"
    for f in detectors.run_all(records):
        assert all(c.site == "SITE-01" for c in f.evidence), f.id
    for coll in (records.invoice_headers, records.invoice_lines, records.sales, records.counts, records.vendors,
                 records.catalog, records.menu, records.recipes, records.mappings, records.notes, records.supplier_documents):
        assert coll and all(r.cite.site == "SITE-01" for r in coll)


def test_the_assistant_never_reads_the_csvs_or_the_fixture_state():
    for src in SOURCES:
        text = src.read_text(encoding="utf-8")
        assert "import csv" not in text and "data/" not in text, src
        assert ".state/" not in text and "/control" not in text and "/approve" not in text, src
    assert not any(n for n in dir(ApiClient) if any(k in n.lower() for k in ("approve", "scenario", "control", "operator")))


def test_configuration_carries_only_the_agent_token():
    names = {f.name for f in fields(Config)}
    assert names == {"api_base", "agent_token", "model", "effort", "state_dir", "dry"}
    assert not any("operator" in k.lower() or "state_path" in k.lower() for k in config.DEFAULTS)
    cfg = config.load(dry=True)
    assert cfg.agent_token == "demo-agent-site-01" and cfg.db_path.parent == cfg.state_dir
    assert cfg.state_dir.name == ".marble" or cfg.state_dir.name.startswith("demo-")
    env = (Path(config.ROOT) / ".env.example").read_text()
    assert "operator" not in env.lower() and "ANTHROPIC_API_KEY=" in env


def test_every_citation_names_file_row_and_revision(records):
    # file, restaurant, row id (or a stated aggregate such as "92 rows 2026-09-14..2026-10-04"), revision, fact
    pattern = re.compile(r"^[a-z_]+\.csv SITE-01 ([A-Za-z0-9-]+|\d+ rows [0-9.-]+) rev\d+(: .+)?$")
    for f in detectors.run_all(records):
        for c in f.evidence:
            assert pattern.match(str(c)), (f.id, str(c))
