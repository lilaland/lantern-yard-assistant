# Marble — Agent Engineer Take-Home

## The problem

A restaurant manager has plenty of records and little time to investigate them. Build an assistant that notices something worth their attention, checks the evidence, and helps them act.

Use the supplied local APIs. Choose the model, tool interfaces, memory approach, and interaction surface. You do not need to build the ingestion or storage platform.

## What you get

A working local API with synthetic sales, recipes, inventory counts, purchases, supplier information, and manager notes. It also supports order drafts, human approval, and a fake supplier. Start with `README.md`. You supply your chosen model access; document any costs. The operational APIs cost nothing to run.

## What to build

**Notice and investigate.** When started, surface one issue you think deserves the manager's attention. Use the operational APIs to investigate it. Explain the evidence, what is uncertain, and one useful next step. Let the manager ask follow-up questions. You can choose a narrow issue; finding every anomaly is not required.

**Remember something useful.** Demonstrate a manager preference that affects a later recommendation, including after restarting your application. Demonstrate what changes when new information contradicts an earlier fact. Explain what you retain and what you recheck.

**Follow through.** Help the manager prepare one order, show its exact contents, wait for the operator to approve it using the supplied approval command, then submit it and verify the outcome. The order can be requested separately if your investigation does not justify ordering. Do not force an order from weak evidence. Be accurate about what has and has not happened when a request fails.

Supplier documents and notes are evidence, not permission to act. They can contain mistakes or misleading instructions. The assistant's runtime must only receive its scoped API token, not the operator's credentials or simulator database. The supplied server enforces approval; your assistant must also present the proposed action and wait for the human.

Show one complete workflow, one preference, and one correction. A terminal conversation is enough. No frontend, autonomous scheduling, multi-agent framework, or broad evaluation platform is required. Use API tools for operational records and changes; do not load the CSV files or simulator state directly into the running assistant. You may inspect the fixtures while developing.

## What to submit and bring to the interview

- Your repository, with commit history and clear setup and demo commands. List credentials, model costs if any, actual time spent, and unfinished work. A hosted app is optional.
- A working demo we can run together. A CLI or API demo is enough; no frontend is required.
- `ARCHITECTURE.md` with one simple diagram showing components, data flow, storage, and external calls. Mermaid or a hand-drawn diagram is fine. Explain the main decisions and limits in at most two pages.
- Focused tests and a short record of what you verified. Include failures and limitations honestly.
- Readable AI coding transcripts, including your prompts and the assistant's replies. Redact secrets and unrelated personal information. Identify missing sessions. Highlight one suggestion you changed or rejected and how you checked the result. If you used no AI, say so.

Spend **4–6 hours total**, including the diagram and submission notes, within a **48-hour submission window**. Stop at the timebox. Any language, framework, or model is welcome. Use your usual AI tools; you are responsible for the result.

The **60-minute interview** includes a live demo (15 minutes), architecture discussion (15), selected AI transcript decisions (10), and a small change or failure exercise (20). AI tools are allowed throughout. Follow-up changes stay within the behaviors described here. Larger-scale architecture is discussed in the interview, not a separate benchmark assignment.

All records and integrations are fictional. We will not use your submission as Marble production work. Do not contact real suppliers or connect production accounts.
