"""Command-line entry point: ``python -m assistant <command>``."""
from __future__ import annotations

import argparse
import shutil
import sys

from . import config

COMMANDS = {
    "onboard": "Resolve data inconsistencies with the manager; record policies and preferences.",
    "brief": "Morning brief: one headline finding, evidence, uncertainty, next step, stock runway.",
    "ask": "Concierge: follow-up questions; state preferences to remember.",
    "order": "Prepare one order, show the exact draft, wait for operator approval, submit, verify.",
    "why": "Show the evidence and beliefs behind a subject, e.g. `why alder.delivery_days`.",
    "memory": "List the ledger: what is retained, what is rechecked, model cost so far.",
    "reset": "Delete local assistant state (.marble/). Does not touch the simulator.",
    "web": "Local observability UI over the same code (http://127.0.0.1:8780). Needs the simulator running.",
}

# Which BUILD_PLAN.md block delivers each command. Removed as commands land.
PLANNED_IN_BLOCK: dict[str, int] = {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assistant",
        description="Lantern Yard Kitchen operations assistant (SITE-01 only).",
    )
    parser.add_argument("--dry", action="store_true", help="Run detectors and render without calling the model.")
    parser.add_argument("--state-dir", help="Override the local state directory (default .marble/).")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        sp = sub.add_parser(name, help=help_text)
        # Accept the global flags after the subcommand too (`brief --dry`), without clobbering the global value.
        sp.add_argument("--dry", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--state-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        if name == "onboard":
            sp.add_argument("--defaults", action="store_true", help="Record default policies without asking.")
            sp.add_argument("--answers", help="Non-interactive answers: JSON object or id=key,... (e.g. inbound=not_seen).")
            sp.add_argument("--again", action="store_true", help="Ask every question again, not only new ones.")
        if name == "why":
            sp.add_argument("subject")
        if name == "ask":
            sp.add_argument("--once", help="Handle one line (a question or a command) and exit.")
            sp.add_argument("--fresh", action="store_true", help="Do not load the earlier conversation.")
            sp.add_argument("--yes", action="store_true", help="Record the model's record_belief calls without asking.")
        if name == "web":
            sp.add_argument("--port", type=int, default=8780)
            sp.add_argument("--host", default="127.0.0.1")
            sp.add_argument("--open", action="store_true", help="Open the browser.")
        if name == "reset":
            sp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
        if name == "memory":
            sp.add_argument("--all", action="store_true", help="Include superseded and conflicted beliefs.")
        if name == "order":
            sp.add_argument("--vendor", default="V1", help="Vendor id to order from (default V1, Alder Wholesale).")
            sp.add_argument("--propose-only", action="store_true", help="Show the proposal and stop; no draft.")
            sp.add_argument("--yes", action="store_true", help="Create the draft without the confirmation prompt.")
            sp.add_argument("--resume", action="store_true", help="Settle earlier drafts (submit approved ones) and stop.")
            sp.add_argument("--poll-seconds", type=float, default=2.0, help="How often to check for approval.")
            sp.add_argument("--timeout", type=float, default=600.0, help="How long to wait for approval, seconds.")
    return parser


def usage_cost(cfg: config.Config) -> float | None:
    """Model cost so far from the usage log written by llm.py. None when there is no log yet."""
    from .llm import usage_total
    total, runs, unknown = usage_total(cfg.usage_log)
    return None if runs == 0 else total


def cmd_memory(cfg: config.Config, args: argparse.Namespace) -> int:
    from .ledger import Ledger
    from .render import memory_text
    if not cfg.db_path.exists():
        print(f"No ledger yet at {cfg.db_path}. Run `onboard --defaults` or `brief` first.")
        return 0
    with Ledger(cfg.db_path) as ledger:
        print(memory_text(ledger, ledger.meta("onboarded_at"), usage_cost(cfg), include_closed=args.all))
    return 0


def cmd_why(cfg: config.Config, args: argparse.Namespace) -> int:
    from .ledger import Ledger
    from .render import why_text
    if not cfg.db_path.exists():
        print(f"No ledger yet at {cfg.db_path}.")
        return 0
    with Ledger(cfg.db_path) as ledger:
        print(why_text(args.subject, ledger.why(args.subject)))
    return 0


def cmd_reset(cfg: config.Config, args: argparse.Namespace) -> int:
    if not cfg.state_dir.exists():
        print(f"nothing to reset: {cfg.state_dir} does not exist")
        return 0
    if not args.yes:
        answer = input(f"Delete {cfg.state_dir}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("cancelled")
            return 1
    shutil.rmtree(cfg.state_dir)
    print(f"deleted {cfg.state_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config.load(dry=args.dry, state_dir=args.state_dir)
    if args.command == "reset":
        return cmd_reset(cfg, args)
    if args.command == "brief":
        from .modes import brief
        return brief.run(cfg)
    if args.command == "onboard":
        from .modes import onboard
        return onboard.run_defaults(cfg) if args.defaults else onboard.run_interactive(cfg, args.answers, args.again)
    if args.command == "memory":
        return cmd_memory(cfg, args)
    if args.command == "ask":
        from .modes import ask
        return ask.run(cfg, once=args.once, resume=not args.fresh, auto_approve=args.yes)
    if args.command == "web":
        from .web.server import serve
        return serve(cfg, args.host, args.port, args.open)
    if args.command == "order":
        from .modes import order
        return order.run(cfg, args.vendor, args.propose_only, args.yes, args.resume, args.poll_seconds, args.timeout)
    if args.command == "why":
        return cmd_why(cfg, args)
    block = PLANNED_IN_BLOCK.get(args.command)
    print(f"{args.command}: not implemented" + (f" yet (BUILD_PLAN.md block {block})" if block else ""), file=sys.stderr)
    return 2
