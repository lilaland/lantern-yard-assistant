# Changed or rejected AI suggestions

The brief asks for one suggestion that was changed or rejected, and how the result was checked.
Candidates are recorded as they happen; the clearest one is used in the interview.

## 1. Model-driven order tools → code-driven state machine (changed; session 1, block 5)

**Suggestion.** BUILD_PLAN.md §2, written by the assistant on 2026-11-02, gave the model an
`ORDER` tool set (`propose_order`, `create_draft`, `get_draft_status`, `submit_order`,
`find_order_by_reference`) and described order mode as "the model narrates and asks, never
transitions on its own", with the state machine enforced around those tools.

**What changed.** In block 5 the order flow was built with no model tools at all. Sizing, draft
creation, polling for approval, submission and verification run in `assistant/orderflow.py` under
the operator's keyboard; the model may explain the proposal it is shown and gets only the READ and
ANALYSIS tool sets in order mode (`tools.MODE_SETS["order"]`). Reason: a model that can call
`submit_order` is a model that can be talked into calling it (SUP-V1-01 tries exactly that), and
the brief's evaluation is about the approval gate and honest failure handling, not about the model
driving the flow. Removing the tools removes the question.

**How it was checked.** `tests/test_tools.py::test_allowlists_by_mode` asserts the model's tool set
in order mode equals the brief-mode set and that no tool name starts with `create_draft`, `submit`
or `propose`. `tests/test_order_flow.py` drives the state machine against the real simulator,
including a 409 before approval, a lost response recovered by reference, an unknown result that
blocks new drafts until a restart settles it, and a cancel after approval that never submits.
`demo/demo.sh` runs the whole path.

## 2. Three helper agents with beliefs in their prompts → one agent, three modes (rejected; session 1, design)

**Suggestion.** The candidate proposed an onboarding phase that writes a "ledger of beliefs" into
the system prompts of three helper agents (brief, concierge, order copilot). The assistant argued
against it: beliefs baked into prompts freeze facts that must be rechecked, and three agents give
three drifting views of the same data and three files to touch in a live-change exercise.

**Direction taken.** One package, one agent core, modes that differ by prompt and tool allowlist;
beliefs live in SQLite with kinds (policy, preference, assertion, observation) and recheck rules,
and are injected per run.

**How it was checked.** The live-change exercise in `demo/DEMO_SCRIPT.md`: switching the on-hand
policy is one ledger row, touches no code, and changes the brief headline, the chicken variance and
the order proposal together. `tests/test_policies.py::test_a_policy_changes_how_inv099_is_treated`
and `tests/test_order_flow.py::test_preferences_and_policies_change_the_order` pin that behaviour.

## Candidates still to confirm

- Block 2: the assistant first decided what counts as stock by consulting the anomaly list; the
  revision-2 test failed because the corrected INV-099 (status `scheduled`) silently counted as on
  hand. The rule became explicit in `analysis.not_on_hand_invoices` and then a manager policy.
  Checked by `tests/test_detectors.py::test_after_delivery_correction_the_phantom_clears`.
- Block 5: two of the assistant's pinned test numbers were wrong (chicken cases after confirming
  INV-099 with a two-day buffer; which vendor has nothing to order). The code was right; the
  expectations were recomputed by hand before pinning. Recorded here because the numbers in the
  tests are the submission's verification record.
