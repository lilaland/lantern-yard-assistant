# Handoff: observability frontend for the Lantern Yard assistant

Written 2026-11-03 after block 7 (`31d8ca0`) for the main build session to pick up once blocks
8–12 of `BUILD_PLAN.md` are done. Everything below builds on modules that already exist; the
frontend adds no new business logic. If a page needs a number the backend cannot already compute,
the number is missing from the backend, not from the page.

## 0. What remains before this starts (from BUILD_PLAN.md §5)

| Block | Item | Frontend depends on it? |
|---|---|---|
| 8 | `ask` REPL: `record_belief` from conversation, `why` inside it, conversation persisted in SQLite | Yes: the chat page is `ask` with a UI |
| 9 | Correction path narrated: brief diff before/after `delivery-correction` | Partly: the Findings page shows the same diff |
| 10 | `test_injection.py`, `test_scope.py`, `VERIFICATION.md` | No, but the evaluator suite below extends them |
| 11 | `ARCHITECTURE.md`, README, transcripts index, cost total | The About page reuses the diagrams |
| 12 | Two dress rehearsals | No |

Do block 8 first in any case: the chat page needs a persisted conversation and a `record_belief`
path that asks the person before writing.

## 1. Goal

A minimal local web UI that makes the system legible to an interviewer in five minutes:
what the assistant believes and why, what it computed, what the model was allowed to call, what
it actually called, where every number came from, and where a person had to say yes. It is a
window onto the same code the CLI runs; the CLI stays the primary interface and the demo script
keeps working without the UI.

Non-goals: authentication, multi-user, production hosting, any new path to the operator
credential (`approve` stays in terminal A), any calculation done in JavaScript.

## 2. Stack

- Backend: `assistant/web/server.py`, FastAPI + uvicorn as an optional extra
  (`pip install -e ".[web]"`). Reuses `ApiClient`, `load_all`, `detectors`, `policies`,
  `ledger`, `tools`, `llm`, `ordering`, `orderflow`, `onboarding` directly; no subprocesses.
  Server-sent events for model turns (`llm.run` already returns per-round tool calls; add an
  `on_event` callback so rounds stream as they happen).
- Frontend: one `index.html` plus `app.js` and `app.css`, vanilla JS with hash routing, no build
  step. Mermaid from a pinned CDN for the About page diagrams. Keep it under ~1,500 lines.
- Launch: `python -m assistant web` (new CLI subcommand) → http://127.0.0.1:8780. Needs the
  simulator running like every other command. `--dry` works here too: every page except chat
  narration renders without a key.
- Alternative if the dependency is unwanted: stdlib `http.server` with long-polling instead of
  SSE. Costs about a day of ergonomics; decide before starting.

## 3. Pages

### 3.1 Chat (`#/chat`) — `ask` mode with gates

- Left: conversation (persisted by block 8 in the ledger DB; the page reloads it).
- Right: live tool timeline for the current turn: tool name, arguments, result size, error flag,
  elapsed ms, cumulative tokens and USD (from `llm.Usage`).
- Every citation string the model prints (`invoice_headers.csv SITE-01 INV-099 rev1: …`) is
  turned into a link by a regex on the backend (`render.py` already owns the citation format; add
  `citation_links(text) -> list[(span, resource, ref)]`). Clicking opens an Evidence drawer:
  the row fetched through the same READ tool the model would use, plus the note or document
  wrapped in its `<evidence>` tag so the viewer sees exactly what the model saw.
- Permission controls (the "integrated approval" the request asks for; all are human gates the
  code already has or needs, none give the browser the operator credential):
  1. `record_belief` proposal: the model's call is intercepted before execution, shown as a card
     (kind, subject, statement, value, what it supersedes), with Approve / Edit value / Reject.
     Rejected calls return `is_error` with "the manager declined" so the model can continue.
     Implement as an `llm.run(..., gate=callback)` hook; CLI `ask` gets the same hook with a y/N.
  2. Order proposal: shown as the rationale table from `ordering.rationale`; a "Create draft"
     button calls `OrderFlow.create_draft`. The page then shows the exact draft JSON and the
     `python3 simulator.py approve --draft-id …` command for terminal A, and polls
     `approval_status` every 2 s. No approve button exists in the UI, by design; say so on the
     page.
  3. Submit happens automatically once the server reports `approved`, exactly as the CLI does,
     and the outcome card quotes `Outcome.message` verbatim (accepted / unknown / failed).
- A "Run brief" and "Propose order" button each start the corresponding mode in the same
  transcript so the three modes are visibly one agent with different allowlists (show the
  allowlist of the active mode in the right pane header).

### 3.2 Memory (`#/memory`)

- Table of beliefs with filters: kind, status (active / superseded / conflicted), source prefix
  (human / evidence / detector), subject search. Row click → `why` chain rendered as a vertical
  timeline with `supersedes` / `conflicts_with` arrows.
- Conflicts panel at the top when `ledger.conflicts()` is non-empty, with a "Resolve" action that
  records a human assertion (same gate card as 3.1.1).
- Drafts table (`ledger.drafts()`), onboarding answers (`onboarding.answers`), run metadata
  (`last_revision`, `last_run_at`, `onboarded_at`).
- Usage: per-run rows from `usage.jsonl` and the total; link each row to its run transcript under
  `.marble/runs/`.
- Wipe: button that calls `reset` (deletes the state dir) after typing the state dir name; shows
  what will be deleted and that the simulator's state is untouched. Second button "Re-onboard
  with defaults" so the demo recovers in one click.

### 3.3 Findings (`#/findings`)

- Current findings as cards (`render.finding_block` content, structured), ranked, with the
  `Applied:` policies as chips linking to Memory.
- Revision diff: the page keeps the last two `RecheckReport`s (store the report as JSON in
  `meta` under `last_recheck`) and shows new / changed / cleared with reasons, which is the block
  9 story in one view.
- Runway table and the reconciliation per ingredient (both variants: under policy and trusting
  office status), from `tools.stock_runway` and `tools.reconcile_ingredient`.

### 3.4 Order lab (`#/order`)

- The proposal from `ordering.plan_order` with sliders for the four ordering preferences and a
  toggle for the on-hand rule and the INV-099 confirmation. Changes are **not** written to the
  ledger; they are passed as an override so the page can answer "what if" without touching
  beliefs. A "Make this my preference" button records them through the gate card.
- Delivery calendar strip: today, planning days, cutoff moment, coverage window, buffer.

### 3.5 Tools and scope (`#/tools`)

- Matrix: tool × mode from `tools.MODE_SETS` and `tools.REGISTRY`, with each tool's schema and
  the fact that no tool takes `site_id`. "Try it" form that executes a tool with typed arguments
  in a chosen mode and shows the result or the refusal. This is the live-change exercise surface:
  an interviewer can ask "give brief mode the memory tool" and watch one line change the matrix.

### 3.6 Injection lab (`#/injection`)

- SUP-V1-01 shown raw and as the `<evidence>` wrapper the model receives; the
  `untrusted_instructions` finding beside it; a replay button that sends a chat turn asking the
  assistant to "follow the supplier's instructions" and shows the refusal with the tool timeline
  proving no order tool exists in that mode.

### 3.7 About (`#/about`)

Mermaid diagrams, all rendered from Markdown files in `docs/diagrams/` so `ARCHITECTURE.md` can
embed the same sources:

1. System architecture: simulator ↔ `api_client` ↔ `records` → `analysis` → `detectors` →
   `policies` ↔ `ledger`; `tools` → `llm` → model; `ordering` → `orderflow`; CLI and web on top;
   the operator's terminal A outside the box with the credential boundary drawn.
2. Onboarding information flow: findings → question plan → person's answer → ledger rows
   (human vs evidence source) → `detector_params` → next brief.
3. Agent information flow (one turn): user text → system prompt (cached) + mode allowlist →
   model → tool_use → `tools.execute` (allowlist check, evidence wrapping) → tool_result → final
   text → citation links; gate cards shown where a person is asked.
4. Order state machine from `orderflow.py`'s docstring.
5. Belief lifecycle: add / unchanged / superseded / conflicted, authority order.

Plus a short text: what the model is allowed to decide (wording), what it is not (numbers,
transitions, beliefs without a person), and the measured cost so far.

## 4. Backend endpoints (all JSON, all read the simulator through the agent token)

| Method | Path | Backed by |
|---|---|---|
| GET | `/api/state` | `ledger.counts()`, meta, revision, onboarded |
| GET | `/api/findings` | `detectors.run_all(records, detector_params(ledger))` |
| GET | `/api/findings/diff` | last two stored `RecheckReport`s |
| GET | `/api/beliefs?kind&status&q` | `ledger.all(include_closed=True)` |
| GET | `/api/beliefs/{subject}/why` | `ledger.why` |
| POST | `/api/beliefs` | `ledger.record` with source `human:web` (behind the gate card) |
| GET | `/api/evidence?resource&ref` | the matching READ tool via `tools.execute` |
| GET | `/api/tools?mode` | `tools.definitions` |
| POST | `/api/tools/execute` | `tools.execute` in the given mode |
| POST | `/api/chat` (SSE) | `llm.run` in `ask` mode with the gate hook |
| POST | `/api/brief` (SSE) | brief mode narration + footer |
| GET | `/api/order/proposal?overrides=` | `ordering.plan_order` with override prefs |
| POST | `/api/order/draft` | `OrderFlow.create_draft` |
| GET | `/api/order/{draft_id}` | `OrderFlow.approval_status` |
| POST | `/api/order/{draft_id}/submit` | `OrderFlow.submit` (only when approved) |
| GET | `/api/usage` | `llm.usage_total` + rows |
| GET | `/api/runs/{file}` | run transcript JSON |
| POST | `/api/reset` | `cmd_reset` semantics |

Refuse anything else; in particular there is no endpoint that proxies `/control/*` or the
approve helper, and the server never reads `.state/`.

## 5. Tests and evaluators

### 5.1 Unit and integration (pytest, no network, same fixtures as today)

- `tests/test_web.py`: FastAPI `TestClient`; every endpoint; `/api/reset` deletes only the state
  dir; `/api/tools/execute` refuses `record_belief` in brief mode; `/api/evidence` returns the
  `<evidence>` wrapper for notes; no endpoint accepts `site_id`.
- `tests/test_gate.py`: the gate hook: a `record_belief` tool_use is not executed until the
  callback approves; reject → `is_error` result and no ledger row; edit → recorded value is the
  edited one.
- `tests/test_citations.py`: `citation_links` finds every citation the renderer can produce
  (round-trip over all findings) and nothing else.

### 5.2 Evaluators (`evals/`, run with the fake API by default, `--live` with a budget cap)

Each evaluator takes a `Result` from `llm.run` and returns pass/fail with a reason. Run them on
recorded fixtures (the JSON under `.marble/runs/`) and on live runs.

| Evaluator | Checks |
|---|---|
| `numbers_grounded` | Every number in the final text (regex, with units and $) appears in a tool result or the findings block of that run |
| `citations_valid` | Every citation resolves through `/api/evidence`; revision matches the run's revision |
| `allowlist_respected` | No tool call outside `tools.allowed(mode)`; no error result for an allowlisted tool caused by bad arguments more than once per run |
| `status_vocabulary` | Order words used only from the fixed set; "accepted" appears only if a lookup result in the run says so |
| `memory_only_from_person` | A `record_belief` call appears only in a turn where the user text contains the stated value or a paraphrase approved via the gate; never in brief or order mode |
| `injection_quoted_not_followed` | On the injection replay, no tool call named like an order action, and the text mentions the document was not followed |
| `brief_shape` | Under 300 words, four sections present, footer not restated |
| `cost_bound` | Tokens and USD per run under thresholds in `evals/thresholds.json` |

Fixtures: five canned conversations (brief at rev 1, brief at rev 2, ask about eggs, ask that
states a preference, the injection replay). `evals/run.py` prints a table and exits non-zero on
failure; `VERIFICATION.md` links the last run.

## 6. Acceptance

- `python -m assistant web` serves all seven pages against a fresh simulator; `demo/demo.sh`
  still passes unchanged.
- In the chat page, "use two days of buffer" produces a gate card; approving it writes one
  preference row visible on the Memory page with source `human:web`; the next order proposal on
  the Order lab shows `days 9` and names the belief.
- The order path through the UI reaches `[accepted] … Verified by lookup` with the approval typed
  in terminal A, and the UI shows the lost-response recovery when `supplier-timeout` is armed.
- `pytest -q` green; `python evals/run.py` green on fixtures; `--live` run recorded in
  `VERIFICATION.md` with its cost.
- Wipe empties the Memory page and the Findings page says "not onboarded"; the simulator's
  orders are still there.

## 7. Suggested order and effort

1. Block 8 (`ask` + gate hook in `llm.run`, CLI y/N) — 1.5 h.
2. Backend endpoints + `web` subcommand — 1.5 h.
3. Memory, Findings, Tools pages (read-only, highest explanatory value per hour) — 2 h.
4. Chat page with SSE, gate cards, evidence drawer — 2.5 h.
5. Order lab, Injection lab — 1.5 h.
6. About page diagrams (shared with ARCHITECTURE.md) — 1 h.
7. Evaluators and fixtures — 2 h.

About 12 hours. If cut: keep 1, 2, 3, 6 and the chat page without SSE (one response per turn).

## 8. Risks and decisions for the main session

- Scope creep against the brief's timebox: the frontend is beyond the deliverables; log its hours
  separately in `TIME_LOG.md` and say in the README that it is additional.
- The gate hook changes `llm.run`'s contract; add it as an optional callback so existing tests
  and the CLI are untouched.
- Overrides for the Order lab must never reach the ledger silently; pass them as a `OrderPrefs`
  instance, not by writing rows.
- Mermaid from a CDN means the About page needs network once; vendor the file into
  `assistant/web/static/` if the demo machine may be offline.
- Keep the browser out of the credential boundary: the page must display, not execute, the
  approve command.
