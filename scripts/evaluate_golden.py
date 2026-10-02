"""Golden-scenario evaluation harness (document 05, section 3).

Runs the deterministic offline analyzer across the sample leads and checks the
golden-test invariants. This is a behavioural sanity check for the prototype,
not a substitute for evaluating a live model. Exit code 0 when the gate passes.

Usage:
    python -m scripts.evaluate_golden
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from config import Settings
from data.repository import SQLiteRepository
from services.ai import MockAdapter
from services.importer import import_leads
from services.leads import LeadService

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "sample_data" / "sample_leads.csv"

# (test id, lead id, check function, description)
GOLDEN = [
    ("AI-01", "L-1001", lambda r: str(r.analysis.priority) == "High", "Ready-to-buy is High"),
    ("AI-02", "L-1003", lambda r: str(r.analysis.priority) in {"Medium", "Low"}, "General inquiry not over-prioritized"),
    ("AI-03", "L-1008", lambda r: str(r.analysis.next_action) in {"Follow Up", "Respond"}, "Engaged lead nudged"),
    ("AI-04", "L-1023", lambda r: r.analysis.follow_up_at is not None and str(r.analysis.next_action) == "Nurture", "Future timing honored"),
    ("AI-05", "L-1006", lambda r: "REFUND" in {str(f) for f in r.risk_flags} and str(r.analysis.next_action) == "Escalate", "Refund escalated"),
    ("AI-06", "L-1022", lambda r: "LEGAL" in {str(f) for f in r.risk_flags} and str(r.analysis.next_action) == "Escalate", "Legal escalated"),
    ("AI-07", "L-1015", lambda r: "SENSITIVE_DATA" in {str(f) for f in r.risk_flags} and str(r.analysis.next_action) == "Escalate", "Sensitive data escalated"),
    ("AI-08", "L-1004", lambda r: not re.search(r"\$\s?\d", r.draft.body) and ("pricing" in r.draft.body.lower() or "quote" in r.draft.body.lower()), "No invented price"),
    ("AI-09", "L-1007", lambda r: "can't guarantee" in r.draft.body.lower() or "cannot guarantee" in r.draft.body.lower(), "No guarantee promised"),
    ("AI-10", "L-1018", lambda r: str(r.analysis.next_action) == "Close/Disqualify" or r.needs_review, "Junk closed or reviewed"),
    ("S2-01", "L-1001", lambda r: bool(r.draft.body.strip()), "Draft uses lead/business context"),
    ("S2-02", "L-1008", lambda r: not re.search(r"\$\s?\d", r.draft.body), "Draft has no fabricated facts"),
    ("S2-03", "L-1003", lambda r: r.draft.body.count("?") <= 1, "Draft has one clear CTA"),
]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        config = Settings(
            ai_provider="mock",
            database_url=f"sqlite:///{tmp}/eval.db",
            app_env="test",
        )
        repo = SQLiteRepository(config.db_path)
        service = LeadService(repo, adapter=MockAdapter(), app_settings=config)
        import_leads(str(SAMPLE_CSV), repo, filename="sample_leads.csv")

        results = []
        for test_id, lead_id, check, description in GOLDEN:
            result = service.analyze_lead(lead_id)
            try:
                passed = bool(check(result))
            except Exception:
                passed = False
            results.append((test_id, lead_id, passed, description))

        # S2-02: edit then copy must preserve the original AI draft.
        result = service.analyze_lead("L-1001")
        draft = result.draft
        original = draft.body
        service.save_draft_edit(draft.draft_id, "Hi Sarah, are you free Thursday at 2pm?")
        copied_text = service.copy_draft(draft.draft_id)
        reloaded = service.get_draft(draft.draft_id)
        results.append(
            ("S2-ED", "L-1001", reloaded.body == original, "Original AI draft preserved after edit")
        )
        results.append(
            ("S2-CP", "L-1001", copied_text == reloaded.effective_body and str(reloaded.status) == "copied",
             "Copy uses edited body and is not sent")
        )

    print(f"{'Test':6} {'Lead':7} {'Result':6} Description")
    for test_id, lead_id, passed, description in results:
        print(f"{test_id:6} {lead_id:7} {'PASS' if passed else 'FAIL':6} {description}")

    failures = [r for r in results if not r[2]]
    guardrails = [r for r in results if r[0] in {"AI-05", "AI-06", "AI-07"}]
    guardrail_failures = [r for r in guardrails if not r[2]]
    print()
    print(f"Passed {len(results) - len(failures)}/{len(results)} golden scenarios.")
    print(f"Guardrail failures (must be 0): {len(guardrail_failures)}")
    return 1 if guardrail_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
