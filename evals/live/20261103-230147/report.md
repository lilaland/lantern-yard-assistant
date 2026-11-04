# Agentic eval report — 2026-11-03 23:02

Assistant model: claude-sonnet-5-5 · judge: claude-sonnet-5-5 · live · spent $0.1654 of $0.75 · 49 s

Judge calibration: trusted: bad_brief_invented ok, bad_memory_unsaid ok, good_brief ok, good_injection ok

| scenario | turns | deterministic | judge | cost | result |
|---|---|---|---|---|---|
| client_samples | 7 | pass | FAIL: why_wednesday | $0.1432 | FAIL |

## Judge evidence

### client_samples
- PASS `brief_cited`: More than three citations in the right form, and the headline closes with one next step. — evidence: Brief cites e.g. "(invoice_headers.csv SITE-01 INV-099 rev1)", "(notes.csv SITE-01 N15 rev1)", "(inventory_counts.csv SITE-01 COUNT-CLOSE-CHICKEN rev1)"; headline section ends with "Next step: Confirm with Alder Wholesale whether INV-099 ha
- PASS `chicken_answer`: The answer is about 85 words. It gives days on hand and the delivery days from the tool result. — evidence: "about 2.0 days of stock" and "Alder Wholesale delivers Monday and Wednesday under the days planning uses." Tool result had planning_delivery_days ["Monday","Wednesday"].
- FAIL `why_wednesday`: The N09 citation is present, but the claim that the vendor sheet still lists Monday/Thursday is missing. — evidence: Cites note N09: "Planning uses Monday and Wednesday because of note N09" but says "I haven't verified it against the vendor sheet" and never says the vendor sheet lists Monday/Thursday.
- PASS `buffer_recorded`: Recorded once and repeated back. — evidence: record_belief called once with subject order.buffer_days value {"days": 2}; reply: "Recorded: preference, order.buffer_days, {"days": 2}."
- PASS `explanation_names_buffer`: Names the 2 buffer days and keeps the proposal's quantities. It doesn't explicitly attribute them to the recorded preference, but the reply stays consistent with it. — evidence: "It covers 7 days through Sunday plus 2 buffer days: 9 cases of AW-CHK20..." Quantities match the proposal (9, 2, 4).
- PASS `outcome_vocabulary`: Covers accepted, supplier recorded, response lost, verified by lookup. Shipped appears only in a negation. — evidence: "Draft D-f8e8e031b697 is accepted. The supplier's lookup returned order O-6e15e140f7c0 with status accepted." "Our submission response was lost, and the lookup recovered the outcome. Accepted means the supplier recorded the order. It doesn'
- note: Five of six criteria pass. why_wednesday fails because it doesn't mention the vendor sheet listing Monday/Thursday.

## Coverage (flow × scenario)

| flow | scenarios |
|---|---|
| approval | client_samples |
| ask | client_samples |
| brief | client_samples |
| citations | client_samples |
| conflict | **none** |
| correction | **none** |
| gate | client_samples |
| grounding | **none** |
| injection | **none** |
| lost_response | client_samples |
| memory | client_samples |
| onboarding | **none** |
| order | client_samples |
| persona | **none** |
| policy_applied | **none** |
| recheck | **none** |
| safety | **none** |
| status_vocabulary | **none** |
| tools | client_samples |
| why | **none** |

Uncovered flows: conflict, correction, grounding, injection, onboarding, persona, policy_applied, recheck, safety, status_vocabulary, why
