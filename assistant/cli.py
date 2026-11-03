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
}

# Which BUILD_PLAN.md block delivers each command. Removed as commands land.
PLANNED_IN_BLOCK = {"brief": 2, "memory": 3, "onboard": 3, "order": 5, "ask": 8, "why": 8}


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
        if name == "onboard":
            sp.add_argument("--defaults", action="store_true", help="Record default policies without asking.")
        if name == "why":
            sp.add_argument("subject")
        if name == "reset":
            sp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    return parser


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
    block = PLANNED_IN_BLOCK[args.command]
    print(f"{args.command}: not implemented yet (BUILD_PLAN.md block {block})", file=sys.stderr)
    return 2
