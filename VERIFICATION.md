# Verification record

What the test suite proves, what it does not, and what failed on the way. Numbers are from the
run on 2026-11-03 at 22:05 local time, after two live tuning iterations.

## How to run

```bash
.venv/bin/python -m pytest -q
```

Every test runs against a real `simulator.py` started by `tests/conftest.py` on a free port with a
throwaway state file. Operator actions (`approve`, `scenario`) run through `simulator.py` as a
subprocess, exactly as a person would in the second terminal; the assistant code under test never
receives the operator credential. Model calls are faked in tests; no test needs a network or a key.

Environment: macOS 24.6, Python 3.10.16, `anthropic` 2.4.0, `pytest` 8. Suite time about 8 s.

## Result

154 passed, 0 failed, 0 skipped.

| File | Tests | What it proves |
|---|---|---|
| `test_config.py` | 5 | Environment and `.env` loading; the fixed "now" per dataset revision; only the agent token is configurable; global flags accepted before or after the subcommand |
| `test_api_client.py` | 15 | Pagination to `next_offset: null`, filters, typed errors, SITE-01 pinned in the client and refused by the fixture, draft validation codes, draft → 409 → approve → submit → verify, idempotent resubmission, a lost response recovered by `client_reference`, revision bump under `delivery-correction` |
| `test_records.py` | 6 | Row counts per resource, parsing, citations, `<evidence>` wrapping with escaping, revision dedupe (higher wins, equal-but-different is a conflict), revision-2 records |
| `test_analysis.py` | 11 | Pinned fixture numbers: theoretical use, purchases from captured pack sizes, reconciliation gaps (eggs exact, tortillas −48, chicken −5/−45), invoice checks (INV-009 +$47.50, INV-010 10x), price history, runway, weekend forecast |
| `test_detectors.py` | 10 | Every planted anomaly has a finding; the phantom receipt headlines at revision 1; ranking is deterministic; every finding is cited, SITE-01 scoped and revision stamped; at revision 2 the phantom clears, chicken is −5 lb and N16 is cited |
| `test_ledger.py` | 8 | Who may write what (policies and preferences only from people, observations only from detectors); unchanged vs superseded vs conflicted; a person resolving a conflict; persistence across reopen and across four separate processes; draft lifecycle |
| `test_policies.py` | 8 | Defaults carry sources; ledger → detector params; a preference removes and restores a finding; a policy or a confirmed receipt flips INV-099; recheck across the correction (one cleared, two superseded, ten human beliefs untouched); a human statement contradicted by a correction is flagged, not overwritten |
| `test_tools.py` | 6 | Per-mode allowlists; strict schemas with no `site_id`; free text only as `<evidence>`; analysis tools match the detectors; `record_belief` guard rails |
| `test_llm.py` | 6 | All tool results in one user message, `is_error` on failures, allowlist enforced at execution, round and output-limit guards, a tool crash becomes an error result, missing key reported before any request, cost table |
| `test_order_flow.py` | 9 | Fixture sizing (8 × AW-CHK20, 2 × eggs, 4 × cheddar for Wednesday 2026-10-07, est. $814.40); lines valid for the catalog; preferences and a confirmed receipt change the order; draft → 409 → approve → submit → verified; lost response recovered without a second draft; unknown result persisted, blocks the vendor, settles on restart; cancel never submits; the CLI across four processes |
| `test_onboarding.py` | 9 | Six questions from the current findings; default answers equal the documented defaults under a human source; "unknown" keeps facts at evidence level; answers change the detectors; re-run asks nothing until `--again`; ordering values validated; CLI run then brief |
| `test_ask.py` | 6 | The prompt lists every default subject; deterministic commands without a model; the model records a stated preference through `record_belief` and the conversation persists and is trimmed; a model cannot record outside the tool; a `set` in one process changes the order proposal in the next |
| `test_correction.py` | 4 | The "what changed" block across `delivery-correction` (headline move, cleared finding, new note N16 cited, corrected records, unchanged counts), "no changes" on a repeat, a conflict surfaced as "Needs you", the order warning for inbound scheduled inside the window, the CLI before/after |
| `test_injection.py` | 7 | SUP-V1-01 is flagged and marked not acted on; free text reaches the model only inside escaped `<evidence>`; the brief request quotes it as excerpts; no tool exists that could submit, approve or draft; a model that obeys the document gets error results and changes nothing; documents cannot write policies; the fixture enforces approval and the flow never invents a reference or calls an order accepted without a lookup |
| `test_gate.py` | 4 | A gated tool call is not executed until a person approves; reject gives an error result and no row; edit records the edited value; ungated tools never consult the gate; events stream; the CLI gate prompts and honours no/edit |
| `test_citations.py` | 2 | Every citation the renderer produces is parsed back and resolves through the READ tools; no false positives; unknown refs fail cleanly |
| `test_web.py` | 8 | Every endpoint against a real server thread; evidence wrapper; `site_id` refused; no control/approve routes; tool execution respects the mode; what-if overrides never touch the ledger; the order path through the API with a lost response; the gate over SSE blocks until the browser answers; reset deletes only the state dir |
| `test_agentic.py` | 10 | The scenario pipeline in fake mode: steps run through the real modes (brief, ask with the gate, onboarding wording and answers, operator correction, order explanation, the real order path with a lost response, persona turns), every turn is graded, a rejected gate leaves no row, the budget stops a scenario, the judge prompt carries rubric and transcript, a judge that skips a criterion fails it, the report and coverage render |
| `test_evals.py` | 9 | Each fixture meets its stated expectation (five pass, two must fail on named evaluators); fresh violations are caught |
| `test_scope.py` | 7 | The client refuses other sites before a request leaves; the fixture returns 403 for SITE-02; no tool takes or returns another site; every record and citation is SITE-01; the assistant source never imports `csv`, reads `data/`, or references `.state/`, `/control` or `/approve`; configuration carries only the agent token; every citation names file, row (or stated aggregate), and revision |

## Evaluators

`python evals/run.py` on 2026-11-03 at 21:37: 7/7 fixtures met their expectation (five passing
fixtures built from real tool outputs, two negatives failing exactly on the named evaluators).
No `--live` run yet.

Agentic suite, fake mode (`python evals/agentic.py --fake`, 2026-11-03 21:55): 8/8 scenarios OK with the
fake assistant and a fake judge that passes every criterion (fake mode tests the pipeline, not judgement); the report shows every declared flow covered by at least one scenario.
No live scenario, judge calibration or persona turn has run with a real model.

## Live runs (real model)

| When | Run | Result | Spend | Notes |
|---|---|---|---|---|
| 21:54 | `evals/agentic.py --budget-usd 3` | 0/8, every scenario errored before any turn | $0.00 | Two API limits the fakes could not see: at most 16 union-typed tool parameters per request (we sent 22 nullable ones) and no `additionalProperties` schema in structured output. Fixed in `33c761e`. |
| 21:56 | same, after the schema fix | 3/8 OK; judge calibrated 4/4 | $0.72 (229 s) | Found: citations written as `(file ID, rev1)` so no link matched; briefs of 301 and 345 words; the ledger dropped the lost-response story once a draft was accepted; the judge re-graded numbers it could not see; two rubrics contradicted the prompt. |
| 22:04 | same, after prompt, ledger-note and rubric fixes | 6/8 OK; all deterministic checks pass; judge calibrated 4/4 | $0.60 (174 s) | Remaining: the eggs answer ran twelve sentences against a six-sentence rule (prompt tightened again); one rubric misread the "unknown" onboarding answer (reworded). |
| 22:10 | same, after those two changes | 6/8 OK; judge calibrated 4/4 | $0.57 (163 s) | Both earlier failures cleared. New: the brief opened with "Defaults are not in force; you are onboarded" (the prompt only covered the not-onboarded case; fixed); the eggs answer rounded a tool's 2.1622 days to 2.2 (the tool now reports the brief's rounding); ten sentences against a six-sentence rule (rule moved to the end of the prompt as a hard limit); one judge call over-strict about a need figure (rubric clarified). Not yet re-run. |

Measured cost per model call on iteration two (Opus 5.5 assistant, Sonnet 5.5 judge): brief $0.039 (11.5k in / 1.2k out, one round), ask $0.016 (3.7k in / 0.5k out, 2.2 rounds), order explanation $0.040, onboarding wording $0.023, judge $0.020 per scenario, persona $0.002 per turn. One full iteration of eight scenarios: about $0.60. The machine-wide cost log (`.marble/costs.jsonl`) held 78 calls and $1.68 at 22:13, every model call from the three runs including judge and persona.

## Smoke path

`demo/demo.sh --no-pause` ran end to end on 2026-11-03 at 20:37 (exit 0): onboarding, brief at
revision 1, `why`, delivery correction and brief at revision 2, order proposal, full order under
`supplier-timeout` (draft, operator approval, response lost, verified by reference), resume, memory.

## Failures found during development, and what changed

| When | What failed | Cause | Fix | Guarded by |
|---|---|---|---|---|
| Block 2 | Revision-2 test: chicken gap −45 instead of −5 | On-hand stock was derived from the anomaly list; the corrected INV-099 (`scheduled`) had no anomaly and silently counted | Explicit rule `analysis.not_on_hand_invoices`; later made a manager policy | `test_detectors.py::test_after_delivery_correction_the_phantom_clears` |
| Block 3 | Revision-2 footer showed 16 "changed" observations | The detector source string embedded the revision, so identical values looked new | Revision moved to its own column; unchanged = same value and same source | `test_policies.py::test_recheck_first_run_repeat_run_and_revision_change` |
| Block 5 | Two pinned order numbers wrong (3 vs 4 cases; "nothing to order" vendor) | Author arithmetic, not code | Recomputed by hand and re-pinned | `test_order_flow.py` |
| Block 5 | Eggs warned about a pre-delivery gap | Seven-day average applied to a weekend-only item | Warning limited to daily-service ingredients | `test_order_flow.py::test_proposal_on_the_fixture_with_default_preferences` |
| Block 7 | "unknown" answer on the route question crashed | Evidence belief spliced into the outer tuple instead of nested | Nested | `test_onboarding.py::test_unknown_keeps_facts_at_evidence_level` |
| Block 8 | `set` command rejected valid JSON | `shlex` stripped the quotes | Plain `split(None, 2)` | `test_ask.py::test_dry_commands_work_without_a_model` |
| Block 8 | Fake API saw requests grow after the call | The loop handed the fake its live message list | Loop passes a snapshot per request | `test_llm.py`, `test_ask.py` |
| Block 8 | Runbook claimed the office-status policy alone flips INV-099 | A recorded receipt fact outranks the policy by design | Runbook corrected to the fact | `test_policies.py::test_a_policy_changes_how_inv099_is_treated` |
| Block 10 | "All orders" assertion failed only in the full suite | Shared simulator across test files | Assertion narrowed to the draft's own reference | `test_injection.py` |
| Block 12 | Runbook command `brief --dry` rejected | `--dry` was only a global flag before the subcommand | Accepted in both positions | `test_config.py::test_global_flags_are_accepted_before_or_after_the_subcommand` |
| Block 12 | Runbook promised 8 cases where the demo order shows 9 | The `ask` step a minute earlier sets a two-day buffer | Runbook corrected; the difference is the point | `test_order_flow.py::test_preferences_and_policies_change_the_order` |
| Block 12 | Ctrl-C during polling appeared not to cancel | Background job of a non-interactive shell ignores SIGINT (test artefact) | Re-run with SIGINT delivered properly: cancels, nothing submitted | `test_order_flow.py::test_cancel_never_submits_even_after_approval`; `demo/REHEARSAL.md` |

## Not verified

- **Live use through the UI and CLI by a person.** The model paths have now run live only through
  the agentic suite (two iterations, above); the narrated `brief` and `ask` commands and the web
  chat have not yet been driven by a person. A fourth iteration, after the changes from the third, has not been run.
- **Two-terminal rehearsal by hand.** Two scripted rehearsals are recorded in `demo/REHEARSAL.md`;
  the runbook has not yet been rehearsed with a person typing in terminal A.
- **Scale dataset.** Only the small dataset is exercised; the scale file was never loaded.

## Limits of what the tests can say

- Theoretical use assumes recipe cards were followed; the data has no intermediate counts or
  waste quantities, so a reconciliation gap is a signal, not a measurement.
- The order cutoff rule is the one note N09 states, applied to every vendor.
- Tests pin behaviour on this fixture's numbers; a different dataset would need its own pins.
