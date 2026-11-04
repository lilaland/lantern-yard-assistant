from assistant import config


def test_defaults_point_at_local_fixture(monkeypatch):
    for key in config.DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    cfg = config.load()
    assert cfg.api_base == "http://127.0.0.1:8765"
    assert cfg.agent_token == "demo-agent-site-01"
    assert cfg.state_dir.name == ".marble"
    assert cfg.dry is False


def test_env_overrides_and_trailing_slash(monkeypatch):
    monkeypatch.setenv("MARBLE_API_BASE", "http://127.0.0.1:9999/")
    monkeypatch.setenv("MARBLE_MODEL", "claude-sonnet-5-5")
    cfg = config.load(dry=True, state_dir="/tmp/x")
    assert cfg.api_base == "http://127.0.0.1:9999"
    assert cfg.model == "claude-sonnet-5-5"
    assert str(cfg.state_dir) == "/tmp/x"
    assert cfg.dry is True


def test_now_is_fixed_by_revision():
    assert config.now_for_revision(1).startswith("2026-10-05T06:00")
    assert config.now_for_revision(2).startswith("2026-10-05T06:15")
    assert config.now_for_revision(99) == config.now_for_revision(2)


def test_site_is_pinned():
    assert config.SITE_ID == "SITE-01"


def test_global_flags_are_accepted_before_or_after_the_subcommand():
    from assistant.cli import build_parser
    parser = build_parser()
    assert parser.parse_args(["--dry", "brief"]).dry is True
    assert parser.parse_args(["brief", "--dry"]).dry is True
    assert parser.parse_args(["brief"]).dry is False
    assert parser.parse_args(["order", "--propose-only", "--dry", "--state-dir", "x"]).state_dir == "x"
    assert parser.parse_args(["--state-dir", "y", "why", "s"]).state_dir == "y"
