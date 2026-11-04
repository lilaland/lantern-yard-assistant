# Onboarding information flow

```mermaid
flowchart LR
  F["Findings at the current revision<br/>(detectors.run_all)"] --> Q["Question plan<br/>≤ 6 questions, one per finding class present<br/>(onboarding.question_plan)"]
  Q --> P{"Person answers<br/>option · custom value · unknown"}
  P -- "option" --> H["Ledger rows, source human:onboarding<br/>policy / preference / assertion"]
  P -- "unknown, keep checking" --> E["Default rule as human:onboarding<br/>+ the fact at evidence level (note:N15, note:N09)"]
  H --> L[("ledger")]
  E --> L
  L --> D["policies.detector_params<br/>on-hand rule · confirmations · thresholds · route"]
  D --> N["Next brief / order proposal<br/>'Applied:' names the belief"]
  L -. "re-run: asks only unanswered questions" .-> Q
```

Options, defaults and what each answer records are fixed in code; the model may only word the
lead-in. A human answer is never rechecked against data; an "unknown" leaves the fact where a later
record can supersede it.
