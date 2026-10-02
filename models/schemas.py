"""Pydantic entities, enums and validators for the AI Lead Follow-Up Assistant.

Sprint 1 scope (see 04_User_Stories): lead intake, manual entry, AI analysis
with structured output validation, and risk handling.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - convenience for UI/printing
        return self.value


class Stage(StrEnum):
    NEW = "New"
    CONTACTED = "Contacted"
    QUALIFIED = "Qualified"
    PROPOSAL_SENT = "Proposal Sent"
    NURTURE = "Nurture"
    WON = "Won"
    LOST = "Lost"
    NOT_A_LEAD = "Not a Lead"


class Priority(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class ResponseStatus(StrEnum):
    NOT_CONTACTED = "Not Contacted"
    AWAITING_RESPONSE = "Awaiting Response"
    RESPONDED = "Responded"
    CLOSED = "Closed"


class NextAction(StrEnum):
    RESPOND = "Respond"
    ASK_CLARIFYING_QUESTION = "Ask Clarifying Question"
    SCHEDULE_DISCOVERY = "Schedule Discovery"
    PREPARE_QUOTE = "Prepare Quote"
    FOLLOW_UP = "Follow Up"
    NURTURE = "Nurture"
    ESCALATE = "Escalate"
    CLOSE_DISQUALIFY = "Close/Disqualify"


class RiskFlag(StrEnum):
    LEGAL = "LEGAL"
    REFUND = "REFUND"
    ACCOUNT_CLOSURE = "ACCOUNT_CLOSURE"
    SENSITIVE_DATA = "SENSITIVE_DATA"
    COMPLAINT = "COMPLAINT"
    SAFETY = "SAFETY"
    REGULATED_ADVICE = "REGULATED_ADVICE"
    NONE = "NONE"


class Confidence(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class BuyingSignal(StrEnum):
    STRONG = "Strong"
    MODERATE = "Moderate"
    WEAK = "Weak"
    NONE = "None"


class Sentiment(StrEnum):
    POSITIVE = "Positive"
    NEUTRAL = "Neutral"
    NEGATIVE = "Negative"
    MIXED = "Mixed"


class DraftStatus(StrEnum):
    """Draft lifecycle. V1 is draft-only: there is intentionally no 'sent' state."""

    DRAFT = "draft"
    EDITED = "edited"
    COPIED = "copied"


OPEN_STAGES = {Stage.NEW, Stage.CONTACTED, Stage.QUALIFIED, Stage.PROPOSAL_SENT, Stage.NURTURE}
CLOSED_STAGES = {Stage.WON, Stage.LOST, Stage.NOT_A_LEAD}


class Lead(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    lead_id: str = Field(default_factory=lambda: new_id("lead"))
    name: str
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    inquiry: str
    stage: Stage = Stage.NEW
    received_at: datetime = Field(default_factory=utc_now)
    last_contact_at: Optional[datetime] = None
    response_status: ResponseStatus = ResponseStatus.NOT_CONTACTED
    notes: Optional[str] = None
    # Internal review state (not part of the imported schema)
    needs_review: bool = False
    review_reason: Optional[str] = None
    # Mutable next follow-up date, recalculated after recorded outcomes
    next_follow_up_at: Optional[datetime] = None

    @field_validator("name", "inquiry")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if v is None or not str(v).strip():
            raise ValueError("must not be empty")
        return str(v).strip()


class BusinessProfile(BaseModel):
    business_name: str = "Northstar Growth Studio"
    description: str = (
        "We help local service businesses improve lead response and customer "
        "follow-up using practical automation."
    )
    services: str = "Lead workflow assessment; automation consulting; implementation support"
    differentiators: Optional[str] = None
    tone: str = "Warm, concise, practical, professional"
    service_area: Optional[str] = "Dallas-Fort Worth"
    allowed_claims: str = "We offer workflow assessments and implementation support."
    restricted_topics: str = (
        "Do not quote prices unless explicitly supplied. Do not guarantee revenue results."
    )
    business_hours: Optional[str] = None
    follow_up_rules: str = (
        "New high-intent lead: same/next business day; normal inquiry: within 2 business "
        "days; proposal: 2-3 business days; honor explicit customer timing."
    )


class AIAnalysisOutput(BaseModel):
    """The validated structured-output contract returned by the AI layer.

    See 02_Technical_Design section 6 / 03_AI_Prompt_and_Behavior_Spec.
    """

    model_config = ConfigDict(extra="ignore")

    intent: str
    summary: str
    priority: Priority
    priority_rationale: str
    buying_signal: BuyingSignal
    sentiment: Sentiment
    risk_flags: List[RiskFlag] = Field(default_factory=lambda: [RiskFlag.NONE])
    next_action: NextAction
    follow_up_days: int = Field(default=2, ge=0, le=365)
    confidence: Confidence
    draft_subject: str
    draft_body: str

    @field_validator("risk_flags", mode="before")
    @classmethod
    def _normalise_flags(cls, v):
        if v is None:
            return [RiskFlag.NONE]
        if isinstance(v, str):
            v = [v]
        flags: List[RiskFlag] = []
        for item in v:
            flag = str(item).strip().upper()
            if not flag:
                continue
            try:
                flags.append(RiskFlag(flag))
            except ValueError:
                # Unknown flags are not silently dropped from a safety standpoint;
                # treat them as a generic complaint requiring review.
                flags.append(RiskFlag.COMPLAINT)
        if not flags:
            flags.append(RiskFlag.NONE)
        if len(flags) > 1:
            flags = [f for f in flags if f != RiskFlag.NONE]
        return flags

    @property
    def has_risk(self) -> bool:
        return any(flag != RiskFlag.NONE for flag in self.risk_flags)


class AIAnalysis(BaseModel):
    analysis_id: str = Field(default_factory=lambda: new_id("analysis"))
    lead_id: str
    prompt_version: str
    intent: str
    summary: str
    priority: Priority
    priority_rationale: str
    buying_signal: BuyingSignal
    sentiment: Sentiment
    risk_flags: List[RiskFlag]
    next_action: NextAction
    follow_up_at: datetime
    confidence: Confidence
    created_at: datetime = Field(default_factory=utc_now)


class DraftMessage(BaseModel):
    draft_id: str = Field(default_factory=lambda: new_id("draft"))
    lead_id: str
    analysis_id: str
    channel: str = "email"
    subject: str
    body: str
    status: DraftStatus = DraftStatus.DRAFT
    user_edited_body: Optional[str] = None
    copied_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def effective_body(self) -> str:
        """Text a user would actually copy: their edit if present, else the AI draft."""
        return self.user_edited_body or self.body

    @property
    def is_edited(self) -> bool:
        return bool(self.user_edited_body) and self.user_edited_body != self.body


class Activity(BaseModel):
    activity_id: str = Field(default_factory=lambda: new_id("activity"))
    lead_id: str
    activity_type: str
    outcome: Optional[str] = None
    notes: Optional[str] = None
    occurred_at: datetime = Field(default_factory=utc_now)


class AnalysisResult(BaseModel):
    analysis: AIAnalysis
    draft: DraftMessage
    needs_review: bool
    risk_flags: List[RiskFlag]


class QueueEntry(BaseModel):
    lead: Lead
    analysis: Optional[AIAnalysis] = None
    queues: List[str] = Field(default_factory=list)
    follow_up_at: Optional[datetime] = None


class AttentionQueue(BaseModel):
    """Ordered attention buckets for a given day (S3-01)."""

    generated_at: datetime = Field(default_factory=utc_now)
    buckets: Dict[str, List[QueueEntry]] = Field(default_factory=dict)
    counts: Dict[str, int] = Field(default_factory=dict)
