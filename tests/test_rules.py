from datetime import datetime, timedelta, timezone

from models.schemas import AIAnalysis, Lead, NextAction, Priority, RiskFlag, Stage
from services import rules


def _lead(inquiry, notes=None, stage=Stage.NEW):
    return Lead(lead_id="lead_test", name="Test", inquiry=inquiry, notes=notes, stage=stage)


def test_refund_detected():
    assert RiskFlag.REFUND in rules.detect_risk_flags(_lead("I want a refund immediately."))


def test_legal_detected():
    flags = rules.detect_risk_flags(_lead("My lawyer says your message was misleading."))
    assert RiskFlag.LEGAL in flags


def test_sensitive_data_detected():
    flags = rules.detect_risk_flags(_lead("I sent my SSN and bank details."))
    assert RiskFlag.SENSITIVE_DATA in flags


def test_regulated_advice_detected():
    flags = rules.detect_risk_flags(_lead("Can your AI give medical advice to my clients?"))
    assert RiskFlag.REGULATED_ADVICE in flags


def test_normal_lead_has_no_risk():
    assert rules.detect_risk_flags(_lead("We need a website redesign.")) == [RiskFlag.NONE]


def test_enforce_risk_action_forces_escalation():
    assert rules.enforce_risk_action([RiskFlag.REFUND], NextAction.RESPOND) == NextAction.ESCALATE
    assert rules.enforce_risk_action([RiskFlag.NONE], NextAction.RESPOND) == NextAction.RESPOND


def _analysis(days_from_now: int, priority=Priority.HIGH) -> AIAnalysis:
    from models.schemas import BuyingSignal, Confidence, Sentiment

    return AIAnalysis(
        lead_id="lead_test",
        prompt_version="test",
        intent="test",
        summary="test",
        priority=priority,
        priority_rationale="test",
        buying_signal=BuyingSignal.STRONG,
        sentiment=Sentiment.POSITIVE,
        risk_flags=[RiskFlag.NONE],
        next_action=NextAction.RESPOND,
        follow_up_at=datetime.now(timezone.utc) + timedelta(days=days_from_now),
        confidence=Confidence.HIGH,
    )


def test_closed_leads_excluded_from_attention_queue():
    today = datetime.now(timezone.utc)
    won = _lead("Signed", stage=Stage.WON)
    overdue_analysis = _analysis(-3)
    assert rules.is_overdue(won, overdue_analysis, today) is False
    assert rules.classify_queues(won, overdue_analysis, today=today) == []


def test_overdue_and_due_today_logic():
    today = datetime.now(timezone.utc)
    assert rules.is_overdue(_lead("x"), _analysis(-2), today) is True
    assert rules.is_due_today(_lead("x"), _analysis(0), today) is True
    queues = rules.classify_queues(_lead("x"), _analysis(-2), today=today)
    assert "Overdue" in queues
    assert "Needs Review" not in queues
