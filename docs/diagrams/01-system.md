# System architecture

```mermaid
flowchart LR
  subgraph A["Terminal A: operator (human)"]
    SIM["simulator.py serve --mode agent<br/>--port P --state .state/NAME.sqlite"]
    SST[("simulator state<br/>drafts · orders · operator token<br/>scenario · dataset_revision")]
    OP["simulator.py approve / scenario<br/>operator credential: never given to the assistant"]
    SIM <--> SST
  end
  subgraph B["Assistant process: python -m assistant … (CLI) or … web"]
    API["api_client.py<br/>Bearer demo-agent-site-01 · site_id pinned"]
    REC["records.py<br/>typed rows · citations · revision dedupe · evidence wrapping"]
    AN["analysis.py → detectors.py<br/>ranked Findings"]
    POL["policies.py<br/>beliefs → Params · recheck · effect of answers"]
    ONB["onboarding.py<br/>question plan · fixed options"]
    TOOLS["tools.py<br/>READ · ANALYSIS · MEMORY by mode"]
    LLM["llm.py<br/>tool loop · gate · events · cost"]
    ORD["ordering.py → orderflow.py<br/>sizing · net of accepted · planner · state machine"]
    SRV["cli.py · web/server.py<br/>JSON + SSE · gates · alerts · tour"]
    LED[("assistant state .marble/DIR/<br/>state.sqlite: beliefs · drafts · exchanges · meta<br/>usage.jsonl · runs/*.json")]
  end
  subgraph W["Browser"]
    CLI_UI["Marbly (client)<br/>setup · alerts · memory · wizard · planner"]
    DEV["dev pages (gear)<br/>chat · memory · alerts · order lab · tools · injection · about"]
  end
  CL["Claude API<br/>Sonnet 5.5 via .env (Opus 5.5 default)"]
  COST[("~/.marble/costs.jsonl<br/>every model call, machine-wide")]
  EV["evals/<br/>8 evaluators · agentic suite with a judge"]
  SIM -- "GET /api/{resource}" --> API --> REC --> AN
  POL --> AN --> SRV
  ONB --> POL
  LED <--> POL
  SRV --> TOOLS --> LLM <--> CL
  TOOLS --> LED
  LLM --> LED
  LLM --> COST
  SRV --> ORD -- "POST drafts · POST orders · GET orders" --> SIM
  ORD <--> LED
  CLI_UI <--> SRV
  DEV <--> SRV
  OP -. "approve · scenario" .-> SIM
  EV -. "replays run records<br/>drives a scenario" .-> SRV
```

Two databases, two credentials. The simulator's SQLite (left) holds the supplier's view and the
operator token; the assistant's SQLite (right) holds what the manager decided, what the detectors
observed, and the drafts as the assistant saw them. The dashed line is the credential boundary:
the operator's helper talks to the fixture; the assistant only ever holds the agent token, and
nothing in its process opens `.state/` or the CSVs. The browser talks only to the assistant.
