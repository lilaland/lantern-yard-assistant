# Time log

The brief asks for actual time spent. This is the honest record, including AI-assisted work.
Adjust the estimates marked `~` to what actually happened.

| Date | Activity | Start | End | Hours | Notes |
|---|---|---|---|---|---|
| 2026-11-02 | Read packet, design discussion, write BUILD_PLAN.md | ~17:00 | ~22:00 | ~? | session 1; fill in real hours |
| 2026-11-03 | Block 0: git init, venv, package skeleton, config, CLI, logs | 00:10 | 00:25 | 0.25 | session 1 continued |
| 2026-11-03 | Block 1: API client, typed records, simulator fixture, 21 tests | ~09:18 | 09:35 | ~0.3 | session 1 continued next morning |
| 2026-11-03 | Block 2: analysis, detectors, renderer, dry brief, 21 tests | ~09:36 | 09:47 | ~0.2 | session 1 continued |
| 2026-11-03 | Block 3: belief ledger, policies, onboard --defaults, memory/why, recheck footer, 16 tests | 16:15 | 16:28 | ~0.2 | session 1 continued, afternoon |
| 2026-11-03 | Block 4: tools + allowlists, model loop, brief prompt, narrated brief, 12 tests | 16:59 | 17:08 | ~0.15 | no API key present, so the live narrated brief is not yet exercised; fallback path verified |
| 2026-11-03 | Block 5: order sizing + rationale, state machine, order CLI, 9 tests incl. lost response and restart | 17:35 | 17:42 | ~0.1 | end-to-end draft -> approve -> submit -> verify runs in the test suite against the real simulator |
| 2026-11-03 | Block 6: demo/demo.sh smoke path, DEMO_SCRIPT.md, STATUS.md, transcript export + renderer, REJECTED.md | 20:35 | 20:42 | ~0.1 | demo.sh ran end to end (exit 0) with the lost-response path |
| 2026-11-03 | Block 7: interactive onboarding (six questions, unknown answers, idempotent re-run, --answers), 9 tests | 20:43 | 20:48 | ~0.1 | model wording of questions not exercised live (no key) |
| 2026-11-03 | Block 8: ask REPL (deterministic commands + model path, persisted exchanges), 6 tests | 20:49 | 20:54 | ~0.1 | live model conversation not exercised (no key) |
| 2026-11-03 | Block 9: "what changed" block (headline, cleared, new notes, corrected records, conflicts), order warning for inbound inside the window, 4 tests | 20:55 | 20:57 | ~0.05 | |
| 2026-11-03 | Block 10: test_injection.py (7), test_scope.py (7), VERIFICATION.md | 20:59 | 21:03 | ~0.07 | 116 tests |
| 2026-11-03 | Block 11: ARCHITECTURE.md (diagram, decisions, retained vs rechecked, limits), README submission section | 21:02 | 21:05 | ~0.05 | |
| 2026-11-03 | Block 12: two scripted rehearsals, three fixes (--dry position, resume message, runbook), REHEARSAL.md | 21:06 | 21:12 | ~0.1 | human rehearsal with a key still to do |

| 2026-11-03 | Frontend phase 1 (additional, beyond the brief): gate + event hooks, stdlib web server with all endpoints, seven pages, machine-wide cost log, evaluator suite with fixtures, 20 tests | 21:15 | 21:45 | ~0.5 | logged separately from the brief's deliverables; see HANDOFF_FRONTEND.md |

| 2026-11-03 | Agentic suite (additional): judge with calibration, scenario runner with persona and budget, eight scenarios, fake mode, 10 tests | 21:46 | 21:51 | ~0.1 | not yet run live |

| 2026-11-03 | Live tuning (additional): four live suite runs, two API-limit fixes, prompt/ledger/tool/rubric fixes, measured costs | 21:52 | 22:15 | ~0.4 | $1.68 of model spend, all in .marble/costs.jsonl |

| 2026-11-03 | Client experience (additional): Marbly page with onboarding cards, recheck, alerts inbox, client memory, order wizard, settings; dev chat mode switch; alerts page relayout; 2 endpoint tests | 22:16 | 22:25 | ~0.15 | clicked through in dry mode; no live key run yet |

| 2026-11-03 | Live client pass (additional): full Marbly flow in the browser with the model; two fixes (recheck kind, null bubble) | 22:27 | 22:32 | ~0.1 | $0.1720 model spend |

| 2026-11-03 | Client setup flow (additional): overview + pinned four-step tour with spotlights, marble avatar + theme, null fixes, tour endpoint, 2 tests + 1 scenario, browser run | 22:50 | 22:56 | ~0.3 | dry-mode browser run; live sample flows via the agentic scenario not yet run |

| 2026-11-03 | Live tuning of the client sample flows (additional): two live scenario runs, grader history fix, word budgets, model label | 22:56 | 23:03 | ~0.1 | $0.58 model spend |

Running total: ~1.85 h of build time (blocks 0–12, wall clock) + ~1.65 h frontend/evals/agentic suite/live tuning/client experience plus session 1 design time (fill in). Blocks 1–6 were built by the assistant in short bursts between the candidate's review pauses; the gaps between blocks are not counted.
