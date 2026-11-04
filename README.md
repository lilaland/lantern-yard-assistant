# Marble candidate packet

Start here. Everything is synthetic.

## Setup


1. Read `CANDIDATE_BRIEF.md` and `SITE_PROFILE.md`.
2. Run `python3 simulator.py serve --mode agent`.
3. Check `curl http://127.0.0.1:8765/health`.
4. Read `API.md` for the scoped agent token, reads, order flow, and manual scenario controls.
5. Build your assistant around those APIs. Use `DATA_GUIDE.md` to understand the records. A human runs approval and scenario commands in a separate terminal.

The supplied APIs already handle operational data storage and the mock order workflow. Implement your assistant's tools, reasoning flow, and useful memory. The CSVs are included for transparency; the running assistant uses the APIs. No dataset download, cloud account, frontend, or scale benchmark is required.

## Requirements

Python 3.10 or newer for the supplied tools; no pip packages. Your implementation can use any stack. The server binds only to 127.0.0.1. Default port: 8765. State is stored in `.state/simulator.sqlite` and survives restarts. Use Ctrl-C to stop it. No real network integration is called.

The assignment is 4–6 hours including submission materials, within 48 hours. Read the brief for the demo, architecture diagram, transcripts, and interview requirements. Do not spend time polishing a UI.

---

# Submission: Lantern Yard Kitchen assistant

An operations assistant for SITE-01 over the supplied API: a morning brief that finds and explains
one issue with evidence, an onboarding pass and an `ask` REPL that record the manager's rules and
preferences in a ledger that survives restarts and explains what it rechecks, and an order copilot
that drafts, waits for the operator's approval, submits and verifies, honestly. Design and limits:
[`ARCHITECTURE.md`](ARCHITECTURE.md). Test record: [`VERIFICATION.md`](VERIFICATION.md). Current
state and unfinished work: [`STATUS.md`](STATUS.md). AI transcripts: [`transcripts/`](transcripts/INDEX.md).

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install anthropic pytest
cp .env.example .env            # add ANTHROPIC_API_KEY for model narration; everything runs without it with --dry
.venv/bin/python -m pytest -q   # 117 tests against a throwaway simulator
```

Python 3.10+ (built on 3.10.16, macOS). Dependencies: `anthropic` (model) and `pytest` (tests);
everything else is the standard library.

## Demo commands

Terminal A (operator):

```bash
python3 simulator.py serve --mode agent --state .state/live.sqlite
```

Terminal B (assistant); add `--dry` to any command to skip the model:

```bash
export MARBLE_STATE_DIR=.marble/live
python -m assistant onboard                    # six questions from the findings (or: onboard --defaults)
python -m assistant brief                      # headline, evidence, uncertainty, next step, runway, retained/rechecked
python -m assistant ask                        # follow-ups; `set preference order.buffer_days {"days": 2}`; `why <subject>`
python -m assistant order --propose-only       # rationale table and the exact draft JSON, nothing sent
python -m assistant order                      # creates the draft, prints the approve command, waits, submits, verifies
python -m assistant memory                     # what is retained, what is rechecked, model cost so far
```

Operator steps during the demo, in terminal A with the same `--state`:
`python3 simulator.py scenario --name delivery-correction`, `scenario --name supplier-timeout`,
`approve --draft-id D-…`. The full 15-minute runbook is [`demo/DEMO_SCRIPT.md`](demo/DEMO_SCRIPT.md);
`demo/demo.sh --no-pause` plays both terminals as a smoke test.

## Credentials

The assistant runtime receives only the agent token `demo-agent-site-01` (`MARBLE_AGENT_TOKEN`) and,
optionally, `ANTHROPIC_API_KEY` from `.env` (gitignored). It never receives the operator credential,
the simulator's `.state/` database, or shell access to the approval helper; `tests/test_scope.py`
asserts the source code contains no path to any of them.

## Model and cost

Default model `claude-opus-5-5` ($4 / $20 per million input / output tokens), override with
`MARBLE_MODEL=claude-sonnet-5-5` ($2 / $10). Every call appends tokens and dollars to
`.marble/usage.jsonl`; `python -m assistant memory` prints the running total. Measured cost at
submission time: **$0.00**, because no key was available during the build and no live call has
been made. The deterministic path (`--dry`) is what every test and the smoke demo exercise.

## Actual time spent

Logged per block from the clock in [`TIME_LOG.md`](TIME_LOG.md): about 1.7 hours of build time
across blocks 0–11 on 2026-11-03, plus the design and planning session on the evening of 2026-11-02
whose hours the candidate fills in from their own record. The candidate chose to exceed the 4–6 hour
timebox and says so there.

## Unfinished work

- A live model run: narrated brief, order explanation, model-worded onboarding and `ask` in plain
  words are built and tested against a fake API but have not been exercised with a real key.
- A rehearsal of the runbook with a person typing in terminal A (two scripted rehearsals are in `demo/REHEARSAL.md`).
- Known limitations of the sizing and recheck logic are listed in `STATUS.md` and `ARCHITECTURE.md`.

## AI use

All code and documents were written with Claude Code (Fable 5.1) from the candidate's design and
review. Transcripts, the rendering script and the changed-or-rejected suggestions are in
[`transcripts/`](transcripts/INDEX.md).
