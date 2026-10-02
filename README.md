# AI Lead Follow-Up Assistant (Prototype)

Human-in-the-loop, draft-only lead assistant. This repository implements
**Sprint 1** of the build kit:

| Story | Feature | Status |
|-------|---------|--------|
| S1-01 | CSV/XLSX lead import with per-row validation | Done |
| S1-02 | Manual lead entry | Done |
| S1-03 | AI analysis with validated structured output | Done |
| S1-04 | Risk flag detection and escalation | Done |

Not yet built (later sprints): draft edit/copy workflow, Today attention queue,
dashboard, activity-driven follow-up recalculation. The deterministic queue
helpers already exist in `services/rules.py`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional; defaults work with the offline mock provider
python -m scripts.seed        # create DB, seed business profile, import 30 leads
streamlit run app.py
```

Open the URL Streamlit prints (default http://localhost:8501).

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
app.py                  Streamlit entry point
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
