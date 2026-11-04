# Status and unfinished work

Updated after block 12 (2026-11-03 evening). All planned blocks are done; what remains is listed below. This is the honest state of the submission; the
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
| Named safety tests: injection (SUP-V1-01 flagged, evidence-only text, no tool can submit, a disobedient model changes nothing, documents cannot write policies, server-enforced approval, no invented reference) and scope (client and fixture refuse SITE-02, no tool takes a site, no CSV or fixture-state access, agent token only) | `tests/test_injection.py`, `tests/test_scope.py` | `VERIFICATION.md` is the written record (117 tests) |

## Not yet verified

- **A live model call.** No `ANTHROPIC_API_KEY` was available while building. The loop is tested
  against a fake API, and the installed SDK accepts every field the loop sends, but no narrated
  brief, order explanation or model-worded onboarding question has been produced yet. Measured
  cost is therefore zero so far.

Submission documents: `ARCHITECTURE.md` (one Mermaid diagram, decisions, the retained-versus-rechecked
table, limits; about two pages), the README's "Submission" section (setup, demo commands, credentials,
model and measured cost, actual time, unfinished work), `VERIFICATION.md`, `transcripts/`.

## Unfinished (planned for the second session)

| Block | Item | Why it matters |
|---|---|---|
| 12 (part) | One rehearsal of `demo/DEMO_SCRIPT.md` with a person typing in terminal A and a key in `.env` | Two scripted rehearsals are recorded in `demo/REHEARSAL.md`; the human one and the live model are the same gap |

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
