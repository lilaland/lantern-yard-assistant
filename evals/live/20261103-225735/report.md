# Agentic eval report — 2026-11-03 22:58

Assistant model: default (claude-opus-5-5) · judge: claude-sonnet-5-5 · live · spent $0.4069 of $0.75 · 57 s

Judge calibration: trusted: bad_brief_invented ok, bad_memory_unsaid ok, good_brief ok, good_injection ok

| scenario | turns | deterministic | judge | cost | result |
|---|---|---|---|---|---|
| client_samples | 7 | FAIL: chip: why Wednesday/numbers_grounded; wizard: explain/cost_bound | FAIL: chicken_answer | $0.3834 | FAIL |

## Judge evidence

### client_samples
- PASS `brief_cited`: Three or more citations in the required form, and the headline ends with one next step. — evidence: Brief cites "(invoice_headers.csv SITE-01 INV-099 rev1)", "(notes.csv SITE-01 N15 rev1)", "(inventory_counts.csv SITE-01 COUNT-CLOSE-CHICKEN rev1)"; headline ends "Next step: confirm with Alder Wholesale whether INV-099 has shipped before p
- FAIL `chicken_answer`: Days and delivery days are present and grounded in the tool result, but the answer exceeds six sentences. — evidence: "About 2.0 days of chicken: 47.9 lb counted at close..." and "Alder delivers Monday and Wednesday per the recorded route change"; the answer runs about nine sentences.
- PASS `why_wednesday`: Cites N09 and says the vendor sheet still lists Monday/Thursday. The deterministic evaluator flagged ungrounded '28' (the Sept 28 date), but the rubric criterion itself is met. — evidence: "Note N09 says Alder confirmed the route changes... (notes.csv SITE-01 N09 rev1)... The vendor sheet still says Monday, Thursday, last reviewed 2026-09-14"
- PASS `buffer_recorded`: Recorded once and repeated back. — evidence: record_belief subject order.buffer_days value {"days": 2}; "Recorded: preference, order.buffer_days, {"days": 2}."
- PASS `explanation_names_buffer`: Names the 2 buffer days via the recorded preference and proposes no different quantities. It does not say "because of the recorded preference" in so many words, but it ties the buffer to the preference key. — evidence: "It covers 7 days plus 2 buffer days (`order.buffer_days=2`)." The quantities restated match the proposal (9, 4, 2 cases).
- PASS `outcome_vocabulary`: States accepted, recorded, response lost, and verified by lookup. It mentions "shipped" only to deny it, and does not claim shipped or delivered. — evidence: "Draft D-0d1edd88d87f is accepted as supplier order O-1a24d27ade6a. Our submission response was lost, but a lookup with the supplier confirmed it recorded the order... Accepted means the supplier recorded it, not that it has shipped."
- note: Five of six pass; the chicken answer fails the six-sentence limit. The explanation turn also failed the cost bound (output_tokens 1430 > 1200), which is not a rubric item.

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
