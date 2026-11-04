# Rehearsal record

Two rehearsals of `demo/DEMO_SCRIPT.md` on 2026-11-03, 21:06–21:11, with the assistant playing the
operator's terminal from scripts. A third rehearsal with a person typing in terminal A is still to
be done by the candidate before the interview.

## Rehearsal 1: the runbook, step by step (default port, `.state/live.sqlite`, `.marble/live`)

| Runbook step | Command | Wall time | Matched the table? |
|---|---|---|---|
| 0–2 | `onboard --defaults` | 0.1 s | yes: 14 beliefs, sources shown |
| 2–5 | `brief` (no key) | 1.3 s | yes; stderr says the model is unavailable and prints the deterministic brief |
| 5–6 | `why invoice.INV-099.delivery_date` | 0.1 s | yes |
| 6–8 | A: `scenario --name delivery-correction`; B: `brief` | 0.7 s | yes: revision 2, "What changed" block first |
| 8–9 | `ask` → `why`, `set preference order.buffer_days {"days": 2}`, `quit`; `memory` | 0.1 s each | yes: superseded row under `human:ask` |
| 9–11 | `order --propose-only` | 1.2 s | **no**: 9 cases of AW-CHK20, not 8, because the buffer had just been set to 2 days. The table now says so and points at the `Applied:` line. |
| 11–15 | `order` (answer `y`); A: `scenario --name supplier-timeout`; A: `approve --draft-id …` | ~6 s incl. polling | yes: `[drafted]` with the exact JSON and the approve command, `[approved]`, `[accepted] The response to our submission was lost … Verified by lookup …` |
| after | `order --resume` | 0.1 s | **no**: printed nothing when nothing was pending. Now prints "No open drafts. Last accepted: D-… as order O-…". |
| live change | `ask --once 'set assertion invoice.INV-099.physically_received {"received": true}'` then `brief --dry` | — | **no**: `brief --dry` failed with "unrecognized arguments: --dry" because the global flag had to precede the subcommand. Fixed: both positions work, with a test. |
| live change, second variant | `set policy inbound.on_hand_rule {"rule": "office_status"}` then `brief` | 0.7 s | yes: INV-099 unchanged while the recorded N15 fact stands; `why inbound.on_hand_rule` shows the superseded default |

Total for the scripted path: well under the 15 minutes; the model would add seconds per call.

## Rehearsal 2: the paths the tests only drive through flags (port 8797, then 8795)

- **Typed onboarding** with Enter (default), a number, the word `unknown`, a custom numeric
  threshold (`7`), and the "set each one" branch with four typed values: every answer was accepted
  and echoed; no re-prompt loops triggered. Onboarding complete with 4 policies, 6 preferences,
  2 assertions.
- **Ctrl-C while polling for approval.** First attempt looked like a failure: the draft was not
  cancelled and a later `--resume` submitted it. Cause: the process had been started as a background
  job of a non-interactive shell, which ignores SIGINT, so the handler never ran; that is a test
  artefact, not the assistant. Delivered from a Python parent with SIGINT restored, Ctrl-C prints
  `[cancelled] D-… was never submitted`, exit 0; approving that draft afterwards and running
  `order --resume` reports "No open drafts." and the supplier has no order for that reference.
- **`reset --yes`** removes only the assistant's own state directory.
- **`demo/demo.sh --no-pause`** (port 8796) ran end to end a second time, exit 0, lost response
  recovered by reference.

## What changed because of the rehearsals

- `cli.py`: `--dry` and `--state-dir` accepted after the subcommand too (test added; 117 tests).
- `order --resume`: a line when nothing is pending, naming the last accepted order.
- `DEMO_SCRIPT.md`: venv activation line; 8 vs 9 cases explained; cancel wording; pointer here.

## Still to do by a person

- Run the 15-minute script once with the operator commands typed by hand in terminal A, with a key
  in `.env`, and note the model's wall time per call and the cost line from `memory`.
