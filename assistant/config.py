"""Runtime configuration from environment variables and an optional .env file.

The assistant only ever receives the scoped agent token. It never reads the
simulator's state database or the operator credential, and it has no setting
for a site other than SITE-01.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_ID = "SITE-01"

# "Now" is fixed by the packet, not by the wall clock (DATA_GUIDE.md, API.md).
# Revision 2 is what the API reports after the delivery-correction scenario.
NOW_BY_REVISION = {
    1: "2026-10-05T06:00:00-04:00",
    2: "2026-10-05T06:15:00-04:00",
}

DEFAULTS = {
    "MARBLE_API_BASE": "http://127.0.0.1:8765",
    "MARBLE_AGENT_TOKEN": "demo-agent-site-01",
    "MARBLE_MODEL": "claude-opus-5-5",
    "MARBLE_EFFORT": "medium",
    "MARBLE_STATE_DIR": str(ROOT / ".marble"),
    "MARBLE_COST_LOG": str(ROOT / ".marble" / "costs.jsonl"),
}


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader. Variables already in the environment win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    api_base: str
    agent_token: str
    model: str
    effort: str
    state_dir: Path
    dry: bool = False
    cost_log: Path | None = None  # every model call from any state dir also lands here (set by load())

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.sqlite"

    @property
    def usage_log(self) -> Path:
        return self.state_dir / "usage.jsonl"


def load(dry: bool = False, state_dir: str | None = None) -> Config:
    load_dotenv()

    def env(key: str) -> str:
        return os.environ.get(key, DEFAULTS[key])

    return Config(
        api_base=env("MARBLE_API_BASE").rstrip("/"),
        agent_token=env("MARBLE_AGENT_TOKEN"),
        model=env("MARBLE_MODEL"),
        effort=env("MARBLE_EFFORT"),
        state_dir=Path(state_dir or env("MARBLE_STATE_DIR")),
        dry=dry,
        cost_log=Path(env("MARBLE_COST_LOG")) if env("MARBLE_COST_LOG") else None,
    )


def now_for_revision(revision: int) -> str:
    """The packet's fixed 'now' for a given dataset revision."""
    return NOW_BY_REVISION.get(revision, NOW_BY_REVISION[max(NOW_BY_REVISION)])
