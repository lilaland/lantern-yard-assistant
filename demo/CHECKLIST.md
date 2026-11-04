# Demo checklist

The night before, and the ten minutes before the call. Everything here is in the repo; nothing needs
the network except the model.

## The night before

- [ ] `.venv/bin/python -m pytest -q` passes (170 tests, about 18 s).
- [ ] `.env` holds `ANTHROPIC_API_KEY` and `MARBLE_MODEL=claude-sonnet-5-5`; never print it, never commit it (it is gitignored).
- [ ] Stop stray servers from earlier rehearsals: `lsof -nP -iTCP -sTCP:LISTEN | grep python` and kill anything on ports you did not start today. The demo uses 8765 (simulator), 8766 (operator helper) and 8780 (web).
- [ ] Start fresh and run the whole browser runbook once, typing the operator commands yourself:

  Terminal A (operator):
  ```bash
  python3 demo/operator_helper.py serve --port 8765 --state .state/live.sqlite --fresh
  ```
  Terminal B (assistant):
  ```bash
  export MARBLE_STATE_DIR=.marble/live MARBLE_SIM_STATE=.state/live.sqlite MARBLE_OPERATOR_URL=http://127.0.0.1:8766
  .venv/bin/python -m assistant web --port 8780 --fresh
  ```
  Then http://127.0.0.1:8780, and the table at the top of `DEMO_SCRIPT.md`. Operator commands go in a
  second tab of terminal A, always with `--state .state/live.sqlite`.
- [ ] Note the cost line under the brief and in Settings; a full run is about $0.20 on Sonnet.
- [ ] `TIME_LOG.md`: add the 2026-11-02 design-session hours (the "fill in" in the running total).
- [ ] Decide how the reviewers get the repository (it is private): add them as collaborators or make it public. `PRIVATE_REVIEW.md` is gitignored and stays local either way.

## Ten minutes before

- [ ] Both terminals up as above (`--fresh` on both gives a clean supplier and a clean memory); the page open on the four-step overview.
- [ ] Settings → developer pages hidden (the gear toggles them); the Alerts, Memory and About pages render if you open them.
- [ ] A second tab of terminal A ready with `python3 simulator.py scenario --name ` and `approve --draft-id ` typed up to the argument.
- [ ] Browser zoom so the chip row and the composer are visible under the chat (about 1000 px wide is the tested layout).

## If something fails

- No key or no network: every step still runs; the brief shows the deterministic text and "no model" where narration would be. Say so and carry on.
- The approval does not arrive: the command must carry the same `--state` as the running simulator; the wizard prints it with a Copy button.
- A lost response: that is the planned `supplier-timeout` moment; step 3 reads "verified by lookup".
- A second draft is refused as "already cover this window": the first order is in; use "Start a new proposal" for another vendor, or the planner.
- The page looks stale after a restart: hard reload (the JavaScript is cached).
- To start over at any point: gear → "Forget everything and start over" (resets the supplier too through the helper), or `python3 demo/operator_helper.py reset` plus `web --fresh`.

## After the demo

- [ ] Day two: 🌅 → "New session, next day" shows the brief with what changed; the chips follow it.
- [ ] If asked for a change on the spot: a rule is one row in Memory or `set` in `ask`; a new check is one function in `detectors.py` with a pinned test; a tool's reach is one tuple in `tools.MODE_SETS`.
