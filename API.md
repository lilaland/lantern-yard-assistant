# Local operational API

Start: `python3 simulator.py serve --mode agent` (Python 3.10+, standard library only).
Base URL: `http://127.0.0.1:8765`. Agent requests use `Authorization: Bearer demo-agent-site-01`.
`GET /health` needs no token. Errors are JSON `{"error":"code"}` with an appropriate HTTP status.
This is a local test fixture, not a production server or a reference architecture you must copy.

## Read records

`GET /api/{resource}?site_id=SITE-01&limit=50&offset=0`

Resources: `sites`, `invoice_headers`, `invoice_lines`, `pos_daily`, `inventory_counts`, `vendors`, `vendor_catalog`, `menu`, `recipes`, `ingredient_mappings`, `notes`, `supplier_documents`.

Responses: `{"records":[...],"total":123,"next_offset":50,"source":"pos_daily.csv","dataset_revision":1}`.
Continue until `next_offset` is null. Limit is 1–100. CSV fields are returned as strings; numeric interpretation belongs in your tools. Every row includes its source identity and restaurant.

All resource fields support exact-value query filters. `date_from` and `date_to` are inclusive YYYY-MM-DD filters on sales business date, invoice header invoice date, count timestamp, or note timestamp. Other resources do not accept date filters. Invalid/unknown filters return 400. SITE-02 requests with the agent token return 403. These are simple record APIs: implement useful bounded tool calls and any calculations you need.

Examples:

```sh
curl -H 'Authorization: Bearer demo-agent-site-01' 'http://127.0.0.1:8765/api/pos_daily?pos_item_id=M05&date_from=2026-09-26&limit=100'
curl -H 'Authorization: Bearer demo-agent-site-01' 'http://127.0.0.1:8765/api/invoice_lines?invoice_id=INV-099'
```

## Draft, human approval, submit, verify

`POST /api/drafts` with JSON:

```json
{"site_id":"SITE-01","vendor_id":"V1","lines":[{"sku":"AW-CHK20","quantity":2,"unit":"case"}]}
```

Returns 201 with `draft_id`, `status: awaiting_approval`, and the exact `order`. This example demonstrates format, not a recommended purchase. Drafts are immutable. A change requires a new draft and new approval. Valid quantities are integer 1–100; 1–20 distinct catalog SKUs, all for the specified vendor. Orders do not model future prices, payments, or fulfillment.

The assistant shows the draft. The **human**, after reviewing it, runs in another terminal:

```sh
python3 simulator.py approve --draft-id D-REPLACE_WITH_ACTUAL_ID
```

The helper reads an operator credential from the local fixture state. Do not give the assistant that credential, the state database, or shell access to the approval helper. The candidate acting as operator can run it manually during the demo. This isolates the fixture's human action from the agent's tool calls; it does not prevent a developer with local filesystem access from modifying their own fixture.

`GET /api/drafts/{draft_id}` returns the draft and its current approval status.
`POST /api/orders` with `{"draft_id":"D-..."}` submits an approved draft. Unapproved submission returns 409. The draft ID is the order's `client_reference`.
`GET /api/orders?client_reference=D-...` returns `{"orders":[...]}`. An empty array means no order exists for that reference in the local supplier.
Accepted orders include `order_id`, `client_reference`, `status: accepted`, and the exact order payload. Accepted means the fake supplier recorded the order; it does not mean delivery. Repeating submission of the same approved draft returns the same order. A newly created draft is a different order.

## Scenario controls (human/developer only)

```sh
python3 simulator.py scenario --name delivery-correction
python3 simulator.py scenario --name supplier-timeout
```

The first publishes a later delivery correction and manager note through the APIs. Treat scenario time as October 5, 06:15 afterward. The second makes the next newly created supplier order commit and then lose its HTTP response. This models an unknown result after a network failure; you may see a connection-closed exception instead of a literal timeout. Existing-order reads and repeat submissions still work.

`python3 simulator.py scenario --name normal` removes scenario overrides; it does not delete orders or approvals. For a fresh run, stop the server and start with a new `--state path.sqlite`. Pass the same `--state` to control commands. `--port` is supported by all commands. Ordinary restarts keep the saved state. Scenario flags are persisted too. Reset only between separate demo runs.
