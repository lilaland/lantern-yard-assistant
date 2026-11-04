# System architecture

```mermaid
flowchart LR
  subgraph A["Terminal A: operator (human)"]
    SIM["simulator.py serve --mode agent<br/>fixture state in .state/"]
    OP["simulator.py approve / scenario<br/>operator credential: never given to the assistant"]
  end
  subgraph B["Assistant process: python -m assistant (CLI) or web"]
    API["api_client.py<br/>Bearer demo-agent-site-01, site_id pinned"]
    REC["records.py<br/>typed rows · citations · revision dedupe · evidence wrapping"]
    AN["analysis.py → detectors.py<br/>ranked Findings"]
    POL["policies.py<br/>beliefs → Params · recheck"]
    LED[("ledger .marble/state.sqlite<br/>beliefs · drafts · exchanges · meta")]
    TOOLS["tools.py<br/>READ · ANALYSIS · MEMORY by mode"]
    LLM["llm.py<br/>tool loop · gate · events · cost log"]
    ORD["ordering.py → orderflow.py<br/>sizing · approval state machine"]
    UI["cli.py · web/server.py"]
  end
  CL["Claude API"]
  SIM -- "GET /api/{resource}" --> API --> REC --> AN
  POL --> AN --> UI
  LED <--> POL
  UI --> TOOLS --> LLM <--> CL
  TOOLS --> LED
  UI --> ORD -- "POST drafts · POST orders · GET orders" --> SIM
  ORD <--> LED
  OP -. "approve · scenario" .-> SIM
```

The credential boundary is the dashed line: the operator's helper talks to the fixture; the
assistant only ever holds the agent token. Nothing in the assistant process reads `.state/` or the CSVs.
