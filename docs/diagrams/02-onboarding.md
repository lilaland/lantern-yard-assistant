# Onboarding flow (Marbly)

```mermaid
flowchart TD
  S0["Fresh state: no onboarded_at in meta"] --> OV["Overview card: the four steps<br/>facts · alerts · memory · first order<br/>'Let's go' / 'Later'"]
  OV --> F["Findings at the current revision<br/>(detectors.run_all)"]
  F --> Q["Question plan: ≤ 6, one per finding class present<br/>options, defaults and effects fixed in code;<br/>the model only words the lead-in (or the fallback text)"]
  Q --> P{"Person answers<br/>option · custom value · unknown"}
  P -- "option / value" --> H["Ledger rows, source human:onboarding<br/>policy · preference · assertion"]
  P -- "unknown, keep checking" --> E["Default rule as human:onboarding<br/>+ the fact at evidence level (note:N15, note:N09)"]
  H --> L[("state.sqlite")]
  E --> L
  H -. "after the first answer" .-> MI["Marbly introduces Memory<br/>screen dimmed around the book"]
  P -- "next question" --> Q
  P -- "last answered" --> C["POST /api/onboarding/complete<br/>onboarded_at stamped · recheck with the answers applied<br/>effect: alerts cleared / raised / moved"]
  C --> AL["Step 2: critical alerts in the chat<br/>bell dimmed in · inbox marks the step"]
  AL --> ME["Step 3: Memory (rules · preferences · facts with sources)<br/>book marks the step · three capabilities with sample prompts"]
  ME --> O1["Step 4: first order in the wizard<br/>proposal → draft → operator approves in terminal A → submit → verify"]
  O1 -- "accepted draft in the ledger" --> DONE["Setup card gone · what matters next per vendor · planner"]
  L --> D["policies.detector_params / order_prefs<br/>on-hand rule · confirmations · thresholds · route · buffer · SKU"]
  D --> N["Every brief, answer and proposal from now on<br/>'Applied:' names the belief"]
  DONE -. "gear: 'Ask me the questions again'<br/>meta only; beliefs stay, a new answer supersedes" .-> Q
  DONE -. "gear: 'Forget everything and start over'<br/>deletes .marble/DIR" .-> S0
```

The four steps are pinned to the top until each is true in the ledger: facts when `onboarded_at` is
stamped, alerts and memory when their panel was opened, the order when a draft is `accepted`. A human
answer is never rechecked against data; "unknown" leaves the fact where a later record can supersede
it. In dry mode the same flow runs with the fallback lead-ins and no model.
