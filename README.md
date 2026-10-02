# AI Lead Follow-Up Assistant (Prototype)

Human-in-the-loop, draft-only lead assistant. This repository implements
All four MVP sprints of the build kit:

| Story | Feature | Status |
|-------|---------|--------|
| S1-01 | CSV/XLSX lead import with per-row validation | Done |
| S1-02 | Manual lead entry | Done |
| S1-03 | AI analysis with validated structured output | Done |
| S1-04 | Risk flag detection and escalation | Done |
| S2-01 | Personalized follow-up draft (context, one CTA, no fabrication) | Done |
| S2-02 | Edit and copy draft; original preserved; copy never means sent | Done |
| S3-01 | Deterministic Today attention queue (overdue/due/new-hot/stale/review) | Done |
| S3-02 | Record outcome; update state; recalculate next follow-up | Done |
| S4-01 | Dashboard: filters, reconciled counts, click-through to lead detail | Done |
| S4-02 | Business profile configuration with blank-profile warnings | Done |

All four MVP sprints are implemented. Deferred to a future pilot (see PRD):
CRM/email/calendar integrations, event-driven ingestion, autonomous sending,
multi-tenant/RBAC.

## Setup

Two UIs are provided over the same domain layer:

- **Streamlit** (`app.py`) — the local prototype UI.
- **Flask** (`api/index.py`) — the Vercel-compatible UI (exports `app`).

### Local (Streamlit)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # optional; defaults work with the offline mock provider
python -m scripts.seed        # create DB, seed business profile, import 30 leads
streamlit run app.py
```

Open the URL Streamlit prints (default http://localhost:8501).

### Local (Flask, same as production entrypoint)

```bash
pip install -r requirements.txt
python api/index.py           # http://localhost:5000
```

## Deploy to Vercel

`api/index.py` exports a Flask `app` and `vercel.json` routes all paths to it.

1. Import the repo in Vercel (Framework Preset: **Other**).
2. Keep the default build settings; Vercel installs `requirements.txt` and uses
   `api/index.py`.
3. (Optional) Add environment variables: `AI_PROVIDER=openai`, `AI_MODEL`,
   `AI_API_KEY` for live analysis. With no variables it runs the offline `mock`
   provider.

**Storage caveat:** Vercel's filesystem is read-only except `/tmp` and function
instances are ephemeral. This deployment seeds the bundled synthetic data into
`/tmp` on cold start, so the demo works with no external services but **data
resets per instance**. For durable data, point `DATABASE_URL` at an external
database and add a matching repository adapter.

## AI providers

- `AI_PROVIDER=mock` (default) — deterministic offline analyzer used for local
  demos and tests. No API key or network required.
- `AI_PROVIDER=openai` — OpenAI-compatible chat completions. Set `AI_MODEL`,
  `AI_API_KEY` and optionally `AI_BASE_URL`.

The AI layer validates every response against the JSON schema, retries once with
a repair prompt, and otherwise marks the lead **Needs Review** without deleting
it. Risk flags are also detected deterministically, and any flagged lead is
forced to the `Escalate` next action.

## Tests

```bash
python -m pytest -q
python -m scripts.evaluate_golden   # golden scenario invariants (doc 05)
```

## Layout

```
app.py                  Streamlit entry point (local UI)
api/index.py            Flask entry point (Vercel-compatible UI, exports `app`)
vercel.json             Vercel Python build + route config
config.py               Environment/config loading
models/schemas.py       Pydantic entities, enums, AI contract
services/ai.py          Provider adapters, prompt assembly, validation, retry
services/rules.py       Risk detection, validation, queue/attention rules
services/leads.py       Application service (create/analyze/activity)
services/importer.py    CSV/XLSX import with validation reports
data/repository.py      SQLite persistence and history
prompts/                Versioned system + analysis prompts
sample_data/            Synthetic 30-lead CSV/XLSX
tests/                  Unit + acceptance tests
scripts/                seed + golden evaluation
```

## Safety boundary

Version 1 never sends messages and makes no customer commitments. Drafts are
generated for human review only.
