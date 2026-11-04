# Status and unfinished work

Updated after block 12 and the first frontend phase (2026-11-03 evening). All planned blocks are done; what remains is listed below. This is the honest state of the submission; the
README's "unfinished work" section is written from it.

## Working and tested

| Area | Where | Evidence |
|---|---|---|
| Scoped API client, pagination, revision capture, typed errors | `assistant/api_client.py`, `records.py` | `tests/test_api_client.py`, `test_records.py` |
| Reconciliation, invoice checks, price history, runway, forecast | `assistant/analysis.py` | `tests/test_analysis.py` (pinned fixture numbers) |
| Ten detectors, deterministic ranking, citations, revision stamps | `assistant/detectors.py` | `tests/test_detectors.py` incl. revision-2 behaviour |
| Belief ledger with kinds, sources, supersede/conflict rules, drafts | `assistant/ledger.py` | `tests/test_ledger.py` incl. a four-process restart test |
| Defaults, ledger → detector params, per-run recheck | `assistant/policies.py` | `tests/test_policies.py` incl. the human-vs-correction conflict |
| Correction path: "what changed since the last brief" block (headline move, cleared findings, new notes, corrected records, conflicts needing a person), "no changes" line, order warning for inbound scheduled inside the window | `assistant/render.py`, `policies.recheck`, `ordering.py` | `tests/test_correction.py` incl. a CLI brief before/after `delivery-correction` |
| Model tools with per-mode allowlists; evidence wrapping | `assistant/tools.py` | `tests/test_tools.py` |
| Tool loop, usage/cost log, keyless fallback | `assistant/llm.py` | `tests/test_llm.py` (fake API) |
| Order sizing with rationale; state machine; lost-response recovery | `assistant/ordering.py`, `orderflow.py`, `modes/order.py` | `tests/test_order_flow.py` against the real simulator |
| Interactive onboarding: six questions from findings, options/defaults/ledger effects fixed in code, "unknown" keeps facts at evidence level, idempotent re-run, `--answers` for scripts | `assistant/onboarding.py`, `modes/onboard.py` | `tests/test_onboarding.py` incl. a CLI run followed by a brief |
| `ask` REPL: deterministic commands (`why`, `memory`, `findings`, `set`) without a model; model answers with READ/ANALYSIS/MEMORY tools; conversation persisted per exchange and resumed across restarts | `assistant/modes/ask.py`, `prompts/ask.md` | `tests/test_ask.py` (fake model; CLI run changes the next order proposal) |
| CLI: `onboard [--defaults|--answers|--again]`, `brief`, `ask [--once|--fresh]`, `order`, `why`, `memory`, `reset` | `assistant/cli.py` | subprocess tests above; `demo/demo.sh` |
| Named safety tests: injection (SUP-V1-01 flagged, evidence-only text, no tool can submit, a disobedient model changes nothing, documents cannot write policies, server-enforced approval, no invented reference) and scope (client and fixture refuse SITE-02, no tool takes a site, no CSV or fixture-state access, agent token only) | `tests/test_injection.py`, `tests/test_scope.py` | `VERIFICATION.md` is the written record (159 tests) |

## Not yet verified

- **A live model call.** No `ANTHROPIC_API_KEY` was available while building. The loop is tested
  against a fake API, and the installed SDK accepts every field the loop sends, but no narrated
  brief, order explanation or model-worded onboarding question has been produced yet. Measured
  cost is therefore zero so far.

| Web UI (additional): stdlib server, SSE model turns, approval gate cards; **Marbly client experience** (overview with a go button, four-step setup pinned until complete with dimmed spotlights, onboarding as cards, a visible recheck that reports the effect of the answers, critical alerts, inbox, client memory with in-place edits and add-to-memory on messages, a three-step order wizard with editable lines and worded revisions (tested live) that lists every draft with its status, resumes an awaiting draft, nets accepted orders out of the next proposal, and survives closing the panel (the chat link reopens it as left), the operator command with where to run it, a planner of what to order next per vendor, marble avatar that squishes while thinking, settings gear hiding the developer pages; "Forget everything" also resets the supplier through the operator helper `demo/operator_helper.py` when `MARBLE_OPERATOR_URL` names it, called from the browser, never from the assistant process); developer pages (chat with mode switch, memory, alerts with revision diff, what-if order lab, tool matrix with try-it, injection lab, diagrams) | `assistant/web/`, `docs/diagrams/` | `tests/test_web.py` (endpoints incl. onboarding, alerts, chat modes; gate over SSE; boundaries), `test_gate.py`, `test_citations.py`; clicked through in a browser |
| Evaluators: eight deterministic checks over recorded runs, fixtures from real tool outputs, negatives, `--live` with a budget | `evals/` | `tests/test_evals.py`; `python evals/run.py` |
| Machine-wide cost log across state dirs, evals and the UI | `config.cost_log`, `llm._log`, Memory page | `tests/test_llm.py` |
| Agentic suite: eight scenarios through the real modes, per-turn deterministic grading, Sonnet judge with calibration gate, persona driver, coverage matrix, budget cap, fake mode | `evals/agentic.py`, `evals/judge.py`, `evals/scenarios/`, `evals/calibration/` | `tests/test_agentic.py` (fake mode); `python evals/agentic.py --fake` |

Submission documents: `ARCHITECTURE.md` (rewritten 2026-11-04 to the current system: one Mermaid diagram with both databases and the web layer, the two-databases section with how to start over, decisions, evaluation, the retained-versus-rechecked
table, limits; about two pages), the README's "Submission" section (setup, demo commands, credentials,
model and measured cost, actual time, unfinished work), `VERIFICATION.md`, `transcripts/`.

## Unfinished (planned for the second session)

| Block | Item | Why it matters |
|---|---|---|
| 12 (part) | One rehearsal of `demo/DEMO_SCRIPT.md` with a person typing in terminal A and a key in `.env` | Two scripted rehearsals are recorded in `demo/REHEARSAL.md`; the human one and the live model are the same gap |

## Frontend and evaluator gaps

- No live model turn has been made through the UI yet; the gate card, timeline and cost line are
  exercised by tests with a fake API only.
- The agentic suite has run live three times (3/8, 6/8, 6/8) plus two runs of the `client_samples`
  scenario (every Marbly sample prompt and the wizard path): the second passes every deterministic
  check with one judge criterion open (a "why" answer that did not mention the vendor sheet). Each run
  surfaced a different defect; the latest prompt changes have not been re-run.
- The client experience has been driven live end to end with Sonnet 5.5 ($0.1720 for the whole
  flow): model-worded lead-ins, recheck, narrated brief, approval card, correction brief, order wizard
  with the lost response. Two defects found there are fixed (see VERIFICATION.md).
- The Memory page's usage table links each row to a run transcript by timestamp prefix; two calls in
  the same second could link to the wrong file.

## Known limitations in what exists

- Order sizing uses a seven-day average of theoretical use for every day in the window. Weekend
  items get the manager's uplift on weekend days; there is no per-weekday demand model.
- The order cutoff rule is the one note N09 states, "noon the day before", applied to every vendor.
  Vendors without a route assertion use the vendor sheet's days.
- Items without a recipe (M99) are reported as unknown draw; the sizing does not add an allowance
  for them. The policy `recipes.missing_item_rule` only implements `unknown_and_flag`.
- Assertions are rechecked only for invoice `status` and `delivery_date`; a route change in a new
  note would be a new detector finding, not an automatic assertion update.
- `SITE_PROFILE.md` carries an informal annotation on its last line that is in git history; strip
  it before sharing if it should not be there.
- A recorded fact about physical receipt (`invoice.X.physically_received`) outranks the general
  on-hand policy, by design; changing the policy alone does not move INV-099 while the N15 fact stands.
- `ask` history passes the last six exchanges to the model; older ones stay in the ledger but are not sent.
- Onboarding questions are generated from finding classes present now; a brand-new class would
  need a new question builder (one function in `onboarding.py`).
- Time log: session-1 design hours are still an estimate to fill in.
