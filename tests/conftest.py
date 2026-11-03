"""Test fixtures: a real simulator on a free port with its own state file.

Operator actions (approve, scenario) run through simulator.py exactly as a
human would in the second terminal. The assistant code under test never
receives the operator credential.
"""
from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from assistant import config
from assistant.api_client import ApiClient

ROOT = config.ROOT
SIMULATOR = ROOT / "simulator.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Sim:
    port: int
    state: Path
    proc: subprocess.Popen

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _operator(self, *args: str) -> dict:
        cmd = [sys.executable, str(SIMULATOR), *args, "--state", str(self.state), "--port", str(self.port)]
        out = subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=ROOT)
        return json.loads(out.stdout)

    def approve(self, draft_id: str) -> dict:
        return self._operator("approve", "--draft-id", draft_id)

    def scenario(self, name: str) -> dict:
        return self._operator("scenario", "--name", name)

    @contextlib.contextmanager
    def under_scenario(self, name: str):
        self.scenario(name)
        try:
            yield
        finally:
            self.scenario("normal")


@pytest.fixture(scope="session")
def sim(tmp_path_factory: pytest.TempPathFactory) -> Sim:
    port = _free_port()
    state = tmp_path_factory.mktemp("sim") / "state.sqlite"
    proc = subprocess.Popen(
        [sys.executable, str(SIMULATOR), "serve", "--mode", "agent", "--port", str(port), "--state", str(state)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                if json.load(r).get("status") == "ok":
                    break
        except OSError:
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("simulator did not start")
    yield Sim(port, state, proc)
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def cfg(sim: Sim, tmp_path: Path) -> config.Config:
    return config.Config(api_base=sim.base, agent_token="demo-agent-site-01", model="test-model",
                         effort="low", state_dir=tmp_path / ".marble", dry=True)


@pytest.fixture
def client(cfg: config.Config) -> ApiClient:
    return ApiClient(cfg)
