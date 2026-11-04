# Demo script (15 minutes)

Two ways to run it: the client experience in the browser (below, recommended) or the CLI runbook
further down. Both drive the same code; the operator's approval is typed in terminal A either way.

## Client experience (browser)

Terminal A: `python3 demo/operator_helper.py serve --port 8765 --state .state/live.sqlite --fresh` (runs `simulator.py serve` from a fresh file and answers "Forget everything" with a reset; approve and scenario are still typed here with the same `--state`)
Terminal B: `export MARBLE_STATE_DIR=.marble/live MARBLE_SIM_STATE=.state/live.sqlite MARBLE_OPERATOR_URL=http://127.0.0.1:8766 && python -m assistant web --fresh` then open http://127.0.0.1:8780 (`MARBLE_SIM_STATE` only makes the printed approve command complete and `MARBLE_OPERATOR_URL` is only handed to the browser; the assistant never reads that file or calls the helper)
Start over at any point: gear → "Forget everything and start over" wipes Marbly's memory and, through the helper, the supplier. From the shell: `python3 demo/operator_helper.py reset` (supplier) and `python -m assistant web --fresh` (assistant: a new state directory next to the old one, nothing deleted).

| Min | Do | Say |
|---|---|---|
| 0–1 | Fresh state: gear → "Forget everything and start over" if needed. Reload. Click "Let's go". | Marbly greets with the four-step overview and waits for your go; the setup card pins to the top with step 1 active. The marble squishes while the model words the questions. |
| 1–5 | Answer the six cards. Pick "Nobody has seen the cases" on the first; "unknown, keep checking" on the route to show the difference; a custom number on the price threshold; "Set each one" on ordering with buffer 2. | Each answer echoes exactly what was recorded and what changes. "Unknown" records the default rule under your name but leaves the fact at evidence level. |
| 5–6 | Watch the recheck. | A thinking bubble while every check re-runs with your answers; then "Because of what you told me: …" naming what cleared, moved or appeared. Step 1 ticks off and Marbly introduces Memory with the screen dimmed around the book; a click (the book, or anywhere) brings "Step 2: 2 alerts need a decision today", the critical cards, and the bell dimmed in. |
| 6–7 | Open the bell (step 2 ticks), close it. | Alerts grouped critical / watch / info with "Ask Marbly", "Plan the next Alder order", "Evidence". "Step 3": the three capabilities with sample prompts and the chip row lit; any prompt ticks step 3, then "Step 4" and the order chip pulses (the order prompt itself skips straight to the wizard). |
| 7–9 | Chip "Give me this morning's brief". | Narrated brief with citations that open the evidence drawer; the footer with retained vs rechecked. Under the text, "Orders to cover": one card per vendor with the items, on hand, days left and the suggested cases, a "Draft the … order" button each, and one suggested draft per vendor (an order goes to one vendor). The chip row turns into follow-ups on the brief's items (why INV-099 is not counted, what to tell Juniper about INV-010, the next Alder and Copper orders); after every answer it re-ranks by what the answer touched. |
| 9–10 | Type "use two days of buffer from now on". | The approval card: approve, edit the value, or reject. Then the book shows the new preference with "you said so". |
| 10–11 | A: `scenario --name delivery-correction`. Chip "Give me this morning's brief" again. | Opens with what changed: INV-099 now scheduled, note N16, records corrected, your rules retained. |
| 11–15 | "Start the order" on the setup card (or the pulsing chip). Change a quantity in the table, or type "drop the cheese" and let Marbly revise. "Create the draft". A: `scenario --name supplier-timeout`, then the approve command exactly as shown (it carries `--port` and, with `MARBLE_SIM_STATE` set in terminal B, `--state`). | Step 2 shows the order as a table with the exact payload behind a toggle, where to run the command, and a Copy button; there is no approve button by design. On approval it submits, loses the response, verifies by lookup, and step 3 reads "accepted … verified by lookup … recorded, not shipped". Step 4 ticks, the card disappears, and Marbly lists what matters next per vendor, with the planner dimmed in. |
| 15 | 🌅 (top right) → "New session, next day". | The chat clears: "Session 2 · Tuesday 2026-10-06, the next day. Here is the brief." The brief opens with what changed since the last one (INV-099's delivery date is now today; Alder's cutoff is noon; the accepted order shows as ordered in the planner) and the chips follow it. "New session" without the day change does the same on the same date. |

A second web server for testing needs its own simulator: start it with `MARBLE_API_BASE=http://127.0.0.1:<sim port>` and `MARBLE_SIM_STATE=<that simulator's --state>`, or its drafts land on the default simulator at 8765.

Fallbacks: without a key every step except narration still works (cards, inbox, memory, wizard);
the brief falls back to the deterministic text. Gear → "Show developer pages" for the tool matrix,
the injection lab and the revision diff if asked.

## CLI runbook (two terminals)

Everything below runs against the supplied local simulator. The assistant holds only the agent
token `demo-agent-site-01`. Approval and scenario changes are typed by the operator in terminal A;
the assistant never has that credential.

`demo/demo.sh` plays both terminals in one run (smoke path). The live demo is done by hand so the
interviewer sees who types what.

## Before the interview

```bash
source .venv/bin/activate              # `python -m assistant` below means this interpreter
python -m pytest -q                    # 159 tests against a throwaway simulator
cp .env.example .env                   # add ANTHROPIC_API_KEY; without it, every command still works with --dry
rm -rf .marble/live .state/live.sqlite # fresh state for this run
```

Keep a second state path ready (`.state/live2.sqlite`, `--state-dir .marble/live2`) in case a
rehearsal leaves state behind.

## Terminal A (operator)

```bash
python3 simulator.py serve --mode agent --state .state/live.sqlite
```

Later, when asked: `scenario --name delivery-correction`, `scenario --name supplier-timeout`,
`approve --draft-id D-…`, always with `--state .state/live.sqlite`.

## Terminal B (assistant)

Set `export MARBLE_STATE_DIR=.marble/live` once. Add `--dry` anywhere on the command line to skip
the model; without a key the commands fall back to the same output on their own and say so on stderr.

| Min | Command | What to point at |
|---|---|---|
| 0–2 | `python -m assistant onboard --defaults` | Ten human beliefs recorded under `human:onboarding-defaults`; four assertions tied to N09, N15 and the INV-099 row. Say: tomorrow's interactive `onboard` asks these as six questions; the defaults are what it records when the manager accepts them. |
| 2–5 | `python -m assistant brief` | Headline: INV-099 marked received, delivery date tomorrow, 40 lb of chicken; 45 lb short if trusted, 5 lb if not; about two days of chicken; next step is to confirm with Alder. Point at the citations (file, row, revision), the `Uncertain` list (office status is typed, M99 has no recipe, N06 unweighed waste), and `Applied:` naming the on-hand policy. Then the footer: retained vs rechecked. |
| 5–6 | `python -m assistant why invoice.INV-099.delivery_date` | One record, source `invoice:INV-099@rev1`, recheck on revision change. |
| 6–8 | A: `scenario --name delivery-correction`; B: `python -m assistant brief` | Footer: revision 1 → 2; `phantom_receipt:INV-099` cleared with the reason; two assertions superseded by `invoice:INV-099@rev2`; policies and preferences untouched. Headline is now the INV-010 10x pepper line. `why invoice.INV-099.delivery_date` now shows the chain. |
| 8–9 | `python -m assistant ask` then `why order.buffer_days`, `set preference order.buffer_days {"days": 2}`, `quit`; `python -m assistant memory` | The REPL: a belief changed by the manager, source `human:ask`, superseding the onboarding default; `memory` shows counts by kind and status and the cost so far. In live mode say it in words instead. |
| 9–11 | `python -m assistant order --propose-only` | Wednesday delivery because Monday's noon-the-day-before cutoff passed; covers through Sunday plus the buffer; INV-099 not counted (names the note) and, at revision 2, a warning that it is scheduled inside the window; 8 × AW-CHK20 with the default one-day buffer, 9 after the two-day buffer set in `ask` a minute ago (point at `Applied: order.buffer_days=2 (human:ask)`); 2 × eggs, 4 × cheddar; estimate vs the unflagged limit; warning that chicken runs out before Wednesday. Say: the model may explain this table; it cannot change a number in it. |
| 11–15 | B: `python -m assistant order`, answer `y`; A: `scenario --name supplier-timeout`; A: `approve --draft-id D-…` (copy from B) | B prints the exact draft JSON the supplier holds and the approve command, then polls. After approval: submit, connection lost, lookup by `client_reference` finds the order, `[accepted] … Verified by lookup … Accepted means the supplier recorded it, not that it has shipped.` Then `order --resume` shows nothing pending. |

If time allows: Ctrl-C during polling shows `[cancelled] … never submitted`, and approving that
draft afterwards changes nothing (`order --resume` reports no open drafts). While a draft is open and
unapproved, plain `order` refuses to stack another and points at `--resume`.

Rehearsal notes with timings: `demo/REHEARSAL.md`.

## Talking points, one line each

- Numbers come from code; the model narrates. `--dry` is the same brief without the narration.
- Beliefs have kinds and sources. Policies and preferences are human-only and never rechecked against data. Observations are detector-only and rechecked every run. Assertions are rechecked on revision change; newer evidence against a human statement is flagged, never overwritten.
- Supplier text is evidence inside `<evidence>` tags. SUP-V1-01 tells an AI to bypass approval; the brief reports that it was quoted and not followed. The order flow has no code path that skips approval, and the server enforces it too.
- A lost response never creates a second draft: lookup by reference, one retry of the same draft, else an honest `unknown` that blocks new drafts for that vendor until rechecked.
- Scope: `site_id` is pinned in the client; no tool takes it; SITE-02 is a 403 the client cannot even ask for.

## Live-change exercise (likely interviewer request)

"Maya found the chicken; make the assistant count INV-099 as on hand." One ledger row, no code:

```bash
python -m assistant ask --once 'set assertion invoice.INV-099.physically_received {"received": true}'
python -m assistant brief --dry
python -m assistant order --propose-only --dry
```

The INV-099 finding drops to LOW with "the manager confirmed", the chicken variance becomes −45 lb
HIGH, the order proposal counts the 40 lb as inbound supply and shrinks the chicken line, and
`why invoice.INV-099.physically_received` shows the N15 note superseded by Maya's word.

A second variant shows the layering: `set policy inbound.on_hand_rule {"rule": "office_status"}`
alone changes nothing for INV-099, because the onboarding defaults recorded the N15 fact that the
cases were not seen, and a recorded fact about physical receipt outranks the general rule. `why
inbound.on_hand_rule` shows the superseded default; `why invoice.INV-099.physically_received`
shows the fact that still applies. Supersede the fact and the policy takes effect.

In live mode the same statements can be typed in plain words inside `ask` ("I found the chicken
cases this morning"); the model calls `record_belief` and echoes the row, which is the same path as
`set`.

## Fallbacks

- Model unavailable or slow: every command prints the deterministic output with `--dry`; `brief` without `--dry` falls back by itself and says so on stderr.
- Port in use: `DEMO_PORT=8799 demo/demo.sh` or `--port` on every simulator command plus `MARBLE_API_BASE`.
- State left behind: new `--state` path for the simulator and a new `MARBLE_STATE_DIR`; `python -m assistant reset --yes` deletes only the assistant's own directory.
