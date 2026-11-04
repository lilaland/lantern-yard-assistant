"""What to do with existing memory before a launch: continue, start fresh, or wipe. Asked once, at a terminal."""
from __future__ import annotations

import shutil
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import Config
from .ledger import Ledger


def describe(cfg: Config) -> str | None:
    """One line about the memory in the state directory, or None if there is none."""
    if not cfg.db_path.exists():
        return None
    with Ledger(cfg.db_path) as led:
        c = led.counts()
        onboarded = led.meta("onboarded_at")
        last = led.meta("last_run_wall")
        accepted = sum(1 for d in led.drafts() if d.status == "accepted")
        n_ex = len(led.exchanges("ask"))
    if not onboarded and not any(v["active"] for v in c.values()):
        return None
    return (f"{cfg.state_dir}: {'onboarded ' + onboarded[:16] if onboarded else 'not onboarded'}; "
            f"{c['policy']['active']} rules, {c['preference']['active']} preferences, {c['assertion']['active']} facts, "
            f"{n_ex} chat exchange(s), {accepted} accepted order(s); last run {last[:16] if last else 'never'}")


def fresh_dir(cfg: Config) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    base = cfg.state_dir
    cand = base.parent / f"{base.name}-{stamp}"
    n = 1
    while cand.exists():
        n += 1
        cand = base.parent / f"{base.name}-{stamp}-{n}"
    return cand


def prelaunch(cfg: Config, fresh: bool = False, keep: bool = False, input_fn: Callable[[str], str] = input,
              interactive: bool | None = None) -> Config:
    """Decide which memory to launch with. Returns the config to use (possibly a new state dir).

    --fresh: a new timestamped state directory next to the configured one, no questions.
    --keep: continue with whatever is there, no questions.
    Otherwise, at a terminal, show what the directory holds and ask; non-interactive runs keep.
    """
    if fresh:
        new = fresh_dir(cfg)
        print(f"Fresh memory: {new} (the previous directory {cfg.state_dir} is untouched).")
        return replace(cfg, state_dir=new)
    summary = describe(cfg)
    if keep or summary is None:
        return cfg
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        return cfg
    print(f"Found memory from an earlier session:\n  {summary}")
    print("  [c] continue with this memory (default)\n  [f] start fresh in a new directory, keep this one\n  [w] wipe this directory and start over")
    while True:
        raw = input_fn("Choose c / f / w: ").strip().lower()
        if raw in ("", "c", "continue"):
            return cfg
        if raw in ("f", "fresh"):
            return prelaunch(cfg, fresh=True)
        if raw in ("w", "wipe"):
            sure = input_fn(f"Type the directory name ({cfg.state_dir.name}) to confirm the wipe: ").strip()
            if sure == cfg.state_dir.name:
                shutil.rmtree(cfg.state_dir)
                print(f"Wiped {cfg.state_dir}. The supplier's own records are untouched.")
                return cfg
            print("  not wiped")
        else:
            print("  c, f or w")
