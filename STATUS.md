# Status and unfinished work

Updated after block 6 (2026-11-03 evening). This is the honest state of the submission; the
README's "unfinished work" section is written from it.

## Working and tested

| Area | Where | Evidence |
|---|---|---|
| Scoped API client, pagination, revision capture, typed errors | `assistant/api_client.py`, `records.py` | `tests/test_api_client.py`, `test_records.py` |
| Reconciliation, invoice checks, price history, runway, forecast | `assistant/analysis.py` | `tests/test_analysis.py` (pinned fixture numbers) |
| Ten detectors, deterministic ranking, citations, revision stamps | `assistant/detectors.py` | `tests/test_detectors.py` incl. revision-2 behaviour |
| Belief ledger with kinds, sources, supersede/conflict rules, drafts | `assistant/ledger.py` | `tests/test_ledger.py` incl. a four-process restart test |
| Defaults, ledger → detector params, per-run recheck | `assistant/policies.py` | `tests/test_policies.py` incl. the human-vs-correction conflict |
| Model tools with per-mode allowlists; evidence wrapping | `assistant/tools.py` | `tests/test_tools.py` |
| Tool loop, usage/cost log, keyless fallback | `assistant/llm.py` | `tests/test_llm.py` (fake API) |
| Order sizing with rationale; state machine; lost-response recovery | `assistant/ordering.py`, `orderflow.py`, `modes/order.py` | `tests/test_order_flow.py` against the real simulator |
| CLI: `onboard --defaults`, `brief`, `order`, `why`, `memory`, `reset` | `assistant/cli.py` | subprocess tests above; `demo/demo.sh` |

## Not yet verified

- **A live model call.** No `ANTHROPIC_API_KEY` was available while building. The loop is tested
  against a fake API, and the installed SDK accepts every field the loop sends, but no narrated
  brief has been produced yet. Measured cost is therefore zero so far.

## Unfinished (planned for the second session)

| Block | Item | Why it matters |
|---|---|---|
| 7 | Interactive `onboard` (six questions from findings, `unknown — keep checking` answers, idempotent re-run) | The brief's "establish a baseline with the human" idea; today only `--defaults` exists |
| 8 | `ask` REPL with `record_belief` from conversation; `why` inside it | Follow-up Q&A and "remember a preference" through natural language rather than a Python one-liner |
| 9 | Brief diff before/after `delivery-correction`; sizing change narrated | The footer already shows cleared/superseded rows; the narrated comparison is not written |
| 10 | `test_injection.py`, `test_scope.py`, `VERIFICATION.md` | Injection and scope are covered indirectly (detector test, tool allowlist test, client site pinning) but not as named tests with a written record |
| 11 | `ARCHITECTURE.md` (≤ 2 pages, diagram), README submission section, transcripts export and index, cost total | Required deliverables |
| 12 | Two dress rehearsals of `demo/DEMO_SCRIPT.md` | |

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
- Time log: session-1 design hours are still an estimate to fill in.
