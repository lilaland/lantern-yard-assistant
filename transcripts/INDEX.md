# AI coding transcripts

Every AI-assisted session on this submission, in order. Each session is exported from the Claude
desktop app (Claude Code) as JSONL and rendered to Markdown by `transcripts/render.py`, which keeps
user and assistant text in full, shortens tool inputs and results, counts thinking blocks, and
redacts e-mail addresses, key-shaped strings and the home directory. The raw exports (about 11 MB
each, including system prompt material) are kept outside the repository and can be provided on
request. Sessions that could not be exported are listed here as missing rather than omitted.

| # | Date | Tool | Purpose | File | Status |
|---|---|---|---|---|---|
| 1 | 2026-11-02 → 2026-11-04 | Claude Code (desktop), model Fable 5.1 | Everything: packet read, design discussion, BUILD_PLAN.md, blocks 0–12, frontend and evaluators, agentic judge suite, three live tuning iterations, the Marbly client experience and its live pass, this overview | `session-01-design-and-build.md` | re-exported 2026-11-04 08:43; 32 user turns, 87 assistant turns, 412 tool calls, one automatic context summary |

`SESSION_OVERVIEW.md` is the short version: timeline, the nine decisions and why, and every defect the
AI introduced with how it was caught.

Reading tips: search for `### [` to step through turns; `- **tool**` lines are the assistant's tool
calls with the result on the next line; "context summary (automatic, after compaction)" marks where
the app compressed earlier context into a summary that the assistant then worked from.

See `REJECTED.md` for AI suggestions that were changed or rejected and how the result was checked.
