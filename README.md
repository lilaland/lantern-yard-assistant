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
.venv/bin/python -m pytest -q   # 159 tests against a throwaway simulator
```

Python 3.10+ (built on 3.10.16, macOS). Dependencies: `anthropic` (model) and `pytest` (tests);
everything else is the standard library.

## Demo commands

Terminal A (operator): the plain simulator, or the helper that runs it and can put it back to a fresh
file (what Marbly's "Forget everything and start over" asks for; approve and scenario stay typed here):

```bash
python3 simulator.py serve --mode agent --state .state/live.sqlite
# or
python3 demo/operator_helper.py serve --port 8765 --state .state/live.sqlite --fresh
```

Terminal B (assistant); add `--dry` to any command to skip the model (`MARBLE_OPERATOR_URL` is only
handed to the browser; the assistant never calls the helper or opens the simulator's file):

```bash
export MARBLE_STATE_DIR=.marble/live MARBLE_SIM_STATE=.state/live.sqlite MARBLE_OPERATOR_URL=http://127.0.0.1:8766
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

## Web UI (additional, beyond the brief's deliverables)

```bash
python -m assistant web            # http://127.0.0.1:8780 ; needs the simulator; --dry disables model turns
```

Run it from the activated venv (`source .venv/bin/activate`), which is where the `anthropic` package
lives. Two kinds of state exist and both survive restarts: the simulator's `--state` file (drafts,
orders, scenario flags; the operator's) and the assistant's memory directory `MARBLE_STATE_DIR`
(default `.marble`: beliefs, chat, usage, setup progress). If the memory directory already holds a
session, `web` says so and asks whether to continue, start fresh in a new directory, or wipe;
`--keep` and `--fresh` answer without asking. The startup lines name the simulator, the memory
directory and whether the model is on. Onboarding runs when the memory is empty; "Ask me the
questions again" in Settings re-asks with memory intact.

The default page is **Marbly**, the client experience (driven live end to end on 2026-11-03 for $0.1720 with Sonnet 5.5). It opens with an
overview and a four-step setup pinned to the top until it is complete: (1) straighten out the facts
(each question is a card whose options and ledger effects are fixed in code; the model may only word
the lead-in), (2) look at today's critical alerts (the bell pulses until opened), (3) see what Marbly
remembers (the book pulses) with the three capabilities and sample prompts, (4) place the first order
and see it through. The order is a three-step wizard that also lists every draft for the vendor with its status (resume one that is awaiting approval, resolve one whose result was lost, reopen an outcome) and nets accepted orders out of the next proposal; closing the panel loses nothing, the link in the chat reopens it as it was left. Every later visit starts a session from the sunrise button: the chat clears, a fresh brief runs and the chips follow it; "next day" moves the demo clock one day (every command and page sees the same clock, kept in the ledger) so cutoffs, delivery dates and the weekend come closer while the counts stay the last ones taken. The wizard: review every line and why, with the cases editable and a
"change it in words" request that Marbly turns into revised lines (validated against the catalog
before anything is drafted), create the draft and show the operator's approve command with where to
run it (never a button), then the verified outcome. A planner (calendar icon) lists every vendor by
order-by cutoff with what runs out before the next delivery, the proposed lines, and what is already
ordered. Memory entries can be changed in place, and any message has an "add to memory" button; both
record under your name. Marbly's avatar is a
marble of white and blue with gold streaks and big eyes; it squishes and bounces while the model is
thinking. The gear reveals the developer pages: Chat (ask mode with approval cards for anything the
model wants to remember), Memory (beliefs, chains, conflicts, drafts, usage), Findings (ranked cards
with citation links, the revision diff, runway and reconciliation), Order lab (what-if sliders that
never touch the ledger, the real draft path with the approve command displayed, never executed),
Alerts (ranked findings with the revision diff, runway and reconciliation), Tools (the tool × mode matrix with a "try it" form), Injection lab, About (diagrams from
`docs/diagrams/`). Standard library only; Mermaid comes from a pinned CDN. Design notes in
`HANDOFF_FRONTEND.md`; its hours are logged separately in `TIME_LOG.md`.

Every model call, from any command, state directory, the UI or the evaluators, appends to
`.marble/usage.jsonl` (per state dir) and to the machine-wide `.marble/costs.jsonl`
(`MARBLE_COST_LOG`); the Memory page and the status bar show both totals.

## Evaluators

```bash
python evals/run.py                    # fixtures against a throwaway simulator; exit 1 on any unmet expectation
python evals/run.py --runs .marble/runs   # also grade every real conversation recorded on this machine
python evals/run.py --live --budget-usd 1.00   # scripted live conversations with the real model, cost-capped
```

Eight deterministic evaluators over a run's messages (numbers grounded in tool results, citations
resolve at the right revision, allowlist respected, status vocabulary, memory only from the person
or a gate, injection quoted not followed, brief shape, cost bound), five passing fixtures built from
real tool outputs and two negatives that must fail. See `evals/evaluators.py`.

### Agentic suite with an LLM judge

```bash
python evals/agentic.py --list                     # scenarios, flows, estimated cost per iteration; no calls
python evals/agentic.py --fake                     # the whole pipeline with a fake assistant and judge; no calls
python evals/agentic.py --budget-usd 5 --iterations 2          # live; stops at the budget
python evals/agentic.py --assistant-model claude-sonnet-5-5    # cheaper tuning runs
```

Eight scenarios in `evals/scenarios/` run scripted or persona-driven conversations through the real
modes (brief, onboarding wording and answers, ask with the approval gate, the order explanation, the
real order path under `supplier-timeout`, the delivery correction with a conflicting human
statement, the injection replay). Every turn is graded by the deterministic evaluators; every
scenario is graded by a Sonnet 5.5 judge against its rubric with quoted evidence. The judge is
trusted only after it grades four calibration transcripts (two good, two bad) correctly. The report
(`evals/live/<timestamp>/report.md`) has the per-scenario table, the judge's evidence, a flow ×
scenario coverage matrix and the spend; each turn is also written as a run record for
`evals/run.py --runs`. Measured on 2026-11-03: about $0.60 per iteration with the Opus assistant and Sonnet judge;
three iterations took the pass rate from 3/8 to 6/8 and surfaced a different defect each time (see
`VERIFICATION.md`, "Live runs").

Two read-only tools were added for these flows: `sizing_preview` (what the order mode would propose
now) and `fulfilment_status` (what happened to a draft). Neither drafts, submits or approves.

## Credentials

The assistant runtime receives only the agent token `demo-agent-site-01` (`MARBLE_AGENT_TOKEN`) and,
optionally, `ANTHROPIC_API_KEY` from `.env` (gitignored). It never receives the operator credential,
the simulator's `.state/` database, or shell access to the approval helper; `tests/test_scope.py`
asserts the source code contains no path to any of them.

## Model and cost

Default model `claude-opus-5-5` ($4 / $20 per million input / output tokens), override with
`MARBLE_MODEL=claude-sonnet-5-5` ($2 / $10). Every call appends tokens and dollars to
`.marble/usage.jsonl` and the machine-wide `.marble/costs.jsonl`; `python -m assistant memory` and the
UI's Memory page print the totals. Measured on 2026-11-03 through the agentic eval suite: a narrated
brief costs about $0.04, an `ask` turn about $0.016, an order explanation about $0.04, a Sonnet judge
verdict about $0.02; one full suite iteration of eight scenarios about $0.60. Spend across every live run through 2026-11-04 (three suite iterations, two client-sample runs, their
calibration, one full client-flow pass in the browser, the worded-revision test and the candidate's own
rehearsals): $2.81 (122 calls). The deterministic path (`--dry`) is what every test and the smoke
demo exercise.

## Actual time spent

Logged per row from the clock in [`TIME_LOG.md`](TIME_LOG.md): about 1.85 hours of build time across
blocks 0–12 on 2026-11-03, about 3.2 hours of frontend, evaluators, live tuning and the client
experience through 2026-11-04, and a short wrap-up, plus the design and planning session on the evening
of 2026-11-02 whose hours the candidate fills in from their own record. The candidate chose to exceed
the 4–6 hour timebox and says so there.

## Unfinished work

- One human-typed rehearsal of the browser runbook in a single sitting with a key; every piece has run
  live (see `VERIFICATION.md`), the latest flow changes (sessions, follow-up chips) only in dry mode.
- Two judge criteria stayed open after the last agentic iteration; the design-session hours in `TIME_LOG.md`.
- `demo/CHECKLIST.md` is the pre-demo list.
- Known limitations of the sizing and recheck logic are listed in `STATUS.md` and `ARCHITECTURE.md`.

## AI use

All code and documents were written with Claude Code (Fable 5.1) from the candidate's design and
review. Transcripts, the rendering script and the changed-or-rejected suggestions are in
[`transcripts/`](transcripts/INDEX.md).
