"""Deterministic rules engine.

Keeps guardrail and attention logic out of the UI and out of the model:
- risk flag detection (S1-04)
- input validation before any model call
- attention queue membership (used by later sprints)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from models.schemas import (
    AIAnalysis,
    CLOSED_STAGES,
    Lead,
    NextAction,
    Priority,
    RiskFlag,
    Stage,
)

# Keyword triggers for risk flags. Deterministic and testable.
RISK_KEYWORDS = {
    RiskFlag.LEGAL: [
        "lawyer", "attorney", "legal action", "legal", "sue", "lawsuit",
        "take action", "misleading", "breach",
    ],
    RiskFlag.REFUND: ["refund", "money back", "reimburse", "chargeback"],
    RiskFlag.ACCOUNT_CLOSURE: [
        "close my account", "close the account", "cancel my account",
        "account closure", "terminate my account",
    ],
    RiskFlag.SENSITIVE_DATA: [
        "ssn", "social security", "bank details", "bank account", "credit card",
        "medical record", "medical history", "passport", "driver's license",
    ],
    RiskFlag.COMPLAINT: [
        "complaint", "did not meet", "didn't meet", "unhappy", "terrible",
        "worst", "disappointed", "poor service",
    ],
    RiskFlag.SAFETY: ["safety", "unsafe", "danger", "injury", "injured", "hazard"],
    RiskFlag.REGULATED_ADVICE: [
        "medical advice", "legal advice", "financial advice", "tax advice",
        "investment advice", "diagnose", "diagnosis", "prescribe",
    ],
}

NON_SALES_STAGES = CLOSED_STAGES
CRITICAL_FIELDS = ("name", "inquiry", "stage", "received_at")


def detect_risk_flags(lead: Lead) -> List[RiskFlag]:
    """Return deterministic risk flags found in the lead text."""
    text = " ".join(filter(None, [lead.inquiry or "", lead.notes or ""])).lower()
    flags: List[RiskFlag] = []
    for flag, keywords in RISK_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            flags.append(flag)
    return flags or [RiskFlag.NONE]


def merge_risk_flags(*flag_lists: Iterable[RiskFlag]) -> List[RiskFlag]:
    merged = {RiskFlag.NONE}
    for flags in flag_lists:
        for flag in flags:
            merged.add(flag)
    result = [f for f in merged if f != RiskFlag.NONE]
    return result or [RiskFlag.NONE]


def has_risk(flags: Iterable[RiskFlag]) -> bool:
    return any(f != RiskFlag.NONE for f in flags)


def validate_lead_input(lead: Lead) -> List[str]:
    """Return a list of validation errors that should block a model call."""
    errors: List[str] = []
    if not lead.name or not lead.name.strip():
        errors.append("name is required")
    if not lead.inquiry or not lead.inquiry.strip():
        errors.append("inquiry is required")
    if lead.stage is None:
        errors.append("stage is required")
    if lead.received_at is None:
        errors.append("received_at is required")
    return errors


def enforce_risk_action(flags: Iterable[RiskFlag], next_action: NextAction) -> NextAction:
    """Risk flags always route to human review (03_AI_Prompt_and_Behavior_Spec)."""
    if has_risk(flags):
        return NextAction.ESCALATE
    return next_action


def default_follow_up_days(priority: Priority, next_action: NextAction) -> int:
    if next_action == NextAction.ESCALATE:
        return 0
    if next_action == NextAction.NURTURE:
        return 30
    if priority == Priority.HIGH:
        return 1
    if priority == Priority.MEDIUM:
        return 2
    return 5


# --------------------------------------------------------------- queue logic
def _today(today: Optional[datetime] = None) -> datetime:
    now = today or datetime.now(timezone.utc)
    return now


def is_overdue(lead: Lead, analysis: Optional[AIAnalysis], today: Optional[datetime] = None) -> bool:
    if lead.stage in CLOSED_STAGES or analysis is None:
        return False
    return analysis.follow_up_at.date() < _today(today).date()


def is_due_today(lead: Lead, analysis: Optional[AIAnalysis], today: Optional[datetime] = None) -> bool:
    if lead.stage in CLOSED_STAGES or analysis is None:
        return False
    return analysis.follow_up_at.date() == _today(today).date()


def is_new_hot(lead: Lead, analysis: Optional[AIAnalysis], window_hours: int = 48,
               today: Optional[datetime] = None) -> bool:
    if lead.stage in CLOSED_STAGES or analysis is None:
        return False
    if analysis.priority != Priority.HIGH:
        return False
    reference = _today(today)
    received = lead.received_at
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    return (reference - received) <= timedelta(hours=window_hours)


def is_stale(lead: Lead, stale_days: int = 7, today: Optional[datetime] = None) -> bool:
    if lead.stage in CLOSED_STAGES:
        return False
    reference = _today(today)
    last = lead.last_contact_at or lead.received_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (reference - last) > timedelta(days=stale_days)


def needs_review(lead: Lead, analysis: Optional[AIAnalysis]) -> bool:
    if lead.needs_review:
        return True
    if analysis is None:
        return True
    if analysis.confidence == "Low":
        return True
    return has_risk(analysis.risk_flags)


def classify_queues(lead: Lead, analysis: Optional[AIAnalysis], *, stale_days: int = 7,
                    window_hours: int = 48, today: Optional[datetime] = None) -> List[str]:
    queues: List[str] = []
    if is_overdue(lead, analysis, today):
        queues.append("Overdue")
    if is_due_today(lead, analysis, today):
        queues.append("Due Today")
    if is_new_hot(lead, analysis, window_hours, today):
        queues.append("New Hot Lead")
    if is_stale(lead, stale_days, today):
        queues.append("Stale")
    if needs_review(lead, analysis):
        queues.append("Needs Review")
    return queues
