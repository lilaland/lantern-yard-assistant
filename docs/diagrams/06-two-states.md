# Two databases, and how to start over

```mermaid
flowchart LR
  subgraph SIMS["Simulator state: .state/NAME.sqlite (operator's)"]
    S1["drafts: id · body · approved"]
    S2["orders: id · reference (draft id) · body"]
    S3["settings: operator token · scenario · dataset_revision"]
  end
  subgraph AST["Assistant state: .marble/DIR/ (assistant's)"]
    A1["state.sqlite beliefs: policy · preference · assertion · observation"]
    A2["state.sqlite drafts: the order as drafted · status · order id · note"]
    A3["state.sqlite exchanges · meta: onboarded_at · answers · tour · last run"]
    A4["usage.jsonl · runs/*.json"]
  end
  S2 -. "same draft id links them<br/>(verified by GET /api/orders?client_reference=)" .- A2
  R1["gear → 'Ask me the questions again'<br/>POST /api/onboarding/restart"] -- "clears onboarded_at · answers · tour" --> A3
  R2["gear → 'Forget everything and start over'<br/>POST /api/reset (type DIR to confirm)<br/>or stop the web server and rm -rf .marble/DIR"] -- "deletes" --> AST
  R3["terminal A: stop serve · rm .state/NAME.sqlite<br/>serve again with the same --port and --state"] -- "recreates: no drafts, no orders,<br/>revision 1, scenario normal, new operator token" --> SIMS
```

The two files never open each other; the only link is the draft id, which the assistant uses to
verify an order after a lost response. Re-asking the questions keeps every belief (a changed answer
supersedes the old one with the chain kept). Forgetting everything wipes the assistant's directory and
Marbly opens with the four-step setup on the next reload, but the supplier still holds yesterday's
drafts and orders, so the wizard's list and the planner start empty while `GET /api/orders` does not.
For a fresh run of the whole demo wipe both: reset the assistant from the gear, then stop the
simulator, delete its file and start it again; the About page prints the exact commands for this
server's paths.
