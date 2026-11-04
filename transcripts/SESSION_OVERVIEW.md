# Session overview: how this submission was built with Claude Code

One session with Claude Code (desktop app, model Fable 5.1), 2026-11-02 17:03 to 2026-11-04 08:43
local time, 32 prompts from the candidate, 87 assistant turns, 412 tool calls. The rendered
transcript is `session-01-design-and-build.md`; the raw export stays outside the repository. This page
is the short version: what happened, what was decided, and what the AI got wrong.

## Timeline

| When | What | Outcome |
|---|---|---|
| Nov 2, 17:03 | Candidate asks for the problem and every possible solution shape | Packet read, planted data story mapped numerically (INV-099 phantom receipt, INV-010 10x line, SKU change, stale route, missing recipe, injection in SUP-V1-01) |
| Nov 2, 21:25 | Candidate proposes onboarding → belief ledger → three helper agents | Design critique; direction becomes one agent with three modes and beliefs injected per run |
| Nov 2–3 | `BUILD_PLAN.md`: twelve blocks, pinned fixture numbers, demo runbook | Candidate chooses to double the 4–6 h timebox and log hours honestly |
| Nov 3, 00:13–17:42 | Blocks 0–5: client, records, analysis, detectors, ledger, policies, tools, model loop, order flow | A runnable dry brief after block 2; the order path with lost-response recovery after block 5 |
| Nov 3, 20:35–21:12 | Blocks 6–12: demo script, onboarding, ask REPL, correction block, safety tests, docs, rehearsals | Twelve blocks done; 117 tests |
| Nov 3, 21:15–21:52 | Frontend (`HANDOFF_FRONTEND.md`), evaluators, agentic judge suite | Seven dev pages, eight deterministic evaluators, LLM judge with calibration |
| Nov 3, 21:54–22:14 | First live model calls, three suite iterations | 0/8 (API limits) → 3/8 → 6/8 → 6/8; four product defects found and fixed |
| Nov 3, 22:16–23:03 | Marbly client experience: setup tour, alerts inbox, memory, order wizard, avatar | Driven live end to end for $0.17; sample-prompt scenario live twice |
| Nov 4, 08:42 | This overview, repository cleanup, push | |

Model spend for every live call on 2026-11-03: $2.42 over 106 calls (`.marble/costs.jsonl`).

## Decisions, and why

1. **One agent, three modes, not three agents.** The candidate's first design had three helper agents
   with beliefs baked into their prompts. Changed to one package with modes that differ only by prompt
   and tool allowlist, and beliefs in SQLite injected per run. Why: a belief in a prompt cannot be
   rechecked; three agents give three drifting views and three files to touch in the live-change
   exercise. Checked by the live-change exercise in `demo/DEMO_SCRIPT.md` (one ledger row changes the
   brief, the variance and the order together).
2. **Numbers come from code; the model narrates.** Detectors, reconciliation, sizing and ranking are
   deterministic and pinned by tests. `--dry` prints the same brief without a model and is the fallback.
   Why: the brief's grading is about evidence and honesty, and a number the model invented cannot be
   cited. The evaluator `numbers_grounded` enforces it after the fact.
3. **Beliefs have kinds and an authority order.** Policies and preferences are human-only and never
   rechecked; observations are detector-only and rechecked every run; assertions are rechecked on
   revision change, and a lower-authority source against a person's word marks a conflict instead of
   overwriting. Why: this is what makes the correction demo honest when `dataset_revision` flips to 2.
4. **Onboarding asks for rules, not facts.** Six questions derived from the finding classes present,
   with "unknown, keep checking" recording the default rule while leaving the fact at evidence level.
5. **The model has no order tools.** `BUILD_PLAN.md` originally gave the model `propose_order`,
   `create_draft`, `submit_order`. Block 5 removed them: the state machine runs under the operator's
   keyboard and the model may only explain a proposal. Why: SUP-V1-01 tries to talk an AI into
   submitting; with no tool there is nothing to be talked into. (`transcripts/REJECTED.md`, item 1.)
6. **A stdlib web server, not FastAPI.** The handoff left this open. Chosen for zero new dependencies
   and tests that run a real server thread without a test client. SSE over `ThreadingHTTPServer`
   carries model turns; a `record_belief` call pauses on a gate until the browser answers.
7. **A machine-wide cost log.** Every model call from any state directory, the UI, the evaluators and
   the judge appends to `.marble/costs.jsonl`, so tuning spend is never invisible.
8. **The judge is trusted only after calibration.** Four known transcripts, two good and two bad, must
   be graded correctly on every run before judge verdicts count. Numbers are never judged by the LLM;
   the deterministic evaluator's verdict is handed to it.
9. **Time is logged from the clock.** Twice the assistant wrote a time-log row from an assumed time
   and had to correct it in a separate commit. The rule became: run `date` before writing a row.

## What the AI got wrong, and how it was caught

| Defect | Caught by |
|---|---|
| On-hand stock derived from the anomaly list, so a corrected invoice silently counted (block 2) | the revision-2 test |
| Every observation reported "changed" at revision 2 because the source string embedded the revision (block 3) | a smoke run of the footer |
| Two pinned order numbers wrong; the eggs warning used a seven-day average for a weekend item (block 5) | tests and a hand check |
| Tuple nesting in an onboarding option; `shlex` stripping JSON quotes in `ask` (blocks 7–8) | tests |
| 22 nullable tool parameters where the API allows 16; `additionalProperties` as a schema (first live run) | the first live call, instantly, at no cost |
| Citations written as `(file ID, rev1)` so no link matched; briefs over 300 words; the ledger forgetting the lost-response story; the judge re-grading numbers it could not see | live iteration 2 |
| A brief opening with "Defaults are not in force; you are onboarded"; model-side rounding of a tool figure | live iteration 3 |
| "No changes since the last brief" after onboarding; a literal `null` under chat bubbles | the live client pass |
| A rubric that misread the "unknown" onboarding answer; a fake judge that inverted criteria containing "not" | fake and live runs |

## What is still open

A fourth suite iteration after the last prompt changes; the "why" answer that should mention the
vendor sheet; one rehearsal of the client flow by a person; the session-1 reading and thinking time
between prompts, which the transcript cannot measure. See `STATUS.md`.
