"""The operator helper (demo/operator_helper.py) runs the simulator and puts it back to a fresh file on request.

It lives outside the assistant package: the assistant never imports it, never calls it, never opens
the state file. The browser does, from the human's click.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from assistant import config
from assistant.api_client import ApiClient, ApiError
from assistant.config import ROOT

HELPER = ROOT / "demo" / "operator_helper.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str) -> tuple[int, dict, dict]:
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.load(r), dict(r.headers)


def _post(url: str) -> tuple[int, dict, dict]:
    req = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.load(r), dict(r.headers)


@pytest.fixture
def helper(tmp_path):
    port, control = _free_port(), _free_port()
    state = tmp_path / "sim.sqlite"
    proc = subprocess.Popen([sys.executable, str(HELPER), "serve", "--port", str(port), "--state", str(state),
                             "--control-port", str(control)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            _get(f"http://127.0.0.1:{control}/status")
            break
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("operator helper did not start:\n" + (proc.stdout.read() if proc.stdout else ""))
    yield port, control, state
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_reset_gives_a_fresh_supplier_and_keeps_the_assistant_out(helper):
    port, control, state = helper
    cfg = config.Config(api_base=f"http://127.0.0.1:{port}", agent_token="demo-agent-site-01", model="m", effort="low",
                        state_dir=state.parent / ".marble", dry=True)
    client = ApiClient(cfg)
    assert state.exists()
    status = _get(f"http://127.0.0.1:{control}/status")[1]
    assert status["running"] and status["port"] == port and status["resets"] == 0
    # the simulator behind it is the real one: a draft can be made with the agent token
    d = client.create_draft("V1", [{"sku": "AW-CHK20", "quantity": 1, "unit": "case"}])
    assert client.get_draft(d["draft_id"])["status"] == "awaiting_approval"
    # reset: the file is recreated and the draft is gone; the browser's cross-origin call is allowed
    code, body, headers = _post(f"http://127.0.0.1:{control}/reset")
    assert code == 200 and body["reset"] and "sim.sqlite" in body["removed"] and body["resets"] == 1
    assert headers.get("Access-Control-Allow-Origin") == "*"
    assert state.exists()
    with pytest.raises(ApiError) as e:
        client.get_draft(d["draft_id"])
    assert e.value.status == 404
    assert client.create_draft("V1", [{"sku": "AW-CHK20", "quantity": 1, "unit": "case"}])["status"] == "awaiting_approval"  # serving again
    # the shell form prints the same answer
    out = subprocess.run([sys.executable, str(HELPER), "status", "--control-port", str(control)], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and json.loads(out.stdout)["resets"] == 1
    # no approve or scenario route: those stay typed commands
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(f"http://127.0.0.1:{control}/approve")
    assert err.value.code == 404


def test_assistant_package_never_imports_or_calls_the_helper():
    """It may name the helper in a note to the person; it never imports it or requests its reset route."""
    for p in (ROOT / "assistant").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "import operator_helper" not in text and "from demo" not in text, p
        assert "/reset\"" not in text.replace("/api/reset", "") and "/status\"" not in text, p
