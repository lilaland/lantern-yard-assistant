#!/usr/bin/env python3
"""Render a Claude Code session export (JSONL) as readable, redacted Markdown.

Usage: python3 transcripts/render.py <export.jsonl> <out.md> [--title "..."]

Keeps every user and assistant message in full. Tool calls become one line each (tool name and a
short view of the input); tool results are truncated. Thinking blocks are counted, not printed.
Redacts e-mail addresses, API-key-shaped strings and the home directory. Stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")
HOME = re.compile(r"/Users/[A-Za-z0-9_.-]+")
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
RESULT_CHARS = 600
INPUT_CHARS = 400


def redact(s: str) -> str:
    s = EMAIL.sub("[email redacted]", s)
    s = KEY.sub("[key redacted]", s)
    return HOME.sub("~", s)


def short(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return json.dumps(content)


def render(path: Path, title: str) -> tuple[str, dict]:
    out = [f"# {title}", "", f"Rendered from the Claude Code export `{path.name}` by `transcripts/render.py`. "
           "User and assistant text are complete; tool inputs and results are shortened; thinking blocks are "
           "counted only; e-mail addresses, key-shaped strings and the home directory are redacted.", ""]
    stats = {"user": 0, "assistant": 0, "tool_calls": 0, "tool_results": 0, "thinking": 0, "sidechain_skipped": 0,
             "compaction_summaries": 0}
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(raw)
        except ValueError:
            continue
        if o.get("type") not in ("user", "assistant") or not isinstance(o.get("message"), dict):
            continue
        if o.get("isSidechain"):
            stats["sidechain_skipped"] += 1
            continue
        role = o["message"].get("role", o["type"])
        ts = (o.get("timestamp") or "")[:16].replace("T", " ")
        content = o["message"].get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                text = REMINDER.sub("", b.get("text", "")).strip()
                if not text:
                    continue
                if text.startswith("This session is being continued from a previous conversation"):
                    stats["compaction_summaries"] += 1
                    out += [f"### [{ts}] context summary (automatic, after compaction)", "", redact(text), ""]
                    continue
                stats[role] += 1
                label = "User" if role == "user" else "Assistant"
                out += [f"### [{ts}] {label}", "", redact(text), ""]
            elif t == "tool_use":
                stats["tool_calls"] += 1
                inp = json.dumps(b.get("input", {}), ensure_ascii=False)
                out.append(f"- **tool** `{b.get('name')}` — {redact(short(inp, INPUT_CHARS))}")
            elif t == "tool_result":
                stats["tool_results"] += 1
                text = redact(result_text(b.get("content")))
                flag = " (error)" if b.get("is_error") else ""
                out.append(f"  - result{flag}, {len(text):,} chars: {short(text, RESULT_CHARS)}")
            elif t == "thinking":
                stats["thinking"] += 1
    out += ["", "---", "", "Counts: " + ", ".join(f"{k} {v}" for k, v in stats.items())]
    return "\n".join(out) + "\n", stats


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    title = argv[argv.index("--title") + 1] if "--title" in argv else dst.stem
    text, stats = render(src, title)
    dst.write_text(text, encoding="utf-8")
    print(f"wrote {dst} ({len(text):,} chars); {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
