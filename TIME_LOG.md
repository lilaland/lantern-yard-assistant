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

Running total: ~1.85 h of build time (blocks 0–12, wall clock) plus session 1 design time (fill in). Blocks 1–6 were built by the assistant in short bursts between the candidate's review pauses; the gaps between blocks are not counted.
