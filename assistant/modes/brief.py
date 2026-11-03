"""Morning brief. Read-only. Deterministic findings; narration is added in block 4."""
from __future__ import annotations

import sys

from .. import analysis, detectors, render
from ..api_client import ApiClient, ServerUnavailable
from ..config import Config
from ..records import load_all


def run(cfg: Config) -> int:
    client = ApiClient(cfg)
    try:
        records = load_all(client)
    except ServerUnavailable:
        print(f"Cannot reach the operational API at {cfg.api_base}. Start it with:\n"
              f"  python3 simulator.py serve --mode agent", file=sys.stderr)
        return 1
    findings = detectors.run_all(records)
    burn, window = analysis.daily_burn(records, 7)
    print(render.brief_text(records, findings, burn, window))
    if not cfg.dry:
        print("\n(model narration arrives in block 4; this is the deterministic brief)", file=sys.stderr)
    return 0
