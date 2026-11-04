# Demo script (15 minutes, two terminals)

Everything below runs against the supplied local simulator. The assistant holds only the agent
token `demo-agent-site-01`. Approval and scenario changes are typed by the operator in terminal A;
the assistant never has that credential.

`demo/demo.sh` plays both terminals in one run (smoke path). The live demo is done by hand so the
interviewer sees who types what.

## Before the interview

```bash
.venv/bin/python -m pytest -q          # 83 tests against a throwaway simulator
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

Set `export MARBLE_STATE_DIR=.marble/live` once. Add `--dry` to any command to skip the model.

| Min | Command | What to point at |
|---|---|---|
| 0–2 | `python -m assistant onboard --defaults` | Ten human beliefs recorded under `human:onboarding-defaults`; four assertions tied to N09, N15 and the INV-099 row. Say: tomorrow's interactive `onboard` asks these as six questions; the defaults are what it records when the manager accepts them. |
| 2–5 | `python -m assistant brief` | Headline: INV-099 marked received, delivery date tomorrow, 40 lb of chicken; 45 lb short if trusted, 5 lb if not; about two days of chicken; next step is to confirm with Alder. Point at the citations (file, row, revision), the `Uncertain` list (office status is typed, M99 has no recipe, N06 unweighed waste), and `Applied:` naming the on-hand policy. Then the footer: retained vs rechecked. |
| 5–6 | `python -m assistant why invoice.INV-099.delivery_date` | One record, source `invoice:INV-099@rev1`, recheck on revision change. |
| 6–8 | A: `scenario --name delivery-correction`; B: `python -m assistant brief` | Footer: revision 1 → 2; `phantom_receipt:INV-099` cleared with the reason; two assertions superseded by `invoice:INV-099@rev2`; policies and preferences untouched. Headline is now the INV-010 10x pepper line. `why invoice.INV-099.delivery_date` now shows the chain. |
| 8–9 | `python -m assistant ask` then `why order.buffer_days`, `set preference order.buffer_days {"days": 2}`, `quit`; `python -m assistant memory` | The REPL: a belief changed by the manager, source `human:ask`, superseding the onboarding default; `memory` shows counts by kind and status and the cost so far. In live mode say it in words instead. |
| 9–11 | `python -m assistant order --propose-only` | Wednesday delivery because Monday's noon-the-day-before cutoff passed; covers through Sunday plus one buffer day; INV-099 not counted (names the note); 8 × AW-CHK20, 2 × eggs, 4 × cheddar; estimate vs the unflagged limit; warning that chicken runs out before Wednesday. Say: the model may explain this table; it cannot change a number in it. |
| 11–15 | B: `python -m assistant order`, answer `y`; A: `scenario --name supplier-timeout`; A: `approve --draft-id D-…` (copy from B) | B prints the exact draft JSON the supplier holds and the approve command, then polls. After approval: submit, connection lost, lookup by `client_reference` finds the order, `[accepted] … Verified by lookup … Accepted means the supplier recorded it, not that it has shipped.` Then `order --resume` shows nothing pending. |

If time allows: Ctrl-C during polling shows `[cancelled] … never submitted`; `order` again refuses
to stack a draft until the open one is settled.

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
