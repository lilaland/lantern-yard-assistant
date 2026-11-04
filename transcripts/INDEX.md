# AI coding transcripts

Every AI-assisted session on this submission, in order. Each session is exported from the Claude
desktop app (Claude Code) as JSONL and rendered to Markdown by `transcripts/render.py`, which keeps
user and assistant text in full, shortens tool inputs and results, counts thinking blocks, and
redacts e-mail addresses, key-shaped strings and the home directory. The raw exports (about 11 MB
each, including system prompt material) are kept outside the repository and can be provided on
request. Sessions that could not be exported are listed here as missing rather than omitted.

| # | Date | Tool | Purpose | File | Status |
|---|---|---|---|---|---|
| 1 | 2026-11-02 → 2026-11-03 | Claude Code (desktop), model Fable 5.1 | Read the packet, map the planted data story, design discussion, BUILD_PLAN.md, blocks 0–6 (client, detectors, ledger, tools and model loop, order flow, demo) | `session-01-design-and-build.md` | exported 2026-11-03 20:39; 11 user turns, 39 assistant turns, 154 tool calls, one automatic context summary |
| 2 | 2026-11-04 (planned) | Claude Code (desktop) | Blocks 7–12: interactive onboarding, ask REPL, correction narration, named injection/scope tests, ARCHITECTURE.md, README, rehearsals | `session-02-iterate.md` | not started |

Reading tips: search for `### [` to step through turns; `- **tool**` lines are the assistant's tool
calls with the result on the next line; "context summary (automatic, after compaction)" marks where
the app compressed earlier context into a summary that the assistant then worked from.

See `REJECTED.md` for AI suggestions that were changed or rejected and how the result was checked.
