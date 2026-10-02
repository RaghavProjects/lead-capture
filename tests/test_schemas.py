from models.schemas import AIAnalysisOutput, RiskFlag, Stage


def _payload(**overrides):
    base = {
        "intent": "x",
        "summary": "x",
        "priority": "High",
        "priority_rationale": "x",
        "buying_signal": "Strong",
        "sentiment": "Positive",
        "risk_flags": ["NONE"],
        "next_action": "Respond",
        "follow_up_days": 1,
        "confidence": "High",
        "draft_subject": "s",
        "draft_body": "b",
    }
    base.update(overrides)
    return base


def test_enums_validate():
    assert Stage("Proposal Sent") == Stage.PROPOSAL_SENT
    assert AIAnalysisOutput.model_validate(_payload()).priority == "High"


def test_unknown_risk_flag_is_treated_as_complaint():
    out = AIAnalysisOutput.model_validate(_payload(risk_flags=["TOTALLY_UNKNOWN"]))
    assert RiskFlag.COMPLAINT in out.risk_flags


def test_none_flag_is_exclusive():
    out = AIAnalysisOutput.model_validate(_payload(risk_flags=["NONE", "REFUND"]))
    assert out.risk_flags == [RiskFlag.REFUND]
    assert out.has_risk is True


def test_follow_up_days_bounds():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AIAnalysisOutput.model_validate(_payload(follow_up_days=-1))
