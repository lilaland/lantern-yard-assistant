# Marble candidate packet

Start here. Everything is synthetic.

## Setup


1. Read `CANDIDATE_BRIEF.md` and `SITE_PROFILE.md`.
2. Run `python3 simulator.py serve --mode agent`.
3. Check `curl http://127.0.0.1:8765/health`.
4. Read `API.md` for the scoped agent token, reads, order flow, and manual scenario controls.
5. Build your assistant around those APIs. Use `DATA_GUIDE.md` to understand the records. A human runs approval and scenario commands in a separate terminal.

The supplied APIs already handle operational data storage and the mock order workflow. Implement your assistant's tools, reasoning flow, and useful memory. The CSVs are included for transparency; the running assistant uses the APIs. No dataset download, cloud account, frontend, or scale benchmark is required.

## Requirements

Python 3.10 or newer for the supplied tools; no pip packages. Your implementation can use any stack. The server binds only to 127.0.0.1. Default port: 8765. State is stored in `.state/simulator.sqlite` and survives restarts. Use Ctrl-C to stop it. No real network integration is called.

The assignment is 4–6 hours including submission materials, within 48 hours. Read the brief for the demo, architecture diagram, transcripts, and interview requirements. Do not spend time polishing a UI.
