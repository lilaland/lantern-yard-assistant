"""The launch dialogue: continue, fresh, or wipe; scripted flags skip it; non-interactive runs keep."""
from pathlib import Path

from assistant import launch, policies
from assistant.api_client import ApiClient
from assistant.config import Config
from assistant.ledger import Ledger
from assistant.records import load_all


def test_empty_memory_asks_nothing(cfg):
    assert launch.describe(cfg) is None
    assert launch.prelaunch(cfg, interactive=True, input_fn=lambda p: (_ for _ in ()).throw(AssertionError("asked"))) == cfg


def feed(*lines):
    it = iter(lines)
    return lambda prompt: next(it)


def test_continue_fresh_and_wipe(cfg, sim, tmp_path):
    with Ledger(cfg.db_path) as l:
        policies.write_defaults(l, load_all(ApiClient(cfg)))
        l.set_meta("onboarded_at", "2026-11-03T20:00:00-05:00")
    desc = launch.describe(cfg)
    assert desc and "onboarded 2026-11-03T20:00" in desc and "4 rules" in desc
    assert launch.prelaunch(cfg, interactive=False) == cfg                      # scripts keep
    assert launch.prelaunch(cfg, keep=True, interactive=True) == cfg
    kept = launch.prelaunch(cfg, interactive=True, input_fn=feed(""))  # Enter = continue
    assert kept == cfg
    fresh = launch.prelaunch(cfg, interactive=True, input_fn=feed("f"))
    assert fresh.state_dir != cfg.state_dir and fresh.state_dir.parent == cfg.state_dir.parent and cfg.db_path.exists()
    assert launch.prelaunch(cfg, fresh=True).state_dir.name.startswith(cfg.state_dir.name + "-")
    refused = launch.prelaunch(cfg, interactive=True, input_fn=feed("w", "wrong-name", "c"))
    assert refused == cfg and cfg.db_path.exists()
    wiped = launch.prelaunch(cfg, interactive=True, input_fn=feed("w", cfg.state_dir.name))
    assert wiped == cfg and not cfg.state_dir.exists()


def test_cli_flags_parse():
    from assistant.cli import build_parser
    a = build_parser().parse_args(["web", "--fresh"])
    assert a.fresh and not a.keep
